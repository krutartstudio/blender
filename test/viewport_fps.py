bl_info = {
    "name": "Viewport FPS Control",
    "author": "Gemini",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "Timeline > Viewport FPS",
    "description": "Sets scene FPS to 60, but plays viewport at 30 FPS by skipping frames.",
    "warning": "",
    "doc_url": "",
    "category": "Animation",
}

import bpy

# --- Properties ---

class ViewportFPSProperties(bpy.types.PropertyGroup):
    """Properties for the Viewport FPS Control addon."""
    is_enabled: bpy.props.BoolProperty(
        name="Enable Viewport FPS Control",
        description="Enable/disable the viewport FPS control",
        default=False
    )

# --- Operators ---

class VIEWPORTFPS_OT_set_scene_fps(bpy.types.Operator):
    """Sets the scene's FPS to 60."""
    bl_idname = "viewport_fps.set_scene_fps"
    bl_label = "Set Scene FPS to 60"

    def execute(self, context):
        """Sets the scene's frames per second to 60."""
        context.scene.render.fps = 60
        self.report({'INFO'}, "Scene FPS set to 60")
        return {'FINISHED'}

# --- UI Panel ---

class VIEWPORTFPS_PT_panel(bpy.types.Panel):
    """Creates a Panel in the Timeline editor."""
    bl_label = "Viewport FPS Control"
    bl_idname = "VIEWPORTFPS_PT_panel"
    bl_space_type = 'TIMELINE'
    bl_region_type = 'UI'
    bl_category = 'Viewport FPS'

    def draw(self, context):
        """Draws the UI panel."""
        layout = self.layout
        scene = context.scene
        props = scene.viewport_fps_props

        row = layout.row()
        row.prop(props, "is_enabled")

        row = layout.row()
        row.operator("viewport_fps.set_scene_fps")

# --- Handler ---

def frame_change_handler(scene):
    """
    This function is called before each frame change.
    If the addon is enabled and the animation is playing,
    it skips every other frame.
    """
    props = scene.viewport_fps_props
    if props.is_enabled and bpy.context.screen.is_animation_playing:
        if scene.frame_current % 2 != 0:
            scene.frame_set(scene.frame_current + 1)

# --- Registration ---

classes = (
    ViewportFPSProperties,
    VIEWPORTFPS_OT_set_scene_fps,
    VIEWPORTFPS_PT_panel,
)

def register():
    """Registers the addon."""
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.viewport_fps_props = bpy.props.PointerProperty(type=ViewportFPSProperties)
    bpy.app.handlers.frame_change_pre.append(frame_change_handler)

def unregister():
    """Unregisters the addon."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.viewport_fps_props
    bpy.app.handlers.frame_change_pre.remove(frame_change_handler)

if __name__ == "__main__":
    register()
