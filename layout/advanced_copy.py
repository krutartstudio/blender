bl_info = {
    "name": "Advanced Copy",
    "author": "iori, Krutart, Gemini",
    "version": (1, 5, 1),
    "blender": (4, 5, 0),
    "location": "Outliner > Right-Click Menu, 3D View > Right-Click Menu",
    "description": "Provides specific hierarchy traversal copy/move functionalities with dynamic shot-based collection visibility.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import re
import logging
import json
from bpy.props import StringProperty
from bpy.app.handlers import persistent

# --- Configure Logging ---
# Provides clear feedback in the system console for artists and developers.
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- Object Visibility Controller ---

class ObjectVisibilityController:
    """
    A helper class to manage object visibility data stored in a scene property.
    This provides a persistent, safe way to track which objects are copies
    of a master and what their active frame ranges are.
    """
    PROP_NAME = "object_visibility_controller"

    def __init__(self, scene):
        self.scene = scene
        self.data = self._load()

    def _load(self):
        """Loads and decodes the visibility data from the scene property."""
        data_str = self.scene.get(self.PROP_NAME, "{}")
        try:
            return json.loads(data_str)
        except json.JSONDecodeError:
            log.error("Could not decode object visibility data. Resetting to empty.")
            return {}

    def _save(self):
        """Encodes and saves the visibility data back to the scene property."""
        self.scene[self.PROP_NAME] = json.dumps(self.data, indent=2)

    def add_copy(self, original_name, copy_name, start_frame, end_frame):
        """Adds or updates a copy entry for a given original object."""
        if original_name not in self.data:
            self.data[original_name] = []
        
        # Check if this copy already exists to update its range
        for entry in self.data[original_name]:
            if entry['copy_name'] == copy_name:
                log.warning(f"Copy '{copy_name}' already managed for '{original_name}'. Overwriting entry.")
                entry['start_frame'] = start_frame
                entry['end_frame'] = end_frame
                self._save()
                return

        new_entry = {
            "copy_name": copy_name,
            "start_frame": start_frame,
            "end_frame": end_frame
        }
        self.data[original_name].append(new_entry)
        self._save()
        log.info(f"Added visibility entry: {original_name} -> {copy_name} ({start_frame}-{end_frame})")

    def get_copies(self, original_name):
        """Retrieves all copy information for a given original object."""
        return self.data.get(original_name, [])


# --- Visibility Animation Helpers ---

def _clear_visibility_keyframes(obj):
    """Removes all hide_viewport and hide_render keyframes from an object."""
    if obj.animation_data and obj.animation_data.action:
        fcurves = obj.animation_data.action.fcurves
        for fcurve in list(fcurves):  # Iterate over a copy to allow removal
            if fcurve.data_path in ("hide_viewport", "hide_render"):
                fcurves.remove(fcurve)

def _set_visibility_keyframe(obj, frame, is_visible):
    """Inserts visibility keyframes for an Object, ensuring constant interpolation."""
    obj.hide_viewport = not is_visible
    obj.hide_render = not is_visible
    
    # Insert keyframes and set their interpolation to CONSTANT for a clean on/off switch
    for path in ["hide_viewport", "hide_render"]:
        # keyframe_insert() returns True on success in modern Blender versions.
        success = obj.keyframe_insert(data_path=path, frame=frame)
        
        # If a keyframe was added, find it and set its interpolation mode.
        if success and obj.animation_data and obj.animation_data.action:
            fcurve = obj.animation_data.action.fcurves.find(path)
            if fcurve:
                for point in fcurve.keyframe_points:
                    # Use a small tolerance for float comparison
                    if abs(point.co.x - frame) < 0.001:
                        point.interpolation = 'CONSTANT'
                        break # Found it, exit the inner loop

def rebuild_object_visibility_animation(scene, original_obj_name):
    """
    Completely rebuilds the visibility animation for an original object and all its copies
    based on the centrally managed data. This is the single source of truth for object keyframing.
    """
    log.info(f"Rebuilding visibility animation for master object '{original_obj_name}' and its copies.")
    controller = ObjectVisibilityController(scene)
    
    original_obj = bpy.data.objects.get(original_obj_name)
    if not original_obj:
        log.error(f"Cannot rebuild animation: Original object '{original_obj_name}' not found.")
        return

    copy_infos = controller.get_copies(original_obj_name)
    
    # --- 1. Clear old state and keyframe the copies ---
    _clear_visibility_keyframes(original_obj)
    
    active_ranges = []
    
    for info in copy_infos:
        copy_obj = bpy.data.objects.get(info['copy_name'])
        if not copy_obj:
            log.warning(f"Managed copy '{info['copy_name']}' not found in scene. Skipping.")
            continue
            
        _clear_visibility_keyframes(copy_obj)
        
        start, end = info['start_frame'], info['end_frame']
        
        # Copy is visible ONLY during its range
        _set_visibility_keyframe(copy_obj, start - 1, is_visible=False)
        _set_visibility_keyframe(copy_obj, start, is_visible=True)
        _set_visibility_keyframe(copy_obj, end + 1, is_visible=False)
        
        active_ranges.append((start, end))

    # --- 2. Keyframe the original based on all copy ranges ---
    if not active_ranges:
        _set_visibility_keyframe(original_obj, scene.frame_start, is_visible=True)
        log.info(f"No active copies for '{original_obj_name}'. Setting it to be always visible.")
        return

    active_ranges.sort(key=lambda r: r[0])

    # Merge overlapping/contiguous ranges to simplify keyframing
    merged_ranges = [active_ranges[0]]
    for current_start, current_end in active_ranges[1:]:
        last_start, last_end = merged_ranges[-1]
        if current_start <= last_end + 1:
            merged_ranges[-1] = (last_start, max(last_end, current_end))
        else:
            merged_ranges.append((current_start, current_end))
            
    log.info(f"Original '{original_obj_name}' will be hidden during these merged frame ranges: {merged_ranges}")

    # Set initial state for original object
    _set_visibility_keyframe(original_obj, scene.frame_start, is_visible=True)
    
    # Keyframe the transitions for being hidden
    for start, end in merged_ranges:
        _set_visibility_keyframe(original_obj, start, is_visible=False)
        _set_visibility_keyframe(original_obj, end + 1, is_visible=True)

# --- General Helper Functions ---

def get_active_datablock(context):
    """
    Determines the active datablock (object or collection) from the context.
    """
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
    """
    Recursively copies a collection and its contents, then remaps object relationships.
    """
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

def get_shot_collections(prefix="MODEL"):
    """Scans the blend file for collections matching the shot naming convention."""
    pattern = re.compile(rf"^{prefix}-SC\d+-SH\d+$")
    return sorted([c for c in bpy.data.collections if pattern.match(c.name)], key=lambda c: c.name)

def get_project_scenes():
    """Retrieves all scenes matching the 'SC##-' naming convention."""
    pattern = re.compile(r"^SC\d+-.*")
    return sorted([s for s in bpy.data.scenes if pattern.match(s.name)], key=lambda s: s.name)

def get_shot_frame_range(shot_name):
    """Finds the start and end frame for a shot based on timeline markers."""
    match = re.search(r"SC(\d+)-SH(\d+)", shot_name)
    if not match:
        log.warning(f"Could not parse shot name '{shot_name}' for frame range.")
        return None, None

    sc_id_num, sh_id_num = int(match.group(1)), int(match.group(2))
    marker_name_pattern = re.compile(rf"CAM-SC{sc_id_num:02d}-SH{sh_id_num:03d}", re.IGNORECASE)

    sorted_markers = sorted(bpy.context.scene.timeline_markers, key=lambda m: m.frame)
    start_marker = next((m for m in sorted_markers if marker_name_pattern.fullmatch(m.name)), None)
    
    if not start_marker:
        log.warning(f"Start marker matching '{marker_name_pattern.pattern}' not found.")
        return None, None

    try:
        start_marker_index = sorted_markers.index(start_marker)
        if start_marker_index + 1 < len(sorted_markers):
            next_marker = sorted_markers[start_marker_index + 1]
            return start_marker.frame, next_marker.frame - 1
    except ValueError:
        log.error("Could not find start marker in sorted list.")
        return None, None
    
    log.warning(f"Could not find end marker for '{start_marker.name}'. Using scene end.")
    return start_marker.frame, bpy.context.scene.frame_end

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

def animate_object_visibility(obj, start_frame, end_frame):
    """Animates visibility for a standalone object (used in 'move' operations)."""
    log.info(f"Animating visibility for object '{obj.name}' from frame {start_frame} to {end_frame}.")
    _set_visibility_keyframe(obj, start_frame - 1, is_visible=False)
    _set_visibility_keyframe(obj, start_frame, is_visible=True)
    _set_visibility_keyframe(obj, end_frame + 1, is_visible=False)

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

# --- Dynamic Collection Visibility Handler ---

@persistent
def update_shot_collection_visibility(scene, depsgraph=None):
    """
    Handler that runs on frame change. Reads scene data and sets collection 'exclude' status dynamically.
    """
    visibility_data_str = scene.get("shot_visibility_data")
    if not visibility_data_str: return

    try:
        visibility_data = json.loads(visibility_data_str)
    except json.JSONDecodeError:
        log.error("Could not parse shot visibility data.")
        return

    current_frame = scene.frame_current
    view_layer = bpy.context.view_layer

    collections_to_show, collections_to_hide = [], []
    
    for shot_info in visibility_data:
        original_coll, shot_coll = shot_info.get("original_collection"), shot_info.get("shot_collection")

        if not bpy.data.collections.get(shot_coll): continue

        is_in_range = shot_info['start_frame'] <= current_frame <= shot_info['end_frame']
        
        if original_coll:  # "Copy to Shot" entry
            if is_in_range:
                collections_to_hide.append(original_coll)
                collections_to_show.append(shot_coll)
            else:
                collections_to_show.append(original_coll)
                collections_to_hide.append(shot_coll)
        else:  # "Move to All Shots" entry
            if is_in_range:
                collections_to_show.append(shot_coll)
            else:
                collections_to_hide.append(shot_coll)
            
    final_to_hide = set(collections_to_hide) - set(collections_to_show)
    final_to_show = set(collections_to_show)

    for coll_name in final_to_hide: set_collection_exclude(view_layer, coll_name, True)
    for coll_name in final_to_show: set_collection_exclude(view_layer, coll_name, False)


# --- Main Operator Classes ---

class ADVCOPY_OT_copy_to_shot(bpy.types.Operator):
    """Copies the datablock to a shot. Manages visibility dynamically for collections and via a controller for objects."""
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
        
        scene_match = re.search(r"-SC(\d+)-", target_coll.name)
        shot_match = re.search(r"-SH(\d+)", target_coll.name)
        name_suffix = f"-{f'SC{scene_match.group(1)}' if scene_match else ''}-{f'SH{shot_match.group(1)}' if shot_match else ''}"

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

        start_frame, end_frame = get_shot_frame_range(target_coll.name)
        if start_frame is None or end_frame is None:
            self.report({'WARNING'}, f"Could not determine frame range for '{target_coll.name}'. Visibility not managed.")
            return {'FINISHED'}

        scene = context.scene
        if datablock_type == 'COLLECTION':
            visibility_data = json.loads(scene.get("shot_visibility_data", "[]"))
            shot_info = {
                "original_collection": datablock.name, "shot_collection": new_datablock.name,
                "start_frame": start_frame, "end_frame": end_frame
            }
            visibility_data.append(shot_info)
            scene["shot_visibility_data"] = json.dumps(visibility_data, indent=2)
            log.info(f"Registered dynamic visibility for '{datablock.name}' -> '{new_datablock.name}'.")
            update_shot_collection_visibility(scene)
        
        elif datablock_type == 'OBJECT':
            # --- NEW ROBUST OBJECT VISIBILITY WORKFLOW ---
            controller = ObjectVisibilityController(scene)
            controller.add_copy(
                original_name=datablock.name, copy_name=new_datablock.name,
                start_frame=start_frame, end_frame=end_frame
            )
            rebuild_object_visibility_animation(scene, datablock.name)

        self.report({'INFO'}, f"Copied '{datablock.name}' to '{new_datablock.name}' in '{target_coll.name}'.")
        return {'FINISHED'}

class ADVCOPY_OT_move_to_all_shots(bpy.types.Operator):
    """Moves the selected item to all relevant shot collections, applying visibility rules, then removes the original."""
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
        shot_collections = get_shot_collections(prefix=prefix)
        if not shot_collections:
            self.report({'WARNING'}, f"No '{prefix}' shot collections found.")
            return {'CANCELLED'}
        
        scene = context.scene
        visibility_data = json.loads(scene.get("shot_visibility_data", "[]"))
        
        copied_count, data_was_modified = 0, False
        
        for target_coll in shot_collections:
            scene_match = re.search(r"-SC(\d+)-", target_coll.name)
            shot_match = re.search(r"-SH(\d+)", target_coll.name)
            name_suffix = f"-SC{scene_match.group(1)}-SH{shot_match.group(1)}" if scene_match and shot_match else "-moved"

            new_datablock = None
            if datablock_type == 'OBJECT':
                new_datablock = datablock.copy()
                if datablock.data: new_datablock.data = datablock.data.copy()
                new_datablock.name = f"{datablock_name}{name_suffix}"
                target_coll.objects.link(new_datablock)
            elif datablock_type == 'COLLECTION':
                new_datablock = copy_collection_hierarchy(datablock, target_coll, name_suffix)
            
            if not new_datablock: continue

            start_frame, end_frame = get_shot_frame_range(target_coll.name)
            if start_frame is not None and end_frame is not None:
                if datablock_type == 'COLLECTION':
                    shot_info = {
                        "shot_collection": new_datablock.name, "start_frame": start_frame, "end_frame": end_frame
                    }
                    visibility_data.append(shot_info)
                    data_was_modified = True
                elif datablock_type == 'OBJECT':
                    animate_object_visibility(new_datablock, start_frame, end_frame)
            
            copied_count += 1
            
        if copied_count > 0:
            if data_was_modified:
                scene["shot_visibility_data"] = json.dumps(visibility_data, indent=2)
                log.info(f"Updated visibility data for {copied_count} moved collection(s).")

            log.info(f"Removing original datablock '{datablock_name}'")
            if datablock_type == 'OBJECT':
                bpy.data.objects.remove(datablock, do_unlink=True)
            elif datablock_type == 'COLLECTION':
                bpy.data.collections.remove(datablock)

            self.report({'INFO'}, f"Moved '{datablock_name}' to {copied_count} shot collection(s).")
            update_shot_collection_visibility(scene)
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "Move operation did not copy to any shots.")
            return {'CANCELLED'}

