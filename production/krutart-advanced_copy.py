bl_info = {
    "name": "Krutart Advanced Copy",
    "author": "iori, Krutart, Gemini",
    "version": (2, 6, 0), # Implements persistent 1-to-1 copy mapping
    "blender": (4, 2, 0),
    "location": "Outliner > Right-Click Menu, 3D View > 'N' Panel > Layout Suite",
    "description": "Provides specific hierarchy traversal copy/move functionalities with dynamic, high-performance, shot-based collection visibility. Correctly duplicates overrides (not-localize) and fixes visibility cache bugs from ENV/LOC operations.",
    "warning": "This version uses a new persistent mapping system stored in a Text Block ('__krutart_copy_map.json').",
    "doc_url": "",
    "category": "Object",
}

import bpy
import re
import logging
import json # <-- ADDED for persistent mapping
from bpy.props import StringProperty, BoolProperty
from bpy.app.handlers import persistent

# --- Configure Logging ---
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- NEW: Persistent Copy Map Helpers (Phase 1) ---

KRUTART_VISIBILITY_MAP_NAME = "__krutart_copy_map.json"

def load_copy_map():
    """
    Loads the persistent copy map from the blend file's Text Blocks.
    Returns a dictionary: { "shot_copy_name": "original_name", ... }
    """
    text_block = bpy.data.texts.get(KRUTART_VISIBILITY_MAP_NAME)
    if not text_block:
        return {}

    json_data = text_block.as_string()
    if not json_data:
        return {}

    try:
        copy_map = json.loads(json_data)
        if isinstance(copy_map, dict):
            return copy_map
        else:
            log.warning(f"'{KRUTART_VISIBILITY_MAP_NAME}' does not contain a valid JSON object. Resetting map.")
            return {}
    except json.JSONDecodeError:
        log.warning(f"Could not parse JSON from '{KRUTART_VISIBILITY_MAP_NAME}'. Resetting map.")
        return {}

def save_copy_map(copy_map_dict):
    """
    Saves the copy map dictionary back to the blend file's Text Blocks
    as a JSON string.
    """
    try:
        text_block = bpy.data.texts.get(KRUTART_VISIBILITY_MAP_NAME)
        if not text_block:
            text_block = bpy.data.texts.new(KRUTART_VISIBILITY_MAP_NAME)
        
        # This is critical to ensure the map saves with the .blend file
        text_block.use_fake_user = True
        
        json_data = json.dumps(copy_map_dict, indent=2)
        
        text_block.clear()
        text_block.write(json_data)
        log.debug(f"Saved {len(copy_map_dict)} mappings to '{KRUTART_VISIBILITY_MAP_NAME}'.")

    except Exception as e:
        log.error(f"Failed to save copy map: {e}")

# --- End of New Helpers ---


# --- Shot Visibility Cache & Helpers ---
# Global caches for performance.
shot_switch_map = {} # Maps frame -> shot_id for timeline scrubbing.
# Maps shot_id -> {set of original bpy.types.Object or .Collection instances}
originals_to_hide_map = {}
# Cache to quickly find original items by their full name.
# Maps full_name_str -> bpy.types.Object or .Collection
original_items_cache = {}
cached_scene_name = None # Tracks the scene the cache was built for.

def get_shot_identifier(name):
    """Extracts 'SC##-SH###' from a collection or marker name."""
    if not name: return None
    match = re.search(r"(SC\d+-SH\d+)", name, re.IGNORECASE)
    return match.group(1).upper() if match else None

# --- DELETED (Phase 4) ---
# The problematic get_base_name function has been removed.
# ---

def get_all_shot_collections():
    """Scans the blend file for all collections matching the shot naming convention."""
    # --- MODIFIED --- Added 'PRP' to the pattern
    pattern = re.compile(r"^(MODEL|CAM|VFX|PRP)-SC\d+-SH\d+$", re.IGNORECASE)
    return [c for c in bpy.data.collections if pattern.match(c.name)]

def _collect_all_items_recursive(collection, collected_items_set):
    """
    Recursively collects all objects and child collections from a starting collection.
    This is the new helper function to support deep scanning.
    """
    if not collection:
        return

    try:
        # Add all objects from this collection
        for obj in collection.objects:
            if obj: # Check if obj is not None
                collected_items_set.add(obj)
        
        # Add all child collections and recurse
        for child_coll in collection.children:
            if child_coll: # Check if child_coll is not None
                collected_items_set.add(child_coll)
                _collect_all_items_recursive(child_coll, collected_items_set)

    except ReferenceError:
        # This can happen if a collection is deleted mid-operation
        log.warning(f"ReferenceError while scanning collection '{collection.name}'. It may be broken or deleted.")


