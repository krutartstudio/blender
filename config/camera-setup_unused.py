bl_info = {
    "name": "Camera Setup From Markers",
    "author": "IORI, Gemini, Krutart",
    "version": (1, 2, 2),
    "blender": (4, 0, 0),
    "location": "Outliner > Context Menu",
    "description": "Creates camera collections based on timeline markers and appends a camera rig.",
    "warning": "",
    "doc_url": "",
    "category": "Scene",
}

import bpy
import re
import os
import logging
from bpy.props import StringProperty
from bpy.types import AddonPreferences

# --- Configure Logging ---
# Set up a logger to provide clear feedback and aid in debugging.
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- Addon Preferences ---
# This class allows users to set the path to the camera file in the addon's preferences,
# making the addon OS-independent and flexible.
class CameraSetupAddonPreferences(AddonPreferences):
    bl_idname = __name__

    camera_hero_path: StringProperty(
        name="Camera Hero File",
        description="Path to the master camera rig .blend file",
        subtype="FILE_PATH",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "camera_hero_path")


# --- Constants ---
# The name of the collection to append from the hero file.
COLLECTION_TO_APPEND = "+CAMERA+"


class SCENE_OT_setup_cameras_from_markers(bpy.types.Operator):
    """
    Scans timeline markers, creates a dedicated collection for each camera shot,
    and appends the master camera rig into it.
    """

    bl_idname = "scene.setup_cameras_from_markers"
    bl_label = "Setup Cameras from Markers"
    bl_description = (
        "Creates and places cameras into collections based on timeline markers"
    )

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        markers = scene.timeline_markers

        # Retrieve the camera file path from addon preferences
        preferences = context.preferences.addons[__name__].preferences
        camera_hero_blend_path = preferences.camera_hero_path

        log.info("--- Starting Camera Setup from Markers ---")

        # --- Initial Scene and Marker Validation ---
        if not base_name.startswith("SC"):
            msg = "This operator must be run in a SCENE (e.g., 'SC##-<env_name>')."
            log.error(msg)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        if not markers:
            msg = "No timeline markers found. Nothing to do."
            log.warning(msg)
            self.report({"WARNING"}, msg)
            return {"FINISHED"}

        if not camera_hero_blend_path or not os.path.exists(camera_hero_blend_path):
            msg = f"Camera hero file not found. Please set the correct path in Addon Preferences. Current path: '{camera_hero_blend_path}'"
            log.error(msg)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        # --- Find the main 'SHOT-SC...-ANI' collection ---
        shot_ani_collection_name = f"SHOT-{base_name}-ANI"
        shot_ani_collection = bpy.data.collections.get(shot_ani_collection_name)

        if not shot_ani_collection:
            msg = f"Parent collection '{shot_ani_collection_name}' not found. Please run the main layout setup first."
            log.error(msg)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        processed_markers = 0
        camera_offset_counter = 0  # Counter for staggering camera rigs
        for marker in sorted(markers, key=lambda m: m.frame):  # Sort markers by frame
            # Match marker names like 'CAM-SC17-SH001-FLAT'
            match = re.match(r"CAM-(SC\d+)-(SH\d+)-FLAT", marker.name, re.IGNORECASE)
            if not match:
                log.info(
                    f"Skipping marker '{marker.name}' as it does not match the required 'CAM-SC##-SH###-FLAT' format."
                )
                continue

            sc_id, sh_id = match.groups()
            sc_id_upper = sc_id.upper()
            sh_id_upper = sh_id.upper()

            # Define names for the new collections
            cam_collection_name = f"CAM-{sc_id_upper}-{sh_id_upper}"
            wrapper_collection_name = f"{sc_id_upper}-{sh_id_upper}-ANI"

            # --- Check if the wrapper collection already exists ---
            if wrapper_collection_name in shot_ani_collection.children:
                log.info(
                    f"Wrapper collection '{wrapper_collection_name}' already exists in '{shot_ani_collection.name}'. Skipping."
                )
                continue

            # --- Create the main wrapper collection for this shot ---
            wrapper_collection = bpy.data.collections.new(wrapper_collection_name)
            shot_ani_collection.children.link(wrapper_collection)
            log.info(f"Created wrapper collection '{wrapper_collection_name}'.")

            # --- Append the Camera Rig ---
            try:
                with bpy.data.libraries.load(camera_hero_blend_path, link=False) as (
                    data_from,
                    data_to,
                ):
                    data_to.collections = [
                        c for c in data_from.collections if c == COLLECTION_TO_APPEND
                    ]

                if not data_to.collections:
                    msg = f"Could not find collection '{COLLECTION_TO_APPEND}' in '{camera_hero_blend_path}'."
                    log.error(msg)
                    self.report({"ERROR"}, msg)
                    # Clean up the created wrapper collection on failure
                    bpy.data.collections.remove(wrapper_collection)
                    continue

                appended_collection = data_to.collections[0]

                # --- Rename and Link the main appended collection into its wrapper ---
                appended_collection.name = cam_collection_name
                wrapper_collection.children.link(appended_collection)

                # --- Set Collection Color ---
                appended_collection.color_tag = "COLOR_05"  # Purple
                log.info(f"Set color for '{appended_collection.name}' to purple.")

                # --- Rename Nested Collections and Objects ---
                cam_mesh_collection = appended_collection.children.get("cam_mesh")
                if cam_mesh_collection:
                    # Rename the cam_mesh collection itself
                    new_mesh_collection_name = f"{sc_id_upper}-{sh_id_upper}-cam_mesh"
                    cam_mesh_collection.name = new_mesh_collection_name
                    log.info(
                        f"Renamed 'cam_mesh' collection to '{new_mesh_collection_name}'"
                    )

                    # Rename cameras within the collection
                    for cam in cam_mesh_collection.objects:
                        if cam.type == "CAMERA":
                            if "cam_flat" in cam.name:
                                new_name = f"CAM-{sc_id_upper}-{sh_id_upper}-FLAT"
                                cam.name = new_name
                                log.info(f"Renamed 'cam_flat' to '{new_name}'")
                            elif "cam_fulldome" in cam.name:
                                new_name = f"CAM-{sc_id_upper}-{sh_id_upper}-FULLDOME"
                                cam.name = new_name
                                log.info(f"Renamed 'cam_fulldome...' to '{new_name}'")
                else:
                    log.warning(
                        f"Could not find 'cam_mesh' collection inside '{appended_collection.name}'"
                    )

                cam_rig_collection = appended_collection.children.get("cam_rig")
                if cam_rig_collection:
                    # Rename the rig collection
                    new_name = f"cam_rig-{sc_id_upper}-{sh_id_upper}"
                    cam_rig_collection.name = new_name
                    log.info(f"Renamed 'cam_rig' collection to '{new_name}'")

                    # --- Move the entire rig collection by an offset ---
                    x_offset = camera_offset_counter * 2.0
                    if x_offset > 0:
                        log.info(
                            f"Applying X-axis offset of {x_offset}m to '{new_name}'."
                        )
                        # Move all objects within the rig collection
                        for obj in cam_rig_collection.all_objects:
                            obj.location.x += x_offset

                    # Increment counter for the next camera
                    camera_offset_counter += 1

                else:
                    log.warning(
                        f"Could not find 'cam_rig' collection inside '{appended_collection.name}'"
                    )

                # --- Unlink from Scene Root ---
                if appended_collection.name in context.scene.collection.children:
                    context.scene.collection.children.unlink(appended_collection)

                log.info(
                    f"Successfully processed and configured rig for '{appended_collection.name}'."
                )

                processed_markers += 1

            except Exception as e:
                msg = f"An error occurred while processing marker '{marker.name}': {e}"
                log.error(msg, exc_info=True)
                self.report({"ERROR"}, msg)
                # Clean up the created wrapper collection on failure
                if wrapper_collection.name in bpy.data.collections:
                    bpy.data.collections.remove(wrapper_collection)
                continue

        log.info(
            f"--- Camera Setup Finished. Processed {processed_markers} markers. ---"
        )
        self.report(
            {"INFO"},
            f"Camera setup complete. Processed {processed_markers} valid markers.",
        )
        return {"FINISHED"}


def draw_camera_setup_menu(self, context):
    """Adds the operator to the Outliner's context menu."""
    scene_name = context.scene.name
    if re.match(r"^SC\d+-", scene_name):
        self.layout.separator()
        self.layout.operator(
            SCENE_OT_setup_cameras_from_markers.bl_idname, icon="CAMERA_DATA"
        )


# --- Registration ---
classes = (
    CameraSetupAddonPreferences,
    SCENE_OT_setup_cameras_from_markers,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # Add the menu item to the Outliner context menu
    bpy.types.OUTLINER_MT_context_menu.append(draw_camera_setup_menu)


def unregister():
    # Remove the menu item
    bpy.types.OUTLINER_MT_context_menu.remove(draw_camera_setup_menu)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
