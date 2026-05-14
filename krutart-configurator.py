bl_info = {
    "name": "Krutart Addon Configurator",
    "author": "iori, Krutart, Gemini",
    "version": (2, 0, 2), 
    "blender": (4, 5, 0),
    "location": "Preferences > Add-ons",
    "description": "Enforces company standards, manages assets, logs save history, and synchronizes company addons.",
    "warning": "",
    "doc_url": "",
    "category": "System",
}

import bpy
import os
import shutil
import ast  # For safely parsing bl_info
import tempfile
import addon_utils  # For managing addons
import socket # For getting hostname
from bpy.app.handlers import persistent
from bpy.types import Operator, AddonPreferences
from bpy.props import BoolProperty, StringProperty

import sys
from pathlib import Path

# --- Configuration ---

# Path to your central addon repository.
# We now calculate this dynamically to support Mac/Windows and different mount points.
PROJECT_NAME = "3212-PREPRODUCTION"
DEFAULT_WIN_DRIVE = "S"

# --- Helper: OS Bridge Integration ---

def get_os_bridge():
    """Safely retrieves the Krutart OS Bridge module if available."""
    # Fast path
    if 'krutart-os_bridge' in sys.modules:
        return sys.modules['krutart-os_bridge']
    
    # Slow path (search by name)
    for mod_name, mod in sys.modules.items():
        if hasattr(mod, "bl_info") and isinstance(mod.bl_info, dict):
            if mod.bl_info.get("name") == "Krutart OS Bridge":
                return mod
    return None

def get_company_root():
    """
    Attempts to find the project root (3212-PREPRODUCTION).
    Prioritizes Windows paths (S:\) to ensure pipeline consistency.
    On Mac, tries to use OS Bridge for detection, then falls back to local logic.
    """
    # 0. Get Preferences (for overrides)
    addon_prefs = None
    try:
        addon_prefs = bpy.context.preferences.addons[__name__].preferences
    except (KeyError, AttributeError):
        pass

    # 1. Windows Priority (or Simulation)
    # If we are on Windows, OR if we want to default to the standard path
    if sys.platform.startswith("win"):
        drive_char = "S"
        if addon_prefs and addon_prefs.win_drive_char:
            drive_char = addon_prefs.win_drive_char.upper().replace(":", "").strip()
            
        p = Path(f"{drive_char}:\\{PROJECT_NAME}")
        if p.exists(): return p
        return p # Return even if not exists (fail fast on standard path)

    # 2. Mac Logic
    
    # A. Manual Override (Configurator Level)
    if addon_prefs and addon_prefs.mac_root_path:
        p = Path(addon_prefs.mac_root_path).expanduser().resolve()
        if p.exists(): return p

    # B. OS Bridge Delegation (Single Source of Truth)
    bridge = get_os_bridge()
    if bridge and hasattr(bridge, "get_mac_root"):
        try:
            # We assume Bridge handles context-aware and its own overrides
            bridge_root = bridge.get_mac_root(bpy.context)
            if bridge_root: return bridge_root
        except Exception as e:
            print(f"[Krutart] Bridge lookup failed: {e}")

    # C. Fallback: Context Aware (Current File)
    if bpy.data.filepath:
        curr = Path(bpy.data.filepath).resolve()
        for p in [curr] + list(curr.parents):
            if p.name == PROJECT_NAME:
                return p
            elif p.name == "3212-PRODUCTION":
                return p.parent / PROJECT_NAME

    # D. Fallback: Dynamic CloudStorage Search
    home = Path.home()
    cloud_storage_dir = home / "Library/CloudStorage"
    if cloud_storage_dir.exists():
        for item in cloud_storage_dir.iterdir():
            if item.is_dir() and item.name.startswith("GoogleDrive-"):
                candidate = item / "Shared drives" / PROJECT_NAME
                if candidate.exists():
                    return candidate

    # E. Standard Volumes Check
    candidates = [
        Path(f"/Volumes/GoogleDrive/Shared drives/{PROJECT_NAME}"),
    ]
    for cand in candidates:
        if cand.exists():
            return cand
            
    return None

