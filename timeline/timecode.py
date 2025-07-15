import bpy
from bpy.props import BoolProperty, IntProperty, FloatVectorProperty
from bpy.app.handlers import persistent
import subprocess
import os
import re

bl_info = {
    "name": "Timecode Burn-in & Embed",
    "author": "iorisek",
    "version": (1, 4, 0),
    "blender": (4, 4, 0),
    "location": "Output Properties > Timecode",
    "description": "Burns timecode into video and embeds metadata",
    "category": "Render",
}

def get_frame_timecode(scene, frame):
    """Calculate timecode for a specific frame"""
    fps = scene.render.fps / scene.render.fps_base
    total_frames = frame - scene.frame_start + scene.timecode_offset
    total_seconds = total_frames / fps
    
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    frames = int(total_frames % int(round(fps)))
    
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"

def setup_compositor_nodes(scene):
    """Create timecode text overlay in compositor"""
    # Enable compositor nodes
    scene.use_nodes = True
    tree = scene.node_tree
    
    # Clear existing timecode nodes
    for node in tree.nodes:
        if "timecode_overlay" in node.name:
            tree.nodes.remove(node)
    
    # Create nodes
    render_node = tree.nodes.get("Render Layers")
    composite_node = tree.nodes.get("Composite")
    
    text_node = tree.nodes.new(type="CompositorNodeText")
    text_node.name = "timecode_overlay_text"
    text_node.label = "Timecode Overlay"
    text_node.location = (100, 300)
    
    transform_node = tree.nodes.new(type="CompositorNodeTransform")
    transform_node.name = "timecode_overlay_transform"
    transform_node.label = "Timecode Position"
    transform_node.location = (300, 300)
    
    alpha_over_node = tree.nodes.new(type="CompositorNodeAlphaOver")
    alpha_over_node.name = "timecode_overlay_alpha"
    alpha_over_node.label = "Timecode Overlay"
    alpha_over_node.location = (500, 300)
    
    # Set default positions
    transform_node.inputs[1].default_value = scene.render.resolution_x - scene.timecode_margin
    transform_node.inputs[2].default_value = scene.render.resolution_y - scene.timecode_margin
    
    # Connect nodes
    tree.links.new(render_node.outputs[0], alpha_over_node.inputs[1])
    tree.links.new(text_node.outputs[0], transform_node.inputs[0])
    tree.links.new(transform_node.outputs[0], alpha_over_node.inputs[2])
    
    if composite_node:
        tree.links.new(alpha_over_node.outputs[0], composite_node.inputs[0])

@persistent
def update_compositor_text(scene):
    """Update compositor text node with current timecode"""
    if not scene.enable_timecode:
        return
    
    if not scene.use_nodes:
        return
        
    tree = scene.node_tree
    text_node = tree.nodes.get("timecode_overlay_text")
    
    if text_node:
        frame = scene.frame_current
        timecode_str = get_frame_timecode(scene, frame)
        text_node.text = timecode_str
        
        # Update styling
        text_node.size = scene.timecode_font_size
        text_node.color = (
            scene.timecode_text_color[0],
            scene.timecode_text_color[1],
            scene.timecode_text_color[2],
            1.0
        )

@persistent
def set_ffmpeg_timecode(scene):
    """Set FFmpeg timecode metadata before rendering starts"""
    if not scene.enable_timecode or not scene.render.is_movie_format:
        return
    
    # Only set for FFmpeg-based formats
    if scene.render.image_settings.file_format != 'FFMPEG':
        return
    
    # Calculate starting timecode
    start_tc = get_frame_timecode(scene, scene.frame_start)
    
    # Set FFmpeg timecode metadata
    if hasattr(scene.render, 'ffmpeg'):
        scene.render.ffmpeg.use_timecode = True
        scene.render.ffmpeg.timecode = start_tc

