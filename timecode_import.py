
# bl_info is a dictionary that contains metadata about the addon.
# It's required for Blender to recognize and manage the addon.
bl_info = {
    "name": "Timecode Directory Importer",
    "author": "Gemini",
    "version": (1, 2, 0),
    "blender": (3, 0, 0),
    "location": "Video Sequence Editor > Header",
    "description": "Imports all video files from a directory and places them on the timeline based on their timecode metadata.",
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
    
    # Blender's timeline is 1-based, so we add 1.
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
        
        return None, f"'{os.path.basename(filepath)}' has no timecode track."
            
    except FileNotFoundError:
        error_msg = "ERROR: ffprobe not found. Please install FFmpeg and add it to your system's PATH."
        print(error_msg)
        return None, error_msg
    except subprocess.CalledProcessError as e:
        error_msg = f"ERROR: ffprobe failed on '{os.path.basename(filepath)}'. File may be corrupt. (Details: {e.stderr})"
        print(error_msg)
        return None, error_msg
    except (KeyError, IndexError, json.JSONDecodeError):
        error_msg = f"ERROR: Could not parse metadata for '{os.path.basename(filepath)}'."
        print(error_msg)
        return None, error_msg


# --- Main Operator ---
class SEQUENCER_OT_import_directory_with_timecode(bpy.types.Operator):
    """Operator to import all videos from a directory and set their start frames from timecode"""
    bl_idname = "sequencer.import_directory_with_timecode"
    bl_label = "Import Directory with Timecode"
    bl_description = "Select a directory, read the timecode for each video, and place them on the timeline"
    
    # Use DIR_PATH to open a directory selector instead of a file selector
    directory: bpy.props.StringProperty(
        name="Directory Path",
        description="Path to the directory containing video files",
        subtype="DIR_PATH"
    )
    
    filter_glob: bpy.props.StringProperty(
        default="*.mov;*.mp4",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        """Main execution logic, runs after a directory is selected."""
        scene = context.scene
        
        if not self.directory or not os.path.isdir(self.directory):
            self.report({'ERROR'}, "Invalid directory selected.")
            return {'CANCELLED'}

        # --- Get the list of video files ---
        supported_extensions = tuple(ext.strip('*.') for ext in self.filter_glob.split(';'))
        try:
            files_to_process = sorted([
                f for f in os.listdir(self.directory) 
                if f.lower().endswith(supported_extensions) and os.path.isfile(os.path.join(self.directory, f))
            ])
        except OSError as e:
            self.report({'ERROR'}, f"Cannot access directory: {e}")
            return {'CANCELLED'}

        if not files_to_process:
            self.report({'WARNING'}, "No supported video files (.mov, .mp4) found in the selected directory.")
            return {'CANCELLED'}

        # --- Prepare the sequence editor ---
        if not scene.sequence_editor:
            scene.sequence_editor_create()
            
        sequences = scene.sequence_editor.sequences
        # Find the highest channel currently in use to stack new clips above it
        next_channel = max((seq.channel for seq in sequences), default=0) + 1
        
        frame_rate = scene.render.fps / scene.render.fps_base
        imported_count = 0
        warning_count = 0

        # --- Process each file ---
        for filename in files_to_process:
            filepath = os.path.join(self.directory, filename)
            
            # 1. Get Timecode
            timecode, error = get_timecode_from_video(filepath)
            
            start_frame = 1  # Default start frame

            if error:
                # If ffprobe is missing, it's a critical error. Stop the operator.
                if "ffprobe not found" in error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                else:
                    # For other issues (no timecode, corrupt file), it's a warning.
                    # The clip will be placed at frame 1.
                    self.report({'WARNING'}, error)
                    warning_count += 1

            if not timecode:
                self.report({'INFO'}, f"Placing '{filename}' at frame 1.")
            else:
                # 2. Convert Timecode to Frame Number
                start_frame = timecode_to_frames(timecode, frame_rate)
                self.report({'INFO'}, f"'{filename}' | Timecode '{timecode}' -> Frame {start_frame}")

            # 3. Add Video to Sequence Editor and a Timeline Marker
            try:
                # Add the movie strip
                new_seq = sequences.new_movie(
                    name=filename,
                    filepath=filepath,
                    channel=next_channel,
                    frame_start=start_frame
                )
                
                # ✨ Add a marker at the start frame of the new clip ✨
                scene.timeline_markers.new(name=new_seq.name, frame=start_frame)

                next_channel += 1  # Increment channel for the next clip
                imported_count += 1
            except Exception as e:
                self.report({'ERROR'}, f"Failed to add movie strip for '{filename}': {e}")
                warning_count += 1

        # --- Final Report ---
        final_message = f"Import finished. Added {imported_count} clips."
        if warning_count > 0:
            final_message += f" Encountered {warning_count} warnings (see console for details)."
            self.report({'WARNING'}, final_message)
        else:
            self.report({'INFO'}, final_message)
            
        return {'FINISHED'}

    def invoke(self, context, event):
        """This method is called when the button is clicked, it opens the directory browser."""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# --- UI Drawing ---
def draw_button(self, context):
    """Function to draw the operator button in the UI."""
    self.layout.operator(
        SEQUENCER_OT_import_directory_with_timecode.bl_idname,
        text="Import Directory TC",
        icon='IMPORT'
    )

# --- Registration ---
classes = [
    SEQUENCER_OT_import_directory_with_timecode,
]

def register():
    """This function is called when the addon is enabled."""
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # Appending the button to the Video Sequence Editor's header.
    bpy.types.SEQUENCER_HT_header.append(draw_button)

def unregister():
    """This function is called when the addon is disabled."""
    # Removing the button from the Video Sequence Editor's header.
    bpy.types.SEQUENCER_HT_header.remove(draw_button)
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