def get_company_addon_path():
    root = get_company_root()
    if root:
        return root / "SOFTWARE/BLENDER/ADDON"
    # Fallback for Windows if root not found (preserving old behavior)
    if sys.platform.startswith("win"):
        return Path(r"S:\3212-PREPRODUCTION\SOFTWARE\BLENDER\ADDON")
    return None

# The filename of THIS addon in the company folder, used for self-updates.
SELF_FILENAME = "krutart-configurator.py"

# Path to the Workstation Identifier File
# We use a raw string (r"...") to handle backslashes safely
# file content:
# "alfa": "lukas",
# "osma": "Ondra",
# "inzenyr": "Jachym",
# "kit": "Vara",
# "simca": "Jakub",
# "myska": "Lisa",
# "schmitt": "Boza",
# "zedix": "zedix",
# "eva": "eva",
# "elon": "elon",
# "pomocnik": "iori",
# "simca": "jakub",

def get_workstation_id_file():
    root = get_company_root()
    if root:
        return root / "MISC/IDENTIFIER/3212-krutart_workstation_identifikator.txt"
    
    # Fallback for Windows if root detection totally failed
    if sys.platform.startswith("win"):
         return Path(r"S:\3212-PREPRODUCTION\MISC\IDENTIFIER\3212-krutart_workstation_identifikator.txt")
    return None

# Roots to determine if a file is a "Company File"
COMPANY_ROOTS = [
    r"S:\3212-PREPRODUCTION",
    r"S:\3212-PRODUCTION",
    "S:/3212-PREPRODUCTION",
    "S:/3212-PRODUCTION",
]

COMPANY_BOOKMARKS = [
    "S:/3212-PREPRODUCTION",
    "S:/3212-PRODUCTION",
]

COMPANY_ASSET_LIBRARIES = {
    "LIBRARY-HERO": r"S:\3212-PREPRODUCTION\LIBRARY\LIBRARY-HERO",
}

# Global cache for the identity map to prevent network lag on save
# Structure: {'hostname': 'artist_name'}
CACHED_IDENTITY_MAP = {}

# --- Helper: Hostname ---

def get_normalized_hostname():
    """Returns the lowercased hostname, stripping .local (common on Mac)."""
    hn = socket.gethostname().lower()
    if hn.endswith(".local"):
        hn = hn[:-6]
    return hn


# --- Helper: Context Safety ---

def is_company_file():
    """Returns True if the current file is saved within company directories."""
    filepath = bpy.data.filepath
    if not filepath:
        return False # Unsaved file
    
    # Normalize slashes for comparison
    filepath = filepath.replace("\\", "/")
    for root in COMPANY_ROOTS:
        clean_root = root.replace("\\", "/")
        if filepath.startswith(clean_root):
            return True

    # Dynamic Mac check
    mac_root = get_company_root()
    if mac_root:
        shared_drives_root = str(mac_root.parent).replace("\\", "/")
        # Both project folders are valid company file locations
        if filepath.startswith(shared_drives_root + "/" + PROJECT_NAME) or \
           filepath.startswith(shared_drives_root + "/3212-PRODUCTION"):
            return True

    return False

# --- Core Logic: AST Parsing ---

