bl_info = {
    "name": "ProRes Timecode Encoder",
    "author": "Your Name & AI Assistant",
    "version": (1, 1, 0),
    "blender": (4, 1, 0),
    "location": "Properties > Output Properties > ProRes Timecode",
    "description": "Automatically embeds scene start timecode into rendered ProRes MOV files using FFmpeg.",
    "warning": "Requires FFmpeg to be installed. The addon will try to find it automatically.",
    "doc_url": "https://ffmpeg.org/download.html",
    "category": "Render",
}

import bpy
import subprocess
import sys
import os
import shutil
from bpy.app.handlers import persistent

# --- Helper Functions ---

def find_ffmpeg_path():
    """
    Tries to find the FFmpeg executable automatically.
    Checks addon preferences first, then system PATH.
    """
    # 1. Check for a manually set path in preferences first.
    prefs = bpy.context.preferences.addons[__name__].preferences
    if prefs.ffmpeg_path and is_path_executable(prefs.ffmpeg_path):
        return prefs.ffmpeg_path

    # 2. If no manual path, search for 'ffmpeg' in the system's PATH environment variable.
    # shutil.which() is the recommended, cross-platform way to do this.
    ffmpeg_executable = shutil.which("ffmpeg")
    if ffmpeg_executable:
        return ffmpeg_executable

    # 3. If not found, return None.
    return None

def is_path_executable(path):
    """Checks if a given path points to an executable file."""
    if not path or not os.path.isfile(path):
        return False
    if os.name == 'nt': # On Windows, just checking for the file is usually enough.
        return True
    # On POSIX systems (Linux, macOS), check for the execute permission bit.
    return os.access(path, os.X_OK)


def frames_to_timecode(frames, fps, fps_base=1.0):
    """Converts a frame number to a SMPTE timecode string (HH:MM:SS:FF)."""
    effective_fps = fps / fps_base
    frames = int(frames)
    
    # Standard SMPTE calculation
    # Drop-frame timecode is complex and only applies to specific frame rates (like 29.97).
    # This calculation is for non-drop-frame timecode, which is most common.
    total_seconds = frames / effective_fps
    ss = int(total_seconds) % 60
    mm = int(total_seconds / 60) % 60
    hh = int(total_seconds / 3600)
    ff = frames % round(effective_fps)

    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

# --- Core Logic ---

@persistent
def embed_timecode_handler(scene):
    """
    This function is called by the 'render_post' handler after a render completes.
    It checks settings and runs FFmpeg to embed the timecode.
    """
    if not scene.prores_timecode_props.enabled:
        print("ProRes Timecode: Feature disabled, skipping.")
        return

    ffmpeg_path = find_ffmpeg_path()
    if not ffmpeg_path:
        print("ProRes Timecode Error: FFmpeg executable not found. Please install it and set the path in Addon Preferences.")
        bpy.context.window_manager.popup_menu(
            lambda self, context: self.layout.label(text="FFmpeg not found. Please configure it in Addon Preferences."),
            title="ProRes Timecode Error",
            icon='ERROR'
        )
        return

    image_settings = scene.render.image_settings
    if image_settings.file_format != 'FFMPEG' or scene.render.ffmpeg.format != 'QUICKTIME':
        print("ProRes Timecode: Output format is not QuickTime (.mov). Skipping.")
        return

    output_path = scene.render.frame_path(frame=scene.frame_current)
    if not os.path.exists(output_path):
        print(f"ProRes Timecode Error: Rendered file not found at {output_path}")
        return

    start_frame = scene.frame_start
    timecode_str = frames_to_timecode(start_frame, scene.render.fps, scene.render.fps_base)
    print(f"ProRes Timecode: Calculated start timecode: {timecode_str} for frame {start_frame}")

    path_parts = os.path.splitext(output_path)
    temp_output_path = f"{path_parts[0]}_tc{path_parts[1]}"

    command = [
        ffmpeg_path,
        '-i', output_path,
        '-c', 'copy',
        '-timecode', timecode_str,
        '-y',
        temp_output_path
    ]

    print(f"ProRes Timecode: Running FFmpeg command: {' '.join(command)}")

    try:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(command, check=True, capture_output=True, text=True, startupinfo=startupinfo)
        print("ProRes Timecode: FFmpeg executed successfully.")
        print("FFmpeg stdout:", result.stdout)
        
        os.remove(output_path)
        os.rename(temp_output_path, output_path)
        print(f"ProRes Timecode: Successfully created timecoded file at {output_path}")

    except FileNotFoundError:
        print(f"ProRes Timecode Error: FFmpeg not found at '{ffmpeg_path}'.")
    except subprocess.CalledProcessError as e:
        print("ProRes Timecode Error: FFmpeg failed to execute.")
        print("Return code:", e.returncode)
        print("FFmpeg stderr:", e.stderr)
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)
    except Exception as e:
        print(f"ProRes Timecode Error: An unexpected error occurred: {e}")
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)

# --- UI & Properties ---

class ProResTimecodeProperties(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="Embed Timecode",
        description="Enable to automatically embed scene start timecode into rendered ProRes files after rendering",
        default=False,
    )

class ProResTimecodePanel(bpy.types.Panel):
    bl_label = "ProRes Timecode"
    bl_idname = "OUTPUT_PT_prores_timecode"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"

    def draw(self, context):
        layout = self.layout
        props = context.scene.prores_timecode_props
        layout.row().prop(props, "enabled")

        ffmpeg_path = find_ffmpeg_path()
        if not ffmpeg_path:
            box = layout.box()
            box.label(text="FFmpeg not found!", icon='ERROR')
            box.label(text="Install it or set the path in Preferences.")
        
        image_settings = context.scene.render.image_settings
        is_mov = image_settings.file_format == 'FFMPEG' and context.scene.render.ffmpeg.format == 'QUICKTIME'
        if props.enabled and not is_mov:
            box = layout.box()
            box.label(text="Output format must be 'FFmpeg Video'", icon='WARNING')
            box.label(text="with Container set to 'Quicktime' (.mov).")

class ProResTimecodeAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    ffmpeg_path: bpy.props.StringProperty(
        name="FFmpeg Manual Path",
        description="Manually set the path to the FFmpeg executable. Leave blank to auto-detect",
        subtype='FILE_PATH',
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        
        ffmpeg_path = find_ffmpeg_path()
        
        if ffmpeg_path:
            box.label(text="FFmpeg Detected:", icon='CHECKMARK')
            box.label(text=ffmpeg_path)
        else:
            box.label(text="FFmpeg Not Found!", icon='ERROR')
            box.label(text="Please install FFmpeg and ensure it's in your system's PATH.")
            row = box.row()
            row.operator("wm.url_open", text="Download FFmpeg").url = "https://ffmpeg.org/download.html"

        layout.separator()
        layout.label(text="You can manually override the path below if needed:")
        layout.prop(self, "ffmpeg_path")

# --- Registration ---

classes = (
    ProResTimecodeProperties,
    ProResTimecodePanel,
    ProResTimecodeAddonPreferences,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.prores_timecode_props = bpy.props.PointerProperty(type=ProResTimecodeProperties)
    if embed_timecode_handler not in bpy.app.handlers.render_post:
        bpy.app.handlers.render_post.append(embed_timecode_handler)

def unregister():
    if embed_timecode_handler in bpy.app.handlers.render_post:
        bpy.app.handlers.render_post.remove(embed_timecode_handler)
    del bpy.types.Scene.prores_timecode_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
