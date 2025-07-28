# bl_info is a dictionary that contains metadata about the addon.
# It's required for Blender to recognize and manage the addon.
bl_info = {
    "name": "ProRes Timecode Importer",
    "author": "Gemini",
    "version": (1, 1, 0), # Updated version
    "blender": (3, 0, 0),  # Minimum Blender version required
    "location": "Dope Sheet > Header",
    "description": "Imports a video file (.mov, .mp4) and sets its start frame based on its timecode metadata.",
    "warning": "Requires FFmpeg (and ffprobe) to be installed and accessible in the system's PATH.",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
import subprocess
import json
import os

def timecode_to_frames(timecode: str, frame_rate: float) -> int:
    """
    Converts a standard SMPTE timecode string to a frame number.

    Args:
        timecode (str): The timecode string in HH:MM:SS:FF or HH:MM:SS.FF format.
        frame_rate (float): The frame rate of the scene.

    Returns:
        int: The calculated absolute frame number.

    Note: This function assumes non-drop-frame timecode.
    """
    # ffprobe can use a '.' or ':' as the final separator for frames.
    if '.' in timecode:
        parts = timecode.split('.')
        frames_part = parts[-1]
        h_m_s_part = parts[0]
    else: # ':' is the standard separator
        parts = timecode.split(':')
        frames_part = parts[-1]
        h_m_s_part = ":".join(parts[:-1])

    h, m, s = map(int, h_m_s_part.split(':'))
    f = int(frames_part)

    # Calculate total seconds from HH:MM:SS
    total_seconds = h * 3600 + m * 60 + s

    # Calculate the total number of frames from the start (0-based index)
    total_frames = int(total_seconds * frame_rate) + f

    # Blender's timeline is 1-based, so we add 1 to the 0-based frame index.
    # A timecode of 00:00:00:00 will correctly start on frame 1.
    return total_frames + 1


def get_timecode_from_video(filepath: str) -> (str or None, str or None):
    """
    Uses ffprobe to extract the timecode from a video file.

    Args:
        filepath (str): The full path to the video file.

    Returns:
        A tuple containing (timecode, error_message).
        On success, (timecode_string, None).
        On failure, (None, error_string).
    """
    command = [
        'ffprobe',
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        '-select_streams', 'v:0',
        filepath
    ]

    try:
        # On Windows, prevent a console window from popping up.
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(command, capture_output=True, text=True, check=True, startupinfo=startupinfo)
        media_info = json.loads(result.stdout)

        if 'streams' in media_info and len(media_info['streams']) > 0:
            stream = media_info['streams'][0]
            if 'tags' in stream and 'timecode' in stream['tags']:
                return stream['tags']['timecode'], None  # Success

        # If we reach here, the command succeeded but no timecode tag was found.
        return None, "Video file does not contain a timecode track."

    except FileNotFoundError:
        error_msg = "ERROR: ffprobe not found. Please install FFmpeg and add it to your system's PATH."
        print(error_msg)
        return None, error_msg
    except subprocess.CalledProcessError as e:
        error_msg = f"ERROR: ffprobe failed. The file may be corrupt. (Details: {e.stderr})"
        print(error_msg)
        return None, error_msg
    except (KeyError, IndexError, json.JSONDecodeError):
        error_msg = "ERROR: Could not parse video metadata."
        print(error_msg)
        return None, error_msg


class DOPESHEET_OT_import_prores_with_timecode(bpy.types.Operator):
    """Operator to import a video file and set its start frame from timecode"""
    bl_idname = "dopesheet.import_prores_with_timecode"
    bl_label = "Import Video with Timecode"
    bl_description = "Select a video file, read its timecode, and place it on the timeline"

    filepath: bpy.props.StringProperty(
        name="File Path",
        description="Path to the video file",
        subtype="FILE_PATH"
    )
    filter_glob: bpy.props.StringProperty(
        default="*.mov;*.mp4",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        """Main execution logic, runs after a file is selected."""
        scene = context.scene

        if not self.filepath:
            self.report({'ERROR'}, "No file selected.")
            return {'CANCELLED'}

        # --- 1. Get Timecode and/or Errors ---
        timecode, error = get_timecode_from_video(self.filepath)

        start_frame = 1 # Default start frame

        if error:
            # If ffprobe is missing, it's a critical error. Stop the operator.
            if "ffprobe not found" in error:
                 self.report({'ERROR'}, error)
                 return {'CANCELLED'}
            else:
                 # For other issues (no timecode track, corrupt file), it's a warning.
                 # The clip will be placed at frame 1.
                 self.report({'WARNING'}, error)

        if not timecode:
            self.report({'INFO'}, "Placing clip at frame 1.")
        else:
            # --- 2. Convert Timecode to Frame Number ---
            frame_rate = scene.render.fps / scene.render.fps_base
            start_frame = timecode_to_frames(timecode, frame_rate)
            self.report({'INFO'}, f"Timecode '{timecode}' corresponds to frame {start_frame}.")

        # --- 3. Add Video to Sequence Editor ---
        if not scene.sequence_editor:
            scene.sequence_editor_create()

        sequences = scene.sequence_editor.sequences
        max_channel = max(seq.channel for seq in sequences) if sequences else 0
        new_channel = max_channel + 1

        try:
            sequences.new_movie(
                name=os.path.basename(self.filepath),
                filepath=self.filepath,
                channel=new_channel,
                frame_start=start_frame
            )
        except Exception as e:
            self.report({'ERROR'}, f"Failed to add movie strip: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Clip '{os.path.basename(self.filepath)}' placed on channel {new_channel} at frame {start_frame}.")

        return {'FINISHED'}

    def invoke(self, context, event):
        """This method is called when the button is clicked, it opens the file browser."""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# --- UI Drawing ---
def draw_button(self, context):
    """Function to draw the operator button in the UI."""
    self.layout.operator(
        DOPESHEET_OT_import_prores_with_timecode.bl_idname,
        text="Import Video TC",
        icon='IMPORT'
    )

# --- Registration ---
classes = [
    DOPESHEET_OT_import_prores_with_timecode,
]

def register():
    """This function is called when the addon is enabled."""
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.DOPESHEET_HT_header.append(draw_button)

def unregister():
    """This function is called when the addon is disabled."""
    bpy.types.DOPESHEET_HT_header.remove(draw_button)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