@persistent
def build_visibility_data(scene):
    """
    Builds all necessary caches for high-performance visibility updates.
    1. Scans timeline markers to map frames to shot IDs (shot_switch_map).
    2. (NEW) Uses the persistent 1-to-1 copy map to determine which original items
       need to be hidden for each shot (originals_to_hide_map).
    """
    global shot_switch_map, cached_scene_name, originals_to_hide_map, original_items_cache
    
    # --- Part 1: Build Shot Switch Map (existing logic, unchanged) ---
    shot_switch_map.clear()
    if not scene or not hasattr(scene, 'timeline_markers'):
        log.warning("build_visibility_data: Called with an invalid scene.")
        cached_scene_name = None
        return

    marker_pattern = re.compile(r"CAM-SC\d+-SH\d+", re.IGNORECASE)
    shot_markers = [m for m in scene.timeline_markers if marker_pattern.match(m.name)]
    for marker in shot_markers:
        shot_id = get_shot_identifier(marker.name)
        if shot_id:
            shot_switch_map[marker.frame] = shot_id
    cached_scene_name = scene.name
    log.info(f"Shot cache rebuilt for scene '{scene.name}'. Found {len(shot_switch_map)} switch frames.")

    # --- Part 2: Build Original Items Visibility Map (NEW LOGIC - Phase 4) ---
    
    originals_to_hide_map.clear()
    original_items_cache.clear()
    
    # 1. Load our persistent 1-to-1 map
    # This map is {"shot_copy_name": "original_name", ...}
    copy_map = load_copy_map()
    if not copy_map:
        log.warning("Visibility map is empty. No originals will be hidden.")
        return

    # 2. Build a simple cache of all data.blocks by their *full name*.
    #    We only cache the *originals* we need, as defined in our map.
    all_original_names = set(copy_map.values())
    
    for name in all_original_names:
        item = bpy.data.objects.get(name) or bpy.data.collections.get(name)
        if item:
            original_items_cache[name] = item
        else:
            log.debug(f"Persistent map references original '{name}', but it's not in the scene. Will be ignored.")

    # 3. Scan shot collections and map them to originals using our new map
    for shot_coll in get_all_shot_collections():
        coll_shot_id = get_shot_identifier(shot_coll.name)
        if not coll_shot_id:
            continue
        
        # Recursively find ALL items within this shot collection hierarchy.
        all_items_in_shot = set()
        _collect_all_items_recursive(shot_coll, all_items_in_shot)
        
        for shot_item in all_items_in_shot:
            # Use our persistent map to find the original's name (1-to-1)
            original_item_name = copy_map.get(shot_item.name)
            
            if original_item_name:
                # Find the original item from our new cache
                original_item = original_items_cache.get(original_item_name)
                
                if original_item:
                    # We found a valid shot_item -> original_item link
                    if coll_shot_id not in originals_to_hide_map:
                        originals_to_hide_map[coll_shot_id] = set()
                    
                    if original_item not in originals_to_hide_map[coll_shot_id]:
                        originals_to_hide_map[coll_shot_id].add(original_item)
                        log.debug(f"Mapped shot item '{shot_item.name}' to original '{original_item.name}' for shot {coll_shot_id}")
    
    # --- End of New Logic ---

    log.info(f"Originals visibility map rebuilt using persistent 1-to-1 map. Found originals for {len(originals_to_hide_map)} shots. Cache size: {len(original_items_cache)} items.")

@persistent
def build_visibility_data_on_load(dummy):
    """Wrapper for the load_post handler."""
    if bpy.context.scene:
        build_visibility_data(bpy.context.scene)


# --- Dynamic Collection Visibility Handler ---

def set_item_visibility(view_layer, item, visible):
    """
    Sets the visibility for an object or a collection within a specific view layer.
    This is safer than directly manipulating properties and handles different data types.
    """
    if not item: return

    try:
        # Check if item still exists
        if item.name not in bpy.data.objects and item.name not in bpy.data.collections:
            log.warning(f"Could not set visibility for '{item.name}'. It may no longer exist.")
            return
            
        if isinstance(item, bpy.types.Object):
            # Use hide_set() for objects, as it's the modern, correct method.
            if item.hide_get() == visible:
                item.hide_set(not visible)
            if item.hide_render == visible:
                item.hide_render = not visible
        elif isinstance(item, bpy.types.Collection):
            
            # Find the "original" LayerCollection in the build hierarchy
            layer_coll = find_original_layer_collection(view_layer.layer_collection, item)

            # --- REMOVED recursive fallback logic per user request ---

            # --- Original Logic (applies to ALL collections) ---
            # Try to hide the collection itself. This works for regular collections
            # and is the "correct" action for overridden ones.
            if layer_coll and layer_coll.exclude == visible:
                new_exclude_state = not visible
                layer_coll.exclude = new_exclude_state

                # --- ADDED logging per user request ---
                if item.override_library:
                    # Log to info panel (which is 'log.info' as configured)
                    log.info(f"Set .exclude = {new_exclude_state} on overridden collection '{item.name}'")
                else:
                    # Keep debug log for regular collections
                    log.debug(f"Attempting to set exclude={new_exclude_state} on LayerCollection '{item.name}'")
                        
            elif not layer_coll:
                log.debug(f"Could not find a 'build' instance for collection '{item.name}' to hide/unhide.")
            # --- END MODIFIED LOGIC ---
            
    except (ReferenceError, RuntimeError):
        # Item might have been deleted; the cache will be rebuilt later.
        log.warning(f"Could not set visibility for '{item.name}'. It may no longer exist.")

