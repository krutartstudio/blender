bl_info = {
    "name": "Timecode Burn-in",
    "author": "Your Name Here",
    "version": (1, 0),
    "blender": (4, 4, 0),
    "location": "Output Properties > Output",
    "description": "Toggles timecode burn-in with predefined settings",
    "category": "Render",
}

import bpy

def update_timecode_burnin(self, context):
    render = context.scene.render

    if context.scene.use_timecode_burnin:
        # Enable stamp with predefined settings
        bpy.context.scene.render.use_stamp_date = False
        bpy.context.scene.render.use_stamp_render_time = False
        bpy.context.scene.render.use_stamp_frame = False
        bpy.context.scene.render.use_stamp_memory = False
        bpy.context.scene.render.use_stamp_hostname = False
        bpy.context.scene.render.use_stamp_lens = False
        bpy.context.scene.render.use_stamp_filename = False
        bpy.context.scene.render.use_stamp_sequencer_strip = False
        bpy.context.scene.render.use_stamp_marker = True
        render.use_stamp = True
        render.use_stamp_time = True
        render.use_stamp_frame_range = True
        render.use_stamp_scene = True
        render.use_stamp_camera = True
        render.stamp_font_size = 45
        render.stamp_foreground = (1, 1, 1, 1)  # White text
        render.use_stamp_labels = False
        render.stamp_background = (0.0, 0.0, 0.0, 0.5)
    else:
        # Disable the stamp feature
        render.use_stamp = False

class RENDER_PT_timecode_burnin(bpy.types.Panel):
    bl_label = "Timecode Burn-in"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 100

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.prop(scene, "use_timecode_burnin", text="Enable Burn-in Metadata")

        if scene.use_timecode_burnin:
            box = col.box()
            box.label(text="Active Burn-in Settings:", icon='SETTINGS')
            box.label(text="- Frame Range")
            box.label(text="- Time")
            box.label(text="- Scene Name")
            box.label(text="- Camera Name")
            box.label(text="White text on semi-transparent black background")

classes = (
    RENDER_PT_timecode_burnin,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.use_timecode_burnin = bpy.props.BoolProperty(
        name="Timecode Burn-in",
        description="Enable metadata burn-in with predefined settings",
        default=False,
        update=update_timecode_burnin
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.use_timecode_burnin

if __name__ == "__main__":
    register()
