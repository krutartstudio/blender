bl_info = {
    "name": "Layout Suite",
    "author": "IORI, Gemini, Krutart",
    "version": (2, 3, 2),
    "blender": (4, 1, 0),
    "location": "3D View > UI > Layout Suite",
    "description": "A unified addon to initialize collection structures, import animatics, and set up cameras from timeline markers based on a specific studio pipeline.",
    "warning": "",
    "doc_url": "",
    "category": "Scene",
}

import bpy
import re
import os
import logging
from bpy.props import StringProperty, EnumProperty
from bpy.types import AddonPreferences

# --- Configure Logging ---
# Set up a logger for clear feedback and debugging.
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- Addon Preferences ---
# Allows users to set the path for the camera rig file in addon preferences.
class LayoutCameraAddonPreferences(AddonPreferences):
    bl_idname = __name__

    camera_hero_path: StringProperty(
        name="Camera Hero File",
        description="Path to the master camera rig .blend file",
        subtype="FILE_PATH",
        default="/run/user/1000/gvfs/afp-volume:host=172.16.20.2,user=fred,volume=VELKE_PROJEKTY/3212-PREPRODUCTION/LIBRARY/LIBRARY-HERO/RIG-HERO/CAMERA-HERO/3212_camera-hero.blend",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "camera_hero_path")


# --- Constants ---
# Color tags for collection organization
COLLECTION_COLORS = {
    "LOCATION": "COLOR_05",  # Blue
    "ENVIRO": "COLOR_04",  # Green
    "SCENE": "COLOR_01",  # Red
    "ART": "COLOR_02",  # Yellow
    "ANI": "COLOR_03",  # Orange
    "VFX": "COLOR_06",  # Pink/Magenta
    "CAMERA": "COLOR_07",  # Purple
}

# Name of the camera collection to append from the hero file
CAMERA_COLLECTION_TO_APPEND = "+CAMERA+"


# --- Helper Functions ---
def get_or_create_collection(name, parent_collection, color_tag=None):
    """
    Checks if a collection exists. If so, links it. If not, creates it.
    Applies a color tag if provided. Returns the collection and a boolean indicating if it was created.
    """
    created = False
    collection = bpy.data.collections.get(name)

    if collection is None:
        collection = bpy.data.collections.new(name)
        parent_collection.children.link(collection)
        created = True
    else:
        # Link collection if it exists but is not in the parent's children
        if name not in parent_collection.children:
            parent_collection.children.link(collection)

    if color_tag:
        collection.color_tag = color_tag

    return collection, created


# --- Collection Setup Operators ---
class SCENE_OT_create_location_structure(bpy.types.Operator):
    """Operator to build the LOCATION collection structure."""

    bl_idname = "scene.create_location_structure"
    bl_label = "Setup LOCATION Collections"
    bl_description = "Creates the collection structure for a LOCATION scene (LOC-)"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name  # e.g., "LOC-LOC_NAME"
        master_collection = scene.collection
        parent_col_name = f"+{base_name}+"

        loc_parent_col, created = get_or_create_collection(
            parent_col_name,
            master_collection,
            color_tag=COLLECTION_COLORS["LOCATION"],
        )

        if not created:
            self.report(
                {"INFO"},
                f"Base collection '{parent_col_name}' already exists. Verifying sub-collections.",
            )

        get_or_create_collection(f"TERRAIN-{base_name}", loc_parent_col)
        get_or_create_collection(f"MODEL-{base_name}", loc_parent_col)
        get_or_create_collection(f"VFX-{base_name}", loc_parent_col)

        self.report({"INFO"}, f"Verified LOCATION structure for '{base_name}'.")
        return {"FINISHED"}


