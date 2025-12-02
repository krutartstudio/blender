bl_info = {
    "name": "Krutart Windows Path Fixer",
    "author": "Krutart Pipeline",
    "version": (1, 0),
    "blender": (3, 0, 0),
    "location": "File > External Data",
    "description": "Remaps broken Windows (S:) paths to local Mac symlinks",
    "category": "System",
}

import bpy
import os

# ------------------------------------------------------------------------
#   Core Logic
# ------------------------------------------------------------------------

def get_target_path(context):
    """Returns the local Mac root path from preferences."""
    addon_prefs = context.preferences.addons[__name__].preferences
    path = addon_prefs.mac_root_path
    # Expand ~ to user home directory if present
    return os.path.abspath(os.path.expanduser(path))

def get_source_prefix(context):
    """Returns the Windows root path prefix to look for."""
    addon_prefs = context.preferences.addons[__name__].preferences
    return addon_prefs.win_root_prefix.lower() # Return lowercase for case-insensitive check

def fix_paths(context, report_only=False):
    """
    Iterates through libraries and images. 
    If they start with the Windows prefix, remap them to the Mac path.
    """
    win_prefix = get_source_prefix(context) # e.g. "s:\3212-preproduction"
    mac_root = get_target_path(context)     # e.g. "/Users/iori/VELKE_PROJEKTY/3212"
    
    remapped_count = 0
    errors = []
    
    # 1. Process Linked Libraries (.blend files)
    for lib in bpy.data.libraries:
        # Normalize path for checking (lower case, forward slashes)
        current_path_raw = lib.filepath
        current_path_norm = current_path_raw.replace('\\', '/').lower()
        
        # Check if it starts with the windows drive letter
        # We strip the 's:' part to map the rest
        clean_win_prefix = win_prefix.replace('\\', '/')
        
        if current_path_norm.startswith(clean_win_prefix):
            # Extract the suffix (the part after S:\3212-PREPRODUCTION)
            # len(clean_win_prefix) gives us the index where the suffix starts
            suffix = current_path_raw.replace('\\', '/')[len(clean_win_prefix):]
            
            # Ensure suffix doesn't start with a slash to avoid double slash
            if suffix.startswith('/'):
                suffix = suffix[1:]
                
            new_path = os.path.join(mac_root, suffix)
            
            if report_only:
                print(f"[Report] Would remap: {current_path_raw} -> {new_path}")
            else:
                print(f"[Fix] Remapping: {current_path_raw} -> {new_path}")
                lib.filepath = new_path
                try:
                    lib.reload()
                except:
                    errors.append(f"Could not reload library: {lib.name}")
                
            remapped_count += 1

    # 2. Process Images
    for img in bpy.data.images:
        if img.source == 'FILE' and img.filepath:
            current_path_raw = img.filepath
            current_path_norm = current_path_raw.replace('\\', '/').lower()
            clean_win_prefix = win_prefix.replace('\\', '/')
            
            if current_path_norm.startswith(clean_win_prefix):
                suffix = current_path_raw.replace('\\', '/')[len(clean_win_prefix):]
                if suffix.startswith('/'): suffix = suffix[1:]
                
                new_path = os.path.join(mac_root, suffix)
                
                if report_only:
                    print(f"[Report] Image: {current_path_raw} -> {new_path}")
                else:
                    print(f"[Fix] Image: {current_path_raw} -> {new_path}")
                    img.filepath = new_path
                    img.reload()
                remapped_count += 1

    return remapped_count, errors

# ------------------------------------------------------------------------
#   Operators
# ------------------------------------------------------------------------

class OT_KrutartRemapPaths(bpy.types.Operator):
    """Remap Windows S: Drive paths to Local Symlinks"""
    bl_idname = "krutart.remap_paths"
    bl_label = "Fix Windows Paths"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count, errors = fix_paths(context, report_only=False)
        
        if errors:
            self.report({'WARNING'}, f"Remapped {count} paths with {len(errors)} errors. Check Console.")
        else:
            self.report({'INFO'}, f"Successfully remapped {count} paths.")
            
        return {'FINISHED'}

class OT_KrutartCheckPaths(bpy.types.Operator):
    """Check for broken S: paths without changing them"""
    bl_idname = "krutart.check_paths"
    bl_label = "Check Broken Links"

    def execute(self, context):
        count, errors = fix_paths(context, report_only=True)
        self.report({'INFO'}, f"Found {count} paths pointing to Windows drive. Check System Console for list.")
        return {'FINISHED'}

# ------------------------------------------------------------------------
#   UI & Preferences
# ------------------------------------------------------------------------

class KrutartPathPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    win_root_prefix: bpy.props.StringProperty(
        name="Windows Root (Source)",
        description="The path prefix to look for (e.g. S:\\Project)",
        default="S:\\3212-PREPRODUCTION"
    )

    mac_root_path: bpy.props.StringProperty(
        name="Mac Root (Target)",
        description="The local symlink path where files actually exist",
        subtype='DIR_PATH',
        default="/Users/iori/VELKE_PROJEKTY/3212"
    )
    
    auto_fix_on_load: bpy.props.BoolProperty(
        name="Auto-Fix on Load",
        description="Automatically run the fixer when opening a file",
        default=False
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Path Configuration")
        layout.prop(self, "win_root_prefix")
        layout.prop(self, "mac_root_path")
        layout.prop(self, "auto_fix_on_load")

def menu_func(self, context):
    self.layout.separator()
    self.layout.operator(OT_KrutartRemapPaths.bl_idname, icon='FILE_REFRESH')
    self.layout.operator(OT_KrutartCheckPaths.bl_idname, icon='INFO')

# ------------------------------------------------------------------------
#   Handlers (Auto-Load)
# ------------------------------------------------------------------------

from bpy.app.handlers import persistent

@persistent
def load_post_handler(dummy):
    """Called after a file is loaded"""
    context = bpy.context
    
    # Check for version warning (from logs: 405.90 vs current)
    # This is a rough check just to warn the user
    if bpy.data.version > bpy.app.version:
        print("WARNING: File version is newer than Blender binary!")
        
    # Auto-Fix logic
    try:
        prefs = context.preferences.addons[__name__].preferences
        if prefs.auto_fix_on_load:
            print("Krutart Addon: Auto-fixing paths...")
            count, errors = fix_paths(context, report_only=False)
            if count > 0:
                print(f"Krutart Addon: Auto-fixed {count} paths.")
    except Exception as e:
        print(f"Krutart Addon Handler Error: {e}")

# ------------------------------------------------------------------------
#   Registration
# ------------------------------------------------------------------------

def register():
    bpy.utils.register_class(KrutartPathPreferences)
    bpy.utils.register_class(OT_KrutartRemapPaths)
    bpy.utils.register_class(OT_KrutartCheckPaths)
    bpy.types.TOPBAR_MT_file_external_data.append(menu_func)
    bpy.app.handlers.load_post.append(load_post_handler)

def unregister():
    bpy.types.TOPBAR_MT_file_external_data.remove(menu_func)
    bpy.app.handlers.load_post.remove(load_post_handler)
    bpy.utils.unregister_class(OT_KrutartCheckPaths)
    bpy.utils.unregister_class(OT_KrutartRemapPaths)
    bpy.utils.unregister_class(KrutartPathPreferences)

if __name__ == "__main__":
    register()
