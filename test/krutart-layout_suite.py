bl_info = {
    "name": "Krutart Master Suite",
    "author": "IORI, Krutart, Gemini",
    "version": (3, 2, 2),
    "blender": (4, 2, 0),
    "location": "3D View > UI > Layout | Outliner & 3D View > Right-Click Menus",
    "description": "A unified addon for initializing collection structures, importing animatics, setting up cameras, and managing shot-based asset visibility with advanced copy/move tools.",
    "warning": "This version includes a toggle for the performance-intensive 'Isolate Active Shot' feature.",
    "doc_url": "",
    "category": "Scene",
}

# --- Standard Library Imports ---
import bpy
import re
import os
import json
import logging
from typing import Optional, Tuple, Dict, Any, List

# --- Blender Imports ---
from bpy.props import StringProperty, EnumProperty, BoolProperty
from bpy.types import AddonPreferences, Operator, Panel, Menu, Scene, Collection, Object
from bpy.app.handlers import persistent


# =============================================================================
# --- 1. CORE SETUP: LOGGING AND PREFERENCES ---
# =============================================================================

# --- Configure Logging ---
# Establishes a consistent logging format for debugging and user feedback.
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- Addon Preferences ---
# Allows users to configure essential paths, making the addon adaptable to different workstations.
class KLS_Preferences(AddonPreferences):
    """Stores user-configurable paths for external files, like the master camera rig."""
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
        box = layout.box()
        box.label(text="Camera Hero File Paths:", icon="CAMERA_DATA")
        box.prop(self, "camera_hero_path_windows")
        box.prop(self, "camera_hero_path_linux")


# =============================================================================
# --- 2. CONSTANTS AND CORE DATA STRUCTURES ---
# =============================================================================

# Color tags for clear visual organization in the Outliner.
COLLECTION_COLORS = {
    "LOCATION": "COLOR_08", "ENVIRO": "COLOR_02", "SCENE": "COLOR_03",
    "ART": "COLOR_05", "ANI": "COLOR_01", "VFX": "COLOR_04", "CAMERA": "COLOR_06",
}
CAMERA_COLLECTION_TO_APPEND = "+CAMERA+"


class ObjectVisibilityController:
    """
    Manages object visibility data via a custom scene property. This class provides a
    persistent, safe way to track master-copy relationships and their active frame
    ranges, ensuring visibility is handled centrally by rebuilding keyframe animation.
    """
    PROP_NAME = "kls_object_visibility_controller"

    def __init__(self, scene: Scene):
        assert scene is not None, "Scene must be provided to initialize controller."
        self.scene = scene
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        """Loads and decodes the visibility data from the custom scene property."""
        data_str = self.scene.get(self.PROP_NAME, "{}")
        try:
            return json.loads(data_str)
        except json.JSONDecodeError:
            log.error("Could not decode object visibility data. Resetting to empty.")
            return {}

    def _save(self):
        """Encodes and saves the visibility data back to the scene property."""
        self.scene[self.PROP_NAME] = json.dumps(self.data, indent=2)
        log.debug("Saved object visibility data to scene property.")

    def add_copy(self, original_name: str, copy_name: str, start_frame: int, end_frame: int):
        """Adds or updates a copy's visibility entry for a given original object."""
        log.info(f"Managing visibility for copy '{copy_name}' of '{original_name}' for frames {start_frame}-{end_frame}.")
        if original_name not in self.data:
            self.data[original_name] = []

        # Check for and update existing entries to prevent duplicates
        for entry in self.data[original_name]:
            if entry['copy_name'] == copy_name:
                log.warning(f"Copy '{copy_name}' already managed for '{original_name}'. Overwriting frame range.")
                entry['start_frame'] = start_frame
                entry['end_frame'] = end_frame
                self._save()
                return

        new_entry = {"copy_name": copy_name, "start_frame": start_frame, "end_frame": end_frame}
        self.data[original_name].append(new_entry)
        self._save()

    def get_copies(self, original_name: str) -> List[Dict[str, Any]]:
        """Retrieves all copy information for a given original object."""
        return self.data.get(original_name, [])


# =============================================================================
# --- 3. HELPER FUNCTIONS ---
# =============================================================================

# --- Section 3.1: Collection and View Layer Management ---

def get_or_create_collection(name: str, parent_collection: Collection, color_tag: Optional[str] = None) -> Tuple[Optional[Collection], bool]:
    """
    Ensures a collection exists and is linked to the correct parent. Creates it if necessary.
    Returns the collection and a boolean indicating if it was newly created.
    """
    assert parent_collection is not None, f"Parent collection must be provided to create '{name}'."
    log.debug(f"Getting or creating collection '{name}' under '{parent_collection.name}'.")
    created = False
    collection = bpy.data.collections.get(name)

    if collection is None:
        collection = bpy.data.collections.new(name)
        parent_collection.children.link(collection)
        created = True
        log.info(f"Created new collection: '{name}'.")
    elif name not in parent_collection.children:
        parent_collection.children.link(collection)
        log.info(f"Linked existing collection '{name}' to '{parent_collection.name}'.")

    if color_tag:
        collection.color_tag = color_tag
    return collection, created

def find_layer_collection_by_name(layer_collection_root, name_to_find: str):
    """Recursively finds the LayerCollection corresponding to a given Collection name."""
    if layer_collection_root.name == name_to_find:
        return layer_collection_root
    for child in layer_collection_root.children:
        found = find_layer_collection_by_name(child, name_to_find)
        if found:
            return found
    return None

