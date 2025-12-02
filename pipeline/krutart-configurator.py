bl_info = {
    "name": "Krutart Configurator",
    "author": "iori, Krutart, Gemini",
    "version": (1, 4, 2), # Bumped version for the fix
    "blender": (4, 5, 0),
    "location": "Preferences > Add-ons",
    "description": "Enforces company standards, manages assets, and synchronizes company addons.",
    "warning": "",
    "doc_url": "",
    "category": "System",
}

import bpy
import os
import shutil
import ast  # For safely parsing bl_info
import addon_utils  # For managing addons
from bpy.app.handlers import persistent
from bpy.types import Operator, AddonPreferences
from bpy.props import BoolProperty, StringProperty

# --- Configuration ---

# Path to your central addon repository.
# Must contain .py and/or .zip files.
COMPANY_ADDON_PATH = "S:\\3212-PREPRODUCTION\\SOFTWARE\\BLENDER\\ADDON"

# The filename of THIS addon in the company folder, used for self-updates.
# Ensure this matches the actual filename in the repository.
SELF_FILENAME = "krutart-configurator.py"

COMPANY_BOOKMARKS = [
    "S:/3212-PREPRODUCTION",
    "S:/3212-PRODUCTION",
]

COMPANY_ASSET_LIBRARIES = {
    "LIBRARY-HERO": "S:\\3212-PREPRODUCTION\\LIBRARY\\LIBRARY-HERO",
}

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

# --- Core Logic: Addon Sync ---

def sync_company_addons(force_update=False, ignore_self=True):
    """
    Scans COMPANY_ADDON_PATH and installs/updates addons.
    
    Args:
        force_update (bool): If True, attempts to update even if versions match (not fully implemented, usually relies on version >).
        ignore_self (bool): If True, skips the 'krutart-configurator' file to prevent conflicts.
    
    Returns:
        list: Messages describing what happened.
    """
    messages = []
    print("[Krutart] Running Company Addon Sync...")
    
    if not os.path.exists(COMPANY_ADDON_PATH):
        msg = f"Error: Path not found: {COMPANY_ADDON_PATH}"
        print(f"  > {msg}")
        messages.append(msg)
        return messages

    # Get installed addons
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
    
    for item in os.listdir(COMPANY_ADDON_PATH):
        item_path = os.path.join(COMPANY_ADDON_PATH, item)
        
        # Skip if it's this addon and we are ignoring self
        if ignore_self and item == SELF_FILENAME:
            continue
            
        module_name = None
        network_version = None
        install_type = None

        try:
            if item.endswith('.py') and os.path.isfile(item_path):
                module_name = item[:-3]
                bl_info = get_bl_info_from_file(item_path)
                if bl_info:
                    network_version = bl_info.get('version', (0, 0, 1))
                install_type = 'py'

            elif item.endswith('.zip') and os.path.isfile(item_path):
                module_name = item[:-4]
                install_type = 'zip'
                # Zips are harder to version check without extracting. 
                # Strategy: Only install if missing.
            
            if not module_name:
                continue

            # Check Status
            current_version = installed_addons.get(module_name)
            
            if current_version is None:
                # New Install
                print(f"  > Installing NEW: {module_name}")
                # overwrite=True ensures clean install
                bpy.ops.preferences.addon_install(filepath=item_path, overwrite=True)
                
                # CRITICAL: Refresh module list so Blender sees the newly installed file
                addon_utils.modules_refresh()
                
                try:
                    # Use addon_utils.enable which is more robust for scripts than the operator
                    addon_utils.enable(module_name, default_set=True)
                    messages.append(f"Installed: {module_name}")
                    updates_count += 1
                except Exception as e:
                    print(f"  > Failed to enable {module_name}: {e}")
                    messages.append(f"Installed {module_name} but failed to enable (Check name match)")
            
            elif install_type == 'py' and network_version:
                if network_version > current_version:
                    # Update
                    print(f"  > Updating {module_name}: v{current_version} -> v{network_version}")
                    
                    # Disable first
                    if addon_utils.check(module_name)[0]:
                        addon_utils.disable(module_name)
                        
                    # Remove and Install
                    bpy.ops.preferences.addon_remove(module=module_name)
                    addon_utils.modules_refresh() # Clear from cache
                    
                    bpy.ops.preferences.addon_install(filepath=item_path, overwrite=True)
                    addon_utils.modules_refresh() # Register new file
                    
                    addon_utils.enable(module_name, default_set=True)
                    
                    messages.append(f"Updated: {module_name} to v{network_version}")
                    updates_count += 1
                else:
                    pass # Up to date
            
            elif install_type == 'zip':
                 # For ZIPs, check if enabled
                 is_loaded, is_installed = addon_utils.check(module_name)
                 if not is_loaded:
                     print(f"  > Enabling existing zip addon: {module_name}")
                     addon_utils.enable(module_name, default_set=True)

        except Exception as e:
            print(f"  > Failed to process {item}: {e}")
            messages.append(f"Error processing {item}")

    if updates_count == 0:
        messages.append("All company addons are up to date.")
    
    return messages

# --- Core Logic: Self Update ---