# ... (The rest of the operators: ADVCOPY_OT_move_to_all_scenes, ADVCOPY_OT_copy_to_all_enviros, can remain the same) ...

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
    """Dynamically lists all available shot collections for copying."""
    bl_idname = "ADVCOPY_MT_copy_to_shot_menu"
    bl_label = "Copy to Shot"

    def draw(self, context):
        layout = self.layout
        datablock, _ = get_active_datablock(context)
        if not datablock: return

        source_collection = get_source_collection(datablock)
        if not source_collection: return
        
        prefix = "MODEL" if "MODEL" in source_collection.name else "VFX"
        shot_collections = get_shot_collections(prefix=prefix)
        if not shot_collections:
            layout.label(text="No Shot Collections Found")
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
    
    # Property for collection visibility data
    bpy.types.Scene.shot_visibility_data = StringProperty(
        name="Shot Visibility Data",
        description="Internal data for managing collection visibility per shot",
        default="[]"
    )

    # NEW property for the object visibility controller
    bpy.types.Scene.object_visibility_controller = StringProperty(
        name="Object Visibility Controller",
        description="Internal data for managing object visibility copies per shot",
        default="{}"
    )
    
    if update_shot_collection_visibility not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(update_shot_collection_visibility)

    bpy.types.OUTLINER_MT_collection.append(add_context_menus)
    bpy.types.OUTLINER_MT_object.append(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.append(add_context_menus)

def unregister():
    if update_shot_collection_visibility in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(update_shot_collection_visibility)

    for prop_name in ["shot_visibility_data", "object_visibility_controller"]:
        try:
            delattr(bpy.types.Scene, prop_name)
        except AttributeError:
            pass

    bpy.types.OUTLINER_MT_collection.remove(add_context_menus)
    bpy.types.OUTLINER_MT_object.remove(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.remove(add_context_menus)
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

