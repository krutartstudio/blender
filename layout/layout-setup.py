"""
--- NAMING CONVENTIONS ---

LOCATION =>
  - Scene Name: LOC-<loc_name>
  - Example: "LOC-MOON_D"

ENVIRO =>
  - Scene Name: ENV-<env_name>
  - Example: "ENV-APOLLO_HILL"

SCENE =>
  - Scene Name: SC<id>-<env_name>
  - Example: "SC17-APOLLO_CRASH"

SC<id> = SC##
SH<id> = SH###

--- COLLECTION STRUCTURE ---

+LOC-<loc_name>+ (Blue)
  LOC-<loc_name>-TERRAIN
  LOC-<loc_name>-MODEL
  LOC-<loc_name>-VFX

+ENV-<env_name>+ (Green)
  ENV-<env_name>-MODEL
  ENV-<env_name>-VFX

+SC<id>-<env_name>+ (Red)
  +SC<id>-<env_name>-ART+
    MODEL-SC<id>-<env_name>
    SHOT-SC<id>-<env_name>-ART
      MODEL-SC<id>-SH<id>

  +SC<id>-<env_name>-ANI+
    ACTOR-SC<id>-<env_name>
    PROP-SC<id>-<env_name>
    SHOT-SC<id>-<env_name>-ANI
      CAM-SC<id>-SH<id>

  +SC<id>-<env_name>-VFX+
    VFX-SC<id>-<env_name>
    SHOT-SC<id>-<env_name>-VFX
      VFX-SC<id>-SH<id>
"""

bl_info = {
    "name": "Project Layout & Animatic Importer",
    "author": "IORI, Gemini, Krutart",
    "version": (1, 8, 2),
    "blender": (4, 0, 0),
    "location": "Outliner > Context Menu & Video Sequencer > UI Panel",
    "description": "Initializes collection structures, verifies shot collections against markers, and imports/updates animatic video guides.",
    "warning": "",
    "doc_url": "",
    "category": "Scene",
}

import bpy
import re
import os
from bpy.props import StringProperty

# --- Color Constants for Collection Tags ---
COLLECTION_COLORS = {
    "LOCATION": "COLOR_05",  # Blue
    "ENVIRO": "COLOR_04",  # Green
    "SCENE": "COLOR_01",  # Red
    "ART": "COLOR_02",  # Yellow
    "ANI": "COLOR_03",  # Orange
    "VFX": "COLOR_06",  # Pink/Magenta
}


