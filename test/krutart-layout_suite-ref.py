* we must ensure both provided blender addons work well in tandem
* let's make sure each addon works well with the other in tandem
* layout_suite.py:
bl_info = {
    "name": "Layout Suite",
    "author": "IORI, Krutart, Gemini",
    "version": (2, 8, 2),
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
    """Operator to build the LOCATION collection structure and link ENVIROs."""

    bl_idname = "scene.create_location_structure"
    bl_label = "Setup LOCATION Collections"
    bl_description = "Creates the collection structure for a LOCATION scene (LOC-) and links all ENVIRO collections"

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

        # --- Link all existing ENVIRO collections ---
        linked_enviros = []
        for collection in bpy.data.collections:
            # Check if it's an enviro parent collection
            if collection.name.startswith("+ENV-") and collection.name.endswith("+"):
                # Link if not already present in the scene's master collection
                if collection.name not in master_collection.children:
                    master_collection.children.link(collection)
                    linked_enviros.append(collection.name)

        if linked_enviros:
            self.report(
                {"INFO"},
                f"Linked existing ENVIRO collections: {', '.join(linked_enviros)}",
            )

        self.report({"INFO"}, f"Verified LOCATION structure for '{base_name}'.")
        return {"FINISHED"}


class SCENE_OT_create_enviro_structure(bpy.types.Operator):
    """Operator to build the ENVIRONMENT collection structure and link LOCATION."""

    bl_idname = "scene.create_enviro_structure"
    bl_label = "Setup ENVIRO Collections"
    bl_description = "Creates the collection structure for an ENVIRONMENT scene (ENV-) and links the LOCATION collection"

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
        
        # --- Link the root LOCATION collection ---
        location_collection = next(
            (c for c in bpy.data.collections if c.name.startswith("+LOC-")), None
        )
        if (
            location_collection
            and location_collection.name not in master_collection.children
        ):
            master_collection.children.link(location_collection)
            self.report({"INFO"}, f"Linked Location: '{location_collection.name}'.")

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


********************************************************************************************************************************************************************************************************************************************************************************************************************
********************************************************************************************************************************************************************************************************************************************************************************************************************
********************************************************************************************************************************************************************************************************************************************************************************************************************
* we must fix:
* currently only the first opened scene in the blend project works well for switching the shot collection exclusions
* currently all other shot collecitons not in the first opened scene are always disabled
* we must make sure all shot-specific collections except the current (playhead) shot are excluded from view layer
> MODEL-SC##-SH###
> CAM-SC##-SH###
> VFX-SC##-SH###
* we must ensure optimal playback framerate
* we must ensure robust automatic disabling of shot collections

* advanced_copy.py:
bl_info = {
    "name": "Advanced Copy",
    "author": "iori, Krutart, Gemini",
    "version": (1, 7, 0),
    "blender": (4, 5, 0),
    "location": "Outliner > Right-Click Menu, 3D View > Right-Click Menu",
    "description": "Provides specific hierarchy traversal copy/move functionalities with dynamic, high-performance, shot-based collection visibility.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import re
import logging
from bpy.props import StringProperty
from bpy.app.handlers import persistent

# --- Configure Logging ---
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- Shot Visibility Cache & Helpers ---
# Global cache for performance. Maps a frame number to a shot identifier (e.g., {101: "SC01-SH010"}).
# This prevents running expensive logic on every single frame, only on shot boundaries.
shot_switch_map = {}

def get_shot_identifier(name):
    """Extracts 'SC##-SH###' from a collection or marker name."""
    if not name: return None
    match = re.search(r"(SC\d+-SH\d+)", name)
    return match.group(1) if match else None

@persistent
def build_shot_cache(scene=None):
    """
    Scans scene markers to build a map of frames where shot visibility needs to change.
    This is called on file load and after certain operations to keep the cache fresh.
    """
    global shot_switch_map
    shot_switch_map.clear()

    # Use bpy.context.scene if no scene is passed (for the handler call)
    active_scene = bpy.context.scene
    if not active_scene or not hasattr(active_scene, 'timeline_markers'):
        log.warning("build_shot_cache: Could not access a scene with timeline markers.")
        return

    # Pattern to find markers that define a shot's start (e.g., CAM-SC01-SH010)
    marker_pattern = re.compile(r"CAM-SC\d+-SH\d+")
    shot_markers = [m for m in active_scene.timeline_markers if marker_pattern.match(m.name)]

    for marker in shot_markers:
        shot_id = get_shot_identifier(marker.name)
        if shot_id:
            shot_switch_map[marker.frame] = shot_id
    
    log.info(f"Shot cache rebuilt. Found {len(shot_switch_map)} switch frames: {shot_switch_map}")

def get_all_shot_collections():
    """Scans the blend file for all collections matching the shot naming convention."""
    pattern = re.compile(r"^(MODEL|CAM|VFX)-SC\d+-SH\d+$")
    return [c for c in bpy.data.collections if pattern.match(c.name)]


# --- Dynamic Collection Visibility Handler ---

@persistent
def on_frame_change_update_visibility(scene, depsgraph=None):
    """
    Handler that runs on frame change. Uses a pre-built cache to determine the
    active shot and updates collection visibility ONLY when the shot context changes.
    This is highly performant and correctly handles timeline scrubbing.
    """
    # If the cache is empty (e.g., new file), try to build it once.
    if not shot_switch_map:
        build_shot_cache(scene)
        if not shot_switch_map: return # No shot markers found.

    current_frame = scene.frame_current
    view_layer = bpy.context.view_layer

    # Determine the current shot based on the last switch frame the playhead has passed.
    active_shot_id = None
    relevant_frames = [f for f in shot_switch_map.keys() if f <= current_frame]
    if relevant_frames:
        latest_switch_frame = max(relevant_frames)
        active_shot_id = shot_switch_map[latest_switch_frame]

    # Use a window_manager property to track the last active shot.
    # The expensive collection-looping logic below only runs if the shot ID actually changes.
    last_active_shot = getattr(bpy.context.window_manager, "active_shot_id", None)
    
    if active_shot_id != last_active_shot:
        bpy.context.window_manager.active_shot_id = active_shot_id
        log.info(f"Frame {current_frame}: Shot changed to '{active_shot_id}'. Updating collection visibility.")

        all_shot_colls = get_all_shot_collections()
        for coll in all_shot_colls:
            coll_shot_id = get_shot_identifier(coll.name)
            # Exclude the collection if it's a shot collection but not the active one.
            is_active = (coll_shot_id is not None and coll_shot_id == active_shot_id)
            set_collection_exclude(view_layer, coll.name, not is_active)


# --- General Helper Functions ---

def get_active_datablock(context):
    """Determines the active datablock (object or collection) from the context."""
    if context.area.type == 'OUTLINER':
        selected = context.selected_ids
        if selected:
            active_id = selected[-1]
            if isinstance(active_id, bpy.types.Collection):
                return active_id, 'COLLECTION'
            elif isinstance(active_id, bpy.types.Object):
                return active_id, 'OBJECT'
    
    active_obj = context.active_object
    if active_obj:
        return active_obj, 'OBJECT'
        
    return None, None

def copy_collection_hierarchy(original_coll, target_parent_coll, name_suffix=""):
    """Recursively copies a collection and its contents, then remaps object relationships."""
    object_map = {}

    def _recursive_copy_and_map(source_coll, target_parent, suffix, obj_map):
        new_coll = bpy.data.collections.new(f"{source_coll.name}{suffix}")
        target_parent.children.link(new_coll)
        new_coll.color_tag = source_coll.color_tag

        for obj in source_coll.objects:
            if obj not in obj_map:
                new_obj = obj.copy()
                if obj.data:
                    new_obj.data = obj.data.copy()
                new_obj.name = f"{obj.name}{suffix}"
                obj_map[obj] = new_obj

        for obj in source_coll.objects:
            new_obj = obj_map.get(obj)
            if new_obj and new_obj.name not in new_coll.objects:
                new_coll.objects.link(new_obj)

        for child in source_coll.children:
            _recursive_copy_and_map(child, new_coll, suffix, obj_map)

        return new_coll

    def _remap_relationships(obj_map):
        log.info(f"Remapping relationships for {len(obj_map)} copied objects...")
        for orig_obj, new_obj in obj_map.items():
            if orig_obj.parent and orig_obj.parent in obj_map:
                new_obj.parent = obj_map[orig_obj.parent]
                new_obj.parent_type = orig_obj.parent_type
                if orig_obj.parent_type == 'BONE':
                    new_obj.parent_bone = orig_obj.parent_bone

            for constraint in new_obj.constraints:
                if hasattr(constraint, 'target') and constraint.target and constraint.target in obj_map:
                    constraint.target = obj_map[constraint.target]
                
                if hasattr(constraint, 'targets'):
                    for subtarget in constraint.targets:
                        if subtarget.target and subtarget.target in obj_map:
                            subtarget.target = obj_map[subtarget.target]

            for modifier in new_obj.modifiers:
                mod_obj_props = ['object', 'target', 'source_object', 'camera', 'curve']
                for prop in mod_obj_props:
                    if hasattr(modifier, prop):
                        mod_obj = getattr(modifier, prop)
                        if mod_obj and mod_obj in obj_map:
                            setattr(modifier, prop, obj_map[mod_obj])

    top_level_new_coll = _recursive_copy_and_map(original_coll, target_parent_coll, name_suffix, object_map)
    _remap_relationships(object_map)
    
    log.info("Hierarchy copy and remapping complete.")
    return top_level_new_coll

def get_project_scenes():
    """Retrieves all scenes matching the 'SC##-' naming convention."""
    pattern = re.compile(r"^SC\d+-.*")
    return sorted([s for s in bpy.data.scenes if pattern.match(s.name)], key=lambda s: s.name)

def find_layer_collection_by_name(layer_collection_root, name_to_find):
    """Recursively finds the LayerCollection corresponding to a given Collection name."""
    if layer_collection_root.name == name_to_find:
        return layer_collection_root
    for child in layer_collection_root.children:
        found = find_layer_collection_by_name(child, name_to_find)
        if found:
            return found
    return None

def set_collection_exclude(view_layer, collection_name, exclude_status):
    """Safely finds a collection by name in the view layer and sets its exclude status."""
    if not collection_name or not bpy.data.collections.get(collection_name): return

    layer_coll = find_layer_collection_by_name(view_layer.layer_collection, collection_name)
    if layer_coll and layer_coll.exclude != exclude_status:
        layer_coll.exclude = exclude_status

def get_source_collection(item):
    """Finds the collection an object or collection belongs to."""
    if isinstance(item, bpy.types.Object):
        if item.users_collection: return item.users_collection[0]
    elif isinstance(item, bpy.types.Collection):
        for coll in bpy.data.collections:
            if item.name in coll.children: return coll
    return bpy.context.scene.collection

def is_in_shot_build_collection(item):
    """Recursively checks if an item is inside a collection whose name starts with '+SC'."""
    parent_map = {child: parent for parent in bpy.data.collections for child in parent.children}
    current_coll = get_source_collection(item)
    while current_coll:
        if current_coll.name.startswith("+SC"): return True
        current_coll = parent_map.get(current_coll)
    return False


# --- Main Operator Classes ---

class ADVCOPY_OT_copy_to_shot(bpy.types.Operator):
    """Copies the datablock to a specified shot collection."""
    bl_idname = "advanced_copy.copy_to_shot"
    bl_label = "Copy to Shot"
    bl_options = {'REGISTER', 'UNDO'}

    target_shot_collection: StringProperty()

    def execute(self, context):
        datablock, datablock_type = get_active_datablock(context)
        if not datablock:
            self.report({'ERROR'}, "No active Object or Collection found.")
            return {'CANCELLED'}

        target_coll = bpy.data.collections.get(self.target_shot_collection)
        if not target_coll:
            self.report({'ERROR'}, f"Target shot collection '{self.target_shot_collection}' not found.")
            return {'CANCELLED'}

        log.info(f"Copying '{datablock.name}' ({datablock_type}) to '{target_coll.name}'.")
        
        shot_id = get_shot_identifier(target_coll.name)
        name_suffix = f"-{shot_id}" if shot_id else "-copy"

        new_datablock = None
        if datablock_type == 'OBJECT':
            new_datablock = datablock.copy()
            if datablock.data: new_datablock.data = datablock.data.copy()
            target_coll.objects.link(new_datablock)
            new_datablock.name = f"{datablock.name}{name_suffix}"
        elif datablock_type == 'COLLECTION':
            new_datablock = copy_collection_hierarchy(datablock, target_coll, name_suffix)

        if not new_datablock:
            self.report({'ERROR'}, "Failed to create a copy.")
            return {'CANCELLED'}

        # Visibility is handled globally by the on_frame_change_update_visibility handler.
        # Force an immediate update for the current frame to reflect the change.
        on_frame_change_update_visibility(context.scene)
        
        self.report({'INFO'}, f"Copied '{datablock.name}' to '{new_datablock.name}' in '{target_coll.name}'.")
        return {'FINISHED'}

class ADVCOPY_OT_move_to_all_shots(bpy.types.Operator):
    """Moves the selected item to all relevant shot collections, then removes the original."""
    bl_idname = "advanced_copy.move_to_all_shots"
    bl_label = "Move to All Shots"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, datablock_type = get_active_datablock(context)
        if not datablock:
            self.report({'ERROR'}, "No active Object or Collection found.")
            return {'CANCELLED'}
        
        datablock_name = datablock.name
        source_collection = get_source_collection(datablock)
        if not source_collection:
            self.report({'ERROR'}, "Could not determine the source collection.")
            return {'CANCELLED'}
        
        prefix = "MODEL" if "MODEL" in source_collection.name else "VFX"
        shot_pattern = re.compile(rf"^{prefix}-SC\d+-SH\d+$")
        shot_collections = sorted([c for c in bpy.data.collections if shot_pattern.match(c.name)], key=lambda c: c.name)

        if not shot_collections:
            self.report({'WARNING'}, f"No '{prefix}' shot collections found.")
            return {'CANCELLED'}
        
        copied_count = 0
        
        for target_coll in shot_collections:
            shot_id = get_shot_identifier(target_coll.name)
            name_suffix = f"-{shot_id}" if shot_id else "-moved"

            new_datablock = None
            if datablock_type == 'OBJECT':
                new_datablock = datablock.copy()
                if datablock.data: new_datablock.data = datablock.data.copy()
                new_datablock.name = f"{datablock_name}{name_suffix}"
                target_coll.objects.link(new_datablock)
            elif datablock_type == 'COLLECTION':
                new_datablock = copy_collection_hierarchy(datablock, target_coll, name_suffix)
            
            if not new_datablock: continue
            
            copied_count += 1
            
        if copied_count > 0:
            log.info(f"Removing original datablock '{datablock_name}'")
            if datablock_type == 'OBJECT':
                bpy.data.objects.remove(datablock, do_unlink=True)
            elif datablock_type == 'COLLECTION':
                bpy.data.collections.remove(datablock)

            self.report({'INFO'}, f"Moved '{datablock_name}' to {copied_count} shot collection(s).")
            # Force an immediate update for the current frame.
            on_frame_change_update_visibility(context.scene)
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "Move operation did not copy to any shots.")
            return {'CANCELLED'}

class ADVCOPY_OT_move_to_all_scenes(bpy.types.Operator):
    """Copies an item from an ENV collection to all SCENE collections with a matching environment name, then removes the original."""
    bl_idname = "advanced_copy.move_to_all_scenes"
    bl_label = "Move to All Matching Scenes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, datablock_type = get_active_datablock(context)
        if not datablock:
            self.report({'ERROR'}, "Operation requires an active Object or Collection.")
            return {'CANCELLED'}

        source_collection = get_source_collection(datablock)
        if not source_collection or not (source_collection.name.startswith("MODEL-ENV") or source_collection.name.startswith("VFX-ENV")):
            self.report({'ERROR'}, "Selected item must be in a 'MODEL-ENV...' or 'VFX-ENV...' collection.")
            return {'CANCELLED'}
        
        enviro_name_match = re.search(r"ENV-(.+)", source_collection.name)
        if not enviro_name_match:
            self.report({'ERROR'}, f"Could not extract environment name from '{source_collection.name}'.")
            return {'CANCELLED'}
        enviro_name = enviro_name_match.group(1)
        
        prefix = "MODEL" if source_collection.name.startswith("MODEL") else "VFX"
        all_scenes = get_project_scenes()
        matching_scenes = [scene for scene in all_scenes if enviro_name in scene.name]
        
        if not matching_scenes:
            self.report({'WARNING'}, f"No scenes found with '{enviro_name}' in their name.")
            return {'CANCELLED'}
        
        copied_count = 0
        for scene in matching_scenes:
            final_target_coll = None
            base_scene_coll = scene.collection.children.get(f"+{scene.name}+")
            if base_scene_coll:
                parent_prefix = "ART" if prefix == "MODEL" else "VFX"
                parent_coll = base_scene_coll.children.get(f"+{parent_prefix}-{scene.name}+")
                if parent_coll:
                    final_target_coll = parent_coll.children.get(f"{prefix}-{scene.name}")

            if final_target_coll:
                scene_suffix = scene.name.split('-')[0]
                name_suffix = f"-{scene_suffix}"
                if datablock_type == 'OBJECT':
                    new_obj = datablock.copy()
                    if datablock.data:
                        new_obj.data = datablock.data.copy()
                    new_obj.name = f"{datablock.name}{name_suffix}"
                    final_target_coll.objects.link(new_obj)
                elif datablock_type == 'COLLECTION':
                    copy_collection_hierarchy(datablock, final_target_coll, name_suffix)
                copied_count += 1
            else:
                log.warning(f"Could not find target collection for '{prefix}' in scene '{scene.name}'.")

        if copied_count > 0:
            if datablock_type == 'OBJECT':
                if datablock.name in source_collection.objects:
                    source_collection.objects.unlink(datablock)
            elif datablock_type == 'COLLECTION':
                if datablock.name in source_collection.children:
                    source_collection.children.unlink(datablock)
            self.report({'INFO'}, f"Moved '{datablock.name}' to {copied_count} matching scene(s).")
        else:
            self.report({'ERROR'}, "Could not find any valid target collections in matching scenes.")
        return {'FINISHED'}

class ADVCOPY_OT_copy_to_all_enviros(bpy.types.Operator):
    """Copies an item from a LOC collection into each ENV collection, creating a unique item for each, and removes the original."""
    bl_idname = "advanced_copy.copy_to_all_enviros"
    bl_label = "-> to each ENV"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, datablock_type = get_active_datablock(context)
        if not datablock:
            self.report({'ERROR'}, "Operation requires an active Object or Collection.")
            return {'CANCELLED'}

        source_collection = get_source_collection(datablock)
        if not source_collection or not (source_collection.name.startswith("MODEL-LOC") or source_collection.name.startswith("VFX-LOC")):
            self.report({'ERROR'}, "Selected item must be in a 'MODEL-LOC...' or 'VFX-LOC...' collection.")
            return {'CANCELLED'}
        
        prefix = "MODEL" if source_collection.name.startswith("MODEL") else "VFX"
        all_env_parent_collections = [c for c in bpy.data.collections if c.name.startswith("+ENV-")]
        if not all_env_parent_collections:
            self.report({'WARNING'}, "No parent '+ENV-...' collections found to copy to.")
            return {'CANCELLED'}

        copied_count = 0
        for env_parent_coll in all_env_parent_collections:
            base_name = env_parent_coll.name.strip('+')
            target_sub_coll_name = f"{prefix}-{base_name}"
            target_sub_coll = env_parent_coll.children.get(target_sub_coll_name)
            
            if target_sub_coll:
                name_suffix = ""
                env_name_suffix_match = re.search(r"ENV-(.+)", base_name)
                if env_name_suffix_match:
                    name_suffix = f"-{env_name_suffix_match.group(1)}"

                if datablock_type == 'OBJECT':
                    new_obj = datablock.copy()
                    if datablock.data:
                        new_obj.data = datablock.data.copy()
                    new_obj.name = f"{datablock.name}{name_suffix}"
                    target_sub_coll.objects.link(new_obj)
                elif datablock_type == 'COLLECTION':
                    copy_collection_hierarchy(datablock, target_sub_coll, name_suffix)
                copied_count += 1
            else:
                log.warning(f"Could not find sub-collection '{target_sub_coll_name}' in '{env_parent_coll.name}'")

        if copied_count > 0:
            self.report({'INFO'}, f"Copied '{datablock.name}' to {copied_count} environment collections.")
            if datablock_type == 'OBJECT':
                if datablock.name in source_collection.objects:
                    source_collection.objects.unlink(datablock)
            elif datablock_type == 'COLLECTION':
                if datablock.name in source_collection.children:
                    source_collection.children.unlink(datablock)
        else:
            self.report({'ERROR'}, "Found ENV collections, but no matching sub-collections.")
        return {'FINISHED'}


# --- Dynamic Menus ---

class ADVCOPY_MT_copy_to_shot_menu(bpy.types.Menu):
    """Dynamically lists available shot collections from the current scene for copying."""
    bl_idname = "ADVCOPY_MT_copy_to_shot_menu"
    bl_label = "Copy to Shot (Current Scene)"

    def draw(self, context):
        layout = self.layout
        datablock, _ = get_active_datablock(context)
        if not datablock: return

        source_collection = get_source_collection(datablock)
        if not source_collection: return

        # --- MODIFICATION START ---
        # Filter the shot list to only include shots from the current scene.
        # 1. Get the current scene's prefix (e.g., "SC01") from "SC01-..."
        current_scene = context.scene
        scene_match = re.match(r"^(SC\d+)", current_scene.name)

        if not scene_match:
            layout.label(text="Scene must be named like 'SC##-...'")
            return
        
        current_scene_prefix = scene_match.group(1)
        # --- MODIFICATION END ---
        
        prefix = "MODEL" if "MODEL" in source_collection.name else "VFX"
        shot_pattern = re.compile(rf"^{prefix}-SC\d+-SH\d+$")
        
        # 2. Filter the collections to match the scene prefix.
        shot_collections = sorted(
            [
                c for c in bpy.data.collections 
                if shot_pattern.match(c.name) and c.name.startswith(f"{prefix}-{current_scene_prefix}")
            ], 
            key=lambda c: c.name
        )

        if not shot_collections:
            layout.label(text=f"No '{prefix}' shots found for {current_scene_prefix}")
            return

        for coll in shot_collections:
            op = layout.operator(ADVCOPY_OT_copy_to_shot.bl_idname, text=coll.name)
            op.target_shot_collection = coll.name


# --- UI Integration ---

def add_context_menus(self, context):
    """Generic function to draw the menu items based on the current context."""
    datablock, _ = get_active_datablock(context)
    if not datablock: return
    
    layout = self.layout
    layout.separator()

    if is_in_shot_build_collection(datablock):
        layout.menu(ADVCOPY_MT_copy_to_shot_menu.bl_idname, icon='COPYDOWN')
        layout.operator(ADVCOPY_OT_move_to_all_shots.bl_idname, icon='GHOST_ENABLED')

    source_collection = get_source_collection(datablock)
    if source_collection:
        if source_collection.name.startswith(("MODEL-ENV", "VFX-ENV")):
            layout.operator(ADVCOPY_OT_move_to_all_scenes.bl_idname, icon='SCENE_DATA')
        if source_collection.name.startswith(("MODEL-LOC", "VFX-LOC")):
            layout.operator(ADVCOPY_OT_copy_to_all_enviros.bl_idname, icon='CON_TRANSLIKE')
    layout.separator()

# --- Registration ---
classes = (
    ADVCOPY_OT_copy_to_shot,
    ADVCOPY_OT_move_to_all_shots,
    ADVCOPY_OT_move_to_all_scenes,
    ADVCOPY_OT_copy_to_all_enviros,
    ADVCOPY_MT_copy_to_shot_menu,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # Property to store the last known active shot ID for performance
    bpy.types.WindowManager.active_shot_id = StringProperty(
        name="Active Shot ID",
        description="Internal property to track the current shot for visibility updates."
    )
    
    # Add handlers. The cache will be built on file load or the first frame change.
    if on_frame_change_update_visibility not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(on_frame_change_update_visibility)
    if build_shot_cache not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(build_shot_cache)

    bpy.types.OUTLINER_MT_collection.append(add_context_menus)
    bpy.types.OUTLINER_MT_object.append(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.append(add_context_menus)

def unregister():
    # Remove handlers
    if on_frame_change_update_visibility in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(on_frame_change_update_visibility)
    if build_shot_cache in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(build_shot_cache)

    try:
        del bpy.types.WindowManager.active_shot_id
    except (AttributeError, TypeError):
        pass

    bpy.types.OUTLINER_MT_collection.remove(add_context_menus)
    bpy.types.OUTLINER_MT_object.remove(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.remove(add_context_menus)
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