class SCENE_OT_create_enviro_structure(bpy.types.Operator):
    """Operator to build the ENVIRONMENT collection structure."""

    bl_idname = "scene.create_enviro_structure"
    bl_label = "Setup ENVIRO Collections"
    bl_description = "Creates the collection structure for an ENVIRONMENT scene (ENV-)"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name  # e.g., "ENV-ENV_NAME"
        master_collection = scene.collection
        parent_col_name = f"+{base_name}+"

        env_parent_col, created = get_or_create_collection(
            parent_col_name,
            master_collection,
            color_tag=COLLECTION_COLORS["ENVIRO"],
        )

        if not created:
            self.report(
                {"INFO"},
                f"Base collection '{parent_col_name}' already exists. Verifying sub-collections.",
            )

        get_or_create_collection(f"MODEL-{base_name}", env_parent_col)
        get_or_create_collection(f"VFX-{base_name}", env_parent_col)

        self.report({"INFO"}, f"Verified ENVIRO structure for '{base_name}'.")
        return {"FINISHED"}


class SCENE_OT_create_scene_structure(bpy.types.Operator):
    """Operator to build the SCENE collection structure and link LOCATION/ENVIROs."""

    bl_idname = "scene.create_scene_structure"
    bl_label = "Setup SCENE Collections"
    bl_description = (
        "Creates SCENE (SC##-) collections and links root LOCATION and matching ENVIROs"
    )

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        master_collection = scene.collection

        match = re.match(r"^(SC\d+)-(.+)", base_name)
        if not match:
            self.report(
                {"ERROR"}, "Scene name format is incorrect. Expected 'SC##-<env_name>'."
            )
            return {"CANCELLED"}

        sc_id, scene_env_name = match.groups()
        sh_id = "SH001"  # Default for initial setup
        parent_col_name = f"+{base_name}+"

        sc_parent_col, created = get_or_create_collection(
            parent_col_name, master_collection, color_tag=COLLECTION_COLORS["SCENE"]
        )
        if not created:
            self.report(
                {"INFO"},
                f"Base collection '{parent_col_name}' already exists. Verifying sub-collections.",
            )

        # --- Sub-structures ---
        art_col, _ = get_or_create_collection(
            f"+ART-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["ART"]
        )
        get_or_create_collection(f"MODEL-{base_name}", art_col)
        shot_art_col, _ = get_or_create_collection(f"SHOT-ART-{base_name}", art_col)
        get_or_create_collection(f"MODEL-{sc_id}-{sh_id}", shot_art_col)

        ani_col, _ = get_or_create_collection(
            f"+ANI-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["ANI"]
        )
        get_or_create_collection(f"ACTOR-{base_name}", ani_col)
        get_or_create_collection(f"PROP-{base_name}", ani_col)
        shot_ani_col, _ = get_or_create_collection(f"SHOT-ANI-{base_name}", ani_col)

        vfx_col, _ = get_or_create_collection(
            f"+VFX-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["VFX"]
        )
        get_or_create_collection(f"VFX-{base_name}", vfx_col)
        shot_vfx_col, _ = get_or_create_collection(f"SHOT-VFX-{base_name}", vfx_col)
        get_or_create_collection(f"VFX-{sc_id}-{sh_id}", shot_vfx_col)

        # --- Link Environment & Location Collections ---
        linked_enviros = []
        for collection in bpy.data.collections:
            enviro_match = re.match(r"^\+ENV-(.+)\+$", collection.name)
            if enviro_match:
                enviro_name = enviro_match.group(1)
                if (
                    enviro_name in scene_env_name
                    and collection.name not in master_collection.children
                ):
                    master_collection.children.link(collection)
                    linked_enviros.append(collection.name)
        if linked_enviros:
            self.report(
                {"INFO"},
                f"Linked matching ENVIRO collections: {', '.join(linked_enviros)}",
            )

        location_collection = next(
            (c for c in bpy.data.collections if c.name.startswith("+LOC-")), None
        )
        if (
            location_collection
            and location_collection.name not in master_collection.children
        ):
            master_collection.children.link(location_collection)
            self.report({"INFO"}, f"Linked Location: '{location_collection.name}'.")

        self.report({"INFO"}, f"Verified SCENE structure for '{base_name}'.")
        return {"FINISHED"}


