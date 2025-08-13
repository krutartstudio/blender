# <blender_addon>
#
# This script is a Blender addon to import and sequence video files
# based on a specific naming convention.
#
# Functionality:
# 1. Adds a UI panel to the Video Sequence Editor.
# 2. Provides a button to open Blender's file browser to select a directory.
# 3. Scans the selected directory and its subdirectories for .mp4 files.
# 4. Filters for files matching the pattern: "sc##-scene_name-sh###-guide.mp4".
# 5. Sorts the found videos sequentially based on the shot number (sh###).
# 6. Adds the sorted video clips to the first channel of the sequencer.
# 7. Adds a timeline marker at the start of each clip named "CAM-SC##-SH###".
#
# </blender_addon>

bl_info = {
    "name": "Video Guide Importer",
    "author": "Gemini",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "Video Sequence Editor > Sidebar > Tool",
    "description": "Imports and sequentially lays out guide videos from a directory based on shot number.",
    "warning": "",
    "doc_url": "",
    "category": "Sequencer",
}

import bpy
import os
import re


class SEQUENCER_OT_import_guide_videos(bpy.types.Operator):
    """Select a directory to import and sequence guide videos"""

    bl_idname = "sequencer.import_guide_videos"
    bl_label = "Import Guide Videos"
    bl_options = {"REGISTER", "UNDO"}

    # Property to store the selected directory path from the file browser
    directory: bpy.props.StringProperty(
        name="Directory",
        description="Choose the main directory containing your scene folders",
        subtype="DIR_PATH",
    )

    def execute(self, context):
        """
        This method is called when the operator is executed.
        It scans the directory, finds, sorts, and places the videos.
        """
        # Ensure the scene has a sequence editor, create if it doesn't exist
        if not context.scene.sequence_editor:
            context.scene.sequence_editor_create()

        sequencer = context.scene.sequence_editor

        # Regular expression to parse filenames.
        # It captures the scene number (group 1) and shot number (group 2).
        # e.g., "sc01-my_scene-sh001-guide.mp4"
        # re.IGNORECASE makes the matching case-insensitive.
        filename_pattern = re.compile(r"sc(\d+)-.*?-sh(\d+)-guide\.mp4", re.IGNORECASE)

        videos_to_import = []

        # Walk through the selected directory and all its subdirectories
        for root, _, files in os.walk(self.directory):
            for filename in files:
                match = filename_pattern.match(filename)
                if match:
                    # If a file matches the pattern, extract the numbers
                    scene_num = int(match.group(1))
                    shot_num = int(match.group(2))
                    full_path = os.path.join(root, filename)

                    # Store the extracted info and path in a list
                    videos_to_import.append(
                        {
                            "scene_num": scene_num,
                            "shot_num": shot_num,
                            "path": full_path,
                        }
                    )

        if not videos_to_import:
            self.report(
                {"WARNING"}, "No matching guide videos found in the selected directory."
            )
            return {"CANCELLED"}

        # Sort the list of videos based on the 'shot_num'
        videos_to_import.sort(key=lambda v: v["shot_num"])

        current_frame = 1
        # Use channel 1 for all strips
        vse_channel = 1

        # Process each video in the sorted list
        for video_data in videos_to_import:
            # Add the video as a new movie strip in the sequencer
            try:
                strip = sequencer.sequences.new_movie(
                    name=os.path.basename(video_data["path"]),
                    filepath=video_data["path"],
                    channel=vse_channel,
                    frame_start=current_frame,
                )
            except Exception as e:
                self.report(
                    {"ERROR"}, f"Could not load video: {video_data['path']}. Error: {e}"
                )
                continue  # Skip to the next video if one fails

            # Create the marker name, formatting numbers with leading zeros
            marker_name = (
                f"CAM-SC{video_data['scene_num']:02d}-SH{video_data['shot_num']:03d}"
            )

            # Add a new timeline marker
            marker = context.scene.timeline_markers.new(
                name=marker_name, frame=current_frame
            )
            marker.camera = (
                context.scene.camera
            )  # Assign the scene's active camera to the marker

            # Update the current_frame to be the end of the newly added strip
            # This ensures the next strip starts right after this one ends
            current_frame += strip.frame_final_duration

        self.report(
            {"INFO"},
            f"Successfully imported and sequenced {len(videos_to_import)} guide videos.",
        )
        return {"FINISHED"}

    def invoke(self, context, event):
        """
        This method is called when the operator is invoked (e.g., by a button click).
        It opens the file browser dialog.
        """
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class SEQUENCER_PT_guide_importer_panel(bpy.types.Panel):
    """Creates a Panel in the Sequencer's Sidebar"""

    bl_label = "Guide Importer"
    bl_idname = "SEQUENCER_PT_guide_importer"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Tool"

    def draw(self, context):
        """Defines the layout of the panel"""
        layout = self.layout

        row = layout.row()
        # This button will trigger the SEQUENCER_OT_import_guide_videos operator
        row.operator(SEQUENCER_OT_import_guide_videos.bl_idname, icon="FILE_FOLDER")


# A list of all classes to register/unregister
classes = (
    SEQUENCER_OT_import_guide_videos,
    SEQUENCER_PT_guide_importer_panel,
)


def register():
    """This function is called when the addon is enabled"""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """This function is called when the addon is disabled"""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


# This allows the script to be run directly in Blender's text editor
# for testing purposes.
if __name__ == "__main__":
    register()
