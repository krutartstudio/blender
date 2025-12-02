bl_info = {
    "name": "Krutart Layout Suite",
    "author": "IORI, Krutart, Gemini",
    "version": (3, 6, 1),  # Bumped for Resolution on Load Fix
    "blender": (4, 5, 0),
    "location": "3D View > UI > Layout Suite",
    "description": "A unified addon for scene setup, animatic import, and a persistent camera switcher based on timeline markers.",
    "warning": "",
    "doc_url": "",
    "category": "Scene",
}

import bpy
import re
import os
import logging
from bpy.props import StringProperty, EnumProperty, BoolProperty
from bpy.types import AddonPreferences
from bpy.app.handlers import persistent

# --- Configure Logging ---
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- Addon Preferences ---
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
COLLECTION_COLORS = {
    "LOCATION": "COLOR_08",
    "ENVIRO": "COLOR_02",
    "SCENE": "COLOR_03",
    "ART": "COLOR_05",
    "ANI": "COLOR_01",
    "VFX": "COLOR_04",
    "CAMERA": "COLOR_06",
}

CAMERA_COLLECTION_TO_APPEND = "+CAMERA+"


# --- Helper Functions ---

def find_view_collections_by_substring_in_collection(layer_collection, substring):
    matching_collections = []
    if substring in layer_collection.name:
        matching_collections.append(layer_collection)
    for child in layer_collection.children:
        matching_collections.extend(
            find_view_collections_by_substring_in_collection(child, substring)
        )
    return matching_collections


def hide_collections_in_view_layer(substring, hide=True):
    log.info(f"Attempting to set exclude={hide} for collections containing '{substring}'.")
    view_layer_collections = find_view_collections_by_substring_in_collection(
        bpy.context.view_layer.layer_collection, substring
    )

    if not view_layer_collections:
        return

    hidden_count = 0
    for col in view_layer_collections:
        if col.exclude != hide:
            col.exclude = hide
            hidden_count += 1


def get_or_create_collection(name, parent_collection, color_tag=None):
    created = False
    collection = bpy.data.collections.get(name)

    if collection is None:
        collection = bpy.data.collections.new(name)
        parent_collection.children.link(collection)
        created = True
    else:
        if name not in parent_collection.children:
            parent_collection.children.link(collection)

    if color_tag:
        collection.color_tag = color_tag

    return collection, created

def parse_shot_filename(filename):
    """
    Parses a filename to extract SC and SH numbers.
    Handles complex prefixes like '3212-sc04...'
    Returns (sc_id_str, sh_id_str, sh_num_int, version_int) or None.
    """
    # Regex matches: ...SC04...SH010...v001... (Case Insensitive)
    # It is not anchored to start (^) so it handles prefixes.
    match = re.search(r"(sc\d+).+?(sh\d+)(?:.*?v(\d+))?", filename, re.IGNORECASE)
    if match:
        sc_str = match.group(1).upper()
        sh_str = match.group(2).upper()
        sh_num = int(sh_str[2:]) # remove 'SH' to get integer
        
        version = 0
        if match.group(3):
            version = int(match.group(3))
            
        return sc_str, sh_str, sh_num, version
    return None

def create_marker_from_strip(scene, strip):
    """
    Creates or updates a marker based on the strip's filename at the strip's start frame.
    """
    filename = os.path.basename(strip.filepath)
    parsed = parse_shot_filename(filename)
    
    if not parsed:
        log.warning(f"Could not parse SC/SH from '{filename}'. Skipping marker.")
        return None
        
    sc_str, sh_str, _, _ = parsed
    marker_name = f"CAM-{sc_str}-{sh_str}"
    
    # Check if marker exists
    existing = scene.timeline_markers.get(marker_name)
    if existing:
        existing.frame = strip.frame_start
        log.info(f"Updated marker '{marker_name}' to frame {strip.frame_start}.")
    else:
        scene.timeline_markers.new(name=marker_name, frame=strip.frame_start)
        log.info(f"Created marker '{marker_name}' at frame {strip.frame_start}.")