def set_collection_exclude(view_layer, collection_name: str, exclude_status: bool):
    """Safely finds a collection by name in the view layer and sets its exclude status."""
    if not collection_name or not bpy.data.collections.get(collection_name):
        return

    layer_coll = find_layer_collection_by_name(view_layer.layer_collection, collection_name)
    if layer_coll and layer_coll.exclude != exclude_status:
        layer_coll.exclude = exclude_status
        log.debug(f"Set collection '{collection_name}' exclude to {exclude_status}.")


# --- Section 3.2: Visibility and Animation ---

def _clear_visibility_keyframes(obj: Object):
    """Removes all hide_viewport and hide_render keyframes from an object."""
    assert obj is not None, "Attempted to clear keyframes from a null object."
    if obj.animation_data and obj.animation_data.action:
        fcurves = obj.animation_data.action.fcurves
        # Iterate over a copy to allow safe removal
        for fcurve in list(fcurves):
            if fcurve.data_path in ("hide_viewport", "hide_render"):
                fcurves.remove(fcurve)

def _set_visibility_keyframe(obj: Object, frame: int, is_visible: bool):
    """Inserts visibility keyframes for an object, ensuring constant interpolation."""
    assert obj is not None, "Attempted to set keyframes on a null object."
    obj.hide_viewport = not is_visible
    obj.hide_render = not is_visible

    for path in ["hide_viewport", "hide_render"]:
        if obj.keyframe_insert(data_path=path, frame=frame) and obj.animation_data and obj.animation_data.action:
            fcurve = obj.animation_data.action.fcurves.find(path)
            if fcurve:
                # Set interpolation on the newly added keyframe
                for point in fcurve.keyframe_points:
                    if abs(point.co.x - frame) < 0.001:  # Float comparison tolerance
                        point.interpolation = 'CONSTANT'
                        break

def rebuild_object_visibility_animation(scene: Scene, original_obj_name: str):
    """
    Rebuilds visibility animation for a master object and all its managed copies,
    ensuring the master is hidden only when a copy is active.
    """
    log.info(f"Rebuilding visibility animation for master '{original_obj_name}'.")
    controller = ObjectVisibilityController(scene)
    original_obj = bpy.data.objects.get(original_obj_name)
    if not original_obj:
        log.error(f"Cannot rebuild animation: Original object '{original_obj_name}' not found.")
        return

    copy_infos = controller.get_copies(original_obj_name)
    _clear_visibility_keyframes(original_obj)
    active_ranges = []

    for info in copy_infos:
        copy_obj = bpy.data.objects.get(info['copy_name'])
        if not copy_obj:
            log.warning(f"Managed copy '{info['copy_name']}' not found. Skipping.")
            continue
        
        _clear_visibility_keyframes(copy_obj)
        start, end = info['start_frame'], info['end_frame']
        
        _set_visibility_keyframe(copy_obj, start - 1, is_visible=False)
        _set_visibility_keyframe(copy_obj, start, is_visible=True)
        _set_visibility_keyframe(copy_obj, end + 1, is_visible=False)
        active_ranges.append((start, end))

    if not active_ranges:
        _set_visibility_keyframe(original_obj, scene.frame_start, is_visible=True)
        return

    active_ranges.sort(key=lambda r: r[0])
    merged_ranges = [active_ranges[0]]
    for current_start, current_end in active_ranges[1:]:
        last_start, last_end = merged_ranges[-1]
        if current_start <= last_end + 1:
            merged_ranges[-1] = (last_start, max(last_end, current_end))
        else:
            merged_ranges.append((current_start, current_end))
    
    log.info(f"Original '{original_obj_name}' will be hidden during merged ranges: {merged_ranges}")
    _set_visibility_keyframe(original_obj, scene.frame_start, is_visible=True)
    for start, end in merged_ranges:
        _set_visibility_keyframe(original_obj, start, is_visible=False)
        _set_visibility_keyframe(original_obj, end + 1, is_visible=True)


# --- Section 3.3: Context and Naming Utilities ---

def get_active_datablock(context) -> Tuple[Optional[bpy.types.ID], Optional[str]]:
    """Determines the active datablock (Object or Collection), prioritizing the Outliner."""
    if context.area and context.area.type == 'OUTLINER':
        active_id = context.active_ids[0] if context.active_ids else None
        if isinstance(active_id, bpy.types.Collection): return active_id, 'COLLECTION'
        if isinstance(active_id, bpy.types.Object): return active_id, 'OBJECT'
    
    if context.active_object:
        return context.active_object, 'OBJECT'
    
    return None, None

def get_shot_frame_range(shot_name: str) -> Tuple[Optional[int], Optional[int]]:
    """Finds the start and end frame for a shot based on timeline markers."""
    match = re.search(r"SC(\d+)-SH(\d+)", shot_name, re.IGNORECASE)
    if not match:
        log.warning(f"Could not parse SC/SH from '{shot_name}' to find frame range.")
        return None, None

    sc_id_num, sh_id_num = int(match.group(1)), int(match.group(2))
    marker_name_pattern = re.compile(rf"CAM-SC{sc_id_num:02d}-SH{sh_id_num:03d}", re.IGNORECASE)
    
    sorted_markers = sorted(bpy.context.scene.timeline_markers, key=lambda m: m.frame)
    start_marker = next((m for m in sorted_markers if marker_name_pattern.fullmatch(m.name)), None)
    
    if not start_marker:
        log.warning(f"Start marker for '{shot_name}' not found.")
        return None, None

    try:
        start_marker_index = sorted_markers.index(start_marker)
        if start_marker_index + 1 < len(sorted_markers):
            next_marker = sorted_markers[start_marker_index + 1]
            return start_marker.frame, next_marker.frame - 1
    except ValueError:
        return None, None
    
    return start_marker.frame, bpy.context.scene.frame_end

