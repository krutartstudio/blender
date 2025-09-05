bl_info = {
    "name": "Layout Suite",
    "author": "IORI, Gemini, Krutart",
    "version": (2, 8, 1),
    "blender": (4, 2, 0),
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
# Allows users to set paths for the camera rig file for different OS.
class LayoutCameraAddonPreferences(AddonPreferences):
    bl_idname = __name__

    camera_hero_path_windows: StringProperty(
        name="Windows Camera Hero File",
        description="Path to the master camera rig .blend file for Windows (Priority)",
        subtype="FILE_PATH",
        default=r"S:\3212-PREPRODUCTION\LIBRARY\LIBRARY-HERO\RIG-HERO\CAMERA-HERO\3212_camera-hero.blend",
    )
    camera_hero_path_linux: StringProperty(
        name="Linux Camera Hero File",
        description="Path to the master camera rig .blend file for Linux",
        subtype="FILE_PATH",
        default="/run/user/1000/gvfs/afp-volume:host=172.16.20.2,user=fred,volume=VELKE_PROJEKTY/3212-PREPRODUCTION/LIBRARY/LIBRARY-HERO/RIG-HERO/CAMERA-HERO/3212_camera-hero.blend",
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Camera Hero File Paths:")
        layout.prop(self, "camera_hero_path_windows")
        layout.prop(self, "camera_hero_path_linux")


# --- Constants ---
# Color tags for collection organization
COLLECTION_COLORS = {
    "LOCATION": "COLOR_08",
    "ENVIRO": "COLOR_02",
    "SCENE": "COLOR_03",
    "ART": "COLOR_05",
    "ANI": "COLOR_01",
    "VFX": "COLOR_04",
    "CAMERA": "COLOR_06",
}

# Name of the camera collection to append from the hero file
CAMERA_COLLECTION_TO_APPEND = "+CAMERA+"


# --- Helper Functions ---

def find_view_collections_by_substring_in_collection(layer_collection, substring):
    """
    Recursively finds all view layer collections whose names contain a specific substring,
    starting the search from a given layer_collection.
    This is used to target collections for hiding/excluding without needing their exact names.
    """
    matching_collections = []
    # Check if the current collection's name matches
    if substring in layer_collection.name:
        matching_collections.append(layer_collection)
    # Recursively check all children
    for child in layer_collection.children:
        matching_collections.extend(
            find_view_collections_by_substring_in_collection(child, substring)
        )
    return matching_collections


def hide_collections_in_view_layer(substring, hide=True):
    """
    Finds and hides/unhides (by setting the 'exclude' property) all collections
    in the active view layer that contain a given substring in their name.
    """
    log.info(f"Attempting to set exclude={hide} for collections containing '{substring}'.")
    view_layer_collections = find_view_collections_by_substring_in_collection(
        bpy.context.view_layer.layer_collection, substring
    )

    if not view_layer_collections:
        log.warning(f"No view layer collections found with substring: '{substring}'.")
        return

    hidden_count = 0
    for col in view_layer_collections:
        if col.exclude != hide:
            col.exclude = hide
            hidden_count += 1
    log.info(f"Set exclude={hide} for {hidden_count} collection(s) containing '{substring}'.")


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
        # Create the parent for shot-specific art collections, but not the shots themselves.
        get_or_create_collection(f"SHOT-ART-{base_name}", art_col)

        ani_col, _ = get_or_create_collection(
            f"+ANI-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["ANI"]
        )
        get_or_create_collection(f"ACTOR-{base_name}", ani_col)
        get_or_create_collection(f"PROP-{base_name}", ani_col)
        get_or_create_collection(f"SHOT-ANI-{base_name}", ani_col)

        vfx_col, _ = get_or_create_collection(
            f"+VFX-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["VFX"]
        )
        get_or_create_collection(f"VFX-{base_name}", vfx_col)
        # Create the parent for shot-specific vfx collections, but not the shots themselves.
        get_or_create_collection(f"SHOT-VFX-{base_name}", vfx_col)

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
    """Scans a selected scene directory, creates a Blender scene if needed, and imports/updates the animatic 'guide' videos with sound."""

    bl_idname = "sequencer.import_animatic_guides"
    bl_label = "Import/Update Animatic Guides"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(
        name="Scene Directory",
        description="Select the scene directory (e.g., SC17-DARKPOINT) containing the guide files",
        subtype="DIR_PATH",
    )

    def execute(self, context):
        scene_path = self.directory
        if not os.path.isdir(scene_path):
            self.report({"ERROR"}, "Invalid directory selected.")
            return {"CANCELLED"}

        # Get scene name from the selected directory path and ensure it's valid
        scene_name = os.path.basename(os.path.normpath(scene_path))
        if not scene_name.upper().startswith("SC"):
            self.report(
                {"ERROR"},
                f"Directory name '{scene_name}' does not start with 'SC'. Please select a valid scene directory.",
            )
            return {"CANCELLED"}

        vse_area = next((area for area in context.screen.areas if area.type == 'SEQUENCE_EDITOR'), None)
        if not vse_area:
            self.report({'ERROR'}, "Operation requires a Video Sequence Editor to be open in the workspace.")
            return {'CANCELLED'}

        # Find all guide files within the selected scene directory
        guide_files = []
        try:
            for dirpath, _, filenames in os.walk(scene_path):
                for f in filenames:
                    if "-guide-" in f.lower() and f.lower().endswith((".mp4", ".mov")):
                        guide_files.append(os.path.join(dirpath, f))
        except OSError as e:
            self.report({"ERROR"}, f"Could not read directory contents: {e}")
            return {"CANCELLED"}

        if not guide_files:
            self.report(
                {"WARNING"},
                f"No guide files found in '{scene_path}'. Nothing to import.",
            )
            return {"FINISHED"}

        # --- Scene Creation and Setup ---
        blender_scene = bpy.data.scenes.get(scene_name)
        if not blender_scene:
            blender_scene = bpy.data.scenes.new(name=scene_name)
            self.report({"INFO"}, f"Created new scene: '{scene_name}'")
            try:
                # Use a context override to run the operator on the new scene.
                with context.temp_override(scene=blender_scene):
                    bpy.ops.scene.create_scene_structure()
                self.report({"INFO"}, f"Automatically ran 'Setup SCENE Collections' for '{scene_name}'.")
            except Exception as e:
                self.report({'ERROR'}, f"Could not auto-run scene setup for '{scene_name}': {e}. Please run it manually.")
        else:
            self.report({"INFO"}, f"Found existing scene: '{scene_name}'")

        if not blender_scene.sequence_editor:
            blender_scene.sequence_editor_create()
        seq_editor = blender_scene.sequence_editor

        guide_files.sort()
        
        # --- Clean up old guide strips for this scene before importing ---
        strips_to_remove = []
        for s in seq_editor.sequences_all:
            path_to_check = None
            if s.type == 'MOVIE':
                path_to_check = s.filepath
            elif s.type == 'SOUND':
                path_to_check = s.sound.filepath

            # Check if the strip's path is inside the selected scene_path and is a guide file
            if path_to_check and os.path.normpath(path_to_check).startswith(os.path.normpath(scene_path)) and "-guide-" in os.path.basename(path_to_check).lower():
                strips_to_remove.append(s)

        if strips_to_remove:
            log.info(f"Removing {len(strips_to_remove)} old guide strips from scene '{scene_name}'.")
            for strip in strips_to_remove:
                if strip.name in seq_editor.sequences:
                    seq_editor.sequences.remove(strip)

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

        # --- Add an END marker ---
        # Remove any existing 'END' marker to ensure a clean update.
        end_marker = next((m for m in blender_scene.timeline_markers if m.name == "END"), None)
        if end_marker:
            blender_scene.timeline_markers.remove(end_marker)

        # Add the new END marker at the frame immediately after the last clip.
        blender_scene.timeline_markers.new(name="END", frame=current_frame)
        log.info(f"Created 'END' marker at frame {current_frame}.")

        self.report({"INFO"}, f"Animatic import for '{scene_name}' finished.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class SCENE_OT_setup_cameras_from_markers(bpy.types.Operator):
    """Scans timeline markers, creates shot collections for ANI, ART, and VFX, and appends the master camera rig."""

    bl_idname = "scene.setup_cameras_from_markers"
    bl_label = "setup shots"
    bl_description = (
        "Creates and places cameras and shot collections based on timeline markers"
    )

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        markers = scene.timeline_markers
        preferences = context.preferences.addons[__name__].preferences
        
        # --- Determine the correct camera hero path based on OS ---
        win_path = preferences.camera_hero_path_windows
        linux_path = preferences.camera_hero_path_linux

        camera_hero_blend_path = None
        if win_path and os.path.exists(win_path):
            camera_hero_blend_path = win_path
            log.info(f"Using Windows camera hero path: {win_path}")
        elif linux_path and os.path.exists(linux_path):
            camera_hero_blend_path = linux_path
            log.info(f"Using Linux camera hero path: {linux_path}")
        # --- End path determination ---

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

        if not camera_hero_blend_path:
            msg = (f"Camera hero file not found. Please check paths in Addon Preferences.\n"
                   f"Windows (checked): '{win_path}'\n"
                   f"Linux (checked): '{linux_path}'")
            log.error(msg)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}
            
        # --- Get all parent SHOT collections ---
        shot_ani_collection_name = f"SHOT-ANI-{base_name}"
        shot_art_collection_name = f"SHOT-ART-{base_name}"
        shot_vfx_collection_name = f"SHOT-VFX-{base_name}"

        shot_ani_collection = bpy.data.collections.get(shot_ani_collection_name)
        shot_art_collection = bpy.data.collections.get(shot_art_collection_name)
        shot_vfx_collection = bpy.data.collections.get(shot_vfx_collection_name)

        # Verify all parent collections exist before continuing
        if not all([shot_ani_collection, shot_art_collection, shot_vfx_collection]):
            missing = []
            if not shot_ani_collection: missing.append(f"'{shot_ani_collection_name}'")
            if not shot_art_collection: missing.append(f"'{shot_art_collection_name}'")
            if not shot_vfx_collection: missing.append(f"'{shot_vfx_collection_name}'")
            msg = f"Parent collection(s) not found: {', '.join(missing)}. Please run the main 'Setup SCENE Collections' first."
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
            
            # --- Define all shot collection names ---
            cam_collection_name = f"CAM-{sc_id_upper}-{sh_id_upper}"
            art_shot_col_name = f"MODEL-{sc_id_upper}-{sh_id_upper}"
            vfx_shot_col_name = f"VFX-{sc_id_upper}-{sh_id_upper}"

            # If the primary camera collection exists, assume all are created and skip.
            if cam_collection_name in shot_ani_collection.children:
                log.info(
                    f"Shot collections for '{cam_collection_name}' already exist. Skipping."
                )
                continue

            # --- Create ART and VFX shot collections ---
            get_or_create_collection(art_shot_col_name, shot_art_collection)
            log.info(f"Created ART shot collection '{art_shot_col_name}'.")
            get_or_create_collection(vfx_shot_col_name, shot_vfx_collection)
            log.info(f"Created VFX shot collection '{vfx_shot_col_name}'.")

            # --- Create Camera (ANI) shot collection and append rig ---
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
                        # Rename without the '__' prefix. Hiding is handled globally later.
                        child_col.name = f"cam_boneshapes-{sc_id_upper}-{sh_id_upper}"
                        log.info(f"Renamed bone shape collection to '{child_col.name}'.")


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
                    'appended_collection' in locals()
                    and appended_collection
                    and appended_collection.name in bpy.data.collections
                ):
                    bpy.data.collections.remove(appended_collection)
                continue

        # --- Hide all bone shape collections after processing all markers ---
        # This is more efficient than calling it inside the loop for every camera.
        hide_collections_in_view_layer("cam_boneshapes", hide=True)

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

        # --- Set render resolution based on camera type ---
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
        # --- END ---

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