def get_or_create_collection(name, parent_collection, color_tag=None):
    """
    Checks if a collection exists. If so, links it. If not, creates it.
    Applies a color tag if provided.
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
        base_name = scene.name
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

        get_or_create_collection(f"{base_name}-TERRAIN", loc_parent_col)
        get_or_create_collection(f"{base_name}-MODEL", loc_parent_col)
        get_or_create_collection(f"{base_name}-VFX", loc_parent_col)

        self.report({"INFO"}, f"Verified LOCATION structure for '{base_name}'.")
        return {"FINISHED"}


class SCENE_OT_create_enviro_structure(bpy.types.Operator):
    """Operator to build the ENVIRONMENT collection structure."""

    bl_idname = "scene.create_enviro_structure"
    bl_label = "Setup ENVIRO Collections"
    bl_description = "Creates the collection structure for an ENVIRONMENT scene (ENV-)"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
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

        get_or_create_collection(f"{base_name}-MODEL", env_parent_col)
        get_or_create_collection(f"{base_name}-VFX", env_parent_col)

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
        # This default SH_id is for the initial setup.
        # The verification operator will check against actual markers.
        sh_id = "SH001"
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
            f"+{base_name}-ART+", sc_parent_col, color_tag=COLLECTION_COLORS["ART"]
        )
        get_or_create_collection(f"MODEL-{base_name}", art_col)
        shot_art_col, _ = get_or_create_collection(f"SHOT-{base_name}-ART", art_col)
        get_or_create_collection(f"MODEL-{sc_id}-{sh_id}", shot_art_col)

        ani_col, _ = get_or_create_collection(
            f"+{base_name}-ANI+", sc_parent_col, color_tag=COLLECTION_COLORS["ANI"]
        )
        get_or_create_collection(f"ACTOR-{base_name}", ani_col)
        get_or_create_collection(f"PROP-{base_name}", ani_col)
        shot_ani_col, _ = get_or_create_collection(f"SHOT-{base_name}-ANI", ani_col)
        # UPDATED: Added "-ANI" suffix to the shot-specific collection name
        get_or_create_collection(f"{sc_id}-{sh_id}-ANI", shot_ani_col)

        vfx_col, _ = get_or_create_collection(
            f"+{base_name}-VFX+", sc_parent_col, color_tag=COLLECTION_COLORS["VFX"]
        )
        get_or_create_collection(f"VFX-{base_name}", vfx_col)
        shot_vfx_col, _ = get_or_create_collection(f"SHOT-{base_name}-VFX", vfx_col)
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
    bl_description = "Checks if a SC-SH-ANI collection exists for each timeline marker"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name

        # Ensure we are in a SCENE context
        if not base_name.startswith("SC"):
            self.report({"ERROR"}, "This operator only works on a SCENE (SC##-).")
            return {"CANCELLED"}

        # Find the main animation shot collection
        shot_ani_collection_name = f"SHOT-{base_name}-ANI"
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
        # Get a set of existing children collections for faster lookups
        existing_shot_collections = set(shot_ani_collection.children.keys())

        for marker in markers:
            # Match marker names like 'CAM-SC17-SH001-FLAT'
            match = re.match(r"CAM-(SC\d+)-(SH\d+)-FLAT", marker.name, re.IGNORECASE)
            if match:
                sc_id, sh_id = match.groups()
                # UPDATED: The expected collection name now has the "-ANI" suffix
                expected_collection_name = f"{sc_id.upper()}-{sh_id.upper()}-ANI"

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


# --- Animatic Guide Importer Operator ---
class SEQUENCER_OT_import_animatic_guides(bpy.types.Operator):
    """
    Scans a directory for scene folders, creates corresponding Blender scenes,
    and imports animatic 'guide' videos into the Video Sequencer for each scene.
    If guides of the same total length already exist, they are not re-imported.
    """

    bl_idname = "sequencer.import_animatic_guides"
    bl_label = "Import Animatic Guides"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(
        name="Source Directory",
        description="Select the root directory containing scene folders (e.g., SC17-APOLLO_CRASH)",
        subtype="DIR_PATH",
    )

    def execute(self, context):
        root_dir = self.directory
        if not os.path.isdir(root_dir):
            self.report(
                {"ERROR"}, "Invalid directory selected. Please choose a valid folder."
            )
            return {"CANCELLED"}

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
            if scene_name not in bpy.data.scenes:
                blender_scene = bpy.data.scenes.new(name=scene_name)
                self.report({"INFO"}, f"Created new scene: '{scene_name}'")
            else:
                blender_scene = bpy.data.scenes[scene_name]
                self.report({"INFO"}, f"Found existing scene: '{scene_name}'")

            if not blender_scene.sequence_editor:
                blender_scene.sequence_editor_create()
            seq_editor = blender_scene.sequence_editor

            scene_path = os.path.join(root_dir, scene_name)
            guide_files = []
            for dirpath, _, filenames in os.walk(scene_path):
                for f in filenames:
                    if "-guide-" in f.lower() and f.lower().endswith(".mp4"):
                        guide_files.append(os.path.join(dirpath, f))

            if not guide_files:
                self.report(
                    {"WARNING"},
                    f"No guide files found for scene '{scene_name}'. Skipping.",
                )
                continue

            guide_files.sort()

            # --- MODIFIED LOGIC: Check for existing guides and compare length ---

            # 1. Calculate the total duration of the NEW guide files from disk.
            new_total_duration = 0
            temp_scene = bpy.data.scenes.new(name="__temp_guide_check")
            try:
                temp_scene.sequence_editor_create()
                temp_seq_editor = temp_scene.sequence_editor
                temp_frame_counter = 1
                for video_path in guide_files:
                    temp_strip = temp_seq_editor.sequences.new_movie(
                        name="temp",
                        filepath=video_path,
                        channel=1,
                        frame_start=temp_frame_counter,
                    )
                    if temp_strip:
                        temp_frame_counter += temp_strip.frame_final_duration
                    else:
                        self.report(
                            {"WARNING"},
                            f"Could not load video to check duration: {video_path}",
                        )
                new_total_duration = temp_frame_counter - 1
            finally:
                # Ensure the temporary scene is always removed
                bpy.data.scenes.remove(temp_scene)

            # 2. Find existing guide strips in the VSE and their total duration.
            existing_guide_strips = [
                s for s in seq_editor.sequences_all if "-guide-" in s.name.lower()
            ]
            existing_total_duration = 0

            if existing_guide_strips:
                last_guide_channel = max(s.channel for s in existing_guide_strips)
                strips_on_last_channel = [
                    s for s in existing_guide_strips if s.channel == last_guide_channel
                ]
                if strips_on_last_channel:
                    existing_total_duration = (
                        max(s.frame_final_end for s in strips_on_last_channel) - 1
                    )

            # 3. Compare durations. If they are the same, skip.
            if existing_guide_strips and new_total_duration == existing_total_duration:
                self.report(
                    {"INFO"},
                    f"Guides for '{scene_name}' are up-to-date (Length: {new_total_duration} frames). Skipping.",
                )
                continue

            # --- END OF MODIFIED LOGIC ---

            # Import guides to a new channel.
            max_channel = 0
            if seq_editor.sequences_all:
                max_channel = max(s.channel for s in seq_editor.sequences_all)
            target_channel = max_channel + 1

            report_msg = f"Found updated guides. Importing to channel {target_channel} for scene '{scene_name}'."
            self.report({"INFO"}, report_msg)

            current_frame = 1
            for video_path in guide_files:
                filename = os.path.basename(video_path)
                match = re.search(r"(sc\d+).+?(sh\d+)", filename, re.IGNORECASE)

                new_strip = seq_editor.sequences.new_movie(
                    name=filename,
                    filepath=video_path,
                    channel=target_channel,
                    frame_start=current_frame,
                )

                if match:
                    sc_id, sh_id = match.groups()
                    marker_name = f"CAM-{sc_id.upper()}-{sh_id.upper()}-FLAT"

                    existing_marker = blender_scene.timeline_markers.get(marker_name)
                    if existing_marker:
                        if existing_marker.frame != current_frame:
                            self.report(
                                {"INFO"},
                                f"Correcting marker '{marker_name}' from frame {existing_marker.frame} to {current_frame}.",
                            )
                            existing_marker.frame = current_frame
                    else:
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

                current_frame += new_strip.frame_final_duration

            blender_scene.frame_end = current_frame - 1

        self.report({"INFO"}, "Animatic import process finished.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# --- UI Panels and Menus ---


class SEQUENCER_PT_animatic_tools(bpy.types.Panel):
    """Adds a UI panel in the Video Sequencer's 'Tool' tab."""

    bl_label = "Animatic Tools"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Tool"

    def draw(self, context):
        layout = self.layout
        layout.operator(
            SEQUENCER_OT_import_animatic_guides.bl_idname,
            text="Import Guide Strips",
            icon="FILE_FOLDER",
        )