def get_source_collection(item: bpy.types.ID) -> Optional[Collection]:
    """Finds the immediate parent collection of an object or collection."""
    if isinstance(item, bpy.types.Object) and item.users_collection:
        return item.users_collection[0]
    elif isinstance(item, bpy.types.Collection):
        for coll in bpy.data.collections:
            if item.name in coll.children:
                return coll
    return bpy.context.scene.collection

def is_in_shot_build_collection(item: bpy.types.ID) -> bool:
    """Recursively checks if an item is inside a main SCENE build collection ('+SC...')."""
    parent_map = {child: parent for parent in bpy.data.collections for child in parent.children}
    current_coll = get_source_collection(item)
    while current_coll:
        if current_coll.name.startswith("+SC"):
            return True
        current_coll = parent_map.get(current_coll)
    return False

# --- Section 3.4: Hierarchy Copying ---

def copy_collection_hierarchy(original_coll: Collection, target_parent_coll: Collection, name_suffix: str = "") -> Optional[Collection]:
    """
    Recursively copies a collection and its contents, then remaps internal relationships
    (parenting, constraints, modifiers) to point to the new copies.
    """
    log.info(f"Copying hierarchy of '{original_coll.name}' to '{target_parent_coll.name}'.")
    object_map = {}  # {original_obj: new_obj}

    def _recursive_copy_and_map(source_coll, target_parent, suffix, obj_map):
        new_coll_name = f"{source_coll.name}{suffix}"
        new_coll, _ = get_or_create_collection(new_coll_name, target_parent, source_coll.color_tag)
        if not new_coll: return None

        for obj in source_coll.objects:
            if obj not in obj_map:
                new_obj = obj.copy()
                if obj.data: new_obj.data = obj.data.copy()
                new_obj.name = f"{obj.name}{suffix}"
                obj_map[obj] = new_obj
            
            new_obj_instance = obj_map.get(obj)
            if new_obj_instance and new_obj_instance.name not in new_coll.objects:
                new_coll.objects.link(new_obj_instance)

        for child in source_coll.children:
            _recursive_copy_and_map(child, new_coll, suffix, obj_map)
        return new_coll

    def _remap_relationships(obj_map):
        log.info(f"Remapping relationships for {len(obj_map)} copied objects.")
        for orig_obj, new_obj in obj_map.items():
            if orig_obj.parent and orig_obj.parent in obj_map:
                new_obj.parent = obj_map[orig_obj.parent]
            for c in new_obj.constraints:
                if hasattr(c, 'target') and c.target in obj_map: c.target = obj_map[c.target]
            for m in new_obj.modifiers:
                if hasattr(m, 'object') and m.object in obj_map: m.object = obj_map[m.object]

    top_level_new_coll = _recursive_copy_and_map(original_coll, target_parent_coll, name_suffix, object_map)
    if not top_level_new_coll:
        log.error("Hierarchy copy failed at the top level.")
        return None
        
    _remap_relationships(object_map)
    return top_level_new_coll


# =============================================================================
# --- 4. PERSISTENT HANDLERS ---
# =============================================================================

@persistent
def update_shot_collection_visibility(scene: Scene, depsgraph=None):
    """
    Handler for COPIED collections. Runs on frame change to dynamically hide/show
    collections based on custom scene data.
    """
    visibility_data_str = scene.get("kls_shot_visibility_data")
    if not visibility_data_str: return

    try:
        visibility_data = json.loads(visibility_data_str)
    except json.JSONDecodeError:
        return

    current_frame = scene.frame_current
    view_layer = bpy.context.view_layer
    collections_to_show, collections_to_hide = set(), set()
    
    for shot_info in visibility_data:
        original_coll, shot_coll = shot_info.get("original_collection"), shot_info.get("shot_collection")

        if not bpy.data.collections.get(shot_coll): continue

        is_in_range = shot_info['start_frame'] <= current_frame <= shot_info['end_frame']
        
        if is_in_range:
            collections_to_show.add(shot_coll)
            if original_coll: collections_to_hide.add(original_coll)
        else:
            collections_to_hide.add(shot_coll)
            if original_coll: collections_to_show.add(original_coll)

    final_to_hide = collections_to_hide - collections_to_show
    for coll_name in final_to_hide: set_collection_exclude(view_layer, coll_name, True)
    for coll_name in collections_to_show: set_collection_exclude(view_layer, coll_name, False)

@persistent
def isolate_current_shot_collections_handler(scene: Scene, depsgraph=None):
    """
    Performance-intensive handler. Dynamically isolates collections related to the
    current shot based on the timeline playhead. Should only be active when toggled by the user.
    """
    if not scene or not hasattr(scene, 'timeline_markers'): return
        
    current_frame = scene.frame_current
    view_layer = bpy.context.view_layer

    sorted_markers = sorted([m for m in scene.timeline_markers if m.name.startswith("CAM-SC")], key=lambda m: m.frame)
    
    active_marker = next((m for m in reversed(sorted_markers) if m.frame <= current_frame), None)

    if not active_marker:
        return

    match = re.search(r"(SC\d+-SH\d+)", active_marker.name, re.IGNORECASE)
    if not match: return
    current_shot_id = match.group(1).upper()

    shot_patterns = (re.compile(r"MODEL-SC\d+-SH\d+"), re.compile(r"CAM-SC\d+-SH\d+"), re.compile(r"VFX-SC\d+-SH\d+"))

    for collection in bpy.data.collections:
        if any(p.fullmatch(collection.name) for p in shot_patterns):
            is_current = current_shot_id in collection.name.upper()
            set_collection_exclude(view_layer, collection.name, exclude_status=not is_current)