def perform_self_update():
    """
    Updates THIS addon file safely.
    1. Checks network version.
    2. Backs up current file.
    3. Copies new file.
    4. Requests restart.
    """
    network_path = os.path.join(COMPANY_ADDON_PATH, SELF_FILENAME)
    
    if not os.path.exists(network_path):
        return "Error: Configurator file not found on network."

    # Check version
    network_info = get_bl_info_from_file(network_path)
    if not network_info:
        return "Error: Could not parse network file version."
    
    network_ver = network_info.get('version', (0,0,0))
    local_ver = bl_info.get('version', (0,0,0))

    if network_ver <= local_ver:
        return f"Configurator is up to date (v{local_ver})."

    # Perform Update
    current_filepath = __file__
    backup_filepath = current_filepath + ".bak"

    try:
        print(f"[Krutart] Self-updating from v{local_ver} to v{network_ver}...")
        
        # 1. Create Backup
        shutil.copy2(current_filepath, backup_filepath)
        print(f"  > Backup created: {backup_filepath}")

        # 2. Overwrite Current
        shutil.copy2(network_path, current_filepath)
        print(f"  > New file copied from: {network_path}")
        
        return "SUCCESS"
    
    except Exception as e:
        return f"Update Failed: {e}"


# --- Operators ---

class KRUTART_OT_sync_addons(Operator):
    """Checks the company folder and updates other addons"""
    bl_idname = "krutart.sync_addons"
    bl_label = "Sync Company Addons"
    bl_description = "Installs missing addons and updates outdated ones from the company library"

    def execute(self, context):
        msgs = sync_company_addons(ignore_self=True)
        
        # Simple report to UI
        self.report({'INFO'}, f"Sync Complete. {len(msgs)} items processed.")
        for m in msgs:
            print(f"[Krutart Result] {m}")
            
        return {'FINISHED'}


class KRUTART_OT_update_configurator(Operator):
    """Updates the Krutart Configurator addon itself"""
    bl_idname = "krutart.update_self"
    bl_label = "Update Configurator"
    bl_description = "Checks for a newer version of this configurator, backs up current, and updates."

    def execute(self, context):
        result = perform_self_update()
        
        if result == "SUCCESS":
            def draw_restart_popup(self, context):
                self.layout.label(text="Configurator Updated Successfully!")
                self.layout.label(text="Please restart Blender to apply changes.")
            
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

    def draw(self, context):
        layout = self.layout
        
        # Header / Info
        box = layout.box()
        box.label(text="Company Addon System", icon='PREFERENCES')
        box.label(text=f"Library Path: {COMPANY_ADDON_PATH}")
        
        # Auto Update Settings
        row = box.row()
        row.prop(self, "auto_sync_on_load")
        
        # Manual Actions
        col = layout.column(align=True)
        col.label(text="Manual Actions:")
        
        row = col.row(align=True)
        row.scale_y = 1.5
        row.operator("krutart.sync_addons", icon='FILE_REFRESH')
        
        row = col.row(align=True)
        row.scale_y = 1.5
        # Check if network has update generally to color button? (Too expensive for draw loop)
        row.operator("krutart.update_self", icon='IMPORT', text="Check for Configurator Updates")

        # Debug Info
        layout.separator()
        layout.label(text=f"Current Configurator Version: {bl_info['version']}")


# --- Existing Handlers (Save/Load) ---

@persistent
def on_save_pre(dummy):
    # 1. Set Absolute Paths
    if bpy.context.preferences.filepaths.use_relative_paths:
        bpy.context.preferences.filepaths.use_relative_paths = False
        print("[Krutart] Enforced: 'Default to relative paths' = OFF")

    # 2. Correct FPS (DISABLED)
    # RATIONALE: This destroys VSE timelines that are not 30fps.
    # if bpy.context.scene.render.fps != 30:
    #     bpy.context.scene.render.fps = 30
    #     print("[Krutart] Set Scene FPS to 30")

    # 3. Force Solid View
    try:
        # Loop carefully to avoid issues in background mode or VSE context
        for window in bpy.context.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        # Ensure we are actually targeting a 3D view space to be safe
                        if space.type == 'VIEW_3D' and space.shading.type != 'SOLID':
                            space.shading.type = 'SOLID'
    except Exception:
        pass

@persistent
def on_load_post(dummy):
    if bpy.data.has_missing_files:
        print("[Krutart] Missing files detected. Switching Outliner...")
        try:
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
            bpy.ops.preferences.asset_library_add(directory=path)
            found = libs.get(path) # It often names it by path initially or index
            if not found: found = libs[-1] # Fallback
            found.name = name
        
        if found:
            if found.path != path: found.path = path
            if found.use_relative_path: found.use_relative_path = False
            if found.import_method != 'LINK': found.import_method = 'LINK'

def configure_startup_settings():
    # OptiX
    try:
        c_prefs = bpy.context.preferences.addons['cycles'].preferences
        c_prefs.compute_device_type = 'OPTIX'
        c_prefs.refresh_devices()
        for dev in c_prefs.devices:
            dev.use = (dev.type == 'OPTIX')
    except Exception:
        pass
    
    # ACES check (logging only)
    datafiles = bpy.utils.resource_path('DATAFILES')
    if not os.path.exists(os.path.join(datafiles, 'colormanagement', 'aces_1.2')):
        print("[Krutart] Warning: ACES 1.2 not found in datafiles.")
        
    # Libraries
    configure_asset_libraries()


# --- Registration ---

_startup_run = False

def run_startup_logic():
    global _startup_run
    if _startup_run: return
    
    print("[Krutart] Initializing...")
    
    # 1. Always run environment setup (Bookmarks, Settings, Libs)
    add_bookmarks()
    configure_startup_settings()
    
    # 2. Conditionally run Addon Sync
    prefs = bpy.context.preferences.addons[__name__].preferences
    if prefs.auto_sync_on_load:
        print("[Krutart] Auto-Sync Enabled: Checking addons...")
        sync_company_addons(ignore_self=True)
    else:
        print("[Krutart] Auto-Sync Disabled (Check Preferences to enable).")

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