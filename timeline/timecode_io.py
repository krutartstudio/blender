import bpy
import subprocess
import os
import json
from bpy.props import StringProperty, BoolProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy.utils import register_class, unregister_class

bl_info = {
    "name": "",
    "author": "",
    "version": (1, 0),
    "blender": (4, 5, 0),
    "location": "Render Settings > Timecode",
    "description": "Embeds/reads timecode in MP4 videos",
    "category": "Render",
}

# Helper functions
def get_ffmpeg_path():
    return bpy.app.binary_path_ffmpeg

def timecode_to_frames(timecode, fps):
    h, m, s, f = map(int, timecode.split(':'))
    return f + (s + m * 60 + h * 3600) * fps

def frames_to_timecode(frames, fps):
    frames = int(frames)
    ff = frames % fps
    ss = (frames // fps) % 60
    mm = (frames // (fps * 60)) % 60
    hh = frames // (fps * 3600)
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

# Timecode Properties
class TimecodeProperties(PropertyGroup):
    enable_export: BoolProperty(
        name="Embed Timecode",
        description="Embed timecode in exported video",
        default=True
    )

    custom_timecode: StringProperty(
        name="Start Timecode",
        description="Timecode for first frame (HH:MM:SS:FF)",
        default="00:00:00:00"
    )

    imported_timecode: StringProperty(
        name="Imported Timecode",
        description="Timecode read from imported video",
        default=""
    )

    use_scene_frame: BoolProperty(
        name="Use Scene Start Frame",
        description="Calculate timecode from scene start frame",
        default=True
    )

# Operators
class TIME_OT_import_timecode(Operator):
    bl_idname = "timecode.import_timecode"
    bl_label = "Read Timecode from Video"
    bl_description = "Extract timecode metadata from selected video file"

    filepath: StringProperty(subtype='FILE_PATH')

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No file selected")
            return {'CANCELLED'}

        ffmpeg = get_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-i", self.filepath,
            "-v", "quiet",
            "-show_entries", "format_tags=timecode",
            "-print_format", "json"
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            timecode = data.get('format', {}).get('tags', {}).get('timecode', '')

            if timecode:
                scene = context.scene
                tc_props = scene.timecode_tools
                tc_props.imported_timecode = timecode

                # Optional: Set scene start frame to match timecode
                if tc_props.use_scene_frame:
                    fps = scene.render.fps / scene.render.fps_base
                    start_frame = timecode_to_frames(timecode, fps)
                    scene.frame_start = start_frame

                self.report({'INFO'}, f"Timecode: {timecode}")
                return {'FINISHED'}
            else:
                self.report({'WARNING'}, "No timecode found in video")
                return {'CANCELLED'}

        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            self.report({'ERROR'}, f"Error reading timecode: {str(e)}")
            return {'CANCELLED'}

class TIME_OT_export_timecode(Operator):
    bl_idname = "timecode.export_timecode"
    bl_label = "Embed Timecode in Render"
    bl_description = "Add timecode metadata to rendered video (requires MP4 output)"

    def execute(self, context):
        scene = context.scene
        tc_props = scene.timecode_tools

        if not tc_props.enable_export:
            return {'FINISHED'}

        # Validate output settings
        if not scene.render.is_movie_format or scene.render.filepath.lower()[-4:] != ".mp4":
            self.report({'ERROR'}, "Timecode requires MP4 video output")
            return {'CANCELLED'}

        # Calculate start timecode
        if tc_props.use_scene_frame:
            fps = scene.render.fps / scene.render.fps_base
            timecode = frames_to_timecode(scene.frame_start, fps)
        else:
            timecode = tc_props.custom_timecode

        # Validate timecode format
        if not all(x.isdigit() for x in timecode.split(':')) or len(timecode.split(':')) != 4:
            self.report({'ERROR'}, "Invalid timecode format. Use HH:MM:SS:FF")
            return {'CANCELLED'}

        # Render normally first
        bpy.ops.render.render(animation=True)

        # Add timecode metadata with FFmpeg
        original = scene.render.filepath
        temp_file = original.replace(".mp4", "_temp.mp4")

        ffmpeg = get_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-i", original,
            "-c", "copy",
            "-metadata", f"timecode={timecode}",
            "-y", temp_file
        ]

        try:
            subprocess.run(cmd, check=True)
            os.replace(temp_file, original)
            self.report({'INFO'}, f"Timecode {timecode} embedded successfully")
            return {'FINISHED'}
        except (subprocess.CalledProcessError, OSError) as e:
            self.report({'ERROR'}, f"Timecode embedding failed: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return {'CANCELLED'}

# UI Panel
class TIMECODE_PT_panel(Panel):
    bl_label = "Timecode Tools"
    bl_idname = "TIMECODE_PT_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        tc_props = scene.timecode_tools

        # Import Section
        box = layout.box()
        box.label(text="Import Timecode")
        row = box.row()
        row.operator("timecode.import_timecode", text="Select Video").filepath = ""

        if tc_props.imported_timecode:
            row = box.row()
            row.label(text=f"Detected Timecode: {tc_props.imported_timecode}")

            row = box.row()
            row.prop(tc_props, "use_scene_frame")

        # Export Section
        box = layout.box()
        box.label(text="Export Timecode")
        box.prop(tc_props, "enable_export")

        if tc_props.enable_export:
            row = box.row()
            row.prop(tc_props, "use_scene_frame")

            if not tc_props.use_scene_frame:
                row = box.row()
                row.prop(tc_props, "custom_timecode")

            box.operator("timecode.export_timecode")

# Registration
classes = (
    TimecodeProperties,
    TIME_OT_import_timecode,
    TIME_OT_export_timecode,
    TIMECODE_PT_panel
)

def register():
    for cls in classes:
        register_class(cls)
    bpy.types.Scene.timecode_tools = bpy.props.PointerProperty(type=TimecodeProperties)

def unregister():
    for cls in reversed(classes):
        unregister_class(cls)
    del bpy.types.Scene.timecode_tools

if __name__ == "__main__":
    register()