# =============================================================================
# --- 5. OPERATORS ---
# =============================================================================

# --- Section 5.1: Scene and Collection Initialization ---

class KLS_OT_CreateLocationStructure(Operator):
    """Builds the primary LOCATION collection structure."""
    bl_idname = "scene.kls_create_location_structure"
    bl_label = "Setup LOCATION Collections"
    bl_description = "Creates collection structure for a LOCATION (LOC-) scene"

    def execute(self, context):
        scene, base_name = context.scene, context.scene.name
        if not base_name.upper().startswith("LOC-"):
            self.report({"ERROR"}, "Scene name must start with 'LOC-'.")
            return {"CANCELLED"}
        
        parent_col, _ = get_or_create_collection(f"+{base_name}+", scene.collection, COLLECTION_COLORS["LOCATION"])
        get_or_create_collection(f"TERRAIN-{base_name}", parent_col)
        get_or_create_collection(f"MODEL-{base_name}", parent_col)
        get_or_create_collection(f"VFX-{base_name}", parent_col)
        self.report({"INFO"}, f"Verified LOCATION structure for '{base_name}'.")
        return {"FINISHED"}

class KLS_OT_CreateEnviroStructure(Operator):
    """Builds the ENVIRONMENT collection structure."""
    bl_idname = "scene.kls_create_enviro_structure"
    bl_label = "Setup ENVIRO Collections"
    bl_description = "Creates collection structure for an ENVIRONMENT (ENV-) scene"

    def execute(self, context):
        scene, base_name = context.scene, context.scene.name
        if not base_name.upper().startswith("ENV-"):
            self.report({"ERROR"}, "Scene name must start with 'ENV-'.")
            return {"CANCELLED"}
        
        parent_col, _ = get_or_create_collection(f"+{base_name}+", scene.collection, COLLECTION_COLORS["ENVIRO"])
        get_or_create_collection(f"MODEL-{base_name}", parent_col)
        get_or_create_collection(f"VFX-{base_name}", parent_col)
        self.report({"INFO"}, f"Verified ENVIRO structure for '{base_name}'.")
        return {"FINISHED"}

class KLS_OT_CreateSceneStructure(Operator):
    """Builds the main SCENE collection structure and links relevant LOCATION/ENVIROs."""
    bl_idname = "scene.kls_create_scene_structure"
    bl_label = "Setup SCENE Collections"
    bl_description = "Creates SCENE (SC##-) collections and links dependencies"

    def execute(self, context):
        scene, base_name, master_collection = context.scene, context.scene.name, context.scene.collection
        
        match = re.match(r"^(SC\d+)-(.+)", base_name, re.IGNORECASE)
        if not match:
            self.report({"ERROR"}, "Scene name must be 'SC##-<env_name>'.")
            return {"CANCELLED"}

        _, scene_env_name = match.groups()
        sc_parent_col, _ = get_or_create_collection(f"+{base_name}+", master_collection, COLLECTION_COLORS["SCENE"])

        art_col, _ = get_or_create_collection(f"+ART-{base_name}+", sc_parent_col, COLLECTION_COLORS["ART"])
        get_or_create_collection(f"MODEL-{base_name}", art_col); get_or_create_collection(f"SHOT-ART-{base_name}", art_col)

        ani_col, _ = get_or_create_collection(f"+ANI-{base_name}+", sc_parent_col, COLLECTION_COLORS["ANI"])
        get_or_create_collection(f"ACTOR-{base_name}", ani_col); get_or_create_collection(f"PROP-{base_name}", ani_col); get_or_create_collection(f"SHOT-ANI-{base_name}", ani_col)

        vfx_col, _ = get_or_create_collection(f"+VFX-{base_name}+", sc_parent_col, COLLECTION_COLORS["VFX"])
        get_or_create_collection(f"VFX-{base_name}", vfx_col); get_or_create_collection(f"SHOT-VFX-{base_name}", vfx_col)

        # Link dependencies
        for coll in bpy.data.collections:
            enviro_match = re.match(r"^\+ENV-(.+)\+$", coll.name)
            if enviro_match and enviro_match.group(1) in scene_env_name and coll.name not in master_collection.children:
                master_collection.children.link(coll)
        loc_coll = next((c for c in bpy.data.collections if c.name.startswith("+LOC-")), None)
        if loc_coll and loc_coll.name not in master_collection.children:
            master_collection.children.link(loc_coll)
        
        self.report({"INFO"}, f"Verified SCENE structure for '{base_name}'.")
        return {"FINISHED"}


# --- Section 5.2: Animatic and Camera Setup ---

