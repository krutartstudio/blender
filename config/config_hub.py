bl_info = {
    "name": "Config Hub (Smart)",
    "author": "Your Studio Name",
    "version": (1, 1, 0),
    "blender": (4, 5, 0),
    "location": "Edit > Preferences > Add-ons",
    "description": "A central addon to manage and provide OS-specific studio asset paths. MUST be enabled for other studio addons to work.",
    "warning": "This addon holds critical paths. Do not disable if other studio addons are in use.",
    "doc_url": "",
    "category": "System",
}

import bpy
import sys
import os
from bpy.props import StringProperty, PointerProperty
from bpy.types import AddonPreferences, PropertyGroup

# --- Getter Function ---
# This function is the core logic. It lives here, isolated from other addons.
def get_active_camera_hero_path(self):
    """
    Checks the OS and returns the correct camera hero path 
    from the addon's preferences.
    """
    platform = sys.platform
    path = ""
    
    if platform == "win32":
        path = self.camera_hero_path_windows
    elif platform == "darwin":
        path = self.camera_hero_path_macos
    elif platform.startswith("linux"):
        path = self.camera_hero_path_linux
    
    return path

# --- Addon Preferences ---
# This class defines the user-configurable settings.
class ConfigHubPreferences(AddonPreferences):
    bl_idname = __name__

    # --- Path Properties (Editable) ---
    camera_hero_path_windows: StringProperty(
        name="Windows Camera Hero",
        description="Path to the master camera rig .blend file for Windows",
        subtype="FILE_PATH",
        default=r"S:\3212-PREPRODUCTION\LIBRARY\LIBRARY-HERO\RIG-HERO\CAMERA-HERO\3212_camera_hero.blend",
    )
    
    camera_hero_path_macos: StringProperty(
        name="macOS Camera Hero",
        description="Path to the master camera rig .blend file for macOS",
        subtype="FILE_PATH",
        default="/Volumes/VELKE_PROJEKTY/3212-PREPRODUCTION/LIBRARY/LIBRARY-HERO/RIG-HERO/CAMERA-HERO/3212_camera_hero.blend",
    )

    camera_hero_path_linux: StringProperty(
        name="Linux Camera Hero",
        description="Path to the master camera rig .blend file for Linux",
        subtype="FILE_PATH",
        default="/run/user/1000/gvfs/afp-volume:host=172.16.20.2,user=fred,volume=VELKE_PROJEKTY/3212-PREPRODUCTION/LIBRARY/LIBRARY-HERO/RIG-HERO/CAMERA-HERO/3212_camera_hero.blend",
    )

    # --- Active Path Property (Read-Only) ---
    # This is the "smart" property that other addons will use. It calls our
    # getter function to perform the OS check.
    active_camera_hero_path: StringProperty(
        name="Active Camera Hero Path",
        description="The validated path for the current OS (read-only)",
        get=get_active_camera_hero_path
    )
    
    # --- Draw Method ---
    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Master Camera Rig Paths:", icon='CAMERA_DATA')
        box.prop(self, "camera_hero_path_windows")
        box.prop(self, "camera_hero_path_macos")
        box.prop(self, "camera_hero_path_linux")

        # Display the active path and its status for user feedback
        info_box = layout.box()
        info_box.label(text="Status for this OS:", icon='INFO')
        
        active_path = self.active_camera_hero_path
        
        if not active_path:
            info_box.label(text="Path is not set for your current OS.", icon='ERROR')
        else:
            # Show the active path
            info_box.label(text=f"Active Path: {active_path}")
            # Check if the path exists and show a warning if not
            if os.path.exists(active_path):
                info_box.label(text="Status: File Found", icon='CHECKMARK')
            else:
                info_box.label(text="Status: File Not Found!", icon='ERROR')


# --- Registration ---
classes = (
    ConfigHubPreferences,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