@persistent
def on_frame_change_update_visibility(scene, depsgraph=None):
    """
    Handler that runs on frame change. Uses pre-built caches for high performance.
    1. Toggles visibility of shot collections based on the active shot.
    2. Toggles visibility of original "build" items if a copy exists in the active shot.
    """
    if not scene.auto_shot_exclusion:
        return # Do nothing if the user has disabled the system

    global cached_scene_name
    
    if scene != bpy.context.scene:
        return

    if scene.name != cached_scene_name:
        build_visibility_data(scene)

    if not shot_switch_map:
        return

    current_frame = scene.frame_current
    view_layer = bpy.context.view_layer

    active_shot_id = None
    relevant_frames = [f for f in shot_switch_map.keys() if f <= current_frame]
    if relevant_frames:
        latest_switch_frame = max(relevant_frames)
        active_shot_id = shot_switch_map[latest_switch_frame]

    last_active_shot = getattr(bpy.context.window_manager, "active_shot_id", None)
    
    if active_shot_id != last_active_shot:
        bpy.context.window_manager.active_shot_id = active_shot_id
        log.info(f"Frame {current_frame}: Shot changed to '{active_shot_id}'. Updating visibility.")

        # --- Logic Part 1: Manage visibility of the SHOT collections (existing logic) ---
        all_shot_colls = get_all_shot_collections()
        for coll in all_shot_colls:
            coll_shot_id = get_shot_identifier(coll.name)
            is_active = (coll_shot_id is not None and coll_shot_id == active_shot_id)
            set_collection_exclude(view_layer, coll.name, not is_active)

        #--- Logic Part 2: Manage visibility of the ORIGINAL items ---
        items_to_hide_now = originals_to_hide_map.get(active_shot_id, set())
        items_that_were_hidden = originals_to_hide_map.get(last_active_shot, set())

        # Unhide items that were hidden for the last shot but shouldn't be for this one.
        items_to_unhide = items_that_were_hidden - items_to_hide_now
        for item in items_to_unhide:
            set_item_visibility(view_layer, item, True)

        # Hide items that are originals of copies present in the current active shot.
        for item in items_to_hide_now:
            set_item_visibility(view_layer, item, False)

# --- General Helper Functions ---

def get_datablock_from_context(context):
    """
    Determines the target datablock from the context, prioritizing what was right-clicked.
    This function is designed to work for both menu drawing and operator execution by
    checking context attributes in a specific, robust order.
    """
    # 1. Prioritize context.id, which is often set for the item under the cursor in UI contexts.
    if hasattr(context, 'id') and context.id:
        item = context.id
        if isinstance(item, bpy.types.Collection):
            log.debug(f"Context target identified via context.id: Collection '{item.name}'")
            return item, 'COLLECTION'
        if isinstance(item, bpy.types.Object):
            log.debug(f"Context target identified via context.id: Object '{item.name}'")
            return item, 'OBJECT'

    # 2. Check selected_ids, reliable for operator execution context after a click.
    if hasattr(context, 'selected_ids') and context.selected_ids:
        target_id = context.selected_ids[0]
        if isinstance(target_id, bpy.types.Collection):
            log.debug(f"Context target identified via selected_ids: Collection '{target_id.name}'")
            return target_id, 'COLLECTION'
        if isinstance(target_id, bpy.types.Object):
            log.debug(f"Context target identified via selected_ids: Object '{target_id.name}'")
            return target_id, 'OBJECT'

    # 3. Fallback to active object.
    active_obj = context.active_object
    if active_obj:
        log.debug(f"Context target identified via active_object: '{active_obj.name}'")
        return active_obj, 'OBJECT'
    
    # 4. Fallback to active collection in the Outliner.
    if context.view_layer and context.view_layer.active_layer_collection:
        active_coll = context.view_layer.active_layer_collection.collection
        log.debug(f"Context target identified via active_layer_collection: '{active_coll.name}'")
        return active_coll, 'COLLECTION'
        
    # This log is commented out to prevent spamming the console when the cursor is over empty space.
    # log.warning("Could not determine a target datablock from the context.")
    return None, None

