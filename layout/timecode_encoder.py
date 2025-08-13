# Blender Addon: Timecode Burn-in Crop
#
# This addon adds tools to the Video Sequence Editor (VSE) to quickly
# isolate a timecode burn-in area from a video strip and render it.
#
# Author: Gemini
# Blender: 4.x
# Version: 1.7
#
# Changelog:
# v1.7: Fixed AttributeError by using 'offset_x'/'offset_y' instead of 'translation_x'/'translation_y' for Blender 4.x transform strips.
# v1.6: Fixed render operator to ensure it renders the VSE output, not the 3D scene.
# v1.5: Added "Render Cropped Clip" operator to render the selection.
#       Fixed bug in "Crop to Burn-in" where it used hardcoded crop values.
# v1.4: Fixed TypeError by using integer pixel values for .crop properties instead of floats.
# v1.3: Corrected crop property access for Blender 4.x.
# v1.1: Fixed TypeError by updating 'seq1' to 'input1' for Blender 4.x API compatibility.
#
# Instructions:
# 1. Save this script as a Python file (e.g., "vse_crop_tools.py").
# 2. In Blender, go to Edit > Preferences > Add-ons.
# 3. Click "Install..." and select the saved Python file.
# 4. Enable the addon by checking the box next to "VSE: Timecode Burn-in Crop".
# 5. In the Video Sequence Editor, select a video strip.
# 6. Open the Sidebar (press 'N' if it's hidden).
# 7. Go to the "Tool" tab. You will find a "Timecode Tools" panel.
# 8. Click "Crop to Burn-in". This creates a new Transform effect strip.
# 9. With the new "BurninCrop_..." strip selected, click "Render Cropped Clip".
# 10. The render will save to a 'renders' subfolder where your .blend file is saved.

bl_info = {
    "name": "VSE: Timecode Burn-in Crop",
    "author": "Gemini",
    "version": (1, 7),
    "blender": (4, 0, 0),
    "location": "Video Sequence Editor > Sidebar (N) > Tool > Timecode Tools",
    "description": "Crops a video strip to a standard timecode burn-in area and renders it",
    "warning": "",
    "doc_url": "",
    "category": "Sequencer",
}

import bpy
import os


# --- Main Operator ---
# This class contains the core logic that runs when the "Crop" button is pressed.
class SEQUENCER_OT_burnin_crop(bpy.types.Operator):
    """Applies a crop effect to the selected strip to isolate the timecode burn-in"""

    bl_idname = "sequencer.burnin_crop"
    bl_label = "Crop to Burn-in"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # This function checks if the operator can run.
        # It requires an active scene with a sequence editor and an active strip.
        return (
            context.scene
            and context.scene.sequence_editor
            and context.scene.sequence_editor.active_strip
        )

    def execute(self, context):
        # --- CONFIGURATION ---
        # You can adjust the size of the crop rectangle here.
        RECT_WIDTH = 164  # pixels
        RECT_HEIGHT = 33  # pixels
        TOP_MARGIN = 40  # pixels from the top of the frame

        # Get the active context
        editor = context.scene.sequence_editor
        active_strip = editor.active_strip

        # Check if the active strip is a valid type (not a sound or effect strip)
        if active_strip.type not in {"MOVIE", "IMAGE", "SCENE", "CLIP", "META"}:
            self.report(
                {"WARNING"},
                "Cannot apply effect to this strip type. Please select a video or image strip.",
            )
            return {"CANCELLED"}

        # Get render dimensions
        render_width = context.scene.render.resolution_x
        render_height = context.scene.render.resolution_y

        # --- Calculate Crop Boundaries ---
        # The Transform effect's crop values are integer pixel coordinates.
        crop_left = (render_width - RECT_WIDTH) // 2
        # Ensure total width is correct even with odd render dimensions
        crop_right = render_width - RECT_WIDTH - crop_left

        crop_top = TOP_MARGIN
        crop_bottom = render_height - RECT_HEIGHT - crop_top

        # --- Add and Configure the Effect Strip ---
        # Add a new Transform effect strip on the channel above the active strip
        transform_strip = editor.sequences.new_effect(
            name=f"BurninCrop_{active_strip.name}",
            type="TRANSFORM",
            channel=active_strip.channel + 1,
            frame_start=active_strip.frame_final_start,
            frame_end=active_strip.frame_final_end,
            input1=active_strip,
        )

        if not transform_strip:
            self.report({"ERROR"}, "Failed to create Transform effect strip.")
            return {"CANCELLED"}

        # Apply the calculated crop values
        transform_strip.crop.min_x = crop_left
        transform_strip.crop.max_x = crop_right
        transform_strip.crop.min_y = crop_bottom
        transform_strip.crop.max_y = crop_top

        # Deselect the original strip and select the new effect strip
        active_strip.select = False
        transform_strip.select = True
        editor.active_strip = transform_strip

        self.report({"INFO"}, f"Applied burn-in crop to '{active_strip.name}'")
        return {"FINISHED"}