@persistent
def finalize_video_metadata(scene):
    """Embed timecode in video file after rendering completes"""
    if not scene.enable_timecode or not scene.render.is_movie_format:
        return
    
    output_path = bpy.path.abspath(scene.render.filepath)
    
    # Handle sequence numbers
    if "#" in output_path:
        output_path = re.sub(r"#+", lambda m: str(scene.frame_start).zfill(len(m.group())), output_path)
    
    # Add extension if missing
    if not output_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
        output_path += scene.render.file_extension
    
    # Check if file exists
    if not os.path.exists(output_path):
        print(f"Output file not found: {output_path}")
        return
    
    # Only process supported formats
    if scene.render.image_settings.file_format != 'FFMPEG':
        return
    
    # Get starting timecode
    start_tc = get_frame_timecode(scene, scene.frame_start)
    
    try:
        # Use FFmpeg to embed timecode metadata
        temp_path = output_path + ".temp.mp4"
        
        cmd = [
            'ffmpeg',
            '-i', output_path,
            '-c', 'copy',
            '-timecode', start_tc,
            '-metadata', f'timecode={start_tc}',
            '-y',  # Overwrite without asking
            temp_path
        ]
        
        subprocess.run(cmd, check=True)
        
        # Replace original file with metadata-enhanced version
        os.replace(temp_path, output_path)
        
        print(f"Successfully embedded timecode metadata: {start_tc}")
        
    except Exception as e:
        print(f"Error embedding timecode metadata: {str(e)}")

class RENDER_PT_timecode(bpy.types.Panel):
    bl_label = "Timecode Burn-in & Embed"
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
            box = layout.box()
            box.label(text="Visual Settings", icon='TEXT')
            box.prop(scene, "timecode_font_size")
            box.prop(scene, "timecode_margin")
            box.prop(scene, "timecode_text_color")
            
            box = layout.box()
            box.label(text="Timecode Settings", icon='TIME')
            box.prop(scene, "timecode_offset")
            
            if scene.render.is_movie_format:
                box = layout.box()
                box.label(text="Metadata Embedding", icon='FILE_SCRIPT')
                box.label(text="Timecode will be embedded in video metadata")
                box.label(text="Compatible with Premiere Pro, Final Cut, etc.")
            else:
                layout.label(text="Metadata embedding only works with video formats", icon='INFO')

def register():
    # Clean up any previous installation
    unregister()
    
    bpy.utils.register_class(RENDER_PT_timecode)
    
    # Visual properties
    bpy.types.Scene.enable_timecode = BoolProperty(
        name="Enable Timecode",
        description="Burn timecode into video and embed metadata",
        default=False,
        update=lambda self, context: setup_compositor_nodes(context.scene)
    )
    bpy.types.Scene.timecode_font_size = IntProperty(
        name="Font Size",
        description="Timecode font size",
        default=24,
        min=8,
        max=100
    )
    bpy.types.Scene.timecode_margin = IntProperty(
        name="Margin",
        description="Distance from screen edges",
        default=20,
        min=0
    )
    bpy.types.Scene.timecode_text_color = FloatVectorProperty(
        name="Text Color",
        description="Timecode text color",
        subtype='COLOR',
        size=3,
        default=(1.0, 1.0, 1.0),
        min=0.0,
        max=1.0
    )
    
    # Timecode properties
    bpy.types.Scene.timecode_offset = IntProperty(
        name="Frame Offset",
        description="Starting frame number for timecode",
        default=0
    )
    
    # Handlers
    bpy.app.handlers.render_init.append(set_ffmpeg_timecode)
    bpy.app.handlers.render_complete.append(finalize_video_metadata)
    bpy.app.handlers.render_cancel.append(finalize_video_metadata)
    bpy.app.handlers.frame_change_pre.append(update_compositor_text)

def unregister():
    # Unregister class
    try:
        bpy.utils.unregister_class(RENDER_PT_timecode)
    except RuntimeError:
        pass
    
    # Remove properties
    props = [
        "enable_timecode",
        "timecode_font_size",
        "timecode_margin",
        "timecode_text_color",
        "timecode_offset"
    ]
    
    for prop in props:
        if hasattr(bpy.types.Scene, prop):
            try:
                delattr(bpy.types.Scene, prop)
            except Exception as e:
                print(f"Error removing property {prop}: {str(e)}")
    
    # Remove handlers
    handler_lists = [
        bpy.app.handlers.render_init,
        bpy.app.handlers.render_complete,
        bpy.app.handlers.render_cancel,
        bpy.app.handlers.frame_change_pre
    ]
    
    handlers = [set_ffmpeg_timecode, finalize_video_metadata, update_compositor_text]
    
    for handler_list in handler_lists:
        for handler in handlers:
            if handler in handler_list:
                handler_list.remove(handler)

# Always call unregister when reloading
if __name__ == "__main__":
    register()