def copy_collection_hierarchy(original_coll, target_parent_coll, name_suffix=""):
    """
    Recursively performs a DEEP COPY (localization) or DUPLICATE (override)
    of a collection and its contents, then remaps object relationships.
    All copies preserve their original names.
    
    MODIFIED (Phase 2): Returns (top_level_new_coll, item_map)
    item_map is a dict { orig_item: new_item } for ALL copied items (objects and collections)
    """
    # MODIFIED (Phase 2): Renamed object_map to item_map
    item_map = {}  # Maps original item -> new item (objects AND collections)

    # --- MODIFICATION START (VERSION 2.5.3) ---
    # This new helper function just *maps* an existing override hierarchy
    # that was created by .copy(). It does *not* link anything.
    def _map_copied_override_hierarchy(source_coll, new_coll, item_map): # MODIFIED: item_map
        """
        Recursively maps objects and collections from a source override hierarchy
        to a new override hierarchy that was just created via .copy().
        This is used to populate the item_map for relationship remapping.
        """
        # MODIFIED (Phase 2): Map collection itself and prevent re-mapping
        if source_coll in item_map:
            return # Already mapped
        item_map[source_coll] = new_coll
        log.debug(f"  Mapped orig coll '{source_coll.name}' to new '{new_coll.name}'")

        # 1. Map objects
        if len(source_coll.objects) == len(new_coll.objects):
            for i, orig_obj in enumerate(source_coll.objects):
                if orig_obj not in item_map:
                    new_obj = new_coll.objects[i]
                    item_map[orig_obj] = new_obj # MODIFIED: item_map
                    log.debug(f"  Mapped orig obj '{orig_obj.name}' to new '{new_obj.name}'")
        else:
            log.warning(f"Object count mismatch in override map: '{source_coll.name}' ({len(source_coll.objects)}) vs '{new_coll.name}' ({len(new_coll.objects)}). Relationship remapping may fail.")
            
        # 2. Map child collections and recurse
        if len(source_coll.children) == len(new_coll.children):
            for i, orig_child_coll in enumerate(source_coll.children):
                new_child_coll = new_coll.children[i]
                # Recurse into the child hierarchy
                # MODIFIED: pass item_map
                _map_copied_override_hierarchy(orig_child_coll, new_child_coll, item_map)
        else:
             log.warning(f"Collection count mismatch in override map: '{source_coll.name}' ({len(source_coll.children)}) vs '{new_coll.name}' ({len(new_coll.children)}). Relationship remapping may fail.")
    # --- END NEW HELPER ---

    def _recursive_copy_and_map(source_coll, target_parent, suffix, item_map): # MODIFIED: item_map
        # The 'suffix' parameter is no longer used, but kept for call signature.
        
        # --- MODIFICATION START (VERSION 2.5.3) ---
        # Logic is now split. We handle overrides and regular collections differently.
        
        if source_coll.override_library:
            # --- This is an OVERRIDE ---
            # Use source_coll.copy() to create a new, duplicate override.
            # This single command copies the *entire* internal hierarchy.
            log.debug(f"Copying '{source_coll.name}' as a new override.")
            new_coll = source_coll.copy()
            
            # 2. Link the new *top-level* collection to its new parent.
            target_parent.children.link(new_coll)
            
            # 3. FIX: Recursively *map* (don't copy/link) the entire
            #    hierarchy that .copy() just created so we can remap
            #    relationships later.
            log.debug(f"Populating item map from new override hierarchy '{new_coll.name}'...")
            # MODIFIED: pass item_map
            _map_copied_override_hierarchy(source_coll, new_coll, item_map)
            # We are DONE. We do not recurse further to link anything.

        else:
            # --- This is a REGULAR, non-overridden collection ---
            # Use the original deep-copy (localization) logic.
            log.debug(f"Deep-copying '{source_coll.name}' as a new collection.")
            new_coll_name = source_coll.name # Preserve name
            new_coll = bpy.data.collections.new(new_coll_name)
            
            # MODIFIED (Phase 2): Map the collection itself
            item_map[source_coll] = new_coll

            # 2. Link the new collection to its new parent in the hierarchy.
            target_parent.children.link(new_coll)
            new_coll.color_tag = source_coll.color_tag
            
            # 3. (Original Logic) Deep copy all objects from the source collection.
            for obj in source_coll.objects:
                if obj not in item_map: # MODIFIED: item_map
                    new_obj = obj.copy()  # This correctly creates a new override if obj is one.
                    if obj.data:
                        # This also correctly creates a new override if data is one.
                        new_obj.data = obj.data.copy()

                    # --- THIS IS THE FIX ---
                    # Preserve the original name for all objects.
                    new_obj.name = obj.name  
                    # --- END FIX ---
                    
                    item_map[obj] = new_obj  # MODIFIED: item_map. Store the mapping

            # 4. (Original Logic) Link the newly created deep-copied objects to our new collection.
            #    This is SAFE because new_coll is NOT an override.
            for obj in source_coll.objects:
                new_obj = item_map.get(obj) # MODIFIED: item_map
                if new_obj and new_obj.name not in new_coll.objects:
                    new_coll.objects.link(new_obj)

            # 5. Recurse for all child collections.
            #    This ONLY happens for regular (non-override) collections.
            for child in source_coll.children:
                # MODIFIED: pass item_map
                _recursive_copy_and_map(child, new_coll, suffix, item_map)

        # --- END OF MODIFICATION (VERSION 2.5.3) ---

        return new_coll
    # --- End of _recursive_copy_and_map helper function ---

    def _remap_relationships(item_map): # MODIFIED: item_map
        log.info(f"Remapping relationships for {len(item_map)} copied items...")
        # MODIFIED: Renamed loop variables
        for orig_item, new_item in item_map.items():
            
            # MODIFIED (Phase 2): Skip collections, they don't have these properties
            if not isinstance(new_item, bpy.types.Object):
                continue
            
            # Parent remapping
            if orig_item.parent and orig_item.parent in item_map: # MODIFIED: item_map
                new_item.parent = item_map[orig_item.parent] # MODIFIED: item_map
                new_item.parent_type = orig_item.parent_type
                if orig_item.parent_type == 'BONE':
                    new_item.parent_bone = orig_item.parent_bone

            # Constraint target remapping
            for constraint in new_item.constraints:
                if hasattr(constraint, 'target') and constraint.target and constraint.target in item_map: # MODIFIED: item_map
                    constraint.target = item_map[constraint.target] # MODIFIED: item_map
                
                if hasattr(constraint, 'targets'):
                    for subtarget in constraint.targets:
                        if subtarget.target and subtarget.target in item_map: # MODIFIED: item_map
                            subtarget.target = item_map[subtarget.target] # MODIFIED: item_map

            # Modifier target remapping
            for modifier in new_item.modifiers:
                mod_obj_props = ['object', 'target', 'source_object', 'camera', 'curve']
                for prop in mod_obj_props:
                    if hasattr(modifier, prop):
                        mod_obj = getattr(modifier, prop)
                        if mod_obj and mod_obj in item_map: # MODIFIED: item_map
                            setattr(modifier, prop, item_map[mod_obj]) # MODIFIED: item_map

    # --- Main execution of the function ---
    # MODIFIED: pass item_map
    top_level_new_coll = _recursive_copy_and_map(original_coll, target_parent_coll, name_suffix, item_map)
    _remap_relationships(item_map) # MODIFIED: pass item_map
    
    log.info("Hierarchy copy and remapping complete.")
    # MODIFIED (Phase 2): Return both the new collection and the full item map
    return top_level_new_coll, item_map

def get_project_scenes():
    """Retrieves all scenes matching the 'SC##-' naming convention."""
    pattern = re.compile(r"^SC\d+-.*", re.IGNORECASE)
    return sorted([s for s in bpy.data.scenes if pattern.match(s.name)], key=lambda s: s.name)