# --- Render Operator ---
# This class handles rendering the cropped clip.
class SEQUENCER_OT_render_cropped_clip(bpy.types.Operator):
    """Renders the selected cropped clip to a new video file"""

    bl_idname = "sequencer.render_cropped_clip"
    bl_label = "Render Cropped Clip"
    bl_options = {"REGISTER"}  # No UNDO for rendering operations

    @classmethod
    def poll(cls, context):
        # Can only run if the active strip is a Transform effect created by this addon.
        strip = context.scene.sequence_editor.active_strip
        return (
            strip and strip.type == "TRANSFORM" and strip.name.startswith("BurninCrop_")
        )

    def execute(self, context):
        scene = context.scene
        editor = scene.sequence_editor
        active_strip = editor.active_strip

        # --- Store Original Settings ---
        # We store them so we can restore them after the render is done or cancelled.
        original_settings = {
            "filepath": scene.render.filepath,
            "resolution_x": scene.render.resolution_x,
            "resolution_y": scene.render.resolution_y,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "offset_x": active_strip.transform.offset_x,
            "offset_y": active_strip.transform.offset_y,
            "use_sequencer": scene.render.use_sequencer,
        }

        # --- Define Output Path ---
        blend_file_path = bpy.data.filepath
        if not blend_file_path:
            self.report({"ERROR"}, "Please save the Blender file before rendering.")
            return {"CANCELLED"}

        # Create a 'renders' subdirectory in the same folder as the .blend file
        render_dir = os.path.join(os.path.dirname(blend_file_path), "renders")
        if not os.path.exists(render_dir):
            os.makedirs(render_dir)

        # Create a unique filename based on the original clip's name
        original_clip_name = active_strip.name.replace("BurninCrop_", "")
        output_filename = f"cropped_{original_clip_name}.mp4"
        output_path = os.path.join(render_dir, output_filename)

        # --- Calculate New Render Settings ---
        crop = active_strip.crop
        render_width = original_settings["resolution_x"]
        render_height = original_settings["resolution_y"]

        new_res_x = render_width - crop.min_x - crop.max_x
        new_res_y = render_height - crop.min_y - crop.max_y

        # Calculate the offset needed to center the cropped area
        # in the new, smaller render frame.
        new_offset_x = (crop.max_x - crop.min_x) / 2
        new_offset_y = (crop.max_y - crop.min_y) / 2

        try:
            # --- Apply New Settings for Rendering ---
            scene.render.filepath = output_path
            scene.render.resolution_x = new_res_x
            scene.render.resolution_y = new_res_y
            scene.frame_start = active_strip.frame_final_start
            scene.frame_end = active_strip.frame_final_end

            # Ensure the render comes from the VSE, not the 3D scene
            scene.render.use_sequencer = True

            # Temporarily adjust the transform strip to center the content
            active_strip.transform.offset_x = new_offset_x
            active_strip.transform.offset_y = new_offset_y

            # --- Render ---
            # 'INVOKE_DEFAULT' shows the render progress window (non-blocking)
            bpy.ops.render.render("INVOKE_DEFAULT", animation=True)

        finally:
            # --- Restore Original Settings ---
            # This block runs even if the render is cancelled or fails,
            # ensuring your scene settings are not permanently changed.
            scene.render.filepath = original_settings["filepath"]
            scene.render.resolution_x = original_settings["resolution_x"]
            scene.render.resolution_y = original_settings["resolution_y"]
            scene.frame_start = original_settings["frame_start"]
            scene.frame_end = original_settings["frame_end"]
            active_strip.transform.offset_x = original_settings["offset_x"]
            active_strip.transform.offset_y = original_settings["offset_y"]
            scene.render.use_sequencer = original_settings["use_sequencer"]

        self.report({"INFO"}, f"Rendering started. Output: {output_path}")
        return {"FINISHED"}


# --- UI Panel ---
# This class draws the panel in the VSE's sidebar.
class SEQUENCER_PT_timecode_tools(bpy.types.Panel):
    """Creates a Panel in the Sequencer's Tool properties window"""

    bl_label = "Timecode Tools"
    bl_idname = "SEQUENCER_PT_timecode_tools"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Tool"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)

        # Draw a button that runs our crop operator
        row_crop = col.row()
        row_crop.scale_y = 1.5  # Make the button a bit taller
        row_crop.operator(SEQUENCER_OT_burnin_crop.bl_idname)

        # Draw a button that runs our render operator
        row_render = col.row()
        row_render.scale_y = 1.5
        row_render.operator(SEQUENCER_OT_render_cropped_clip.bl_idname)


# --- Registration ---
# This is standard Blender addon code to register and unregister the classes.
classes = (
    SEQUENCER_OT_burnin_crop,
    SEQUENCER_OT_render_cropped_clip,
    SEQUENCER_PT_timecode_tools,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
