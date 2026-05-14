bl_info = {
    "name": "Krutart Publisher",
    "author": "iori, Krutart, Gemini",
    "version": (1, 8, 8), 
    "blender": (4, 0, 0),
    "location": "Properties > Output; Dope Sheet > Sidebar > Publisher",
    "description": "Streamlines incremental saving and hero file creation with detailed logging. Syncs identity with Configurator. Stamps version info for production pipelines.",
    "warning": "",
    "doc_url": "",
    "category": "Output",
}

import bpy
import os
import re
import logging
import shutil
import sys
import socket
import csv
import urllib.request
import urllib.error
import io
import json
import threading
import time
from bpy.app.handlers import persistent

# --- Constants ---
DAB_SPREADSHEET_ID = '1HxVVFK2ixML5MHv83ZhOXrUQNsgw6rpwoWHVMm9UZJc'
DAB_GID = '649829434'
DAB_CSV_URL = f"https://docs.google.com/spreadsheets/d/{DAB_SPREADSHEET_ID}/export?format=csv&gid={DAB_GID}"
# WebApp URLs - Default fallback values
DEFAULT_SHOTLIST_URL = "https://script.google.com/macros/s/AKfycbxJm8DPQ9hw5CRg9pgsbQMEyPzl9eTVu8LFaPPUzPfx5EF5zDfL4o8apxzUXS02wShTxQ/exec"

# Global cache for dashboard data
CACHED_DASH_DATA = {}
DASH_FETCH_STATUS = "Ready"

# ---

# --- Helper Functions ---

def get_os_bridge(context=None):
    """Safely retrieves the krutart-os_bridge module if available."""
    if 'krutart-os_bridge' in sys.modules:
        return sys.modules['krutart-os_bridge']
    for mod_name, mod in sys.modules.items():
        if hasattr(mod, "bl_info") and isinstance(mod.bl_info, dict):
            if mod.bl_info.get("name") == "Krutart OS Bridge":
                return mod
    return None

def get_current_filepath():
    """Returns the absolute path of the current Blender file."""
    return bpy.data.filepath

def get_current_user():
    """
    Determines the current user.
    """
    user_name = None
    configurator_mod = None

    if 'krutart-configurator' in sys.modules:
        configurator_mod = sys.modules['krutart-configurator']
    else:
        for mod_name, mod in sys.modules.items():
            if hasattr(mod, "bl_info") and isinstance(mod.bl_info, dict):
                if "Configurator" in mod.bl_info.get("name", ""):
                    configurator_mod = mod
                    break

    if configurator_mod:
        try:
            addon_prefs_obj = bpy.context.preferences.addons.get(configurator_mod.__name__)
            if addon_prefs_obj:
                prefs = addon_prefs_obj.preferences
                hostname = socket.gethostname().lower()
                if prefs.user_name_override.strip():
                    user_name = prefs.user_name_override.strip()
                elif hasattr(configurator_mod, "CACHED_IDENTITY_MAP"):
                    cached_map = configurator_mod.CACHED_IDENTITY_MAP
                    if hostname in cached_map:
                        user_name = cached_map[hostname]
        except Exception:
            pass

    if not user_name:
        text_block_name = "krutart-configurations.info"
        if text_block_name in bpy.data.texts:
            text_block = bpy.data.texts[text_block_name]
            match = re.search(r"last saved by:\s*(.*?)\s+-", text_block.as_string(), re.IGNORECASE)
            if match:
                user_name = match.group(1).strip()

    if not user_name:
        user_name = socket.gethostname().lower()

    if user_name:
        return re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)
    
    return "user"

def get_prefs(context):
    """Safely retrieves the addon preferences."""
    try:
        return context.preferences.addons[__name__].preferences
    except:
        # Fallback if __name__ is not registered correctly (e.g. text editor)
        for addon_name, addon in context.preferences.addons.items():
            if "Krutart Publisher" in addon.preferences.__class__.__name__ or "publisher" in addon_name.lower():
                return addon.preferences
        return None

# --- Dash Logic ---

class GoogleCSVClient:
    @staticmethod
    def fetch_dash_data():
        global CACHED_DASH_DATA, DASH_FETCH_STATUS
        try:
            logger.info(f"Fetching DAB Dashboard from: {DAB_CSV_URL}")
            response = urllib.request.urlopen(DAB_CSV_URL, timeout=10)
            data = response.read().decode('utf-8')
            
            f = io.StringIO(data)
            reader = csv.DictReader(f)
            
            new_data = {}
            for row in reader:
                shot_id = row.get('SHOT ID', '').strip()
                if shot_id:
                    new_data[shot_id] = row
            
            CACHED_DASH_DATA = new_data
            DASH_FETCH_STATUS = "Synced"
            logger.info(f"Dashboard synced successfully. {len(new_data)} shots loaded.")
            return True
        except Exception as e:
            DASH_FETCH_STATUS = f"Error: {str(e)}"
            logger.error(f"Failed to fetch dashboard: {e}")
            return False

def get_active_phase_for_shot(shot_id):
    """
    Scans the phase columns for a shot and returns the first active phase.
    Order: BLOCKING, FINCAM, ANIMATION, SETDRESS, VFX, LIGHTING
    """
    if not CACHED_DASH_DATA or shot_id not in CACHED_DASH_DATA:
        return None
    
    row = CACHED_DASH_DATA[shot_id]
    phases = ['BLOCKING', 'FINCAM', 'ANIMATION', 'SETDRESS', 'VFX', 'LIGHTING']
    
    for phase in phases:
        status = row.get(phase, '').strip().lower()
        # Active means it has a status that is not 'done', 'skip', 'not ready', or empty
        if status and status not in ('done', 'skip', 'not ready', 'nenalezeno v blk'):
            return phase.lower()
            
    return None