# --- NEW HELPER FUNCTIONS START ---

def is_in_build_hierarchy(layer_coll):
    """
    Checks if a LayerCollection is part of an 'original' hierarchy,
    i.e., NOT part of a 'shot' hierarchy (MODEL-SC##-SH###, etc.).
    """
    # --- MODIFICATION START ---
    # --- MODIFIED --- Added 'PRP' to the pattern
    shot_pattern = re.compile(r"^(MODEL|CAM|VFX|PRP)-SC\d+-SH\d+$", re.IGNORECASE)
    current = layer_coll
    
    # Check self and parents
    while current:
        if current.collection and shot_pattern.match(current.collection.name):
            # It's inside a shot collection, so it's NOT an original "build" instance.
            return False
        
        # --- FIX --- (This fix was already present)
        # Check if current has a parent attribute before accessing it.
        # The root LayerCollection (view_layer.layer_collection) does not have a .parent
        if hasattr(current, "parent"):
            current = current.parent
        else:
            # We are at the root, stop iterating.
            current = None
        # --- END FIX ---
    
    # If we reached the root and found no shot collection, it's an original.
    return True
    # --- MODIFICATION END ---

def find_original_layer_collection(layer_collection_root, collection_datablock):
    """
    Recursively finds the LayerCollection that uses collection_datablock
    AND is part of an original 'build' hierarchy.
    """
    if layer_collection_root.collection == collection_datablock:
        if is_in_build_hierarchy(layer_collection_root):
            return layer_collection_root
        # If not in build hierarchy, it's a shot-copy. Ignore it and keep searching.
    
    for child in layer_collection_root.children:
        found = find_original_layer_collection(child, collection_datablock)
        if found:
            return found
    return None

# --- NEW HELPER FUNCTIONS END ---

def find_layer_collection_by_name(layer_collection_root, name_to_find):
    """Recursively finds the LayerCollection corresponding to a given Collection name."""
    if layer_collection_root.collection.name == name_to_find:
        return layer_collection_root
    for child in layer_collection_root.children:
        found = find_layer_collection_by_name(child, name_to_find)
        if found:
            return found
    return None

def set_collection_exclude(view_layer, collection_name, exclude_status):
    """Safely finds a collection by name in the view layer and sets its exclude status."""
    if not collection_name or not bpy.data.collections.get(collection_name): return

    # --- MODIFICATION ---
    # User confirmed this part is working, so no changes made to the logic here.
    # The original recursive find is correct for this part.
    # ---
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

def get_item_and_containing_collection(item):
    """Returns the item itself and its immediate parent collection."""
    if isinstance(item, bpy.types.Object):
        return item, item.users_collection[0] if item.users_collection else bpy.context.scene.collection
    elif isinstance(item, bpy.types.Collection):
        for coll in bpy.data.collections:
            if item.name in coll.children:
                return item, coll
    return item, bpy.context.scene.collection

def is_in_shot_build_collection(item):
    """
    Recursively checks if an item is inside a collection whose name starts with '+SC', '+ART', etc.
    NOTE: This is the legacy check. The new cache builder uses a more direct prefix check.
    This is still used by the menu drawing function to decide *if* the menus should appear.
    """
    parent_map = {child: parent for parent in bpy.data.collections for child in parent.children}
    
    _, current_coll = get_item_and_containing_collection(item)

    while current_coll:
        if current_coll.name.startswith(("+SC", "+ART", "+ANI", "+VFX")):
            return True
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
        datablock, datablock_type = get_datablock_from_context(context)
        if not datablock:
            self.report({'ERROR'}, "No active or selected Object/Collection found.")
            return {'CANCELLED'}

        target_coll = bpy.data.collections.get(self.target_shot_collection)
        if not target_coll:
            self.report({'ERROR'}, f"Target shot collection '{self.target_shot_collection}' not found.")
            return {'CANCELLED'}

        log.info(f"Copying '{datablock.name}' ({datablock_type}) to '{target_coll.name}'.")
        
        # --- MODIFICATION START ---
        # shot_id and name_suffix logic is REMOVED
        # --- MODIFICATION END ---

        new_datablock = None
        if datablock_type == 'OBJECT':
            new_datablock = datablock.copy()
            if datablock.data: new_datablock.data = datablock.data.copy()
            
            # --- THIS IS THE FIX ---
            new_datablock.name = datablock.name # Preserve name
            # --- END FIX ---
            
            target_coll.objects.link(new_datablock)
            
            # --- MODIFIED (Phase 3): Save 1-to-1 mapping ---
            if new_datablock:
                try:
                    map_data = load_copy_map()
                    map_data[new_datablock.name] = datablock.name
                    save_copy_map(map_data)
                except Exception as e:
                    log.error(f"Failed to save copy map for object: {e}")
            # --- End Modification ---

        elif datablock_type == 'COLLECTION':
            # copy_collection_hierarchy now handles overrides correctly
            # MODIFIED (Phase 3): Get hierarchy_map back
            new_datablock, hierarchy_map = copy_collection_hierarchy(datablock, target_coll, "")

            # --- MODIFIED (Phase 3): Save 1-to-1 mapping for ENTIRE hierarchy ---
            if new_datablock and hierarchy_map:
                try:
                    map_data = load_copy_map()
                    for orig_item, new_item in hierarchy_map.items():
                        if new_item and orig_item: # Safety check
                            map_data[new_item.name] = orig_item.name
                    save_copy_map(map_data)
                except Exception as e:
                    log.error(f"Failed to save copy map for collection hierarchy: {e}")
            # --- End Modification ---

        if not new_datablock:
            self.report({'ERROR'}, "Failed to create a copy.")
            return {'CANCELLED'}

        build_visibility_data(context.scene)
        on_frame_change_update_visibility(context.scene)
        
        self.report({'INFO'}, f"Copied '{datablock.name}' to '{new_datablock.name}' in '{target_coll.name}'.")
        return {'FINISHED'}