class SCENE_OT_verify_shot_collections(bpy.types.Operator):
    """Checks if a shot collection exists for every timeline marker."""

    bl_idname = "scene.verify_shot_collections"
    bl_label = "Verify Shot Collections"
    bl_description = "Checks if a CAM-SC-SH collection exists for each timeline marker"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name

        if not base_name.startswith("SC"):
            self.report({"ERROR"}, "This operator only works on a SCENE (SC##-).")
            return {"CANCELLED"}

        shot_ani_collection_name = f"SHOT-ANI-{base_name}"
        shot_ani_collection = bpy.data.collections.get(shot_ani_collection_name)

        if not shot_ani_collection:
            self.report(
                {"ERROR"},
                f"Collection '{shot_ani_collection_name}' not found. Please run 'Setup SCENE Collections' first.",
            )
            return {"CANCELLED"}

        markers = scene.timeline_markers
        if not markers:
            self.report({"INFO"}, "No timeline markers found to verify against.")
            return {"FINISHED"}

        missing_collections = []
        existing_shot_collections = set(shot_ani_collection.children.keys())

        for marker in markers:
            match = re.match(r"CAM-(SC\d+)-(SH\d+)$", marker.name, re.IGNORECASE)
            if match:
                sc_id, sh_id = match.groups()
                expected_collection_name = f"CAM-{sc_id.upper()}-{sh_id.upper()}"
                if expected_collection_name not in existing_shot_collections:
                    missing_collections.append(
                        f"'{expected_collection_name}' (for marker '{marker.name}')"
                    )

        if missing_collections:
            self.report(
                {"ERROR"},
                f"Verification failed. Missing collections in '{shot_ani_collection_name}': {', '.join(missing_collections)}",
            )
        else:
            self.report(
                {"INFO"},
                "Verification successful. All timeline markers have a corresponding shot collection.",
            )

        return {"FINISHED"}