@persistent
def auto_switch_phase_on_load(dummy):
    """Triggered on file load to sync phase with dashboard."""
    # Run fetch in background thread to avoid UI freeze if we were being fancy,
    # but for now we do a simple sync or rely on manual refresh if needed.
    # Actually, let's just trigger a fetch.
    GoogleCSVClient.fetch_dash_data()
    
    # Try to auto-select phase
    _, asset_name, _, _ = parse_filename(bpy.data.filepath)
    if asset_name:
        active_phase = get_active_phase_for_shot(asset_name)
        if active_phase:
            # We need a context to set the property, but scene is available
            for scene in bpy.data.scenes:
                scene.krutart_publish_type = active_phase
                logger.info(f"Auto-switched {asset_name} to phase: {active_phase}")

# --- Publisher Sheets Logging ---

def _send_publisher_payload_thread(url, payload):
    """
    Worker function to send data to Google Sheets.
    Uses standard library urllib for zero-dependency compatibility.
    """
    data = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Blender-KrutartPublisher-Client'
    }

    max_retries = 2
    base_delay = 1 
    
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method='POST')
            logger.info(f"Uploading to Sheets: {url} (Attempt {attempt}/{max_retries})...")
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = response.read().decode('utf-8')
                logger.info(f"Google Sheet Response: {result}")
                break # Success!
                
        except Exception as e:
            logger.error(f"Error during Sheets upload: {e}")
        
        if attempt < max_retries:
            time.sleep(base_delay)

def upload_publisher_data(context, filepath, comment):
    """
    Prepares data and starts the upload thread for the publisher sheet.
    Determines if the project is a Shot or generic Asset.
    """
    if not filepath:
        return

    # Use URL from preferences if available, otherwise fallback
    prefs = get_prefs(context)
    target_url = prefs.shotlist_webapp_url if prefs and prefs.shotlist_webapp_url else DEFAULT_SHOTLIST_URL

    filename = os.path.basename(filepath)
    project, asset, flags, version = parse_filename(filepath)
    
    # Determine Project Type
    project_type = "Asset"
    if asset and re.search(r"sc\d+-sh\d+", asset, re.IGNORECASE):
        project_type = "Shot"
    
    user_name = get_current_user()
    version_str = f"v{version:03d}" if version is not None else "v000"

    payload = {
        "type": "publisher",
        "filename": filename,
        "project_type": project_type,
        "filepath": filepath,
        "version": version_str,
        "user": user_name,
        "comment": comment
    }

    t = threading.Thread(target=_send_publisher_payload_thread, args=(target_url, payload))
    t.start()

# ---

def get_publish_type_items(self, context):
    """
    Callback for Scene.krutart_publish_type EnumProperty.
    Detects the work line (ANI or ART) from the file path and returns appropriate phases.
    Includes any auto-detected active phase from the dashboard.
    """
    filepath = bpy.data.filepath.lower()
    items = []
    
    # Detect Workflow from path
    if "-art-" in filepath or "art-work" in filepath:
        items = [
            ('setdress', 'Setdress', 'Art: Setdress phase'),
            ('lighting', 'Lighting', 'Art: Lighting phase'),
            ('vfx', 'VFX', 'Art: VFX phase'),
            ('animation', 'Animation', 'Ani: Animation phase'),
        ]
    else:
        # Default to ANI
        items = [
            ('blocking', 'Blocking', 'Ani: Animation Blocking phase'),
            ('fincam', 'FinCam', 'Ani: Final Camera phase'),
        ]

    # --- NEW LOGIC: Dynamic Injection from Dashboard ---
    _, asset_name, _, _ = parse_filename(filepath)
    if asset_name:
        active_phase = get_active_phase_for_shot(asset_name)
        if active_phase:
            # Ensure the active phase is in the items list
            existing_ids = [it[0] for it in items]
            if active_phase not in existing_ids:
                items.insert(0, (active_phase, active_phase.capitalize(), f"Auto-detected phase from Dashboard: {active_phase}"))
    # --- END NEW LOGIC ---

    return items