class ADVCOPY_OT_move_to_all_shots(bpy.types.Operator):
    """Moves the selected item to all relevant shot collections, then removes the original."""
    bl_idname = "advanced_copy.move_to_all_shots"
    bl_label = "Move to All Shots"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, datablock_type = get_datablock_from_context(context)
        if not datablock:
            self.report({'ERROR'}, "No active or selected Object/Collection found.")
            return {'CANCELLED'}
        
        datablock_name = datablock.name
        source_collection = get_source_collection(datablock)
        if not source_collection:
            self.report({'ERROR'}, "Could not determine the source collection.")
            return {'CANCELLED'}
        
        # --- MODIFIED --- Added 'PRP' logic
        if "MODEL" in source_collection.name:
            prefix = "MODEL"
        elif "PRP" in source_collection.name:
            prefix = "PRP"
        else:
            prefix = "VFX" # Keep original fallback
        # --- END MODIFIED ---
            
        shot_pattern = re.compile(rf"^{prefix}-SC\d+-SH\d+$", re.IGNORECASE)
        shot_collections = sorted([c for c in bpy.data.collections if shot_pattern.match(c.name)], key=lambda c: c.name)

        if not shot_collections:
            self.report({'WARNING'}, f"No '{prefix}' shot collections found.")
            return {'CANCELLED'}
        
        copied_count = 0
        # --- MODIFIED (Phase 3): Load map once before looping ---
        try:
            map_data = load_copy_map()
        except Exception as e:
            log.error(f"Failed to load copy map before move: {e}")
            map_data = {}
            
        for target_coll in shot_collections:
            # --- MODIFICATION START ---
            # shot_id and name_suffix logic is REMOVED
            # --- MODIFICATION END ---

            new_datablock = None
            if datablock_type == 'OBJECT':
                new_datablock = datablock.copy()
                if datablock.data: new_datablock.data = datablock.data.copy()
                
                # --- THIS IS THE FIX ---
                new_datablock.name = datablock_name # Preserve original name
                # --- END FIX ---
                
                target_coll.objects.link(new_datablock)
                
                # --- MODIFIED (Phase 3): Add to map ---
                if new_datablock:
                    map_data[new_datablock.name] = datablock.name
                # --- End Modification ---

            elif datablock_type == 'COLLECTION':
                # copy_collection_hierarchy now handles overrides correctly
                # MODIFIED (Phase 3): Get hierarchy_map back
                new_datablock, hierarchy_map = copy_collection_hierarchy(datablock, target_coll, "")

                # --- MODIFIED (Phase 3): Add ENTIRE hierarchy to map ---
                if new_datablock and hierarchy_map:
                    for orig_item, new_item in hierarchy_map.items():
                        if new_item and orig_item: # Safety check
                            map_data[new_item.name] = orig_item.name
                # --- End Modification ---
            
            if not new_datablock: continue
            copied_count += 1
            
        if copied_count > 0:
            # --- MODIFIED (Phase 3): Save map once after looping ---
            try:
                save_copy_map(map_data)
            except Exception as e:
                log.error(f"Failed to save copy map after move: {e}")
            # --- End Modification ---

            log.info(f"Removing original datablock '{datablock_name}'")
            if datablock_type == 'OBJECT':
                bpy.data.objects.remove(datablock, do_unlink=True)
            elif datablock_type == 'COLLECTION':
                bpy.data.collections.remove(datablock)

            self.report({'INFO'}, f"Moved '{datablock_name}' to {copied_count} shot collection(s).")
            build_visibility_data(context.scene)
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
        datablock, datablock_type = get_datablock_from_context(context)
        if not datablock:
            self.report({'ERROR'}, "Operation requires an active or selected Object/Collection.")
            return {'CANCELLED'}

        source_collection = get_source_collection(datablock)
        # --- MODIFIED --- Added 'PRP-ENV' check
        if not source_collection or not (
            source_collection.name.startswith("MODEL-ENV") or 
            source_collection.name.startswith("VFX-ENV") or 
            source_collection.name.startswith("PRP-ENV")
        ):
            self.report({'ERROR'}, "Selected item must be in a 'MODEL-ENV...', 'VFX-ENV...', or 'PRP-ENV...' collection.")
            return {'CANCELLED'}
        
        enviro_name_match = re.search(r"ENV-(.+)", source_collection.name, re.IGNORECASE)
        if not enviro_name_match:
            self.report({'ERROR'}, f"Could not extract environment name from '{source_collection.name}'.")
            return {'CANCELLED'}
        enviro_name = enviro_name_match.group(1)
        
        # --- MODIFIED --- Added 'PRP' logic
        if source_collection.name.startswith("MODEL"):
            prefix = "MODEL"
        elif source_collection.name.startswith("PRP"):
            prefix = "PRP"
        else:
            prefix = "VFX" # Keep original fallback
        # --- END MODIFIED ---
            
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
                # --- MODIFIED --- Handle 'PRP' -> 'ART' mapping same as 'MODEL'
                parent_prefix = "ART" if (prefix == "MODEL" or prefix == "PRP") else "VFX"
                parent_coll = base_scene_coll.children.get(f"+{parent_prefix}-{scene.name}+")
                if parent_coll:
                    final_target_coll = parent_coll.children.get(f"{prefix}-{scene.name}")

            if final_target_coll:
                # --- MODIFICATION START ---
                # scene_suffix and name_suffix logic is REMOVED
                # --- MODIFICATION END ---
                
                if datablock_type == 'OBJECT':
                    new_obj = datablock.copy()
                    if datablock.data:
                        new_obj.data = datablock.data.copy()
                        
                    # --- THIS IS THE FIX ---
                    new_obj.name = datablock.name # Preserve name
                    # --- END FIX ---
                    
                    final_target_coll.objects.link(new_obj)
                elif datablock_type == 'COLLECTION':
                    # copy_collection_hierarchy now handles overrides correctly
                    # This returns a map, but we DON'T save it, per user request.
                    copy_collection_hierarchy(datablock, final_target_coll, "")
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

            # --- FIX 2: Rebuild cache after modifying build hierarchy ---
            build_visibility_data(context.scene)
            on_frame_change_update_visibility(context.scene)
            # --- End of Fix ---

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
        datablock, datablock_type = get_datablock_from_context(context)
        if not datablock:
            self.report({'ERROR'}, "Operation requires an active or selected Object/Collection.")
            return {'CANCELLED'}

        source_collection = get_source_collection(datablock)
        # --- MODIFIED --- Added 'PRP-LOC' check
        if not source_collection or not (
            source_collection.name.startswith("MODEL-LOC") or 
            source_collection.name.startswith("VFX-LOC") or
            source_collection.name.startswith("PRP-LOC")
        ):
            self.report({'ERROR'}, "Selected item must be in a 'MODEL-LOC...', 'VFX-LOC...', or 'PRP-LOC...' collection.")
            return {'CANCELLED'}
        
        # --- MODIFIED --- Added 'PRP' logic
        if source_collection.name.startswith("MODEL"):
            prefix = "MODEL"
        elif source_collection.name.startswith("PRP"):
            prefix = "PRP"
        else:
            prefix = "VFX" # Keep original fallback
        # --- END MODIFIED ---
            
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
                # --- MODIFICATION START ---
                # name_suffix and env_name_suffix_match logic is REMOVED
                # --- MODIFICATION END ---

                if datablock_type == 'OBJECT':
                    new_obj = datablock.copy()
                    if datablock.data:
                        new_obj.data = datablock.data.copy()
                        
                    # --- THIS IS THE FIX ---
                    new_obj.name = datablock.name # Preserve name
                    # --- END FIX ---
                    
                    target_sub_coll.objects.link(new_obj)
                elif datablock_type == 'COLLECTION':
                    # copy_collection_hierarchy now handles overrides correctly
                    # This returns a map, but we DON'T save it, per user request.
                    copy_collection_hierarchy(datablock, target_sub_coll, "")
                copied_count += 1
            else:
                log.warning(f"Could not find sub-collection '{target_sub_coll_name}' in '{env_parent_coll.name}'")

        if copied_count > 0:
            # --- FIX 2: Rebuild cache after modifying build hierarchy ---
            build_visibility_data(context.scene)
            on_frame_change_update_visibility(context.scene)
            # --- End of Fix ---

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