class KLS_OT_ImportAnimaticGuides(Operator):
    """Scans a directory, creates a scene if needed, and imports animatic videos."""
    bl_idname = "sequencer.kls_import_animatic_guides"
    bl_label = "Import/Update Animatic Guides"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(name="Scene Directory", subtype="DIR_PATH")

    def execute(self, context):
        scene_path = self.directory
        if not os.path.isdir(scene_path):
            self.report({"ERROR"}, "Invalid directory selected.")
            return {"CANCELLED"}

        scene_name = os.path.basename(os.path.normpath(scene_path))
        if not scene_name.upper().startswith("SC"):
            self.report({"ERROR"}, f"Directory '{scene_name}' must start with 'SC'.")
            return {"CANCELLED"}

        guide_files = sorted([os.path.join(dp, f) for dp, _, fn in os.walk(scene_path) for f in fn if "-guide-" in f.lower() and f.lower().endswith((".mp4", ".mov"))])
        if not guide_files:
            self.report({"WARNING"}, f"No guide files found in '{scene_path}'.")
            return {"FINISHED"}

        blender_scene = bpy.data.scenes.get(scene_name) or bpy.data.scenes.new(name=scene_name)
        if blender_scene.name not in context.window.scene.name: # If newly created
             with context.temp_override(scene=blender_scene):
                 bpy.ops.scene.kls_create_scene_structure()
        
        if not blender_scene.sequence_editor: blender_scene.sequence_editor_create()
        seq_editor = blender_scene.sequence_editor

        # Clean up old content before import
        for s in list(seq_editor.sequences_all):
            path = getattr(s, 'filepath', None) or getattr(getattr(s, 'sound', None), 'filepath', None)
            if path and "-guide-" in os.path.basename(path).lower(): seq_editor.sequences.remove(s)
        for m in list(blender_scene.timeline_markers):
            if "CAM-" in m.name or m.name == "END": blender_scene.timeline_markers.remove(m)
        
        current_frame = 1
        target_channel = max((s.channel for s in seq_editor.sequences_all), default=0) + 1
        vse_area = next((a for a in context.screen.areas if a.type == 'SEQUENCE_EDITOR'), None)
        
        for video_path in guide_files:
            with context.temp_override(window=context.window, area=vse_area, scene=blender_scene):
                bpy.ops.sequencer.movie_strip_add(filepath=video_path, frame_start=current_frame, channel=target_channel)
            
            new_strip = next((s for s in reversed(seq_editor.sequences_all) if s.frame_start == current_frame), None)
            if not new_strip: continue

            match = re.search(r"(sc\d+).+?(sh\d+)", os.path.basename(video_path), re.IGNORECASE)
            if match:
                blender_scene.timeline_markers.new(f"CAM-{match.group(1).upper()}-{match.group(2).upper()}", frame=current_frame)
            current_frame += new_strip.frame_final_duration

        blender_scene.frame_end = current_frame - 1
        blender_scene.timeline_markers.new("END", frame=current_frame)
        self.report({"INFO"}, f"Animatic import for '{scene_name}' finished.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

class KLS_OT_SetupCamerasFromMarkers(Operator):
    """Creates shot collections and appends camera rigs for each timeline marker."""
    bl_idname = "scene.kls_setup_cameras_from_markers"
    bl_label = "Setup Shots from Markers"
    bl_description = "Creates cameras and collections based on timeline markers"

    def execute(self, context):
        scene, base_name, prefs = context.scene, context.scene.name, context.preferences.addons[__name__].preferences
        
        win_path, linux_path = prefs.camera_hero_path_windows, prefs.camera_hero_path_linux
        cam_hero_path = win_path if os.path.exists(win_path) else (linux_path if os.path.exists(linux_path) else None)
        
        if not cam_hero_path:
            self.report({"ERROR"}, "Camera hero file not found. Set path in Addon Preferences.")
            return {"CANCELLED"}
        
        shot_ani_coll = bpy.data.collections.get(f"SHOT-ANI-{base_name}")
        shot_art_coll = bpy.data.collections.get(f"SHOT-ART-{base_name}")
        shot_vfx_coll = bpy.data.collections.get(f"SHOT-VFX-{base_name}")
        
        if not all([shot_ani_coll, shot_art_coll, shot_vfx_coll]):
            self.report({"ERROR"}, "Parent SHOT collections not found. Run 'Setup SCENE Collections' first.")
            return {"CANCELLED"}

        valid_markers = sorted([m for m in scene.timeline_markers if m.name.startswith("CAM-")], key=lambda m: m.frame)
        for i, marker in enumerate(valid_markers):
            match = re.match(r"CAM-(SC\d+)-(SH\d+)$", marker.name, re.IGNORECASE)
            if not match: continue

            sc_id, sh_id = match.groups()
            cam_coll_name = f"CAM-{sc_id.upper()}-{sh_id.upper()}"
            if cam_coll_name in shot_ani_coll.children: continue

            get_or_create_collection(f"MODEL-{sc_id.upper()}-{sh_id.upper()}", shot_art_coll)
            get_or_create_collection(f"VFX-{sc_id.upper()}-{sh_id.upper()}", shot_vfx_coll)

            try:
                with bpy.data.libraries.load(cam_hero_path, link=False) as (data_from, data_to):
                    data_to.collections = [c for c in data_from.collections if c == CAMERA_COLLECTION_TO_APPEND]
                
                if not data_to.collections:
                    self.report({"ERROR"}, f"'{CAMERA_COLLECTION_TO_APPEND}' not in hero file.")
                    return {"CANCELLED"}

                appended_coll = data_to.collections[0]
                appended_coll.name = cam_coll_name
                shot_ani_coll.children.link(appended_coll)
                
                new_suffix = f"-{sc_id.upper()}-{sh_id.upper()}"
                rig_object = None
                for obj in appended_coll.all_objects:
                    obj.name = obj.name.replace("cam_flat", f"CAM{new_suffix}-FLAT") \
                                       .replace("cam_fulldome", f"CAM{new_suffix}-FULLDOME") \
                                       .replace("+cam_rig", f"+cam_rig{new_suffix}")
                    if obj.type == 'ARMATURE': rig_object = obj
                if rig_object: rig_object.location.x = i * 2.0
                
            except Exception as e:
                log.error(f"Error processing marker '{marker.name}': {e}", exc_info=True)
                continue

        self.report({"INFO"}, f"Processed {len(valid_markers)} markers.")
        return {"FINISHED"}

class KLS_OT_BindCamerasToMarkers(Operator):
    """Binds cameras of a specific type to their corresponding timeline markers."""
    bl_idname = "scene.kls_bind_cameras_to_markers"
    bl_label = "Bind Cameras"
    bl_options = {"REGISTER", "UNDO"}

    camera_type: EnumProperty(items=[("FLAT", "Flat", ""), ("FULLDOME", "Fulldome", "")])

    def execute(self, context):
        scene = context.scene
        if not scene.timeline_markers: return {"CANCELLED"}
        
        scene.render.resolution_x, scene.render.resolution_y = (1920, 1080) if self.camera_type == "FLAT" else (2048, 2048)

        marker_dict = {m.name.upper(): m for m in scene.timeline_markers}
        cam_pattern = re.compile(rf"CAM-(SC\d+)-(SH\d+)-{self.camera_type}", re.IGNORECASE)

        bound_count = 0
        for cam in [obj for obj in scene.objects if obj.type == "CAMERA" and self.camera_type in obj.name.upper()]:
            match = cam_pattern.search(cam.name)
            if not match: continue
            marker_name = f"CAM-{match.group(1).upper()}-{match.group(2).upper()}"
            if marker_name in marker_dict:
                marker_dict[marker_name].camera = cam
                bound_count += 1
        
        self.report({"INFO"}, f"Bound {bound_count} {self.camera_type} camera(s).")
        return {"FINISHED"}


# --- Section 5.3: Advanced Copy and Move Operators ---

class KLS_OT_CopyToShot(Operator):
    """Copies the datablock to a shot and manages its visibility."""
    bl_idname = "object.kls_copy_to_shot"
    bl_label = "Copy to Shot"
    bl_options = {'REGISTER', 'UNDO'}

    target_shot_collection: StringProperty()

    def execute(self, context):
        datablock, db_type = get_active_datablock(context)
        if not datablock: return {'CANCELLED'}
        
        target_coll = bpy.data.collections.get(self.target_shot_collection)
        if not target_coll:
            self.report({'ERROR'}, f"Target collection '{self.target_shot_collection}' not found.")
            return {'CANCELLED'}

        match = re.search(r"-(SC\d+)-(SH\d+)", target_coll.name)
        if not match:
            self.report({'ERROR'}, f"Target collection '{target_coll.name}' has an invalid name.")
            return {'CANCELLED'}
        name_suffix = f"-{match.group(1)}-{match.group(2)}"
        
        start_frame, end_frame = get_shot_frame_range(target_coll.name)
        if start_frame is None:
            self.report({'ERROR'}, f"No marker found for '{target_coll.name}'.")
            return {'CANCELLED'}

        if db_type == 'OBJECT':
            new_db = datablock.copy()
            if datablock.data: new_db.data = datablock.data.copy()
            new_db.name = f"{datablock.name}{name_suffix}"
            target_coll.objects.link(new_db)
            
            controller = ObjectVisibilityController(context.scene)
            controller.add_copy(datablock.name, new_db.name, start_frame, end_frame)
            rebuild_object_visibility_animation(context.scene, datablock.name)
        else:  # COLLECTION
            new_db = copy_collection_hierarchy(datablock, target_coll, name_suffix)
            if not new_db: return {'CANCELLED'}
            
            vis_data = json.loads(context.scene.get("kls_shot_visibility_data", "[]"))
            vis_data.append({"original_collection": datablock.name, "shot_collection": new_db.name, "start_frame": start_frame, "end_frame": end_frame})
            context.scene["kls_shot_visibility_data"] = json.dumps(vis_data, indent=2)
            update_shot_collection_visibility(context.scene)

        self.report({'INFO'}, f"Copied '{datablock.name}' to '{new_db.name}'.")
        return {'FINISHED'}

class KLS_OT_MoveToAllShots(Operator):
    """Moves an item to all relevant shot collections and removes the original."""
    bl_idname = "object.kls_move_to_all_shots"
    bl_label = "Move to All Shots"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, db_type = get_active_datablock(context)
        if not datablock: return {'CANCELLED'}
        
        source_coll = get_source_collection(datablock)
        prefix = "MODEL" if "MODEL" in source_coll.name else "VFX"
        shot_collections = sorted([c for c in bpy.data.collections if c.name.startswith(f"{prefix}-SC")], key=lambda c: c.name)

        if not shot_collections:
            self.report({'WARNING'}, f"No '{prefix}' shot collections found.")
            return {'CANCELLED'}
        
        for target_coll in shot_collections:
            match = re.search(r"-(SC\d+)-(SH\d+)", target_coll.name)
            if not match: continue
            name_suffix = f"-{match.group(1)}-{match.group(2)}"
            
            if db_type == 'OBJECT':
                new_db = datablock.copy()
                if datablock.data: new_db.data = datablock.data.copy()
                new_db.name = f"{datablock.name}{name_suffix}"
                target_coll.objects.link(new_db)
            else: # COLLECTION
                copy_collection_hierarchy(datablock, target_coll, name_suffix)

        # Remove original
        if db_type == 'OBJECT': bpy.data.objects.remove(datablock, do_unlink=True)
        else: bpy.data.collections.remove(datablock)
        
        self.report({'INFO'}, f"Moved to {len(shot_collections)} shot collections.")
        return {'FINISHED'}

class KLS_OT_MoveToAllScenes(Operator):
    """Copies an item from an ENV collection to all matching SCENE collections and removes original."""
    bl_idname = "object.kls_move_to_all_scenes"
    bl_label = "Move to All Matching Scenes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, db_type = get_active_datablock(context)
        if not datablock: return {'CANCELLED'}
        
        source_collection = get_source_collection(datablock)
        if not source_collection or not (source_collection.name.startswith("MODEL-ENV") or source_collection.name.startswith("VFX-ENV")):
            self.report({'ERROR'}, "Item must be in a 'MODEL-ENV...' or 'VFX-ENV...' collection.")
            return {'CANCELLED'}
        
        enviro_name_match = re.search(r"ENV-(.+)", source_collection.name)
        if not enviro_name_match: return {'CANCELLED'}
        enviro_name = enviro_name_match.group(1)
        
        prefix = "MODEL" if source_collection.name.startswith("MODEL") else "VFX"
        matching_scenes = [s for s in bpy.data.scenes if s.name.startswith("SC") and enviro_name in s.name]
        
        copied_count = 0
        for scene in matching_scenes:
            target_coll_name = f"{prefix}-{scene.name}"
            target_coll = bpy.data.collections.get(target_coll_name)
            if target_coll:
                if db_type == 'OBJECT':
                    new_obj = datablock.copy(); new_obj.data = datablock.data.copy(); target_coll.objects.link(new_obj)
                else:
                    copy_collection_hierarchy(datablock, target_coll)
                copied_count += 1

        if copied_count > 0:
            if db_type == 'OBJECT': bpy.data.objects.remove(datablock, do_unlink=True)
            else: bpy.data.collections.remove(datablock)
            self.report({'INFO'}, f"Moved to {copied_count} matching scenes.")
        else:
            self.report({'WARNING'}, "No matching scenes found.")
            
        return {'FINISHED'}

class KLS_OT_CopyToAllEnviros(Operator):
    """Copies an item from a LOC collection to each ENV collection and removes original."""
    bl_idname = "object.kls_copy_to_all_enviros"
    bl_label = "-> to each ENV"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, db_type = get_active_datablock(context)
        if not datablock: return {'CANCELLED'}
        
        source_collection = get_source_collection(datablock)
        if not source_collection or not (source_collection.name.startswith("MODEL-LOC") or source_collection.name.startswith("VFX-LOC")):
            self.report({'ERROR'}, "Item must be in a 'MODEL-LOC...' or 'VFX-LOC...' collection.")
            return {'CANCELLED'}
        
        prefix = "MODEL" if source_collection.name.startswith("MODEL") else "VFX"
        all_env_parent_colls = [c for c in bpy.data.collections if c.name.startswith("+ENV-")]
        
        copied_count = 0
        for env_parent in all_env_parent_colls:
            base_name = env_parent.name.strip('+')
            target_sub_coll = env_parent.children.get(f"{prefix}-{base_name}")
            if target_sub_coll:
                if db_type == 'OBJECT':
                    new_obj = datablock.copy(); new_obj.data = datablock.data.copy(); target_sub_coll.objects.link(new_obj)
                else:
                    copy_collection_hierarchy(datablock, target_sub_coll)
                copied_count += 1

        if copied_count > 0:
            if db_type == 'OBJECT': bpy.data.objects.remove(datablock, do_unlink=True)
            else: bpy.data.collections.remove(datablock)
            self.report({'INFO'}, f"Copied to {copied_count} environments.")
        return {'FINISHED'}


# =============================================================================
# --- 6. UI: PANELS AND MENUS ---
# =============================================================================

class KLS_PT_MainPanel(Panel):
    """The main UI panel for the addon in the 3D View's N-Panel."""
    bl_label = "Krutart Master Suite"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Layout"

    def draw(self, context):
        layout = self.layout
        scene_name = context.scene.name.upper()

        if scene_name.startswith("LOC-"):
            box = layout.box(); box.label(text="Location Tools", icon="WORLD_DATA"); box.operator(KLS_OT_CreateLocationStructure.bl_idname)
        elif scene_name.startswith("ENV-"):
            box = layout.box(); box.label(text="Environment Tools", icon="OUTLINER_OB_LIGHTPROBE"); box.operator(KLS_OT_CreateEnviroStructure.bl_idname)
        elif re.match(r"^SC\d+-", scene_name):
            box = layout.box(); box.label(text="1. Initial Scene Setup", icon="SCENE_DATA"); box.operator(KLS_OT_CreateSceneStructure.bl_idname)
            box = layout.box(); box.label(text="2. Animatic & Markers", icon="SEQUENCE"); box.operator(KLS_OT_ImportAnimaticGuides.bl_idname, text="Import/Update Guides", icon="FILE_FOLDER")
            box = layout.box(); box.label(text="3. Camera Management", icon="CAMERA_DATA"); box.operator(KLS_OT_SetupCamerasFromMarkers.bl_idname, icon="CAMERA_DATA"); box.menu(KLS_MT_BindCamerasMenu.bl_idname, icon="LINKED")
            
            # --- Performance Optimization Toggle ---
            box = layout.box()
            box.label(text="Viewport Tools", icon="HIDE_ON")
            box.prop(context.scene, "kls_isolate_shot_view", text="Isolate Active Shot")
        else:
            box = layout.box(); box.label(text="Scene Naming Instructions:", icon='INFO'); box.label(text="Rename scene to use tools:"); box.label(text="- 'LOC-<name>'"); box.label(text="- 'ENV-<name>'"); box.label(text="- 'SC##-<name>'")

class KLS_MT_BindCamerasMenu(Menu):
    """Menu for camera binding operators."""
    bl_label = "Bind Cameras"
    bl_idname = "SCENE_MT_kls_bind_cameras_menu"

    def draw(self, context):
        layout = self.layout
        op_flat = layout.operator(KLS_OT_BindCamerasToMarkers.bl_idname, text="All FLAT"); op_flat.camera_type = "FLAT"
        op_fulldome = layout.operator(KLS_OT_BindCamerasToMarkers.bl_idname, text="All FULLDOME"); op_fulldome.camera_type = "FULLDOME"

class KLS_MT_CopyToShotMenu(Menu):
    """Dynamically lists available shot collections for the 'Copy to Shot' operator."""
    bl_idname = "OBJECT_MT_kls_copy_to_shot_menu"
    bl_label = "Copy to Shot"

    def draw(self, context):
        layout = self.layout
        datablock, _ = get_active_datablock(context)
        if not datablock: return
        
        scene_match = re.match(r"^(SC\d+)-", context.scene.name, re.IGNORECASE)
        if not scene_match: return
        current_sc_id = scene_match.group(1).upper()
        
        source_collection = get_source_collection(datablock)
        if not source_collection: return
        
        prefix = "MODEL" if "MODEL" in source_collection.name else "VFX"
        pattern = re.compile(rf"^{prefix}-{current_sc_id}-SH\d+$")
        shot_collections = sorted([c for c in bpy.data.collections if pattern.match(c.name)], key=lambda c: c.name)

        if not shot_collections:
            layout.label(text=f"No '{prefix}' shots for {current_sc_id}")
            return

        for coll in shot_collections:
            op = layout.operator(KLS_OT_CopyToShot.bl_idname, text=coll.name)
            op.target_shot_collection = coll.name

def add_advanced_copy_context_menus(self, context):
    """Draws the advanced copy menu items in context-sensitive menus."""
    datablock, _ = get_active_datablock(context)
    if not datablock: return
    
    layout = self.layout
    layout.separator()
    if is_in_shot_build_collection(datablock):
        layout.menu(KLS_MT_CopyToShotMenu.bl_idname, icon='COPYDOWN')
        layout.operator(KLS_OT_MoveToAllShots.bl_idname, icon='GHOST_ENABLED')
    
    source_collection = get_source_collection(datablock)
    if source_collection:
        if source_collection.name.startswith(("MODEL-ENV", "VFX-ENV")):
            layout.operator(KLS_OT_MoveToAllScenes.bl_idname, icon='SCENE_DATA')
        if source_collection.name.startswith(("MODEL-LOC", "VFX-LOC")):
            layout.operator(KLS_OT_CopyToAllEnviros.bl_idname, icon='CON_TRANSLIKE')
    layout.separator()


# =============================================================================
# --- 7. REGISTRATION ---
# =============================================================================

# --- Handler Management for Performance Toggle ---
def toggle_isolate_shot_handler(self, context):
    """Registers or unregisters the performance-heavy handler based on the UI toggle."""
    handler = isolate_current_shot_collections_handler
    handlers_list = bpy.app.handlers.frame_change_post
    is_registered = handler in handlers_list

    if context.scene.kls_isolate_shot_view and not is_registered:
        handlers_list.append(handler)
        log.info("Registered 'Isolate Active Shot' handler.")
    elif not context.scene.kls_isolate_shot_view and is_registered:
        handlers_list.remove(handler)
        log.info("Unregistered 'Isolate Active Shot' handler.")

classes = (
    KLS_Preferences, KLS_OT_CreateLocationStructure, KLS_OT_CreateEnviroStructure,
    KLS_OT_CreateSceneStructure, KLS_OT_ImportAnimaticGuides, KLS_OT_SetupCamerasFromMarkers,
    KLS_OT_BindCamerasToMarkers, KLS_OT_CopyToShot, KLS_OT_MoveToAllShots,
    KLS_OT_MoveToAllScenes, KLS_OT_CopyToAllEnviros, KLS_PT_MainPanel,
    KLS_MT_BindCamerasMenu, KLS_MT_CopyToShotMenu
)

def register():
    log.info("Registering Krutart Master Suite.")
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # --- Register Scene Properties ---
    Scene.kls_shot_visibility_data = StringProperty(default="[]")
    Scene.kls_object_visibility_controller = StringProperty(default="{}")
    Scene.kls_isolate_shot_view = BoolProperty(
        name="Isolate Active Shot",
        description="Automatically hide collections not in the current shot. Can impact performance.",
        default=False,
        update=toggle_isolate_shot_handler
    )
    
    # --- Register Handlers and Menus ---
    if update_shot_collection_visibility not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(update_shot_collection_visibility)
    
    for menu_type in (bpy.types.OUTLINER_MT_collection, bpy.types.OUTLINER_MT_object, bpy.types.VIEW3D_MT_object_context_menu):
        menu_type.append(add_advanced_copy_context_menus)

def unregister():
    log.info("Unregistering Krutart Master Suite.")
    # --- Unregister Handlers and Menus first ---
    if update_shot_collection_visibility in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(update_shot_collection_visibility)
    if isolate_current_shot_collections_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(isolate_current_shot_collections_handler)
    
    for menu_type in (bpy.types.OUTLINER_MT_collection, bpy.types.OUTLINER_MT_object, bpy.types.VIEW3D_MT_object_context_menu):
        menu_type.remove(add_advanced_copy_context_menus)

    # --- Unregister Classes and Properties ---
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    for prop in ("kls_shot_visibility_data", "kls_object_visibility_controller", "kls_isolate_shot_view"):
        if hasattr(Scene, prop):
            delattr(Scene, prop)

if __name__ == "__main__":
    register()