# --- CAMERA SWITCHER LOGIC ---

def apply_shot_camera_state(context, update_resolution=True):
    """
    Core logic to apply camera settings.
    Separated to allow controlling resolution updates independently.
    """
    if not context.scene:
        return

    camera_suffix = context.scene.shot_camera_toggle
    
    log_msg = f"--- Shot Camera Switcher: Setting all scenes and markers to '{camera_suffix}'"
    if not update_resolution:
        log_msg += " (Resolution Update SKIPPED) ---"
    else:
        log_msg += " ---"
    log.info(log_msg)

    for scene in bpy.data.scenes:
        # Resolution switching (Only if triggered by user/UI)
        if update_resolution:
            if camera_suffix == 'FLAT':
                if (scene.render.resolution_x != 1920) or (scene.render.resolution_y != 1080):
                    scene.render.resolution_x = 1920
                    scene.render.resolution_y = 1080
            elif camera_suffix == 'FULLDOME':
                if (scene.render.resolution_x != 2048) or (scene.render.resolution_y != 2048):
                    scene.render.resolution_x = 2048
                    scene.render.resolution_y = 2048

        # Marker binding
        for marker in scene.timeline_markers:
            if marker.name.startswith("CAM-SC"):
                shot_name = marker.name
                target_cam_name = f"{shot_name}-{camera_suffix}"
                target_cam_obj = bpy.data.objects.get(target_cam_name)
                
                if target_cam_obj and target_cam_obj.type == 'CAMERA':
                    marker.camera = target_cam_obj
                else:
                    marker.camera = None

    if bpy.context.scene:
        on_frame_change(bpy.context.scene)


def update_all_shot_cameras(self, context):
    """
    Callback for the UI Property.
    Since this is a user action, we ALLOW resolution updates.
    """
    apply_shot_camera_state(context, update_resolution=True)
    return None


@persistent
def on_frame_change(scene):
    if not scene == bpy.context.scene:
        return

    current_frame = scene.frame_current
    active_marker = None
    
    # Find marker for current frame
    sorted_markers = sorted(scene.timeline_markers, key=lambda m: m.frame)
    for marker in sorted_markers:
        if marker.frame <= current_frame:
            active_marker = marker
        else:
            break
            
    if active_marker and active_marker.camera:
        if scene.camera != active_marker.camera:
            scene.camera = active_marker.camera

def draw_camera_toggle(self, context):
    layout = self.layout
    scene = context.scene
    layout.prop(scene, "shot_camera_toggle", text="FLAT | FULLDOME", expand=True)


@persistent
def on_file_loaded(dummy):
    """
    Handler for file load.
    We apply marker logic but PREVENT resolution changes to respect the saved file state.
    """
    if bpy.context.scene:
        apply_shot_camera_state(bpy.context, update_resolution=False)


# --- Collection Setup Operators ---
class SCENE_OT_create_location_structure(bpy.types.Operator):
    bl_idname = "scene.create_location_structure"
    bl_label = "Setup LOCATION Collections"
    bl_description = "Creates structure for LOCATION scene"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        master_collection = scene.collection
        parent_col_name = f"+{base_name}+"

        loc_parent_col, created = get_or_create_collection(
            parent_col_name, master_collection, color_tag=COLLECTION_COLORS["LOCATION"]
        )

        get_or_create_collection(f"TERRAIN-{base_name}", loc_parent_col)
        get_or_create_collection(f"MODEL-{base_name}", loc_parent_col)
        get_or_create_collection(f"VFX-{base_name}", loc_parent_col)

        for collection in bpy.data.collections:
            if collection.name.startswith("+ENV-") and collection.name.endswith("+"):
                if collection.name not in master_collection.children:
                    master_collection.children.link(collection)

        return {"FINISHED"}