class ADVCOPY_OT_clear_original_visibility(bpy.types.Operator):
    """Resets the visibility of all original items that have been hidden by the shot system."""
    bl_idname = "advanced_copy.clear_original_visibility"
    bl_label = "Make All Originals Visible"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        log.info("Clearing visibility for all original items.")
        view_layer = context.view_layer

        # Collect all unique original items from the cache
        all_originals = set()
        for original_set in originals_to_hide_map.values():
            all_originals.update(original_set)

        if not all_originals:
            self.report({'INFO'}, "No original items are currently managed by the shot system.")
            return {'CANCELLED'}

        count = 0
        for item in all_originals:
            try:
                # Check if item still exists before trying to modify it
                if item and (item.name in bpy.data.objects or item.name in bpy.data.collections):
                    set_item_visibility(view_layer, item, True)
                    count += 1
            except ReferenceError:
                log.warning(f"Could not unhide item as it no longer exists. A cache rebuild is recommended.")
                pass

        self.report({'INFO'}, f"Made {count} original item(s) visible.")
        
        # Disable auto-exclusion so the user's action isn't immediately overridden.
        if context.scene.auto_shot_exclusion:
            context.scene.auto_shot_exclusion = False
            self.report({'INFO'}, "Auto Shot Visibility has been disabled to maintain visibility.")
            
        return {'FINISHED'}

class ADVCOPY_OT_rebuild_visibility_cache(bpy.types.Operator):
    """Manually rebuilds the shot visibility cache for the current scene."""
    bl_idname = "advanced_copy.rebuild_visibility_cache"
    bl_label = "Rebuild Visibility Cache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        log.info("Manual cache rebuild requested.")
        build_visibility_data(context.scene)
        self.report({'INFO'}, "Visibility cache has been rebuilt.")
        return {'FINISHED'}

# --- Dynamic Menus ---