def get_bl_info_from_file(filepath):
    """
    Safely reads an addon's .py file and extracts its bl_info dictionary
    using AST. Does NOT execute the file.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == 'bl_info':
                        if isinstance(node.value, ast.Dict):
                            return ast.literal_eval(node.value)
        return None
    except Exception as e:
        print(f"[Krutart] Error parsing bl_info from {filepath}: {e}")
        return None

# --- Core Logic: Workstation Identity ---

def load_identity_map():
    """
    Parses the text file at WORKSTATION_ID_FILE.
    Expected format lines: "hostname": "artistname",
    """
    global CACHED_IDENTITY_MAP
    new_map = {}
    
    id_file = get_workstation_id_file()
    if not id_file or not id_file.exists():
        print(f"[Krutart] Identifier file not found: {id_file}")
        return False

    try:
        # Cache Busting: Force OS to check metadata
        try:
            id_file.stat()
        except OSError:
            pass

        with open(id_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

            
        for line in lines:
            clean_line = line.strip()
            # Skip comments or empty lines
            if not clean_line or clean_line.startswith('#') or clean_line.startswith('//'):
                continue
            
            # Simple string parsing for format: "key": "value",
            if ':' in clean_line:
                parts = clean_line.split(':', 1) # Split only on first colon
                if len(parts) == 2:
                    # Strip quotes, commas, and whitespace
                    raw_host = parts[0].strip().strip('"\'').lower()
                    raw_name = parts[1].strip().strip('"\' ,')
                    
                    if raw_host and raw_name:
                        new_map[raw_host] = raw_name
        
        CACHED_IDENTITY_MAP = new_map
        print(f"[Krutart] Identity Map Loaded ({len(new_map)} entries). Current Host: {get_normalized_hostname()}")
        return True


    except (OSError, IOError) as e:
        print(f"[Krutart] TIMEOUT/IO ERROR loading identity map: {e}")
        print("  > Try: Right-click '3212-PREPRODUCTION/MISC' > 'Make Available Offline'")
        return False
    except Exception as e:
        print(f"[Krutart] Error loading identity map: {e}")
        return False

def append_identity_to_file(hostname, artist_name):
    """
    Appends a new mapping to the external file.
    """
    id_file = get_workstation_id_file()
    if not id_file or not id_file.exists():
        return "Error: Identifier file not found on network."
        
    # We removed the 'already registered' check to allow the UI's 'Overwrite' button to work.
    # The load_identity_map() will use the latest entry (bottom of file) in case of duplicates.

    entry = f'\n"{hostname.lower()}": "{artist_name}",'

    
    try:
        with open(id_file, 'a', encoding='utf-8') as f:
            f.write(entry)
        
        # Reload cache immediately
        load_identity_map()
        return "SUCCESS"
    except Exception as e:
        return f"Error writing to file: {e}"

# --- Core Logic: Addon Sync (Hardened) ---

def sync_company_addons(ignore_self=True):
    """
    Scans COMPANY_ADDON_PATH and installs/updates .py addons.
    Uses a temp folder and raw reads to avoid Windows network caching.
    """
    messages = []
    print("[Krutart] Running Company Addon Sync...")
    
    COMPANY_ADDON_PATH = get_company_addon_path()
    
    if not COMPANY_ADDON_PATH or not COMPANY_ADDON_PATH.exists():
        msg = f"Error: Path not found: {COMPANY_ADDON_PATH}"
        print(f"  > {msg}")
        messages.append(msg)
        return messages

    # Get currently installed addons
    try:
        addon_utils.modules_refresh()
        installed_addons = {
            mod.__name__: getattr(mod, 'bl_info', {}).get('version', (0, 0, 0))
            for mod in addon_utils.modules()
        }
    except Exception as e:
        msg = f"Error refreshing modules: {e}"
        print(f"  > {msg}")
        messages.append(msg)
        return messages

    # Scan Network
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Force OS to check directory metadata (Cache Busting)
            COMPANY_ADDON_PATH.stat()
            dir_contents = os.listdir(str(COMPANY_ADDON_PATH))
        except OSError:
            return ["Error: Cannot list company addon directory"]

        for item in dir_contents:
            network_path = COMPANY_ADDON_PATH / item
            
            # 1. Filter: Only .py files, ignore self, ignore hidden
            if not item.endswith('.py') or not os.path.isfile(network_path):
                continue
            
            if ignore_self and item == SELF_FILENAME:
                continue
                
            module_name = item[:-3]
            
            try:
                # Force OS to refresh specific file metadata (Cache Busting)
                network_path.stat()
                
                # 2. Safety: Copy to temp before reading via pure file streams
                # This bypasses `shutil.copy2` which uses the OS sendfile cache.
                temp_path = os.path.join(temp_dir, item)
                with open(network_path, 'rb') as f_in:
                    with open(temp_path, 'wb') as f_out:
                        f_out.write(f_in.read())
                
                # 3. Parse Local Temp File
                file_info = get_bl_info_from_file(temp_path)
                
                if not file_info:
                    print(f"  > Skipped {item}: No bl_info found.")
                    continue
                
                network_version = file_info.get('version', (0, 0, 1))
                current_version = installed_addons.get(module_name)

                # Explicit Logging for Debugging
                print(f"  > Evaluated {module_name}: Network=v{network_version}, Local=v{current_version}")

                # 4. Logic: Install or Update
                if current_version is None:
                    # NEW INSTALL
                    print(f"  > Installing NEW: {module_name} v{network_version}")
                    
                    # Install from the TEMP path to ensure we have the full file
                    bpy.ops.preferences.addon_install(filepath=temp_path, overwrite=True)
                    
                    addon_utils.modules_refresh()
                    addon_utils.enable(module_name, default_set=True)
                    messages.append(f"Installed: {module_name}")
                    
                elif network_version > current_version:
                    # UPDATE
                    print(f"  > Updating {module_name}: v{current_version} -> v{network_version}")
                    
                    if addon_utils.check(module_name)[0]:
                        addon_utils.disable(module_name)
                    
                    # Install new (from temp) with overwrite instead of dangerous addon_remove
                    try:
                        bpy.ops.preferences.addon_install(filepath=temp_path, overwrite=True)
                    except Exception as fallback_e:
                        print(f"  > Warning during install, attempting override: {fallback_e}")
                        # If it fails, fallback to forcing copy
                        dest = os.path.join(addon_utils.paths()[0], os.path.basename(temp_path))
                        shutil.copy2(temp_path, dest)
                    
                    addon_utils.modules_refresh()
                    addon_utils.enable(module_name, default_set=True)
                    
                    messages.append(f"Updated: {module_name} to v{network_version}")
                    
            except Exception as e:
                print(f"  > Failed to process {item}: {e}")
                messages.append(f"Error processing {item}")
    
    return messages

# --- Core Logic: Self Update (Windows Safe) ---

def perform_self_update():
    """
    Updates THIS addon file using the Rename-then-Copy method 
    to avoid Windows PermissionErrors on locked files.
    """
    addon_path = get_company_addon_path()
    if not addon_path:
        return "Error: Company path not available."
        
    network_path = str(addon_path / SELF_FILENAME)
    
    if not os.path.exists(network_path):
        return "Error: Configurator file not found on network."

    # Check version using temp copy safety
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_check_path = os.path.join(temp_dir, "check_" + SELF_FILENAME)
        try:
            shutil.copy2(network_path, temp_check_path)
            network_info = get_bl_info_from_file(temp_check_path)
        except Exception:
            return "Error: Could not copy/read network file."

    if not network_info:
        return "Error: Could not parse network file version."
    
    network_ver = network_info.get('version', (0,0,0))
    local_ver = bl_info.get('version', (0,0,0))

    if network_ver <= local_ver:
        return f"Configurator is up to date (v{local_ver})."

    # Perform Update
    current_filepath = __file__
    old_filepath = current_filepath + ".old"

    try:
        print(f"[Krutart] Self-updating from v{local_ver} to v{network_ver}...")
        
        # 1. Clean up previous .old file if it exists
        if os.path.exists(old_filepath):
            try:
                os.remove(old_filepath)
            except OSError:
                print("  > Warning: Could not remove existing .old file (might be locked).")

        # 2. RENAME current file to .old (This releases the filename lock on Windows)
        os.rename(current_filepath, old_filepath)
        print(f"  > Renamed current file to: {os.path.basename(old_filepath)}")

        # 3. COPY new file to the original filename
        shutil.copy2(network_path, current_filepath)
        print(f"  > Copied new file from network.")
        
        return "SUCCESS"
    
    except Exception as e:
        # Attempt rollback if rename happened but copy failed
        if os.path.exists(old_filepath) and not os.path.exists(current_filepath):
            try:
                os.rename(old_filepath, current_filepath)
                return f"Update Failed (Rolled Back): {e}"
            except:
                return f"Update Failed (CRITICAL - REINSTALL NEEDED): {e}"
        return f"Update Failed: {e}"


# --- Core Logic: Save History ---

def update_internal_save_log(context):
    """
    Updates the internal krutart-configurations.info text block
    with the current user and filename, maintaining a history of 5.
    """
    # 1. Resolve Name
    try:
        prefs = context.preferences.addons[__name__].preferences
    except KeyError:
        return # Addon might not be fully registered yet
    
    hostname = get_normalized_hostname()
    
    # Priority: 1. Manual Override, 2. Network Map, 3. Hostname
    if prefs.user_name_override.strip():
        user_name = prefs.user_name_override.strip()
    else:
        # Use Cached Map
        user_name = CACHED_IDENTITY_MAP.get(hostname, hostname) 

    filename = os.path.basename(bpy.data.filepath) or "Untitled.blend"
    current_entry = f"{user_name} - {filename}"


    # 2. Get or Create Text Block
    text_name = "krutart-configurations.info"
    text_block = bpy.data.texts.get(text_name)
    if not text_block:
        text_block = bpy.data.texts.new(text_name)
    
    # 3. Parse History
    # Robust parsing to capture the previous 'last saved' entry
    lines = text_block.as_string().splitlines()
    history = []
    
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
            
        # Specific check to capture the previous "last saved by" entry
        if clean.startswith("last saved by:"):
            # Extract content after the prefix
            entry = clean.replace("last saved by:", "").strip()
            if entry:
                history.append(entry)
            continue

        # Skip headers and empty slot markers
        if "---" in clean or "configurator data" in clean or "previous saves" in clean or "[]" in clean:
            continue
        
        # Determine if it's a valid history line (basic check)
        history.append(clean)

    # 4. Construct New History (Max 5 items total)
    # Prepend current entry to the top
    new_history = [current_entry] + history
    new_history = new_history[:5] # Keep top 5

    # 5. Format Output
    output_str = "--------------------\n"
    output_str += "| configurator data |\n"
    output_str += f"last saved by: {new_history[0]}\n"
    output_str += "previous saves:\n"
    
    for item in new_history[1:]:
        output_str += f"{item}\n"
        
    # Fill empty slots if history is short
    for _ in range(5 - len(new_history)):
         output_str += "[]\n"

    output_str += "--------------------"

    # 6. Write back
    text_block.clear()
    text_block.write(output_str)
    
    print(f"[Krutart] Updated save log: {current_entry}")


# --- Operators ---

class KRUTART_OT_sync_addons(Operator):
    """Checks the company folder and updates other addons"""
    bl_idname = "krutart.sync_addons"
    bl_label = "Sync Company Addons"
    bl_description = "Installs missing .py addons and updates outdated ones"

    def execute(self, context):
        msgs = sync_company_addons(ignore_self=True)
        
        if msgs:
            self.report({'INFO'}, f"Sync Complete. {len(msgs)} updates applied.")
            for m in msgs:
                print(f"[Krutart Result] {m}")
        else:
            self.report({'INFO'}, "All company addons are already up to date.")
            print("[Krutart Result] All company addons are up to date.")
            
        return {'FINISHED'}


class KRUTART_OT_update_configurator(Operator):
    """Updates the Krutart Configurator addon itself"""
    bl_idname = "krutart.update_self"
    bl_label = "Update Configurator"
    bl_description = "Safely updates this addon using rename-swap method."

    def execute(self, context):
        result = perform_self_update()
        
        if result == "SUCCESS":
            def draw_restart_popup(self, context):
                self.layout.label(text="Configurator Updated Successfully!")
                self.layout.label(text="Restart Blender to apply changes.")
            
            context.window_manager.popup_menu(draw_restart_popup, title="Update Complete", icon='INFO')
            self.report({'INFO'}, "Update Successful. Please Restart Blender.")
        else:
            self.report({'WARNING'}, result)
            
        return {'FINISHED'}

class KRUTART_OT_refresh_identity(Operator):
    """Reloads the workstation identifier map from the server"""
    bl_idname = "krutart.refresh_identity"
    bl_label = "Refresh Identity Map"
    bl_description = "Reloads the artist/host mapping file from the network"

    def execute(self, context):
        success = load_identity_map()
        if success:
            self.report({'INFO'}, "Identity Map Reloaded.")
        else:
            self.report({'ERROR'}, "Failed to load Identity Map. Check console.")
        return {'FINISHED'}

class KRUTART_OT_register_identity(Operator):
    """Registers the current workstation to the text file"""
    bl_idname = "krutart.register_identity"
    bl_label = "Register This Workstation"
    bl_description = "Appends the current Hostname and Override Name to the network file"

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        hostname = get_normalized_hostname()
        artist_name = prefs.user_name_override.strip()

        
        if not artist_name:
            self.report({'ERROR'}, "Please type a name in 'User Name Override' first.")
            return {'CANCELLED'}

        result = append_identity_to_file(hostname, artist_name)
        
        if result == "SUCCESS":
            self.report({'INFO'}, f"Registered {hostname} as {artist_name}")
            prefs.user_name_override = "" # Clear after successful register
        else:
            self.report({'ERROR'}, result)
            
        return {'FINISHED'}

# --- Preferences Panel ---

class KrutartConfiguratorPreferences(AddonPreferences):
    bl_idname = __name__

    auto_sync_on_load: BoolProperty(
        name="Auto-Sync on Startup",
        default=True,
        description="If enabled, automatically checks and installs company addons when Blender starts."
    )

    user_name_override: StringProperty(
        name="User Name Override",
        default="",
        description="Type your name here to override detection or to register this computer."
    )

    # --- Match OS Bridge Settings ---
    mac_root_path: StringProperty(
        name="Mac Root Override",
        subtype='DIR_PATH',
        description="Manually select the 3212-PREPRODUCTION folder if detection fails."
    )

    win_drive_char: StringProperty(
        name="Win Drive Letter",
        default="S",
        description="Drive letter for Windows paths (Default: S)"
    )

    def draw(self, context):
        layout = self.layout
        hostname = get_normalized_hostname()
        
        # Check cache
        detected_name = CACHED_IDENTITY_MAP.get(hostname, None)
        is_known = detected_name is not None

        
        # --- Box 1: Identity ---
        box = layout.box()
        box.label(text="Identity System", icon='USER')
        
        row = box.row()
        if is_known:
            row.label(text=f"Recognized as: {detected_name}", icon='CHECKMARK')
        else:
            row.label(text=f"Unknown Workstation ({hostname})", icon='ERROR')
        
        row = box.row()
        row.prop(self, "user_name_override", text="Artist Name (Override/Register)")
        
        # Registration Button logic
        if not is_known and self.user_name_override:
            sub = box.row()
            sub.operator("krutart.register_identity", icon='IMPORT', text="Register This Workstation")
        elif is_known and self.user_name_override:
            sub = box.row()
            sub.label(text="Use override to change temporary name, or...")
            sub.operator("krutart.register_identity", icon='IMPORT', text="Overwrite Registration")
        
        row = box.row()
        row.operator("krutart.refresh_identity", icon='FILE_REFRESH', text="Reload Identity File")

        # --- Box 1.5: Paths (New) ---
        box = layout.box()
        box.label(text="Path Configuration", icon='FILE_FOLDER')
        
        if sys.platform.startswith("win"):
             box.prop(self, "win_drive_char")
        else:
             box.prop(self, "mac_root_path")
             box.label(text=f"Drive Mapping: {self.win_drive_char}:\\...", icon='INFO')
        
        # UI Enhancement: Show what we actually found
        detected = get_company_root()
        if detected:
             box.label(text=f"Detected Root: {detected}", icon='CHECKMARK')
        else:
             box.label(text="Root NOT Detected!", icon='ERROR')


        # --- Box 2: System ---
        box = layout.box()
        box.label(text="Company Addon System", icon='PREFERENCES')
        
        path_str = str(get_company_addon_path()) if get_company_addon_path() else "NOT FOUND"
        box.label(text=f"Library Path: {path_str}")
        
        row = box.row()
        row.prop(self, "auto_sync_on_load")
        
        col = layout.column(align=True)
        col.label(text="Manual Actions:")
        row = col.row(align=True)
        row.scale_y = 1.5
        row.operator("krutart.sync_addons", icon='FILE_REFRESH')
        
        row = col.row(align=True)
        row.scale_y = 1.5
        row.operator("krutart.update_self", icon='IMPORT', text="Check for Configurator Updates")

        layout.separator()
        layout.label(text=f"Current Configurator Version: {bl_info['version']}")


# --- Safe Handlers ---

@persistent
def on_save_pre(dummy):
    """
    Enforces pipeline standards before saving.
    Checks if file is a company file before applying aggressive overrides.
    """
    # 1. Company File Check
    if not is_company_file():
        # Optional: Print debug or skip entirely
        # print("[Krutart] Not a company file. Skipping strict enforcement.")
        return 

    print("[Krutart] Company File Detected. Enforcing Standards...")

    # 2. Set Absolute Paths (Standard Pipeline Rule)
    if bpy.context.preferences.filepaths.use_relative_paths:
        bpy.context.preferences.filepaths.use_relative_paths = False
        bpy.context.preferences.filepaths.use_relative_paths = False # Redundant but safe
        print("  > Enforced: 'Default to relative paths' = OFF")

    # 3. Force Solid View (Refined Strategy)
    # We iterate over bpy.data.screens to catch ALL workspaces, not just open windows.
    count = 0
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            # Only switch if not already solid
                            if space.shading.type != 'SOLID':
                                space.shading.type = 'SOLID'
                                count += 1
        if count > 0:
            print(f"  > Switched {count} 3D views to SOLID mode.")
    except Exception as e:
        print(f"  > Error enforcing solid view: {e}")

    # 4. Update Internal Save Log (New Feature)
    try:
        update_internal_save_log(bpy.context)
    except Exception as e:
        print(f"  > Error updating save log: {e}")

@persistent
def on_load_post(dummy):
    """
    Runs on file open. 
    """
    # Fix Outliner for missing files (Generic helper, safe for all files)
    
    # Check if there are missing libraries or broken library links
    has_missing = any(lib.filepath.startswith("//") == False and not os.path.exists(bpy.path.abspath(lib.filepath)) for lib in bpy.data.libraries)
    
    if has_missing:
        print("[Krutart] Missing files detected. Switching Outliner...")
        try:
            # We only affect the active window context for immediate visual feedback
            for window in bpy.context.windows:
                for area in window.screen.areas:
                    if area.type == 'OUTLINER':
                        area.spaces[0].display_mode = 'BLENDER_FILE'
                        break
        except Exception:
            pass

# --- Standard Setup Helpers ---

def add_bookmarks():
    try:
        bookmarks = bpy.context.preferences.view.file_browser_favorites
        existing = [b.path for b in bookmarks]
        
        # Use Dynamic Bookmarks
        for path in get_company_bookmarks():
            if path not in existing:
                bookmarks.new(path)
    except Exception:
        pass

def configure_asset_libraries():
    prefs = bpy.context.preferences
    libs = prefs.filepaths.asset_libraries
    
    # Use Dynamic Libraries
    for name, path in COMPANY_ASSET_LIBRARIES.items():
        if not path: continue
        
        found = libs.get(name)
        if not found:
            # Try to find by path in case name mismatch
            for lib in libs:
                if lib.path == path:
                    found = lib
                    break
        
        if not found:
            bpy.ops.preferences.asset_library_add(directory=path)
            # Re-fetch the last added one
            found = libs[-1] 
            found.name = name
        
        if found:
            # Enforce settings
            if found.path != path: found.path = path
            if hasattr(found, "import_method") and found.import_method != 'LINK':
                found.import_method = 'LINK'
            if hasattr(found, "use_relative_path") and found.use_relative_path:
                found.use_relative_path = False

def configure_startup_settings():
    # Hardware/System settings apply regardless of file path
    try:
        c_prefs = bpy.context.preferences.addons['cycles'].preferences
        c_prefs.compute_device_type = 'OPTIX'
        # Refresh is sometimes expensive, so we wrap it
        c_prefs.refresh_devices() 
        for dev in c_prefs.devices:
            dev.use = (dev.type == 'OPTIX')
    except Exception:
        pass
        
    configure_asset_libraries()


# --- Registration ---

_startup_run = False
_startup_attempts = 0

def run_startup_logic():
    global _startup_run, _startup_attempts
    if _startup_run: return None
    
    _startup_attempts += 1
    print(f"[Krutart] Initializing (Attempt {_startup_attempts})...")
    
    # 1. Check Network Drive (The Race Condition Fix)
    addon_path = get_company_addon_path()
    
    # If network is not reachable yet, retry in 3 seconds (up to 5 times = 15 seconds)
    if not addon_path or not addon_path.exists():
        if _startup_attempts < 6:
            print(f"[Krutart] Network drive not ready. Retrying in 3 seconds...")
            return 3.0  # Tells Blender to run this function again in 3 seconds
        else:
            print("[Krutart] Network drive failed to mount after 15 seconds. Giving up auto-sync.")
            # We continue anyway to set up local UI and bookmarks
    
    # 2. Environment Setup (Safe for home use too, mostly)
    add_bookmarks()
    configure_startup_settings()
    
    # 3. Load Identity Map (Cache network file once on startup)
    identity_loaded = load_identity_map()
    current_host = get_normalized_hostname()
    is_known = current_host in CACHED_IDENTITY_MAP
    
    # If identity failed to load OR the current workstation is still unknown, retry.
    # This handles Google Drive sync latency where the file exists but is empty or stale.
    if not identity_loaded or not is_known:
        if _startup_attempts < 8: # Retry for up to 24 seconds
            status = "file not found/busy" if not identity_loaded else f"unknown workstation ({current_host})"
            print(f"[Krutart] Identity {status}. Retrying in 3 seconds...")
            return 3.0
        else:
            if not identity_loaded:
                print("[Krutart] Identity file failed to load after multiple attempts.")
            else:
                print(f"[Krutart] Workstation '{current_host}' remains unregistered after sync wait.")


    
    # 4. Addon Sync & Silent Self-Update
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        if prefs.auto_sync_on_load:
            if addon_path and addon_path.exists():
                # --- NEW: Silent Background Self-Update ---
                print("[Krutart] Auto-Sync Enabled: Checking for Configurator self-update...")
                update_result = perform_self_update()
                if update_result == "SUCCESS":
                    print("[Krutart] Configurator staged an update in the background. Will apply on next restart.")
                else:
                    print(f"[Krutart] Configurator self-update check: {update_result}")
                
                # --- Existing: Sync Other Company Addons ---
                print("[Krutart] Checking company addons...")
                sync_company_addons(ignore_self=True)
            else:
                print("[Krutart] Company path unreachable. Skipping Sync.")
        else:
            print("[Krutart] Auto-Sync Disabled.")
    except KeyError:
        print("[Krutart] Preferences not ready yet. Skipping sync.")

    _startup_run = True
    return None # Returning None kills the timer loop

def register():
    bpy.utils.register_class(KRUTART_OT_sync_addons)
    bpy.utils.register_class(KRUTART_OT_update_configurator)
    bpy.utils.register_class(KRUTART_OT_refresh_identity)
    bpy.utils.register_class(KRUTART_OT_register_identity)
    bpy.utils.register_class(KrutartConfiguratorPreferences)
    
    bpy.app.handlers.save_pre.append(on_save_pre)
    bpy.app.handlers.load_post.append(on_load_post)
    
    # Timer allows Blender to fully load prefs before we read them
    bpy.app.timers.register(run_startup_logic, first_interval=1.0)
    
    print("Krutart Configurator Registered.")

def unregister():
    bpy.utils.unregister_class(KRUTART_OT_sync_addons)
    bpy.utils.unregister_class(KRUTART_OT_update_configurator)
    bpy.utils.unregister_class(KRUTART_OT_refresh_identity)
    bpy.utils.unregister_class(KRUTART_OT_register_identity)
    bpy.utils.unregister_class(KrutartConfiguratorPreferences)
    
    if on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(on_save_pre)
    if on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)
    
    global _startup_run
    _startup_run = False
    print("Krutart Configurator Unregistered.")

if __name__ == "__main__":
    register()