# --- Animatic and Camera Operators ---
class SEQUENCER_OT_import_animatic_guides(bpy.types.Operator):
    """Scans a directory for scene folders, creates Blender scenes, and imports animatic 'guide' videos with sound."""

    bl_idname = "sequencer.import_animatic_guides"
    bl_label = "Import/Update Animatic Guides"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(
        name="Source Directory",
        description="Select the root directory containing scene folders (e.g., SC17-APOLLO_CRASH)",
        subtype="DIR_PATH",
    )

    def execute(self, context):
        root_dir = self.directory
        if not os.path.isdir(root_dir):
            self.report({"ERROR"}, "Invalid directory selected.")
            return {"CANCELLED"}

        vse_area = next((area for area in context.screen.areas if area.type == 'SEQUENCE_EDITOR'), None)
        if not vse_area:
            self.report({'ERROR'}, "Operation requires a Video Sequence Editor to be open in the workspace.")
            return {'CANCELLED'}

        try:
            scene_dirs = [
                d
                for d in os.listdir(root_dir)
                if os.path.isdir(os.path.join(root_dir, d))
                and d.upper().startswith("SC")
            ]
        except OSError as e:
            self.report({"ERROR"}, f"Could not read directory: {e}")
            return {"CANCELLED"}

        if not scene_dirs:
            self.report(
                {"WARNING"},
                f"No scene directories (e.g., 'SC17-...') found in '{root_dir}'",
            )
            return {"FINISHED"}

        for scene_name in scene_dirs:
            blender_scene = bpy.data.scenes.get(scene_name)
            if not blender_scene:
                blender_scene = bpy.data.scenes.new(name=scene_name)
                self.report({"INFO"}, f"Created new scene: '{scene_name}'")
            else:
                self.report({"INFO"}, f"Found existing scene: '{scene_name}'")

            if not blender_scene.sequence_editor:
                blender_scene.sequence_editor_create()
            seq_editor = blender_scene.sequence_editor

            scene_path = os.path.join(root_dir, scene_name)
            guide_files = []
            for dirpath, _, filenames in os.walk(scene_path):
                for f in filenames:
                    if "-guide-" in f.lower() and f.lower().endswith((".mp4", ".mov")):
                        guide_files.append(os.path.join(dirpath, f))

            if not guide_files:
                self.report(
                    {"WARNING"},
                    f"No guide files found for scene '{scene_name}'. Skipping.",
                )
                continue

            guide_files.sort()
            
            # --- NEW: Clean up old guide strips for this scene before importing ---
            strips_to_remove = []
            for s in seq_editor.sequences_all:
                path_to_check = None
                if s.type == 'MOVIE':
                    path_to_check = s.filepath
                elif s.type == 'SOUND':
                    path_to_check = s.sound.filepath

                if path_to_check and path_to_check.startswith(scene_path) and "-guide-" in os.path.basename(path_to_check).lower():
                    strips_to_remove.append(s)

            if strips_to_remove:
                log.info(f"Removing {len(strips_to_remove)} old guide strips from scene '{scene_name}'.")
                for strip in strips_to_remove:
                    if strip.name in seq_editor.sequences:
                         seq_editor.sequences.remove(strip)
            # --- END NEW CODE ---


            # --- Clean up old markers for this scene ---
            sc_match = re.match(r"^(SC\d+)", scene_name, re.IGNORECASE)
            if sc_match:
                current_sc_id = sc_match.group(1).upper()
                markers_to_remove = [
                    m
                    for m in blender_scene.timeline_markers
                    if m.name.startswith(f"CAM-{current_sc_id}-")
                ]

                if markers_to_remove:
                    log.info(
                        f"Removing {len(markers_to_remove)} old markers for {current_sc_id} before update."
                    )
                    for m in markers_to_remove:
                        blender_scene.timeline_markers.remove(m)

            # --- Import to new channels ---
            max_channel = 0
            if seq_editor.sequences_all:
                max_channel = max(s.channel for s in seq_editor.sequences_all)
            
            target_channel = max_channel + 1
            self.report(
                {"INFO"},
                f"Importing guides for '{scene_name}' to Video Channel {target_channel} and Sound Channel {target_channel + 1}.",
            )

            current_frame = 1
            for video_path in guide_files:
                filename = os.path.basename(video_path)

                with context.temp_override(window=context.window, area=vse_area, scene=blender_scene):
                    bpy.ops.sequencer.movie_strip_add(
                        filepath=video_path,
                        directory=os.path.dirname(video_path),
                        files=[{"name": filename}],
                        frame_start=current_frame,
                        channel=target_channel,
                        fit_method='FIT',
                        adjust_playback_rate=True
                    )

                new_video_strip = None
                for s in reversed(seq_editor.sequences_all):
                    if s.channel == target_channel and s.frame_start == current_frame:
                        new_video_strip = s
                        break
                
                if not new_video_strip:
                    self.report({"WARNING"}, f"Failed to import or find video strip from: {video_path}")
                    current_frame += 1 
                    continue

                # Create new markers
                match = re.search(r"(sc\d+).+?(sh\d+)", filename, re.IGNORECASE)
                if match:
                    sc_id, sh_id = match.groups()
                    marker_name = f"CAM-{sc_id.upper()}-{sh_id.upper()}"
                    blender_scene.timeline_markers.new(
                        name=marker_name, frame=current_frame
                    )
                    self.report(
                        {"INFO"},
                        f"Created marker '{marker_name}' at frame {current_frame}.",
                    )
                else:
                    self.report(
                        {"WARNING"},
                        f"Could not parse SC/SH from '{filename}'. Skipping marker creation.",
                    )

                current_frame += new_video_strip.frame_final_duration

            blender_scene.frame_end = current_frame - 1
            log.info(f"Set scene '{scene_name}' end frame to {blender_scene.frame_end}")

        self.report({"INFO"}, "Animatic import process finished.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class SCENE_OT_setup_cameras_from_markers(bpy.types.Operator):
    """Scans timeline markers, creates a collection for each camera shot, and appends the master camera rig."""

    bl_idname = "scene.setup_cameras_from_markers"
    bl_label = "Setup Cameras from Markers"
    bl_description = (
        "Creates and places cameras into collections based on timeline markers"
    )

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        markers = scene.timeline_markers
        preferences = context.preferences.addons[__name__].preferences
        camera_hero_blend_path = preferences.camera_hero_path

        log.info("--- Starting Camera Setup from Markers ---")

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
            msg = f"Camera hero file not found. Please set the path in Addon Preferences. Current: '{camera_hero_blend_path}'"
            log.error(msg)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        shot_ani_collection_name = f"SHOT-ANI-{base_name}"
        shot_ani_collection = bpy.data.collections.get(shot_ani_collection_name)
        if not shot_ani_collection:
            msg = f"Parent collection '{shot_ani_collection_name}' not found. Please run the main layout setup first."
            log.error(msg)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        processed_markers = 0
        camera_offset_counter = 0
        for marker in sorted(markers, key=lambda m: m.frame):
            match = re.match(r"CAM-(SC\d+)-(SH\d+)$", marker.name, re.IGNORECASE)
            if not match:
                log.info(
                    f"Skipping marker '{marker.name}' as it does not match 'CAM-SC##-SH###' format."
                )
                continue

            sc_id, sh_id = match.groups()
            sc_id_upper, sh_id_upper = sc_id.upper(), sh_id.upper()
            cam_collection_name = f"CAM-{sc_id_upper}-{sh_id_upper}"

            if cam_collection_name in shot_ani_collection.children:
                log.info(
                    f"Camera collection '{cam_collection_name}' already exists. Skipping."
                )
                continue

            try:
                with bpy.data.libraries.load(camera_hero_blend_path, link=False) as (
                    data_from,
                    data_to,
                ):
                    data_to.collections = [
                        c
                        for c in data_from.collections
                        if c == CAMERA_COLLECTION_TO_APPEND
                    ]

                if not data_to.collections:
                    msg = f"Could not find collection '{CAMERA_COLLECTION_TO_APPEND}' in '{camera_hero_blend_path}'."
                    log.error(msg)
                    self.report({"ERROR"}, msg)
                    continue

                appended_collection = data_to.collections[0]
                appended_collection.name = cam_collection_name

                shot_ani_collection.children.link(appended_collection)
                log.info(
                    f"Created and linked camera collection '{cam_collection_name}'."
                )

                appended_collection.color_tag = COLLECTION_COLORS["CAMERA"]

                # --- Rename Nested Collections and Objects ---
                for child_col in appended_collection.children:
                    if "cam_mesh" in child_col.name:
                        child_col.name = f"cam_mesh-{sc_id_upper}-{sh_id_upper}"
                        for cam in child_col.objects:
                            if cam.type == "CAMERA":
                                if "cam_flat" in cam.name:
                                    cam.name = f"CAM-{sc_id_upper}-{sh_id_upper}-FLAT"
                                elif "cam_fulldome" in cam.name:
                                    cam.name = (
                                        f"CAM-{sc_id_upper}-{sh_id_upper}-FULLDOME"
                                    )
                    elif "cam_rig" in child_col.name:
                        child_col.name = f"cam_rig-{sc_id_upper}-{sh_id_upper}"

                        # Find the specific rig object to move, rename it, and store a reference.
                        rig_object_to_move = None
                        for obj in child_col.objects:
                            if obj.type == "ARMATURE" and obj.name.startswith(
                                "+cam_rig"
                            ):
                                obj.name = f"+cam_rig-{sc_id_upper}-{sh_id_upper}"
                                rig_object_to_move = obj  # Store the object reference
                                log.info(f"Renamed armature to '{obj.name}'.")
                                break  # Found it, no need to continue looping

                        # Calculate the offset for this camera instance.
                        x_offset = camera_offset_counter * 2.0

                        # Apply the offset ONLY to the found rig object.
                        if rig_object_to_move and x_offset > 0:
                            rig_object_to_move.location.x += x_offset
                            log.info(
                                f"Moved '{rig_object_to_move.name}' by {x_offset} on the X-axis."
                            )

                    elif "cam_boneshapes" in child_col.name:
                        child_col.name = f"__cam_boneshapes-{sc_id_upper}-{sh_id_upper}"

                camera_offset_counter += 1

                if appended_collection.name in context.scene.collection.children:
                    context.scene.collection.children.unlink(appended_collection)

                log.info(
                    f"Successfully configured rig for '{appended_collection.name}'."
                )
                processed_markers += 1

            except Exception as e:
                msg = f"An error occurred while processing marker '{marker.name}': {e}"
                log.error(msg, exc_info=True)
                self.report({"ERROR"}, msg)
                if (
                    appended_collection
                    and appended_collection.name in bpy.data.collections
                ):
                    bpy.data.collections.remove(appended_collection)
                continue

        log.info(
            f"--- Camera Setup Finished. Processed {processed_markers} markers. ---"
        )
        self.report(
            {"INFO"},
            f"Camera setup complete. Processed {processed_markers} valid markers.",
        )
        return {"FINISHED"}


class SCENE_OT_bind_cameras_to_markers(bpy.types.Operator):
    """Binds all cameras of a specific type (FLAT or FULLDOME) to their corresponding timeline markers."""

    bl_idname = "scene.bind_cameras_to_markers"
    bl_label = "Bind Cameras to Markers"
    bl_description = "Finds all cameras of a given type and binds them to timeline markers with matching SC-SH names"
    bl_options = {"REGISTER", "UNDO"}

    camera_type: EnumProperty(
        name="Camera Type",
        items=[
            ("FLAT", "Flat", "Bind all FLAT cameras"),
            ("FULLDOME", "Fulldome", "Bind all FULLDOME cameras"),
        ],
        description="The type of camera to bind to markers",
    )

    def execute(self, context):
        scene = context.scene
        markers = scene.timeline_markers
        cameras = [obj for obj in scene.objects if obj.type == "CAMERA"]

        log.info(f"--- Starting Camera Binding for '{self.camera_type}' cameras ---")

        # --- NEW: Set render resolution based on camera type ---
        if self.camera_type == "FLAT":
            scene.render.resolution_x = 1920
            scene.render.resolution_y = 1080
            log.info("Set render resolution to 1920x1080 for FLAT cameras.")
            self.report({"INFO"}, "Set resolution to 1920x1080 (FLAT).")
        elif self.camera_type == "FULLDOME":
            scene.render.resolution_x = 2048
            scene.render.resolution_y = 2048
            log.info("Set render resolution to 2048x2048 for FULLDOME cameras.")
            self.report({"INFO"}, "Set resolution to 2048x2048 (FULLDOME).")
        # --- END NEW CODE ---

        if not markers:
            msg = "No timeline markers found in the scene."
            log.warning(msg)
            self.report({"WARNING"}, msg)
            return {"CANCELLED"}

        bound_count = 0
        unbound_count = 0

        # Create a dictionary for quick marker lookup
        marker_dict = {marker.name: marker for marker in markers}

        for cam in cameras:
            # Check if the camera name contains the type we're looking for
            if self.camera_type not in cam.name.upper():
                continue

            # Extract SC and SH from the camera name (e.g., CAM-SC01-SH001-FLAT)
            match = re.search(
                r"CAM-(SC\d+)-(SH\d+)-" + self.camera_type, cam.name, re.IGNORECASE
            )
            if not match:
                continue

            sc_id, sh_id = match.groups()
            marker_name = f"CAM-{sc_id.upper()}-{sh_id.upper()}"

            if marker_name in marker_dict:
                marker = marker_dict[marker_name]
                # This is the core action: bind the camera to the marker
                marker.camera = cam
                log.info(
                    f"Bound camera '{cam.name}' to marker '{marker.name}' at frame {marker.frame}."
                )
                bound_count += 1
            else:
                log.warning(
                    f"Could not bind camera '{cam.name}'. No matching marker found for '{marker_name}'."
                )
                unbound_count += 1

        if bound_count > 0:
            self.report(
                {"INFO"},
                f"Successfully bound {bound_count} {self.camera_type} camera(s).",
            )
        else:
            self.report(
                {"WARNING"},
                f"No matching {self.camera_type} cameras were found or could be bound.",
            )

        if unbound_count > 0:
            self.report(
                {"WARNING"},
                f"Could not find matching markers for {unbound_count} {self.camera_type} camera(s). Check naming conventions.",
            )

        log.info(
            f"--- Camera Binding Finished. Bound: {bound_count}, Unbound: {unbound_count} ---"
        )
        return {"FINISHED"}


# --- UI Panels and Menus ---


class SCENE_MT_bind_cameras_menu(bpy.types.Menu):
    """A reusable menu for the camera binding operators."""

    bl_label = "Bind Cameras"
    bl_idname = "SCENE_MT_bind_cameras_menu"

    def draw(self, context):
        layout = self.layout

        op_flat = layout.operator(
            SCENE_OT_bind_cameras_to_markers.bl_idname, text="All FLAT"
        )
        op_flat.camera_type = "FLAT"

        op_fulldome = layout.operator(
            SCENE_OT_bind_cameras_to_markers.bl_idname, text="All FULLDOME"
        )
        op_fulldome.camera_type = "FULLDOME"


class VIEW3D_PT_layout_suite_main_panel(bpy.types.Panel):
    """The main UI panel for the Layout Suite addon in the 3D View."""

    bl_label = "Layout Suite"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Layout Suite"  # This creates the tab in the N-panel

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        scene_name = scene.name

        # --- Scene Type Specific Tools ---
        # Draw UI elements based on the current scene's name prefix.

        if re.match(r"^LOC-", scene_name, re.IGNORECASE):
            box = layout.box()
            box.label(text="Location Tools", icon="WORLD_DATA")
            box.operator(SCENE_OT_create_location_structure.bl_idname)

        elif re.match(r"^ENV-", scene_name, re.IGNORECASE):
            box = layout.box()
            box.label(text="Environment Tools", icon="OUTLINER_OB_LIGHTPROBE")
            box.operator(SCENE_OT_create_enviro_structure.bl_idname)

        elif re.match(r"^SC\d+-", scene_name, re.IGNORECASE):
            # Main Scene Setup
            box = layout.box()
            box.label(text="Initial Scene Setup", icon="SCENE_DATA")
            box.operator(SCENE_OT_create_scene_structure.bl_idname)

            # Animatic and Timeline Tools
            box = layout.box()
            box.label(text="Animatic & Markers", icon="SEQUENCE")
            box.operator(
                SEQUENCER_OT_import_animatic_guides.bl_idname,
                text="Import/Update Guides",
                icon="FILE_FOLDER",
            )
            box.operator(SCENE_OT_verify_shot_collections.bl_idname, icon="CHECKMARK")

            # Camera Tools
            box = layout.box()
            box.label(text="Camera Management", icon="CAMERA_DATA")
            box.operator(
                SCENE_OT_setup_cameras_from_markers.bl_idname, icon="CAMERA_DATA"
            )
            box.separator()
            box.menu(SCENE_MT_bind_cameras_menu.bl_idname, icon="LINKED")

        else:
            layout.label(text="Scene name not recognized.")
            layout.label(text="Use LOC-, ENV-, or SC##- prefix.")


# --- Registration ---
classes = (
    LayoutCameraAddonPreferences,
    SCENE_OT_create_location_structure,
    SCENE_OT_create_enviro_structure,
    SCENE_OT_create_scene_structure,
    SCENE_OT_verify_shot_collections,
    SEQUENCER_OT_import_animatic_guides,
    SCENE_OT_setup_cameras_from_markers,
    SCENE_OT_bind_cameras_to_markers,
    SCENE_MT_bind_cameras_menu,
    VIEW3D_PT_layout_suite_main_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