class SCENE_OT_create_enviro_structure(bpy.types.Operator):
    bl_idname = "scene.create_enviro_structure"
    bl_label = "Setup ENVIRO Collections"
    bl_description = "Creates structure for ENVIRONMENT scene"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        master_collection = scene.collection
        parent_col_name = f"+{base_name}+"

        env_parent_col, created = get_or_create_collection(
            parent_col_name, master_collection, color_tag=COLLECTION_COLORS["ENVIRO"]
        )

        get_or_create_collection(f"MODEL-{base_name}", env_parent_col)
        get_or_create_collection(f"VFX-{base_name}", env_parent_col)

        location_collection = next((c for c in bpy.data.collections if c.name.startswith("+LOC-")), None)
        if location_collection and location_collection.name not in master_collection.children:
            master_collection.children.link(location_collection)

        return {"FINISHED"}


class SCENE_OT_create_scene_structure(bpy.types.Operator):
    bl_idname = "scene.create_scene_structure"
    bl_label = "Setup SCENE Collections"
    bl_description = "Creates SCENE collections"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        master_collection = scene.collection

        match = re.match(r"^(SC\d+)-(.+)", base_name)
        if not match:
            self.report({"ERROR"}, "Scene name format incorrect. Expected 'SC##-<env_name>'.")
            return {"CANCELLED"}

        sc_id, scene_env_name = match.groups()
        parent_col_name = f"+{base_name}+"

        sc_parent_col, created = get_or_create_collection(
            parent_col_name, master_collection, color_tag=COLLECTION_COLORS["SCENE"]
        )

        art_col, _ = get_or_create_collection(f"+ART-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["ART"])
        get_or_create_collection(f"MODEL-{base_name}", art_col)
        get_or_create_collection(f"SHOT-ART-{base_name}", art_col)

        ani_col, _ = get_or_create_collection(f"+ANI-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["ANI"])
        get_or_create_collection(f"ACTOR-{base_name}", ani_col)
        get_or_create_collection(f"PROP-{base_name}", ani_col)
        get_or_create_collection(f"SHOT-ANI-{base_name}", ani_col)

        vfx_col, _ = get_or_create_collection(f"+VFX-{base_name}+", sc_parent_col, color_tag=COLLECTION_COLORS["VFX"])
        get_or_create_collection(f"VFX-{base_name}", vfx_col)
        get_or_create_collection(f"SHOT-VFX-{base_name}", vfx_col)

        # Link Environment & Location
        linked_enviros = []
        for collection in bpy.data.collections:
            enviro_match = re.match(r"^\+ENV-(.+)\+$", collection.name)
            if enviro_match:
                enviro_name = enviro_match.group(1)
                if enviro_name in scene_env_name and collection.name not in master_collection.children:
                    master_collection.children.link(collection)
                    linked_enviros.append(collection.name)

        location_collection = next((c for c in bpy.data.collections if c.name.startswith("+LOC-")), None)
        if location_collection and location_collection.name not in master_collection.children:
            master_collection.children.link(location_collection)

        return {"FINISHED"}


class SCENE_OT_verify_shot_collections(bpy.types.Operator):
    bl_idname = "scene.verify_shot_collections"
    bl_label = "Verify Shot Collections"
    bl_description = "Checks if shot collections exist for markers"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name

        if not base_name.startswith("SC"):
            self.report({"ERROR"}, "This operator only works on a SCENE (SC##-).")
            return {"CANCELLED"}

        shot_ani_collection_name = f"SHOT-ANI-{base_name}"
        shot_ani_collection = bpy.data.collections.get(shot_ani_collection_name)

        if not shot_ani_collection:
            self.report({"ERROR"}, f"Collection '{shot_ani_collection_name}' not found.")
            return {"CANCELLED"}

        missing_collections = []
        existing_shot_collections = set(shot_ani_collection.children.keys())

        for marker in scene.timeline_markers:
            match = re.match(r"CAM-(SC\d+)-(SH\d+)$", marker.name, re.IGNORECASE)
            if match:
                sc_id, sh_id = match.groups()
                expected_collection_name = f"CAM-{sc_id.upper()}-{sh_id.upper()}"
                if expected_collection_name not in existing_shot_collections:
                    missing_collections.append(expected_collection_name)

        if missing_collections:
            self.report({"ERROR"}, f"Missing collections: {', '.join(missing_collections)}")
        else:
            self.report({"INFO"}, "Verification successful.")

        return {"FINISHED"}


# --- Animatic Operators ---

class SEQUENCER_OT_import_single_guide(bpy.types.Operator):
    bl_idname = "sequencer.import_single_guide"
    bl_label = "Import Single Guide"
    bl_description = "Imports a single guide clip (Video Ch2, Audio Ch1) and creates a marker"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(name="File Path", subtype="FILE_PATH")

    def execute(self, context):
        scene = context.scene
        vse_area = next((area for area in context.screen.areas if area.type == 'SEQUENCE_EDITOR'), None)
        if not vse_area:
            self.report({'ERROR'}, "Video Sequence Editor not found.")
            return {'CANCELLED'}

        if not self.filepath or not os.path.exists(self.filepath):
            self.report({'ERROR'}, "Invalid file path.")
            return {'CANCELLED'}
        
        if not scene.sequence_editor:
            scene.sequence_editor_create()
        
        try:
            with context.temp_override(window=context.window, area=vse_area, scene=scene):
                # Snapshot existing sequences to identify the new one
                existing_strips = set(scene.sequence_editor.sequences)

                # Import (attempt channel 2)
                bpy.ops.sequencer.movie_strip_add(
                    filepath=self.filepath,
                    frame_start=scene.frame_current,
                    channel=2, 
                    fit_method='FIT',
                    adjust_playback_rate=True,
                    sound=True,
                    overlap_shuffle_override=True
                )
                
                # Find the new strips by set difference
                current_strips = set(scene.sequence_editor.sequences)
                new_strips = list(current_strips - existing_strips)

                video_strip = None
                for s in new_strips:
                    if s.type == 'MOVIE':
                        s.channel = 2 # Force Channel 2
                        video_strip = s
                    elif s.type == 'SOUND':
                        s.channel = 1 # Force Channel 1

                if video_strip:
                    create_marker_from_strip(scene, video_strip)
                    self.report({"INFO"}, f"Imported '{video_strip.name}' and set marker.")
                else:
                    self.report({"WARNING"}, "Imported strip, but could not locate it via selection.")

        except Exception as e:
            self.report({"ERROR"}, f"Failed to import: {e}")
            return {"CANCELLED"}
            
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class SEQUENCER_OT_import_animatic_guides(bpy.types.Operator):
    """
    Robustly imports all guide clips from the selected file's directory.
    Cleans up OLD guide strips and markers for this scene, then rebuilds them in order.
    OPTIMIZED: Uses set difference instead of selection to avoid UI redraw freeze.
    """
    bl_idname = "sequencer.import_animatic_guides"
    bl_label = "Import/Update Animatic Guides"
    bl_description = "Select ONE guide clip. Operator will find, sort, and import ALL guides in that folder."
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(
        name="Select Any Guide Clip",
        description="Select any guide clip in the folder. The operator will handle the rest.",
        subtype="FILE_PATH",
    )

    def execute(self, context):
        scene = context.scene
        
        if not self.filepath or not os.path.exists(self.filepath):
            self.report({"ERROR"}, "Invalid file path.")
            return {"CANCELLED"}

        # Ensure VSE exists
        if not scene.sequence_editor:
            scene.sequence_editor_create()

        vse_area = next((area for area in context.screen.areas if area.type == 'SEQUENCE_EDITOR'), None)
        if not vse_area:
            self.report({'ERROR'}, "Video Sequence Editor area required.")
            return {'CANCELLED'}

        # 1. Analyze Directory and Target Scene
        directory = os.path.dirname(self.filepath)
        try:
            files = os.listdir(directory)
        except Exception as e:
            self.report({"ERROR"}, f"Cannot read directory: {e}")
            return {"CANCELLED"}

        # Parse the selected file to determine the target SCENE (SCxx)
        init_parsed = parse_shot_filename(os.path.basename(self.filepath))
        if not init_parsed:
             self.report({"ERROR"}, "Selected file does not match '...SC##...SH##...' pattern.")
             return {"CANCELLED"}
        
        target_sc_str = init_parsed[0]  # e.g. "SC04"
        log.info(f"Targeting Scene ID: {target_sc_str}")

        # 2. Gather & Sort Files
        # Map: sh_num -> {version: int, filename: str, ...}
        shots_map = {} 
        
        for f in files:
            # Basic extension check
            if not f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                continue
            # STRICT guide check to avoid importing renders or playblasts
            if "-guide-" not in f.lower():
                continue
                
            parsed = parse_shot_filename(f)
            if not parsed:
                continue
                
            sc, sh, sh_num, ver = parsed
            
            # Filter: Only import clips that belong to the identified SCENE ID
            if sc != target_sc_str:
                continue
                
            # Logic: Keep only the Highest Version of each Shot Number
            if sh_num not in shots_map or ver > shots_map[sh_num]['ver']:
                shots_map[sh_num] = {'ver': ver, 'file': f, 'sc': sc, 'sh': sh}
                
        if not shots_map:
             self.report({"WARNING"}, f"No matching '-guide-' files found for {target_sc_str}.")
             return {"CANCELLED"}
             
        sorted_shot_nums = sorted(shots_map.keys())
        log.info(f"Found {len(sorted_shot_nums)} shots to import: {sorted_shot_nums}")

        # 3. Clean Setup (Remove OLD Guides & Markers)
        
        # A. Remove VSE Strips (Safe removal: only matches pattern)
        strips_to_remove = []
        for s in scene.sequence_editor.sequences_all:
            # Check filepath safely
            fp = None
            if hasattr(s, 'filepath'):
                fp = s.filepath
            elif hasattr(s, 'sound') and s.sound:
                fp = s.sound.filepath
            
            if fp:
                fn = os.path.basename(fp)
                # Delete if it looks like a guide for this scene
                if "-guide-" in fn.lower() and target_sc_str.lower() in fn.lower():
                    strips_to_remove.append(s)
        
        for s in strips_to_remove:
            scene.sequence_editor.sequences.remove(s)
        log.info(f"Removed {len(strips_to_remove)} old guide strips.")

        # B. Remove Markers (Safe removal: only matches CAM-SCxx pattern)
        markers_to_remove = []
        for m in scene.timeline_markers:
            if m.name.startswith(f"CAM-{target_sc_str}-"):
                markers_to_remove.append(m)
                
        for m in markers_to_remove:
            scene.timeline_markers.remove(m)
        log.info(f"Removed {len(markers_to_remove)} old markers.")

        # 4. Build Timeline (Deterministic Loop)
        current_frame = 1
        
        with context.temp_override(window=context.window, area=vse_area, scene=scene):
            for sh_num in sorted_shot_nums:
                shot_data = shots_map[sh_num]
                filename = shot_data['file']
                filepath = os.path.join(directory, filename)
                
                try:
                    # Snapshot existing sequences to identify the new one
                    # This avoids using bpy.ops.select_all which triggers UI redraws and causes freezing
                    existing_strips = set(scene.sequence_editor.sequences)

                    # Import
                    bpy.ops.sequencer.movie_strip_add(
                        filepath=filepath,
                        frame_start=current_frame,
                        channel=2,
                        fit_method='FIT',
                        adjust_playback_rate=True,
                        sound=True,
                        use_framerate=False,
                        overlap_shuffle_override=True
                    )
                    
                    # Find the new strips by set difference (Instant)
                    current_strips = set(scene.sequence_editor.sequences)
                    new_strips = list(current_strips - existing_strips)

                    video_strip = None
                    
                    for s in new_strips:
                        if s.type == 'MOVIE':
                            s.channel = 2 # Force Channel 2
                            video_strip = s
                        elif s.type == 'SOUND':
                            s.channel = 1 # Force Channel 1
                    
                    if video_strip:
                        # Create Marker
                        marker_name = f"CAM-{shot_data['sc']}-{shot_data['sh']}"
                        scene.timeline_markers.new(name=marker_name, frame=current_frame)
                        
                        # Advance Frame based on ACTUAL imported length
                        current_frame += int(video_strip.frame_final_duration)
                    else:
                        log.warning(f"Imported {filename} but could not locate strip via selection.")

                except Exception as e:
                     log.error(f"Error importing {filename}: {e}")

        # 5. Finalize
        scene.frame_start = 1
        scene.frame_end = current_frame - 1
        
        # Update END marker
        end_marker = scene.timeline_markers.get("END")
        if end_marker:
            end_marker.frame = current_frame
        else:
            scene.timeline_markers.new(name="END", frame=current_frame)
            
        # Run Scene Setup (Create collections if missing)
        bpy.ops.scene.create_scene_structure()
        
        # Run Camera Setup (Create rigs if missing)
        bpy.ops.scene.setup_cameras_from_markers()
        
        # Force Camera Switcher Update (Rebinds all markers to cameras)
        update_all_shot_cameras(scene, context)

        self.report({"INFO"}, f"Successfully imported and setup {len(sorted_shot_nums)} shots.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class SCENE_OT_setup_cameras_from_markers(bpy.types.Operator):
    bl_idname = "scene.setup_cameras_from_markers"
    bl_label = "Setup Shots"
    bl_description = "Creates cameras and collections based on markers"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        markers = scene.timeline_markers
        preferences = context.preferences.addons[__name__].preferences
        
        win_path = preferences.camera_hero_path_windows
        linux_path = preferences.camera_hero_path_linux

        camera_hero_blend_path = None
        if win_path and os.path.exists(win_path):
            camera_hero_blend_path = win_path
        elif linux_path and os.path.exists(linux_path):
            camera_hero_blend_path = linux_path

        if not base_name.startswith("SC"):
            self.report({"ERROR"}, "Must be run in a SCENE (SC##).")
            return {"CANCELLED"}

        if not camera_hero_blend_path:
             self.report({"ERROR"}, "Camera Hero file not found.")
             return {"CANCELLED"}

        shot_ani_collection = bpy.data.collections.get(f"SHOT-ANI-{base_name}")
        shot_art_collection = bpy.data.collections.get(f"SHOT-ART-{base_name}")
        shot_vfx_collection = bpy.data.collections.get(f"SHOT-VFX-{base_name}")

        if not all([shot_ani_collection, shot_art_collection, shot_vfx_collection]):
             self.report({"ERROR"}, "Parent collections missing. Run 'Setup SCENE Collections'.")
             return {"CANCELLED"}

        camera_offset_counter = 0
        for marker in sorted(markers, key=lambda m: m.frame):
            match = re.match(r"CAM-(SC\d+)-(SH\d+)$", marker.name, re.IGNORECASE)
            if not match: continue

            sc_id, sh_id = match.groups()
            sc_upper, sh_upper = sc_id.upper(), sh_id.upper()
            
            cam_collection_name = f"CAM-{sc_upper}-{sh_upper}"
            
            # SKIP if already exists
            if cam_collection_name in shot_ani_collection.children:
                camera_offset_counter += 1 # Still increment to keep spacing consistent if we were creating
                continue

            # Create sub-collections
            get_or_create_collection(f"MODEL-{sc_upper}-{sh_upper}", shot_art_collection)
            get_or_create_collection(f"VFX-{sc_upper}-{sh_upper}", shot_vfx_collection)

            # Append Camera Rig
            try:
                with bpy.data.libraries.load(camera_hero_blend_path, link=False) as (data_from, data_to):
                    data_to.collections = [c for c in data_from.collections if c == CAMERA_COLLECTION_TO_APPEND]

                if data_to.collections:
                    appended_col = data_to.collections[0]
                    appended_col.name = cam_collection_name
                    shot_ani_collection.children.link(appended_col)
                    appended_col.color_tag = COLLECTION_COLORS["CAMERA"]

                    # Rename internals
                    for child in appended_col.children:
                        if "cam_mesh" in child.name:
                            child.name = f"cam_mesh-{sc_upper}-{sh_upper}"
                            for obj in child.objects:
                                if "cam_flat" in obj.name: obj.name = f"CAM-{sc_upper}-{sh_upper}-FLAT"
                                elif "cam_fulldome" in obj.name: obj.name = f"CAM-{sc_upper}-{sh_upper}-FULLDOME"
                        elif "cam_rig" in child.name:
                            child.name = f"cam_rig-{sc_upper}-{sh_upper}"
                            for obj in child.objects:
                                if obj.type == "ARMATURE":
                                    obj.name = f"+cam_rig-{sc_upper}-{sh_upper}"
                                    # Offset
                                    obj.location.x += (camera_offset_counter * 2.0)
                        elif "cam_boneshapes" in child.name:
                            child.name = f"cam_boneshapes-{sc_upper}-{sh_upper}"

                    # Cleanup root link
                    if appended_col.name in scene.collection.children:
                        scene.collection.children.unlink(appended_col)
                    
                    camera_offset_counter += 1

            except Exception as e:
                log.error(f"Error appending camera for {marker.name}: {e}")

        hide_collections_in_view_layer("cam_boneshapes", hide=True)
        update_all_shot_cameras(scene, context)
        
        return {"FINISHED"}


# --- UI Panel ---
class VIEW3D_PT_layout_suite_main_panel(bpy.types.Panel):
    bl_label = "Layout Suite"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Layout Suite"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        scene_name = scene.name

        if re.match(r"^LOC-", scene_name, re.IGNORECASE):
            box = layout.box()
            box.label(text="Location Tools", icon="WORLD_DATA")
            box.operator(SCENE_OT_create_location_structure.bl_idname)

        elif re.match(r"^ENV-", scene_name, re.IGNORECASE):
            box = layout.box()
            box.label(text="Environment Tools", icon="OUTLINER_OB_LIGHTPROBE")
            box.operator(SCENE_OT_create_enviro_structure.bl_idname)

        elif re.match(r"^SC\d+-", scene_name, re.IGNORECASE):
            box = layout.box()
            box.label(text="Initial Scene Setup", icon="SCENE_DATA")
            box.operator(SCENE_OT_create_scene_structure.bl_idname)

            box = layout.box()
            box.label(text="Animatic & Markers", icon="SEQUENCE")
            
            # The Robust Import Button
            op = box.operator(
                SEQUENCER_OT_import_animatic_guides.bl_idname,
                text="Import/Update Scene Guides",
                icon="FILE_FOLDER",
            )
            
            # Single Guide Button (now adds markers too)
            box.operator(
                SEQUENCER_OT_import_single_guide.bl_idname,
                text="Place Single Guide Clip",
                icon="FILE_NEW",
            )
            
            box.separator()
            box.operator(SCENE_OT_verify_shot_collections.bl_idname, icon="CHECKMARK")

            box = layout.box()
            box.label(text="Camera Management", icon="CAMERA_DATA")
            box.operator(SCENE_OT_setup_cameras_from_markers.bl_idname, icon="CAMERA_DATA")
            
            box.separator()
            box.label(text="Global Camera & Resolution Switcher:")
            draw_camera_toggle(self, context)

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
    SEQUENCER_OT_import_single_guide,
    SCENE_OT_setup_cameras_from_markers,
    VIEW3D_PT_layout_suite_main_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    bpy.types.Scene.shot_camera_toggle = bpy.props.EnumProperty(
        name="Shot Camera Type",
        description="Switch all shot markers and scene resolutions to FLAT or FULLDOME",
        items=[
            ('FLAT', "Flat", "Use all FLAT cameras and set 1920x1080"),
            ('FULLDOME', "Fulldome", "Use all FULLDOME cameras and set 2048x2048")
        ],
        default='FLAT',
        update=update_all_shot_cameras
    )
    
    if on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(on_frame_change)
    if on_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_file_loaded)


def unregister():
    if on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(on_frame_change)
    if on_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_file_loaded)

    try:
        del bpy.types.Scene.shot_camera_toggle
    except AttributeError:
        pass

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()