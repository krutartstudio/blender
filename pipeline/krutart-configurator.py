bl_info = {
    "name": "Krutart Configurator",
    "author": "iori, Krutart, Gemini",
    "version": (1, 6, 2), 
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

# --- Configuration ---

# Path to your central addon repository.
COMPANY_ADDON_PATH = "S:\\3212-PREPRODUCTION\\SOFTWARE\\BLENDER\\ADDON"

# The filename of THIS addon in the company folder, used for self-updates.
SELF_FILENAME = "krutart-configurator.py"

# Roots to determine if a file is a "Company File"
COMPANY_ROOTS = [
    "S:\\3212-PREPRODUCTION",
    "S:\\3212-PRODUCTION",
    "S:/3212-PREPRODUCTION",
    "S:/3212-PRODUCTION",
]

COMPANY_BOOKMARKS = [
    "S:/3212-PREPRODUCTION",
    "S:/3212-PRODUCTION",
]

COMPANY_ASSET_LIBRARIES = {
    "LIBRARY-HERO": "S:\\3212-PREPRODUCTION\\LIBRARY\\LIBRARY-HERO",
}

# Mapping hostnames to Artist Names
HOSTNAME_MAP = {
    "alfa": "lukas",
    "osma": "Ondra",
    "inzenyr": "Jachym",
    "kit": "Vara",
    "simca": "Jakub",
    "myska": "Lisa",
    "schmitt": "Boza",
    "zedix": "zedix",
}

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

# --- Core Logic: Addon Sync (Hardened) ---

def sync_company_addons(ignore_self=True):
    """
    Scans COMPANY_ADDON_PATH and installs/updates .py addons.
    Uses a temp folder to avoid network locking issues.
    """
    messages = []
    print("[Krutart] Running Company Addon Sync...")
    
    if not os.path.exists(COMPANY_ADDON_PATH):
        msg = f"Error: Path not found: {COMPANY_ADDON_PATH}"
        print(f"  > {msg}")
        messages.append(msg)
        return messages

    # Get currently installed addons
    try:
        addon_utils.modules_refresh()
        installed_addons = {
            mod.__name__: mod.bl_info.get('version', (0, 0, 0))
            for mod in addon_utils.modules()
        }
    except Exception as e:
        msg = f"Error refreshing modules: {e}"
        print(f"  > {msg}")
        messages.append(msg)
        return messages

    # Scan Network
    updates_count = 0
    
    # Create a temporary directory for safe inspection
    with tempfile.TemporaryDirectory() as temp_dir:
        
        for item in os.listdir(COMPANY_ADDON_PATH):
            network_path = os.path.join(COMPANY_ADDON_PATH, item)
            
            # 1. Filter: Only .py files, ignore self, ignore hidden
            if not item.endswith('.py') or not os.path.isfile(network_path):
                continue
            
            if ignore_self and item == SELF_FILENAME:
                continue
                
            module_name = item[:-3]
            
            try:
                # 2. Safety: Copy to temp before reading
                # This prevents partial reads if someone is uploading to the server,
                # and prevents us from locking the server file.
                temp_path = os.path.join(temp_dir, item)
                shutil.copy2(network_path, temp_path)
                
                # 3. Parse Local Temp File
                file_info = get_bl_info_from_file(temp_path)
                
                if not file_info:
                    print(f"  > Skipped {item}: No bl_info found.")
                    continue
                
                network_version = file_info.get('version', (0, 0, 1))
                current_version = installed_addons.get(module_name)

                # 4. Logic: Install or Update
                if current_version is None:
                    # NEW INSTALL
                    print(f"  > Installing NEW: {module_name} v{network_version}")
                    
                    # Install from the TEMP path to ensure we have the full file
                    bpy.ops.preferences.addon_install(filepath=temp_path, overwrite=True)
                    
                    addon_utils.modules_refresh()
                    addon_utils.enable(module_name, default_set=True)
                    messages.append(f"Installed: {module_name}")
                    updates_count += 1
                    
                elif network_version > current_version:
                    # UPDATE
                    print(f"  > Updating {module_name}: v{current_version} -> v{network_version}")
                    
                    if addon_utils.check(module_name)[0]:
                        addon_utils.disable(module_name)
                    
                    # Remove old
                    bpy.ops.preferences.addon_remove(module=module_name)
                    
                    # Install new (from temp)
                    bpy.ops.preferences.addon_install(filepath=temp_path, overwrite=True)
                    
                    addon_utils.modules_refresh()
                    addon_utils.enable(module_name, default_set=True)
                    
                    messages.append(f"Updated: {module_name} to v{network_version}")
                    updates_count += 1
                    
            except Exception as e:
                print(f"  > Failed to process {item}: {e}")
                messages.append(f"Error processing {item}")

    if updates_count == 0:
        messages.append("All company addons are up to date.")
    
    return messages

# --- Core Logic: Self Update (Windows Safe) ---

def perform_self_update():
    """
    Updates THIS addon file using the Rename-then-Copy method 
    to avoid Windows PermissionErrors on locked files.
    """
    network_path = os.path.join(COMPANY_ADDON_PATH, SELF_FILENAME)
    
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


# --- Core Logic: Save History (New) ---

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
    
    if prefs.user_name_override.strip():
        user_name = prefs.user_name_override.strip()
    else:
        hostname = socket.gethostname().lower()
        user_name = HOSTNAME_MAP.get(hostname, hostname) # Default to hostname if not found

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
        self.report({'INFO'}, f"Sync Complete. {len(msgs)} items processed.")
        for m in msgs:
            print(f"[Krutart Result] {m}")
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


# --- Preferences Panel ---

class KrutartConfiguratorPreferences(AddonPreferences):
    bl_idname = __name__

    auto_sync_on_load: BoolProperty(
        name="Auto-Sync on Startup",
        default=False,
        description="If enabled, automatically checks and installs company addons when Blender starts."
    )

    user_name_override: StringProperty(
        name="User Name Override",
        default="",
        description="Manually override the user name logged in .blend files. Leave empty to use Hostname/Map."
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Company Addon System", icon='PREFERENCES')
        box.label(text=f"Library Path: {COMPANY_ADDON_PATH}")
        
        # User Config
        row = box.row()
        row.prop(self, "auto_sync_on_load")
        
        # Calculate and Display Detected Name
        hostname = socket.gethostname().lower()
        default_name = HOSTNAME_MAP.get(hostname, hostname)
        
        row = box.row()
        row.label(text=f"Detected Identity: {default_name}", icon='INFO')
        
        row = box.row()
        row.prop(self, "user_name_override", icon='USER')
        
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
    if bpy.data.has_missing_files:
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
        for path in COMPANY_BOOKMARKS:
            if path not in existing:
                bookmarks.new(path)
    except Exception:
        pass

def configure_asset_libraries():
    prefs = bpy.context.preferences
    libs = prefs.filepaths.asset_libraries
    
    for name, path in COMPANY_ASSET_LIBRARIES.items():
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

def run_startup_logic():
    global _startup_run
    if _startup_run: return
    
    print("[Krutart] Initializing...")
    
    # 1. Environment Setup (Safe for home use too, mostly)
    add_bookmarks()
    configure_startup_settings()
    
    # 2. Addon Sync
    prefs = bpy.context.preferences.addons[__name__].preferences
    if prefs.auto_sync_on_load:
        # Check if we can even reach the server
        if os.path.exists(COMPANY_ADDON_PATH):
            print("[Krutart] Auto-Sync Enabled: Checking addons...")
            sync_company_addons(ignore_self=True)
        else:
            print("[Krutart] Company path unreachable. Skipping Sync.")
    else:
        print("[Krutart] Auto-Sync Disabled.")

    _startup_run = True

def register():
    bpy.utils.register_class(KRUTART_OT_sync_addons)
    bpy.utils.register_class(KRUTART_OT_update_configurator)
    bpy.utils.register_class(KrutartConfiguratorPreferences)
    
    bpy.app.handlers.save_pre.append(on_save_pre)
    bpy.app.handlers.load_post.append(on_load_post)
    
    # Timer allows Blender to fully load prefs before we read them
    bpy.app.timers.register(run_startup_logic, first_interval=1.0)
    
    print("Krutart Configurator Registered.")

def unregister():
    bpy.utils.unregister_class(KRUTART_OT_sync_addons)
    bpy.utils.unregister_class(KRUTART_OT_update_configurator)
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