def parse_filename(filepath):
    """
    Parses the filename to extract project name, asset name, flags, and version.
    This function is case-insensitive and returns all parts in lowercase.
    Expected format: PROJECT_NAME-ASSET_NAME-flags-v001-optional_comment.blend
    OR
    Expected format: PROJECT_NAME-ASSET_NAME-v001-optional_comment.blend
    """
    if not filepath:
        logger.warning("File has not been saved yet. Cannot parse filename.")
        return None, None, None, None

    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    
    # Make parsing case-insensitive by converting to lowercase
    name_lower = name.lower()
    
    # Find the version number flag, e.g., "-v001"
    version_match = re.search(r'-v(\d{3,})', name_lower)
    
    if not version_match:
        logger.warning(f"Filename '{name}' does not contain a version flag like '-v###'.")
        return None, None, None, None

    # Extract version and the part of the name before it
    version_str = version_match.group(1)
    version_int = int(version_str)
    
    before_version_part = name_lower[:version_match.start()]
    
    # Split the pre-version part to get project, asset, and flags
    parts = before_version_part.split('-')
    
    # --- MODIFIED LOGIC (v1.7.0): Shot Naming Support ---
    # Try SC-SH format: 3212-SC11-SH080-flags-v001
    shot_match = re.search(r"(sc\d+)-(sh\d+)", before_version_part, re.IGNORECASE)
    
    if shot_match:
        # It's a shot file
        sc_part = shot_match.group(1)
        sh_part = shot_match.group(2)
        asset_name = f"{sc_part}-{sh_part}".lower()
        
        # Project is everything before the shot identifier
        project_part = before_version_part[:shot_match.start()].strip('-')
        project_name = project_part if project_part else "unknown"
        
        # Flags are everything after the shot identifier (if any)
        flags_part = before_version_part[shot_match.end():].strip('-')
        flags = flags_part
        logger.debug("Parsed as SHOT file")
    else:
        # Original logic for generic assets
        parts = before_version_part.split('-')
        if len(parts) < 2:
            logger.debug(f"Filename '{name}' format is incorrect before version flag.")
            return None, None, None, None
        
        if len(parts) == 2:
            project_name = parts[0]
            asset_name = parts[1]
            flags = ""
        else:
            flags = parts[-1]
            asset_name = parts[-2]
            project_name = '-'.join(parts[:-2])
        logger.debug("Parsed as GENERIC asset file")
    
    logger.debug(f"Parsed filename: project='{project_name}', asset='{asset_name}', flags='{flags}', version='{version_str}'")
    return project_name, asset_name, flags, version_int

def _is_production(filepath):
    """Detects if we are currently operating on a PRODUCTION file."""
    if not filepath:
        return False
    return "3212-production" in filepath.lower()


# --- Operators ---

