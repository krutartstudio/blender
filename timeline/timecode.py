import bpy
from bpy.props import BoolProperty
from bpy.app.handlers import persistent

bl_info = {
    "name": "timecode burnin",
    "author": "",
    "version": (1, 0),
    "blender": (4, 5, 0),
    "location": "Output Properties > Timecode",
    "description": "Burns timecode into rendered video",
    "category": "Render",
}

@persistent
def render_timecode(scene):
    """Handler that draws timecode on each frame"""
    if not scene.enable_timecode:
        return
    
    # Get current frame info
    frame = scene.frame_current
    fps = scene.render.fps / scene.render.fps_base
    
    # Calculate time components
    total_seconds = frame / fps
    hours = int(total_seconds / 3600)
    minutes = int((total_seconds % 3600) / 60)
    seconds = int(total_seconds % 60)
    frames = int(frame % fps)
    
    # Format timecode
    timecode_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
    
    # Setup OpenGL for text drawing
    font_id = 0
    bpy.context.preferences.view.use_text_antialiasing = True
    blf.size(font_id, 20, 72)
    blf.color(font_id, 1, 1, 1, 1)  # White text
    
    # Calculate position (top-right corner)
    width = bpy.context.region.width
    x = width - 200
    y = 50
    
    # Draw timecode
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, timecode_str)

class RENDER_PT_timecode(bpy.types.Panel):
    """Creates Panel in Output properties window"""
    bl_label = "Timecode"
    bl_idname = "RENDER_PT_timecode"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_parent_id = "RENDER_PT_output"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        layout.prop(scene, "enable_timecode")
        if scene.enable_timecode:
            layout.label(text="Timecode will be burned into rendered video", icon='INFO')

def register():
    bpy.utils.register_class(RENDER_PT_timecode)
    bpy.types.Scene.enable_timecode = BoolProperty(
        name="Enable Timecode",
        description="Burn timecode into rendered video",
        default=False
    )
    bpy.app.handlers.render_pre.append(render_timecode)

def unregister():
    bpy.utils.unregister_class(RENDER_PT_timecode)
    del bpy.types.Scene.enable_timecode
    if render_timecode in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.remove(render_timecode)

if __name__ == "__main__":
    register()