class OUTLINER_MT_custom_structure_menu(bpy.types.Menu):
    bl_label = "Custom Scene Setup"
    bl_idname = "OUTLINER_MT_custom_structure_menu"

    def draw(self, context):
        layout = self.layout
        scene_name = context.scene.name

        if re.match(r"^LOC-", scene_name):
            layout.operator(SCENE_OT_create_location_structure.bl_idname)
        if re.match(r"^ENV-", scene_name):
            layout.operator(SCENE_OT_create_enviro_structure.bl_idname)
        if re.match(r"^SC\d+-", scene_name):
            layout.operator(SCENE_OT_create_scene_structure.bl_idname)
            layout.separator()
            layout.operator(
                SCENE_OT_verify_shot_collections.bl_idname, icon="CHECKMARK"
            )


def draw_menu_in_outliner(self, context):
    scene_name = context.scene.name
    if re.match(r"^(LOC-|ENV-|SC\d+-)", scene_name):
        self.layout.separator()
        self.layout.menu(OUTLINER_MT_custom_structure_menu.bl_idname)


# --- Registration ---
classes = (
    SCENE_OT_create_location_structure,
    SCENE_OT_create_enviro_structure,
    SCENE_OT_create_scene_structure,
    SCENE_OT_verify_shot_collections,
    OUTLINER_MT_custom_structure_menu,
    SEQUENCER_OT_import_animatic_guides,
    SEQUENCER_PT_animatic_tools,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.OUTLINER_MT_context_menu.append(draw_menu_in_outliner)


def unregister():
    bpy.types.OUTLINER_MT_context_menu.remove(draw_menu_in_outliner)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