class KRUTART_OT_save_increment(bpy.types.Operator):
    """Saves the file with an incremented version number and opens the new file"""
    bl_idname = "krutart.save_increment"
    bl_label = "Save Increment"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        current_filepath = get_current_filepath()
        if not current_filepath:
            self.report({'ERROR'}, "Please save the file first.")
            logger.error("Save Increment failed: File has not been saved yet.")
            return {'CANCELLED'}

        directory = os.path.dirname(current_filepath)
        project, asset, flags, version = parse_filename(current_filepath)

        if version is None:
            self.report({'ERROR'}, "Filename format incorrect. Expected 'PROJECT-ASSET-[flags]-v###.blend'")
            logger.error("Save Increment failed: Could not parse filename.")
            return {'CANCELLED'}

        # Increment version
        new_version = version + 1
        new_version_str = f"v{new_version:03d}"
        
        # --- NEW LOGIC (v1.8.0): Dynamic publish type for PRODUCTION ---
        if _is_production(current_filepath):
            new_version_str += f"-{context.scene.krutart_publish_type}"
        # --- END NEW LOGIC ---

        logger.info(f"Incrementing version from v{version:03d} to {new_version_str}")

        # Get comment
        comment = context.scene.krutart_comment.strip()
        
        # --- MODIFIED: Check for comment ---
        if not comment:
            self.report({'ERROR'}, "Comment is required to save increment.")
            logger.error("Save Increment failed: No comment provided.")
            return {'CANCELLED'}
        # --- END MODIFICATION ---
        
        # Construct new filename base
        if flags:
            base_name = f"{project}-{asset}-{flags}-{new_version_str}"
        else:
            base_name = f"{project}-{asset}-{new_version_str}"
        
        # Sanitize comment for filename
        sanitized_comment = re.sub(r'[^a-zA-Z0-9_-]', '_', comment)
        
        # --- NEW LOGIC (v1.6.1): Insert User Name ---
        user_name = get_current_user()
        
        if user_name:
            new_filename = f"{base_name}-{user_name}-{sanitized_comment}.blend"
        else:
            # Fallback to old behavior if user not found
            new_filename = f"{base_name}-{sanitized_comment}.blend"
        # --- END NEW LOGIC ---

        logger.info(f"Comment added: '{comment}', sanitized to '{sanitized_comment}'")

        # Ensure filename is lowercase
        new_filepath = os.path.join(directory, new_filename.lower())

        logger.info(f"Saving new incremented file to: {new_filepath}")
        
        try:
            # Save the file and make it the active file
            bpy.ops.wm.save_as_mainfile(filepath=new_filepath)
            self.report({'INFO'}, f"Saved and switched to: {os.path.basename(new_filepath)}")
            context.scene.krutart_comment = "" # Clear comment field after save
            logger.info(f"File saved and opened successfully: {new_filepath}")
            
            # --- NEW: Log to Sheets ---
            upload_publisher_data(context, new_filepath, comment)
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save file: {e}")
            logger.error(f"An exception occurred during file save: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

class KRUTART_OT_make_hero(bpy.types.Operator):
    """Saves the current file, creates a 'hero' copy, then saves an incremented version of the work file."""
    bl_idname = "krutart.make_hero"
    bl_label = "Make hero"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        logger.info("-" * 50)
        logger.info("Starting 'Make hero' process...")

        # --- Preliminary Checks ---
        current_filepath = get_current_filepath()
        if not current_filepath:
            self.report({'ERROR'}, "Please save the file first.")
            logger.error("Make Hero failed: File has not been saved yet.")
            return {'CANCELLED'}

        project, asset, flags, version = parse_filename(current_filepath)
        if version is None:
            self.report({'ERROR'}, "Filename format incorrect. Expected 'PROJECT-ASSET-[flags]-v###.blend'")
            logger.error("Make Hero failed: Could not parse filename.")
            return {'CANCELLED'}

        # --- MODIFIED: Check for comment ---
        comment = context.scene.krutart_comment.strip()
        if not comment:
            self.report({'ERROR'}, "Comment is required to make hero.")
            logger.error("Make Hero failed: No comment provided.")
            return {'CANCELLED'}
        # --- END MODIFICATION ---

        # Define hero_filepath here to make it available for the final report
        hero_filepath = "[not saved]" # Initialize with a default/error string
        # This will be used by Step 3, so we define it early
        hero_asset_dir_path = "[not set]" 

        # --- Step 1: Normal save of current file ---
        try:
            logger.info(f"Step 1/4: Performing a normal save of the current file: {os.path.basename(current_filepath)}")
            bpy.ops.wm.save_mainfile()
            saved_work_filepath = get_current_filepath()
            logger.info(f"Step 1/4: Successfully saved current file to: {saved_work_filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save current file: {e}")
            logger.error(f"An exception occurred during initial save: {e}", exc_info=True)
            return {'CANCELLED'}

        # --- NEW LOGIC: Version Tracking Handshake (Production Only) ---
        injected_collection = None
        if _is_production(saved_work_filepath):
            filepath_lower = saved_work_filepath.lower()
            target_col_name = None
            
            # Detect Workflow from filename
            if "-ani-" in filepath_lower or "ani-work" in filepath_lower:
                target_col_name = "+ANI+"
            elif "-art-" in filepath_lower or "art-work" in filepath_lower:
                target_col_name = "+ART+"

            if target_col_name:
                injected_collection = bpy.data.collections.get(target_col_name)
                if injected_collection:
                    version_str = f"v{version:03d}"
                    injected_collection["source_work_version"] = version_str
                    logger.info(f"Injected custom property 'source_work_version'='{version_str}' into '{target_col_name}' for HERO stamp.")
        # --- END NEW LOGIC ---

        # --- Step 2: Create Hero File (as a copy) ---
        try:
            logger.info(f"Step 2/4: Creating Hero file from: {os.path.basename(saved_work_filepath)}")
            work_dir = os.path.dirname(saved_work_filepath)
            
            # --- UPDATED LOGIC (v1.7.1): Shot-Aware & OS Bridge ---
            bridge = get_os_bridge(context)
            work_dir_prod = None
            if bridge:
                work_dir_prod = bridge.to_win_absolute(work_dir, context)
            
            # Failsafe: If bridge fails to map the drive, fallback to original path
            if not work_dir_prod:
                work_dir_prod = work_dir

            # 1. Try to find if this is a Shot (for nested structure)
            parsed_proj, parsed_asset, parsed_dept, parsed_ver = parse_filename(saved_work_filepath)
            
            # Re-normalize for comparison
            if work_dir_prod:
                clean_dir = work_dir_prod.replace("\\", "/")
                path_parts = clean_dir.split("/")
            else:
                path_parts = []
            
            is_shot = False
            shot_root_dir = None
            
            # asset_name from parse_filename for shots is "scxx-shxxx"
            if parsed_asset and re.search(r"sc\d+-sh\d+", parsed_asset, re.IGNORECASE):
                # Search for the Shot Root folder in the path
                for i, p in enumerate(path_parts):
                    if p.lower().startswith(parsed_asset.lower()):
                         shot_root_dir = "/".join(path_parts[:i+1])
                         is_shot = True
                         break
            
            if is_shot and shot_root_dir:
                # Construct nested: {ShotRoot}/{ShotName}-HERO/{ShotName}-{Dept}-HERO/
                dept_tag = parsed_dept.upper() if parsed_dept else "ANI" 
                hero_asset_dir_prod = os.path.join(shot_root_dir, f"{parsed_asset.upper()}-HERO", f"{parsed_asset.upper()}-{dept_tag}-HERO")
                logger.debug(f"Shot detected. Constructing nested HERO structure: {hero_asset_dir_prod}")
            else:
                if work_dir_prod:
                    # 2. Global replacement for generic assets
                    # Replaces every instance of '-WORK' (case-insensitive) at the end of a folder name with '-HERO'
                    # e.g., LIBRARY-WORK\MODEL-WORK -> LIBRARY-HERO\MODEL-HERO
                    hero_asset_dir_prod = re.sub(r'-work\b', '-HERO', work_dir_prod, flags=re.IGNORECASE)
                    logger.debug(f"Asset detected. Using global transformation: {hero_asset_dir_prod}")
                else:
                    hero_asset_dir_prod = ""

            if work_dir_prod and work_dir_prod.lower() == hero_asset_dir_prod.lower():
                error_msg = "Could not identify how to transform this path to a HERO directory."
                self.report({'ERROR'}, error_msg)
                logger.error(f"{error_msg} Original path: {work_dir_prod}")
                return {'CANCELLED'}
            
            # Convert back to local path for actual OS operations (using improved bridge v1.7.4+)
            if bridge and hero_asset_dir_prod:
                hero_asset_dir_path = bridge.to_mac_absolute(hero_asset_dir_prod, context)
            
            # Failsafe: If bridge fails to resolve or is missing, use production path directly
            if not hero_asset_dir_path or hero_asset_dir_path == "[not set]":
                hero_asset_dir_path = hero_asset_dir_prod

            # Ensure slash consistency for Windows local operations
            if sys.platform.startswith("win") and hero_asset_dir_path:
                hero_asset_dir_path = hero_asset_dir_path.replace("/", "\\")
            # --- END UPDATED LOGIC ---

            logger.info(f"Transformed WORK path '{work_dir}' to HERO path '{hero_asset_dir_path}'")

            if not os.path.exists(hero_asset_dir_path):
                logger.info(f"Creating missing hero directory: {hero_asset_dir_path}")
                os.makedirs(hero_asset_dir_path, exist_ok=True)

            # Conditionally add flags to hero filename
            if flags:
                hero_filename = f"{project}-{asset}-{flags}-hero.blend"
            else:
                hero_filename = f"{project}-{asset}-hero.blend"
                
            hero_filepath = os.path.join(hero_asset_dir_path, hero_filename.lower())

            logger.info(f"Attempting to save Hero file copy to: {hero_filepath}")
            # Using copy=True saves the file without making it the active file in Blender
            bpy.ops.wm.save_as_mainfile(filepath=hero_filepath, copy=True)
            logger.info(f"Hero file successfully saved to {hero_filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create Hero file: {e}")
            logger.critical(f"An unexpected error in Hero creation logic: {e}", exc_info=True)
            return {'CANCELLED'}

        # --- NEW LOGIC: Cleanup Version Tracking Handshake ---
        if injected_collection and "source_work_version" in injected_collection:
            del injected_collection["source_work_version"]
            logger.info(f"Cleaned up 'source_work_version' from '{injected_collection.name}' after HERO save.")
        # --- END NEW LOGIC ---

        # --- Step 3: Failsafe copy of blender_assets.cats.txt (v1.4.9) ---
        try:
            logger.info("Step 3/4: Searching for 'blender_assets.cats.txt' in parent 'LIBRARY-WORK' folder...")
            
            # 1. Get current .blend directory
            current_blend_dir = os.path.dirname(saved_work_filepath)

            # 2. Find Source 'LIBRARY-WORK'
            source_library_dir = None
            temp_path = current_blend_dir
            
            # Limit search depth to 10 levels up to prevent infinite loops
            for _ in range(10): 
                # Check if the base folder name is 'library-work'
                if os.path.basename(temp_path).lower() == 'library-work':
                    source_library_dir = temp_path
                    logger.info(f"Found 'LIBRARY-WORK' directory at: {source_library_dir}")
                    break
                
                parent_path = os.path.dirname(temp_path)
                if parent_path == temp_path: # We've hit the root (e.g., S:\)
                    break
                temp_path = parent_path

            # 5. Failsafe Checks & Copy Logic
            if not source_library_dir:
                # This is a warning, not an error. The hero process can continue.
                logger.warning("Step 3/4: Could not find a parent 'LIBRARY-WORK' directory. Skipping .cats.txt copy.")
                self.report({'WARNING'}, "Could not find 'LIBRARY-WORK' folder. Skipping .cats.txt copy.")
            else:
                # 3. Define Source cats.txt Path
                source_cats_file = os.path.join(source_library_dir, "blender_assets.cats.txt")
                
                if not os.path.exists(source_cats_file):
                    # This is also a warning.
                    logger.warning(f"Step 3/4: Found '{source_library_dir}' but 'blender_assets.cats.txt' is missing. Skipping copy.")
                    self.report({'WARNING'}, "'blender_assets.cats.txt' not found in LIBRARY-WORK. Skipping copy.")
                else:
                    # 4. Define Destination cats.txt Path
                    # We build the path cleanly: '.../LIBRARY-WORK' -> '.../LIBRARY-HERO'
                    parent_of_library_work = os.path.dirname(source_library_dir)
                    dest_library_dir = os.path.join(parent_of_library_work, 'LIBRARY-HERO')
                    dest_cats_file = os.path.join(dest_library_dir, "blender_assets.cats.txt")
                    
                    logger.info(f"Source file: {source_cats_file}")
                    logger.info(f"Destination file: {dest_cats_file}")

                    # 5. Create Dest Dir & Copy
                    os.makedirs(dest_library_dir, exist_ok=True)
                    shutil.copy2(source_cats_file, dest_cats_file)
                    logger.info(f"Successfully copied 'blender_assets.cats.txt' to '{dest_library_dir}'.")
                    
        except Exception as e:
            # Report as an error, but do not cancel the 'Make Hero' process,
            # as the .cats.txt file is not critical.
            logger.error(f"Failed to copy 'blender_assets.cats.txt': {e}", exc_info=True)
            self.report({'ERROR'}, "Failed to copy 'blender_assets.cats.txt': See logs for details.")
            # This is not considered a critical failure, so the process continues.

        # --- Step 4: Run Save Incremental ---
        try:
            logger.info("Step 4/4: Performing final incremental save...")
            
            new_version = version + 1
            new_version_str = f"v{new_version:03d}"
            
            # --- NEW LOGIC (v1.8.0): Dynamic publish type for PRODUCTION ---
            if _is_production(saved_work_filepath):
                new_version_str += f"-{context.scene.krutart_publish_type}"
            # --- END NEW LOGIC ---

            logger.info(f"Incrementing work file from v{version:03d} to {new_version_str}")
            
            # We already have the comment from the preliminary check
            
            # Conditionally add flags to new filename
            if flags:
                base_name = f"{project}-{asset}-{flags}-{new_version_str}"
            else:
                base_name = f"{project}-{asset}-{new_version_str}"
            
            sanitized_comment = re.sub(r'[^a-zA-Z0-9_-]', '_', comment)
            
            # --- NEW LOGIC (v1.6.1): Insert User Name ---
            user_name = get_current_user()
            
            if user_name:
                new_filename = f"{base_name}-{user_name}-{sanitized_comment}.blend"
            else:
                new_filename = f"{base_name}-{sanitized_comment}.blend"
            # --- END NEW LOGIC ---
            
            logger.info(f"Comment added: '{comment}', sanitized to '{sanitized_comment}'")

            work_dir = os.path.dirname(saved_work_filepath)
            new_incremental_filepath = os.path.join(work_dir, new_filename.lower())

            logger.info(f"Saving new incremented file to: {new_incremental_filepath}")
            
            # This save action opens the new file, fulfilling the last requirement.
            bpy.ops.wm.save_as_mainfile(filepath=new_incremental_filepath)
            
            context.scene.krutart_comment = ""
            logger.info(f"New incremental file saved and opened successfully: {new_incremental_filepath}")
            
            # --- NEW: Log to Sheets ---
            upload_publisher_data(context, new_incremental_filepath, comment)
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save incremental file: {e}")
            logger.error(f"An exception occurred during final incremental save: {e}", exc_info=True)
            return {'CANCELLED'}

        # --- Final Report ---
        hero_basename = os.path.basename(hero_filepath)
        self.report({'INFO'}, f"Hero '{hero_basename}' created, and work file incremented to {new_version_str}")
        logger.info(f"Hero file saved to: {hero_filepath}") # Redundant log, but ensures it's logged at the end
        logger.info("'Make hero' process completed successfully.")
        logger.info("-" * 50)
        return {'FINISHED'}

class KRUTART_OT_send_to_tex_paint(bpy.types.Operator):
    """Copies the current Modeling asset to the Texturing structure, clears asset status, and initializes HERO."""
    bl_idname = "krutart.send_to_tex_paint"
    bl_label = "Send to Texture Paint"
    bl_description = "Prepares asset for texturing phase: copies file to TEX structure, resets version, clears asset status, and makes hero"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        filepath = bpy.data.filepath
        if not filepath:
            return False
        filepath_lower = filepath.lower()
        
        # Must be in LIBRARY-WORK
        if "library-work" not in filepath_lower:
            return False
            
        # EXCLUSION: If we are already in a texturing file, hide the button
        if "tex-work" in filepath_lower or "-tex-" in os.path.basename(filepath_lower):
            return False

        # Robust triggers: Look for department folders or asset prefixes
        triggers = ["model-work", "rig-work", "actor-work", "prop-work", "mod-", "act-", "prp-", "rig-"]
        return any(t in filepath_lower for t in triggers)

    def execute(self, context):
        logger.info("-" * 50)
        logger.info("Starting 'Send to Texture Paint' process...")

        # 1. Save current file
        try:
            bpy.ops.wm.save_mainfile()
            src_path = bpy.data.filepath
            logger.info(f"Step 1: Current source file saved: {src_path}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save current file: {e}")
            return {'CANCELLED'}

        # 2. Path Mapping (Generalized)
        try:
            dir_name = os.path.dirname(src_path)
            filename = os.path.basename(src_path)
            
            # Find the LIBRARY-WORK root
            match = re.search(r'LIBRARY-WORK', dir_name, re.IGNORECASE)
            if not match:
                self.report({'ERROR'}, "Could not identify 'LIBRARY-WORK' in path.")
                return {'CANCELLED'}
            
            root_path = dir_name[:match.end()]
            sub_path = dir_name[match.end():].strip(os.sep)
            
            # Target Root: ...\LIBRARY-WORK\TEX-WORK
            target_root = os.path.join(root_path, "TEX-WORK")
            
            # Transform sub_path components
            sub_parts = sub_path.replace('\\', '/').split('/')
            transformed_parts = []
            
            dept_map = {
                "MODEL-WORK": "TEX-MODEL-WORK",
                "RIG-WORK": "TEX-RIG-WORK",
                "ACTOR-WORK": "TEX-ACTOR-WORK",
                "PROP-WORK": "TEX-PROP-WORK",
            }
            prefix_map = {
                "MOD-": "TEX-",
                "ACT-": "TEX-",
                "PRP-": "TEX-",
            }
            
            for part in sub_parts:
                if not part: continue
                upper_part = part.upper()
                new_part = part
                
                if upper_part in dept_map:
                    new_part = dept_map[upper_part]
                else:
                    for old_p, new_p in prefix_map.items():
                        if upper_part.startswith(old_p):
                            new_part = new_p + part[len(old_p):]
                            break
                transformed_parts.append(new_part)
            
            final_dir = os.path.join(target_root, *transformed_parts)
            
            # Convert back to OS native path
            if sys.platform.startswith('win'):
                final_dir = final_dir.replace('/', '\\')
            
            # Filename transformation
            name_no_ext, ext = os.path.splitext(filename)
            # Replace first occurrence of -mod-, -act-, -prp- with -tex-
            new_name = re.sub(r'-(mod|act|prp)-', '-tex-', name_no_ext, count=1, flags=re.IGNORECASE)
            
            # Find version string -v###
            version_match = re.search(r'-v(\d{3,})', new_name)
            if not version_match:
                self.report({'ERROR'}, "Could not identify version in filename.")
                return {'CANCELLED'}
                
            before_version = new_name[:version_match.start()]
            
            # --- NEW LOGIC: Version Incrementing (No Overwrites) ---
            existing_versions = [0]
            if os.path.exists(final_dir):
                # Search for files that match the PROJECT-tex-NAME-v### pattern
                # This ensures we don't overwrite if the asset was sent before
                search_pattern = f"{re.escape(before_version)}-v(\d{{3}})"
                for f in os.listdir(final_dir):
                    m = re.search(search_pattern, f, re.IGNORECASE)
                    if m:
                        existing_versions.append(int(m.group(1)))
            
            next_version = max(existing_versions) + 1
            version_str = f"v{next_version:03d}"
            logger.info(f"Next available version in TEX-WORK: {version_str}")
            # --- END NEW LOGIC ---

            # Construct new filename with user and comment
            comment = context.scene.krutart_comment.strip() or "tex publish"
            user = get_current_user()
            sanitized_comment = re.sub(r'[^a-zA-Z0-9_-]', '_', comment)
            
            final_filename = f"{before_version}-{version_str}-{user}-{sanitized_comment}{ext}".lower()
            dst_path = os.path.join(final_dir, final_filename)
            logger.info(f"Step 2: Calculated Target Path: {dst_path}")

        except Exception as e:
            self.report({'ERROR'}, f"Path mapping failed: {e}")
            logger.error(f"Error in path mapping: {e}", exc_info=True)
            return {'CANCELLED'}

        # 3. Create folders and Copy
        try:
            if not os.path.exists(final_dir):
                os.makedirs(final_dir, exist_ok=True)
                logger.info(f"Step 3: Created target directory: {final_dir}")
            
            shutil.copy2(src_path, dst_path)
            logger.info(f"Step 3: File copied to target.")
        except Exception as e:
            self.report({'ERROR'}, f"Copy failed: {e}")
            return {'CANCELLED'}

        # 4. Open new file
        try:
            bpy.ops.wm.open_mainfile(filepath=dst_path)
            logger.info(f"Step 4: Switched to new TEX file.")
            
            # --- NEW: Log to Sheets ---
            upload_publisher_data(context, dst_path, comment)
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to open new file: {e}")
            return {'CANCELLED'}

        # 5. Cleanup (Clear Asset Statuses)
        try:
            cleared_cols = 0
            for c in bpy.data.collections:
                if c.asset_data:
                    c.asset_clear()
                    cleared_cols += 1
            
            cleared_mats = 0
            for m in bpy.data.materials:
                if m.asset_data:
                    m.asset_clear()
                    cleared_mats += 1
            
            logger.info(f"Step 5: Cleared asset status from {cleared_cols} collections and {cleared_mats} materials.")
        except Exception as e:
            logger.warning(f"Cleanup encountered an issue: {e}")

        # 6. Make Hero
        try:
            # We need to ensure the comment is passed to the new scene
            # Note: Opening the file might have cleared our context property if it wasn't saved, 
            # but we just saved it or can re-set it.
            bpy.context.scene.krutart_comment = comment
            logger.info(f"Step 6: Initializing TEX HERO with comment: {comment}")
            bpy.ops.krutart.make_hero()
        except Exception as e:
            self.report({'ERROR'}, f"Final Hero creation failed: {e}")
            logger.error(f"Make Hero failed: {e}", exc_info=True)
            return {'CANCELLED'}

        self.report({'INFO'}, f"Sent to Texture Paint: {os.path.basename(dst_path)}")
        logger.info("'Send to Texture Paint' completed successfully.")
        logger.info("-" * 50)
        return {'FINISHED'}

# --- UI Functions (Shared) ---

def draw_publisher_ui(layout, context):
    """Shared function to draw the publisher UI in multiple panels."""
    scene = context.scene

    is_valid_file = False
    if bpy.data.is_saved:
        _, _, _, version = parse_filename(get_current_filepath())
        if version is not None:
            is_valid_file = True
    
    if not bpy.data.is_saved:
        layout.label(text="Save file to enable addon.", icon='ERROR')
        return

    if not is_valid_file:
        box = layout.box()
        box.label(text="Filename format is incorrect!", icon='ERROR')
        box.label(text="Expected: PROJECT-ASSET-[flags]-v###.blend")
        return
        
    # --- Unified Publishing Box ---
    box = layout.box()
    
    # --- NEW LOGIC (v1.6.2): Dynamic Header ---
    user_name = get_current_user()
    if user_name:
        box.label(text=f"Publish as '{user_name}'", icon='FILE_NEW')
    else:
        box.label(text="Publishing Actions", icon='FILE_NEW')
    # --- END NEW LOGIC ---
    
    # --- NEW LOGIC (v1.9.0): Dashboard Sync Info ---
    if _is_production(get_current_filepath()):
        dash_row = box.row(align=True)
        dash_row.label(text=f"DAB: {DASH_FETCH_STATUS}", icon='URL')
        dash_row.operator("krutart.refresh_dash", icon='FILE_REFRESH', text="")
        
        row = box.row(align=True)
        # Expanded buttons for quick switching
        row.prop(scene, "krutart_publish_type", expand=True)
    # --- END NEW LOGIC ---
    
    # Shared comment field at the top
    box.prop(scene, "krutart_comment", text="Comment")
    
    # Check if comment is empty
    comment = scene.krutart_comment.strip()
    is_comment_empty = not comment
    
    # Create a row for the buttons
    row = box.row()
    
    # Disable row if comment is empty
    if is_comment_empty:
        row.enabled = False
        
    # Add Save Increment button to the row
    row.operator(KRUTART_OT_save_increment.bl_idname)
    
    # Add Make Hero button to the row
    row.operator(KRUTART_OT_make_hero.bl_idname)

    # --- NEW LOGIC: Send to Texture Paint ---
    filepath_lower = get_current_filepath().lower()
    is_tex = "tex-work" in filepath_lower or "-tex-" in os.path.basename(filepath_lower)
    
    triggers = ["model-work", "rig-work", "actor-work", "prop-work", "mod-", "act-", "prp-", "rig-"]
    if "library-work" in filepath_lower and not is_tex and any(t in filepath_lower for t in triggers):
        row = box.row()
        row.operator(KRUTART_OT_send_to_tex_paint.bl_idname, icon='BRUSH_DATA')

    # --- NEW LOGIC: Debug/Preferences Section ---
    box = layout.box()
    box.label(text="Debug & Settings", icon='SETTINGS')
    row = box.row()
    row.operator("krutart.test_publisher_payload", icon='EXPORT', text="Test Sheet Payload")

# --- Additional Operators ---

class KRUTART_OT_refresh_dash(bpy.types.Operator):
    """Manually refreshes the DAB dashboard status"""
    bl_idname = "krutart.refresh_dash"
    bl_label = "Refresh Dashboard"
    
    def execute(self, context):
        GoogleCSVClient.fetch_dash_data()
        
        # Try to auto-select phase
        _, asset_name, _, _ = parse_filename(bpy.data.filepath)
        if asset_name:
            active_phase = get_active_phase_for_shot(asset_name)
            if active_phase:
                context.scene.krutart_publish_type = active_phase
                self.report({'INFO'}, f"Auto-switched to phase: {active_phase}")
        
        return {'FINISHED'}

class KRUTART_OT_test_publisher_payload(bpy.types.Operator):
    """Sends a dummy payload to verify Google Sheets integration"""
    bl_idname = "krutart.test_publisher_payload"
    bl_label = "Test Sheet Payload"
    
    def execute(self, context):
        current_fp = get_current_filepath()
        filepath = current_fp if current_fp else "S:/3212-PRODUCTION/TEST/TEST-FILE-v001.blend"
        
        comment = "TEST: Manual Connection Verification"
        upload_publisher_data(context, filepath, comment)
        
        self.report({'INFO'}, "Test payload sent to Sheet. Check the logs!")
        return {'FINISHED'}

# --- Preferences ---

class KRUTART_Publisher_Preferences(bpy.types.AddonPreferences):
    """Addon Preferences for Krutart Publisher"""
    bl_idname = __name__

    shotlist_webapp_url: bpy.props.StringProperty(
        name="Shotlist WebApp URL",
        description="The Google Apps Script WebApp URL for logging publishes",
        default=DEFAULT_SHOTLIST_URL,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "shotlist_webapp_url")
        
        box = layout.box()
        box.label(text="Connection Test", icon='CONSOLE')
        box.operator("krutart.test_publisher_payload", icon='EXPORT')

# --- UI Panels ---

class KRUTART_PT_autopublisher_panel(bpy.types.Panel):
    """Creates a Panel in the Output Properties window"""
    bl_label = "KRUTART-AUTOPUBLISHER"
    bl_idname = "OUTPUT_PT_krutart_autopublisher"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_order = -1  # Moves panel to the top

    def draw(self, context):
        draw_publisher_ui(self.layout, context)

class KRUTART_PT_autopublisher_dopesheet(bpy.types.Panel):
    """Creates a Panel in the Dope Sheet Sidebar"""
    bl_label = "Krutart Publisher"
    bl_idname = "DOPESHEET_PT_krutart_autopublisher"
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Publisher"

    def draw(self, context):
        draw_publisher_ui(self.layout, context)

# --- Registration ---

classes = (
    KRUTART_OT_save_increment,
    KRUTART_OT_make_hero,
    KRUTART_OT_send_to_tex_paint,
    KRUTART_OT_refresh_dash,
    KRUTART_OT_test_publisher_payload,
    KRUTART_PT_autopublisher_panel,
    KRUTART_PT_autopublisher_dopesheet,
    KRUTART_Publisher_Preferences,
)

def register():
    # --- Logger Setup ---
    # We configure the logger here to ensure it's set up every time
    # the addon is registered, which fixes issues with script reloading.
    global logger
    logger = logging.getLogger("KrutartAutoPublisher")
    logger.setLevel(logging.INFO)

    # Clear existing handlers to prevent duplicate logs on reload
    if logger.hasHandlers():
        logger.handlers.clear()

    # Add a fresh handler to print to the system console
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # --- End Logger Setup ---

    logger.info("Registering Krutart Publisher Addon v1.8.8")
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # Add handlers
    if auto_switch_phase_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(auto_switch_phase_on_load)
    
    # Initial fetch
    # threading.Thread(target=GoogleCSVClient.fetch_dash_data, daemon=True).start()
    # For stability, we'll let load_post handle the first fetch
    
    bpy.types.Scene.krutart_comment = bpy.props.StringProperty(
        name="Comment",
        description="Optional comment for the incremental save filename",
        default="",
    )
    bpy.types.Scene.krutart_publish_type = bpy.props.EnumProperty(
        name="Phase",
        items=get_publish_type_items,
        description="Select the production phase for this publish",
    )

def unregister():
    # --- Removed 'global logger' declaration ---
    # The logger is defined at the module-level (global).
    logger.info("Unregistering Krutart Publisher Addon")

    # --- Logger Teardown ---
    # Get the logger and clear its handlers
    if 'logger' in globals() and logger and logger.hasHandlers():
        logger.handlers.clear()
    # --- End Logger Teardown ---

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls) 
    del bpy.types.Scene.krutart_comment
    del bpy.types.Scene.krutart_publish_type

if __name__ == "__main__":
    register()