class ADVCOPY_MT_copy_to_shot_menu(bpy.types.Menu):
    """Dynamically lists available shot collections from the current scene for copying."""
    bl_idname = "ADVCOPY_MT_copy_to_shot_menu"
    bl_label = "Copy to Shot (Current Scene)"

    def draw(self, context):
        layout = self.layout
        datablock, _ = get_datablock_from_context(context)
        if not datablock: return

        source_collection = get_source_collection(datablock)
        if not source_collection: return

        current_scene = context.scene
        scene_match = re.match(r"^(SC\d+)", current_scene.name, re.IGNORECASE)

        if not scene_match:
            layout.label(text="Scene must be named like 'SC##-...'")
            return
        
        current_scene_prefix = scene_match.group(1).upper()
        
        # --- MODIFIED --- Added 'PRP' logic
        if "MODEL" in source_collection.name:
            prefix = "MODEL"
        elif "PRP" in source_collection.name:
            prefix = "PRP"
        else:
            prefix = "VFX" # Keep original fallback
        # --- END MODIFIED ---
            
        shot_pattern = re.compile(rf"^{prefix}-SC\d+-SH\d+$", re.IGNORECASE)
        
        shot_collections = sorted(
            [
                c for c in bpy.data.collections 
                if shot_pattern.match(c.name) and c.name.upper().startswith(f"{prefix}-{current_scene_prefix}")
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
    datablock, _ = get_datablock_from_context(context)
    if not datablock: return
    
    layout = self.layout
    layout.separator()

    if is_in_shot_build_collection(datablock):
        layout.menu(ADVCOPY_MT_copy_to_shot_menu.bl_idname, icon='COPYDOWN')
        layout.operator(ADVCOPY_OT_move_to_all_shots.bl_idname, icon='GHOST_ENABLED')

    source_collection = get_source_collection(datablock)
    if source_collection:
        # --- MODIFIED --- Added 'PRP' checks
        if source_collection.name.startswith(("MODEL-ENV", "VFX-ENV", "PRP-ENV")):
            layout.operator(ADVCOPY_OT_move_to_all_scenes.bl_idname, icon='SCENE_DATA')
        if source_collection.name.startswith(("MODEL-LOC", "VFX-LOC", "PRP-LOC")):
            layout.operator(ADVCOPY_OT_copy_to_all_enviros.bl_idname, icon='CON_TRANSLIKE')
    layout.separator()


class ADVCOPY_PT_layout_suite_panel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport 'N' menu for visibility tools."""
    bl_label = "Advanced Copy"
    bl_idname = "ADVCOPY_PT_layout_suite"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Layout Suite'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        col = layout.column(align=True)
        col.prop(scene, "auto_shot_exclusion", text="Auto Shot Visibility", toggle=True)
        
        layout.separator()
        
        col = layout.column(align=True)
        col.label(text="Manual Controls:")
        col.operator(ADVCOPY_OT_clear_original_visibility.bl_idname, icon='HIDE_OFF')
        col.operator(ADVCOPY_OT_rebuild_visibility_cache.bl_idname, icon='FILE_REFRESH')


def update_auto_shot_exclusion(self, context):
    """
    Called when the auto_shot_exclusion property is changed.
    Resets visibility when turned off, or forces an update when turned on.
    """
    scene = context.scene
    if not scene.auto_shot_exclusion:
        log.info("Auto Shot Exclusion turned OFF. Enabling all shot collections for manual workflow.")
        view_layer = context.view_layer
        
        # Make all shot collections visible
        for coll in get_all_shot_collections():
            set_collection_exclude(view_layer, coll.name, False)
        
        # Unhide all possible original items that the system might have hidden
        all_originals = set()
        for original_set in originals_to_hide_map.values():
            all_originals.update(original_set)
        
        for item in all_originals:
            try:
                if item and (item.name in bpy.data.objects or item.name in bpy.data.collections):
                    set_item_visibility(view_layer, item, True)
            except ReferenceError:
                pass # Item no longer exists

        log.info("Manual visibility control restored.")
    else:
        log.info("Auto Shot Exclusion turned ON.")
        # Trigger an immediate update to apply the automatic visibility rules
        if on_frame_change_update_visibility in bpy.app.handlers.frame_change_pre:
             on_frame_change_update_visibility(scene)
    return None

# --- Registration ---
classes = (
    ADVCOPY_OT_copy_to_shot,
    ADVCOPY_OT_move_to_all_shots,
    ADVCOPY_OT_move_to_all_scenes,
    ADVCOPY_OT_copy_to_all_enviros,
    ADVCOPY_OT_clear_original_visibility,
    ADVCOPY_OT_rebuild_visibility_cache,
    ADVCOPY_MT_copy_to_shot_menu,
    ADVCOPY_PT_layout_suite_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.WindowManager.active_shot_id = StringProperty(
        name="Active Shot ID",
        description="Internal property to track the current shot for visibility updates."
    )
    
    bpy.types.Scene.auto_shot_exclusion = BoolProperty(
        name="Auto Shot Exclusion",
        description="Enable automatic shot-based collection visibility",
        default=True,
        update=update_auto_shot_exclusion
    )
    
    if on_frame_change_update_visibility not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(on_frame_change_update_visibility)
    if build_visibility_data_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(build_visibility_data_on_load)
        
    # Build cache on initial registration - REMOVED due to context error on registration
    # if bpy.context.scene:
    #     build_visibility_data(bpy.context.scene)

    bpy.types.OUTLINER_MT_collection.append(add_context_menus)
    bpy.types.OUTLINER_MT_object.append(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.append(add_context_menus)

def unregister():
    if on_frame_change_update_visibility in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(on_frame_change_update_visibility)
    if build_visibility_data_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(build_visibility_data_on_load)

    try:
        del bpy.types.WindowManager.active_shot_id
        del bpy.types.Scene.auto_shot_exclusion
    except (AttributeError, TypeError):
        pass

    bpy.types.OUTLINER_MT_collection.remove(add_context_menus)
    bpy.types.OUTLINER_MT_object.remove(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.remove(add_context_menus)
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        

if __name__ == "__main__":
    register()

