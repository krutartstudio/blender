bl_info = {
    "name": "Krutart Butcher Suite",
    "version": (2, 5, 1),
    "blender": (4, 5, 0),
    "location": "3D View > UI > Butcher",
    "description": "Butching [LOC/ANI/ART] pipeline tool for cleaning, publishing, and relinking.",
    "warning": "",
    "category": "Pipeline",
}

import bpy
import os
import sys
import re
import logging
import glob
import time

from bpy.types import Panel, Operator
from bpy.props import BoolProperty, StringProperty, IntProperty, EnumProperty, CollectionProperty

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

def _setup_batch_file_logger(master_filepath):
    """
    Adds a FileHandler to the logger to record batch progress in the source directory.
    """
    if not master_filepath:
        return
    
    dir_path = os.path.dirname(master_filepath)
    log_file = os.path.join(dir_path, "butcher_batch.log")
    
    # Clean cleanup of any existing file handlers to prevent file locking or duplicates
    _close_batch_file_logger()
            
    try:
        # Use 'w' to start fresh for each batch, or 'a' to append? 
        # User said "safety logging", 'a' is safer if they run multiple batches.
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        file_handler.set_name("butcher_batch_handler")
        log.addHandler(file_handler)
        log.info(f"\n{'='*60}\nBATCH SESSION START: {master_filepath}\n{'='*60}")
    except Exception as e:
        print(f"CRITICAL: Could not setup file logging: {e}")

def _close_batch_file_logger():
    """
    Removes and closes any FileHandlers attached to the logger.
    """
    for handler in log.handlers[:]:
        if isinstance(handler, logging.FileHandler) or handler.get_name() == "butcher_batch_handler":
            log.removeHandler(handler)
            handler.close()

# --- Configuration ---
CONTEXT_RULES = {
    'LOC': {'prefixes': ['LOC'], 'keywords': ['TERRAIN', 'LOCATION']},
    'ANI': {'prefixes': [], 'keywords': ['ANI', 'ANIMATION', 'VFX', 'SIM', 'FX']},
    'ART': {'prefixes': [], 'keywords': ['ART', 'LGT', 'LIGHTING', 'RENDER']},
}

MODE_LABELS = {
    'LOC': ("Loc", "WORLD_DATA"),
    'ANI': ("Ani", "ARMATURE_DATA"),
    'ART': ("Art", "MATERIAL_DATA"),
}


# --- Core Helper Functions ---

def _force_free_name(target_name):
    """Forcefully renames any existing collection holding 'target_name'."""
    existing = bpy.data.collections.get(target_name)
    if existing:
        import random
        existing.name = f"{target_name}_{random.randint(1000, 9999)}"

def _merge_collection_to_target(src_col, target_name):
    """Safely merges a collection into target_name."""
    dest = bpy.data.collections.get(target_name)
    if not dest:
        src_col.name = target_name
        return
        
    for child in list(src_col.children):
        if child.name not in dest.children:
            dest.children.link(child)
        src_col.children.unlink(child)
        
    for obj in list(src_col.objects):
        if obj.name not in dest.objects:
            dest.objects.link(obj)
        src_col.objects.unlink(obj)
        
    try:
        bpy.data.collections.remove(src_col)
    except Exception as e:
        log.warning(f"Could not remove merged collection wrapper {src_col.name}: {e}")


def get_current_user():
    """Determines the current user via krutart-configurator."""
    user_name = None
    configurator_mod = None

    # Fast path: guess the module name
    if 'krutart-configurator' in sys.modules:
        configurator_mod = sys.modules['krutart-configurator']
    else:
        # Slow path: iterate modules to find by bl_info name
        for mod_name, mod in sys.modules.items():
            if hasattr(mod, "bl_info") and isinstance(mod.bl_info, dict):
                if "Configurator" in mod.bl_info.get("name", ""):
                    configurator_mod = mod
                    break

    if configurator_mod:
        import socket
        try:
            addon_prefs_obj = bpy.context.preferences.addons.get(configurator_mod.__name__)
            if addon_prefs_obj:
                prefs = addon_prefs_obj.preferences
                hostname = socket.gethostname().lower()
                
                if prefs.user_name_override.strip():
                    user_name = prefs.user_name_override.strip()
                elif hasattr(configurator_mod, "CACHED_IDENTITY_MAP"):
                    cached_map = configurator_mod.CACHED_IDENTITY_MAP
                    if hostname in cached_map:
                        user_name = cached_map[hostname]
                
                # Ultimate fallback to hostname if map is empty
                if not user_name:
                    user_name = hostname
        except Exception:
            pass

    if user_name:
        return re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)
    
    return "user"

def _debug_trace(msg):
    try:
        import bpy, os
        if bpy.data.filepath:
            trace_file = os.path.join(os.path.dirname(bpy.data.filepath), "butcher_crash_trace.txt")
            with open(trace_file, "a") as f:
                f.write(msg + "\n")
    except:
        pass

def get_os_bridge(context=None):
    """Safely retrieves the krutart-os_bridge module if available."""
    # Fast path
    if 'krutart-os_bridge' in sys.modules:
        return sys.modules['krutart-os_bridge']
    
    # Slow path: iterate modules to find by bl_info name
    for mod_name, mod in sys.modules.items():
        if hasattr(mod, "bl_info") and isinstance(mod.bl_info, dict):
            if mod.bl_info.get("name") == "Krutart OS Bridge":
                return mod
    return None

def get_current_mode(context):
    scene_name = context.scene.name.upper()
    vl_name = context.view_layer.name.upper()
    
    # 1. Direct Prefix Overrides
    if scene_name.startswith("LOC"): return 'LOC'
    
    # 2. Check the manual UI override toggle on generic scenes
    if hasattr(context.scene, "butcher_workflow_mode"):
        override = context.scene.butcher_workflow_mode
        if override != 'AUTO':
            return override
            
    # 3. Keyword heuristic fallback
    for mode in ['ANI', 'ART']:
        for kw in CONTEXT_RULES[mode]['keywords']:
            if kw in scene_name or kw in vl_name:
                return mode

    # 4. Ultimate Fallback for generic SC scenes with no distinct naming
    return 'ANI'


def recursive_purge():
    """Aggressively purges unused data blocks."""
    previous_count = -1
    for i in range(10):
        current_count = (len(bpy.data.objects) + len(bpy.data.meshes) + 
                         len(bpy.data.materials) + len(bpy.data.collections))
        if current_count == previous_count:
            break
        previous_count = current_count
        _debug_trace(f"  [PURGE] Running orphans_purge loop {i}...")
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        _debug_trace(f"  [PURGE] Finished orphans_purge loop {i}.")


def _get_safe_win(context):
    """Retrieves a valid window for context overrides."""
    return getattr(context, 'window', None) if getattr(context, 'window', None) else (bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None)

def _safe_remove_scene(context, scene):
    """Removes a scene with window context override to prevent crashes."""
    win = _get_safe_win(context)
    if win:
        with context.temp_override(window=win):
            bpy.data.scenes.remove(scene)
    else:
        bpy.data.scenes.remove(scene)

def _safe_remove_collection(context, collection):
    """
    Removes a collection with window context override to prevent crashes.
    For library overrides, we strictly unlink them from the scene/hierarchy 
    rather than deleting them directly, allowing the orphan_purge pass to handle
    disposal safely without triggering the bke.liboverride_resync Segfault!
    """
    try:
        if getattr(collection, 'override_library', None):
            if context.scene.butcher_debug_mode:
                pass
            # Unlink from scenes
            for s in list(bpy.data.scenes):
                if collection.name in s.collection.children.keys():
                    try: 
                        _debug_trace(f"    [REMOVE_COL] Unlinking {collection.name} from scene {s.name}")
                        s.collection.children.unlink(collection)
                    except Exception as e: 
                        _debug_trace(f"    [REMOVE_COL EXCEPTION] {collection.name} from scene {s.name}: {e}")
            # Unlink from other collections
            for p_col in list(bpy.data.collections):
                if collection.name in p_col.children.keys():
                    if getattr(p_col, 'override_library', None):
                        continue # Cannot unlink from a parent that is also an override
                    try: 
                        _debug_trace(f"    [REMOVE_COL] Unlinking {collection.name} from parent {p_col.name}")
                        p_col.children.unlink(collection)
                    except Exception as e: 
                        _debug_trace(f"    [REMOVE_COL EXCEPTION] {collection.name} from parent {p_col.name}: {e}")
            return

        win = _get_safe_win(context)
        if win:
            with context.temp_override(window=win):
                bpy.data.collections.remove(collection)
        else:
            bpy.data.collections.remove(collection)
    except Exception as e:
        log.warning(f"Error in safe remove collection {collection.name}: {e}")

def _safe_remove_object(context, obj):
    """
    Removes an object with window context override to prevent crashes.
    Similarly unlinks override objects to prevent Segfaults.
    """
    try:
        if getattr(obj, 'override_library', None):
            if context.scene.butcher_debug_mode:
                pass
            for col in list(obj.users_collection):
                if getattr(col, 'override_library', None):
                    continue # Cannot unlink from a parent that is also an override
                try: 
                    _debug_trace(f"    [REMOVE_OBJ] Unlinking {obj.name} from {col.name}")
                    col.objects.unlink(obj)
                except Exception as e: 
                    _debug_trace(f"    [REMOVE_OBJ EXCEPTION] {obj.name} from {col.name}: {e}")
            return

        win = _get_safe_win(context)
        if win:
            with context.temp_override(window=win):
                bpy.data.objects.remove(obj, do_unlink=True)
        else:
            bpy.data.objects.remove(obj, do_unlink=True)
    except Exception as e:
        log.warning(f"Error in safe remove object {obj.name}: {e}")

def parse_shot_filename(filename):
    """
    Parses a filename to extract SC and SH numbers.
    Fallback purely for mode detection or initial parsing if needed.
    """
    match = re.search(r"(sc\d+).+?(sh\d+)", filename, re.IGNORECASE)
    if match:
        return match.group(1).upper(), match.group(2).upper()
    return None, None

def get_active_shot_from_timeline(scene):
    """
    Determines the active shot (SC/SH) based on the current frame's position 
    relative to timeline markers.
    Returns (sc, sh) or (None, None) if not found.
    """
    if not hasattr(scene, 'timeline_markers'):
        return None, None

    marker_pattern = re.compile(r"CAM-(SC\d+)-(SH\d+)", re.IGNORECASE)
    shot_markers = [m for m in scene.timeline_markers if marker_pattern.match(m.name)]
    
    if not shot_markers:
        return None, None
        
    # Sort markers chronologically
    shot_markers.sort(key=lambda m: m.frame)
    
    current_frame = scene.frame_current
    active_marker = None
    
    # Find the last marker that is at or before the current frame
    for m in shot_markers:
        if m.frame <= current_frame:
            active_marker = m
        else:
            break
            
    # If playhead is before the very first marker, default to the first marker
    if not active_marker and shot_markers:
        active_marker = shot_markers[0]
        
    if active_marker:
        match = marker_pattern.match(active_marker.name)
        if match:
            return match.group(1).upper(), match.group(2).upper()
            
    return None, None

def get_production_scene_dir(context, sc, sh):
    """
    Uses os_bridge to find the absolute Krutart root, then scans 
    3212-PRODUCTION directly for the full SC folder (e.g. SC17-DARKPOINT/[version])
    and returns to the specific SH folder.
    """
    os_bridge = get_os_bridge(context)
    if not os_bridge:
        log.warning("[DIAG] get_production_scene_dir: os_bridge not found.")
        return None

    # Get absolute drive root (e.g., .../Shared drives/3212-PREPRODUCTION)
    mac_root = os_bridge.get_mac_root(context)
    if not mac_root:
        log.warning("[DIAG] get_production_scene_dir: mac_root could not be resolved.")
        return None
        
    # Step up to the Shared Drives level, then down into PRODUCTION
    shared_drives = mac_root.parent
    production_root = shared_drives / "3212-PRODUCTION"
    
    if not production_root.exists():
        log.warning(f"[DIAG] get_production_scene_dir: PRODUCTION root missing at {production_root}")
        return None
        
    sc_upper = sc.upper()
    sh_upper = sh.upper()
    search_prefix = f"{sc_upper}-"
    
    # 1. Find the SC folder (e.g. SC17-DARKPOINT)
    sc_dir_name = None
    for d in production_root.iterdir():
        if d.is_dir() and d.name.upper().startswith(search_prefix):
            sc_dir_name = d.name
            break
            
    if not sc_dir_name:
        log.warning(f"[DIAG] get_production_scene_dir: Could not find SC folder starting with {search_prefix} in {production_root}")
        return None
        
    # 2. Find the SH folder inside that (e.g. SC17-SH010)
    sh_target = f"{sc_upper}-{sh_upper}"
    sh_dir = production_root / sc_dir_name / sh_target
    
    return str(sh_dir)

# --- Implementation Actions ---

def _make_visible_recursive(col):
    """
    Recursively ensures all objects in a collection (and its children)
    are visible, selectable, and have full viewport alpha to prevent 'Outline Only' issues.
    """
    for obj in col.objects:
        try:
            # Ensure basic visibility
            obj.hide_viewport = False
            obj.hide_render = False
            
            # CRITICAL: Reset selectability (often locked in ANI-REFERENCE)
            obj.hide_select = False
            
            # MATERIAL FIX: If material alpha is 0, Workbench/Solid mode shows only an outline.
            if hasattr(obj.data, "materials"):
                for mat in obj.data.materials:
                    if mat and hasattr(mat, "diffuse_color"):
                        # If alpha is suspiciously low (invisible), reset to 1.0
                        if mat.diffuse_color[3] < 0.1:
                            mat.diffuse_color[3] = 1.0
        except:
            pass
            
    for child in col.children:
        _make_visible_recursive(child)


def _prepare_references(context, mode):
    """
    Step 0: Prepare
    Creates rigorous reference structure according to pipeline specifications.
    """
    scene = context.scene
    base_name = scene.name
    sc_prefix = base_name.split('-')[0]
    
    if mode == 'LOC':
        return
        
    if context.scene.butcher_debug_mode:
        log.info(f"[DRY RUN] Would create REFERENCE collections and move ART/VFX objects for {base_name}")
        return

    # Determine where to put main references: try to find the +SC...+ collection
    target_parent = bpy.data.collections.get(f"+{base_name}+")
    if not target_parent:
        target_parent = scene.collection
        log.warning(f"Could not find parent collection '+{base_name}+'. Placing references at scene root.")

    # Move content Helper
    def _move_collection_contents(src_coll, dest_coll, delete_src=False):
        for child_coll in list(src_coll.children):
            try:
                dest_coll.children.link(child_coll)
                src_coll.children.unlink(child_coll)
            except Exception as e: pass
        
        for obj in list(src_coll.objects):
            try:
                dest_coll.objects.link(obj)
                src_coll.objects.unlink(obj)
            except Exception as e: pass

        if delete_src:
            try: _safe_remove_collection(context, src_coll)
            except Exception: pass

    # 1. ANI REFERENCE
    ani_ref_name = f"ANI-REFERENCE-{base_name}"
    ani_ref_col = bpy.data.collections.get(ani_ref_name)
    if not ani_ref_col:
        ani_ref_col = bpy.data.collections.new(ani_ref_name)
        target_parent.children.link(ani_ref_col)

    # ACTOR & PROP inside ANI REFERENCE
    actor_ref_name = f"ACTOR-REFERENCE-{base_name}"
    actor_ref_col = bpy.data.collections.get(actor_ref_name)
    if not actor_ref_col:
        actor_ref_col = bpy.data.collections.new(actor_ref_name)
        ani_ref_col.children.link(actor_ref_col)

    prop_ref_name = f"PROP-REFERENCE-{base_name}"
    prop_ref_col = bpy.data.collections.get(prop_ref_name)
    if not prop_ref_col:
        prop_ref_col = bpy.data.collections.new(prop_ref_name)
        ani_ref_col.children.link(prop_ref_col)

    # Move ACTOR and PROP contents (delete_src=False to retain the original wrappers)
    actor_src = bpy.data.collections.get(f"ACTOR-{base_name}")
    if actor_src:
        _move_collection_contents(actor_src, actor_ref_col, delete_src=False)
        
    prop_src = bpy.data.collections.get(f"PROP-{base_name}")
    if prop_src:
        _move_collection_contents(prop_src, prop_ref_col, delete_src=False)

    # 2. VFX REFERENCE
    vfx_ref_name = f"VFX-REFERENCE-{base_name}"
    vfx_ref_col = bpy.data.collections.get(vfx_ref_name)
    if not vfx_ref_col:
        vfx_ref_col = bpy.data.collections.new(vfx_ref_name)
        target_parent.children.link(vfx_ref_col)

    # Get all shots from timeline
    all_shots = get_all_butcher_shots(context)
    shot_numbers = []
    for marker in all_shots:
        match = re.match(r"CAM-SC\d+-(SH\d+)", marker.name, re.IGNORECASE)
        if match:
            shot_numbers.append(match.group(1).upper())

    # For each shot, create wrapper and move content
    for sh_num in shot_numbers:
        shot_ref_name = f"VFX-REFERENCE-{sc_prefix}-{sh_num}"
        shot_ref_col = bpy.data.collections.get(shot_ref_name)
        if not shot_ref_col:
            shot_ref_col = bpy.data.collections.new(shot_ref_name)
            vfx_ref_col.children.link(shot_ref_col)

        src_vfx_shot = bpy.data.collections.get(f"VFX-{sc_prefix}-{sh_num}")
        if src_vfx_shot:
            # PATCH: Changed delete_src to False to leave the wrapper collections intact
            _move_collection_contents(src_vfx_shot, shot_ref_col, delete_src=False)

    # 3. ART REFERENCE
    art_ref_name = f"ART-REFERENCE-{base_name}"
    art_ref_col = bpy.data.collections.get(art_ref_name)
    if not art_ref_col:
        art_ref_col = bpy.data.collections.new(art_ref_name)
        target_parent.children.link(art_ref_col)
        
    art_src_col = bpy.data.collections.get(f"ART-{base_name}")
    if art_src_col:
        _move_collection_contents(art_src_col, art_ref_col, delete_src=True)
                
    log.info(f"Prepared references for {base_name}")

def _simple_delete_collection(context, col):
    """Helper to merge collection contents into parents before deleting it (Unzip)"""
    parent_scenes = [s for s in bpy.data.scenes if col.name in s.collection.children.keys()]
    parent_collections = [pcol for pcol in bpy.data.collections if col.name in pcol.children.keys()]
    
    for child_col in list(col.children):
        for s in parent_scenes:
            if child_col.name not in s.collection.children.keys(): s.collection.children.link(child_col)
        for pcol in parent_collections:
            if child_col.name not in pcol.children.keys(): pcol.children.link(child_col)
            
    for obj in list(col.objects):
        for s in parent_scenes:
            if obj.name not in s.collection.objects.keys(): s.collection.objects.link(obj)
        for pcol in parent_collections:
            if obj.name not in pcol.objects.keys(): pcol.objects.link(obj)
            
    try: _safe_remove_collection(context, col)
    except Exception as e:
        log.warning(f"Failed to simple-delete collection {col.name}: {e}")

def _save_loc_work(context):
    if not bpy.data.is_saved:
        raise Exception("Please save the original file first!")

    filepath = bpy.data.filepath
    dir_path = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name_part, ext = os.path.splitext(filename)

    # Make parsing case-insensitive
    name_lower = name_part.lower()

    # Find and cleanly strip any old version number to get the pure base name
    version_match = re.search(r'-v\d{3,}', name_lower)
    if version_match:
        before_version_part = name_lower[:version_match.start()]
    else:
        before_version_part = name_lower

    project_match = re.match(r'^(\d+)-', before_version_part)
    if True:
        if project_match:
            project_name = project_match.group(1)
            asset_name = before_version_part[project_match.end():]
        else:
            project_name = "3212"
            asset_name = before_version_part
        
        # Cleanly strip prefixes to get a pure 'moon_d'
        clean_asset_name = re.sub(r'^(loc_|loc-|layout_|layout-)', '', asset_name, flags=re.IGNORECASE)
        
        # Always use the current artist detected by the system/configurator
        user_name = get_current_user()
        
        # Use clean asset name for the folder structure
        location_id = clean_asset_name
        
        # Navigate to structure: LIBRARY/LIBRARY-WORK/LOCATION-WORK/LOC-{locationID}-WORK/
        os_bridge = get_os_bridge(context)
        mac_root = os_bridge.get_mac_root(context) if os_bridge else None
        
        if mac_root:
            target_dir = os.path.join(str(mac_root), "LIBRARY", "LIBRARY-WORK", "LOCATION-WORK", f"LOC-{location_id.upper()}-WORK")
        else:
            # Fallback to relative climb
            base_dir = dir_path
            for _ in range(5):
                if os.path.basename(base_dir).upper() == "LIBRARY":
                    break
                if "LOCATION-WORK" in os.path.basename(base_dir).upper():
                    base_dir = os.path.dirname(os.path.dirname(base_dir)) # go to LIBRARY level
                    break
                parent = os.path.dirname(base_dir)
                if parent == base_dir:
                    base_dir = dir_path
                    break
                base_dir = parent
            target_dir = os.path.join(base_dir, "LIBRARY-WORK", "LOCATION-WORK", f"LOC-{location_id.upper()}-WORK")

        # --- Folder-Exclusive Versioning ---
        highest_version = -1
        existing_files = glob.glob(os.path.join(target_dir, "*.blend"))
        for f in existing_files:
            fname = os.path.basename(f).lower()
            v_match = re.search(r'-v(\d{3,})', fname)
            if v_match:
                highest_version = max(highest_version, int(v_match.group(1)))
        
        new_version_int = highest_version + 1
        new_version_str = f"v{new_version_int:03d}"
        new_filename = f"{project_name}-loc-{clean_asset_name}-{new_version_str}-{user_name}-butch{ext}"
        new_filepath = os.path.join(target_dir, new_filename)
        # -----------------------------------

        if context.scene.butcher_debug_mode:
            if not os.path.exists(target_dir):
                log.info(f"[DRY RUN] Would create directory: {target_dir}")
            log.info(f"[DRY RUN] Would save LOC WORK file to: {new_filepath}")
            return

        os.makedirs(target_dir, exist_ok=True)

        if os_bridge:
            os_bridge.run_bridge_to_windows(context)
            
        bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=False)
        log.info(f"Saved LOC WORK file: {new_filepath}")
        
        if os_bridge:
            os_bridge.run_bridge_to_mac(context, force=False)

def _loc_extract_scene(context):
    """
    Step 2: Extract Scene
    - Identify and delete hierarchy (collections and objects) for +ANI-SC..., SHOT-ART-..., SHOT-VFX-...
    - Identify and simple-delete collections +ART-..., +VFX-...
    - Delete markers on timeline in all blend scenes
    - Delete video sequencer content in all blend scenes
    """
    def _delete_hierarchy(coll):
        # Recursively delete children collections
        for child in list(coll.children):
            _delete_hierarchy(child)

        # Unlink and remove all objects
        for obj in list(coll.objects):
            if context.scene.butcher_debug_mode:
                pass
            else:
                try:
                    _debug_trace(f"    [_delete_hierarchy] REMOVING OBJ: {obj.name}")
                    _safe_remove_object(context, obj)
                except Exception as e:
                    _debug_trace(f"    [_delete_hierarchy] EXCEPTION ON OBJ: {obj.name} -> {e}")

        # Delete the collection itself
        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would delete hierarchy collection: {coll.name}")
        else:
            try:
                cname = coll.name
                _safe_remove_collection(context, coll)
                survivor = bpy.data.collections.get(cname)
                if survivor:
                    survivor.hide_viewport = True
                    survivor.hide_render = True
                    import random
                    survivor.name = f"GARBAGE_{cname}_{random.randint(1000, 9999)}"
            except:
                pass

    # Delete Hierarchy targets
    delete_hierarchy_patterns = [
        re.compile(r"^ANI-REFERENCE-", re.IGNORECASE),
        re.compile(r"^ART-REFERENCE-", re.IGNORECASE),
        re.compile(r"^VFX-REFERENCE-", re.IGNORECASE),
        re.compile(r"^\+ANI-SC", re.IGNORECASE),
        re.compile(r"^SHOT-ART-", re.IGNORECASE),
        re.compile(r"^SHOT-VFX-", re.IGNORECASE),
        re.compile(r"^LGT-REFERENCE", re.IGNORECASE)
    ]

    def _unzip_collection(coll):
        parent_scenes = [scene for scene in bpy.data.scenes if coll.name in scene.collection.children.keys()]
        parent_collections = [pcol for pcol in bpy.data.collections if coll.name in pcol.children.keys()]
        
        for child_col in list(coll.children):
            if context.scene.butcher_debug_mode:
                log.info(f"[DRY RUN] Would reparent child collection '{child_col.name}' to parents of '{coll.name}'")
            else:
                for scene in parent_scenes:
                    if child_col.name not in scene.collection.children.keys():
                        scene.collection.children.link(child_col)
                for pcol in parent_collections:
                    if child_col.name not in pcol.children.keys():
                        pcol.children.link(child_col)

        for obj in list(coll.objects):
            if context.scene.butcher_debug_mode:
                pass # Too noisy to log every object
            else:
                for scene in parent_scenes:
                    if obj.name not in scene.collection.objects.keys():
                        scene.collection.objects.link(obj)
                for pcol in parent_collections:
                    if obj.name not in pcol.objects.keys():
                        pcol.objects.link(obj)
        
        # Now truly delete the container collection wrapper
        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would UNZIP (delete wrapper) collection: {coll.name}")
        else:
            try:
                _debug_trace(f"    [_unzip_collection] DELETING COLLECTION WRAPPER: {coll.name}")
                _safe_remove_collection(context, coll)
            except Exception as e:
                _debug_trace(f"    [_unzip_collection] EXCEPTION: {coll.name} -> {e}")


    # Unzip Delete targets (e.g. +ART-, +VFX-)
    unzip_delete_patterns = [
        re.compile(r"^\+ART-", re.IGNORECASE),
        re.compile(r"^\+VFX-", re.IGNORECASE)
    ]

    for col in list(bpy.data.collections):
        try:
            cname = col.name
        except ReferenceError:
            continue
        
        # Check hierarchy delete
        if any(p.match(cname) for p in delete_hierarchy_patterns):
            _delete_hierarchy(col)
            continue
            
        # Check Unzip delete
        if any(p.match(cname) for p in unzip_delete_patterns):
            _unzip_collection(col)

    # Scene cleanups
    for scene in bpy.data.scenes:
        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would clear timeline markers and video sequencer in scene: {scene.name}")
        else:
            scene.timeline_markers.clear()
            if scene.sequence_editor:
                for strip in list(scene.sequence_editor.sequences_all):
                    scene.sequence_editor.sequences.remove(strip)

def _loc_models_visible(context):
    """
    Step 3: Scene Models Visible
    - Calls Advanced Copy's Make All Visible operator 
      to robustly turn off hides/excludes safely 
      while disabling its auto-override feature.
    """
    if context.scene.butcher_debug_mode:
        log.info("[DRY RUN] Would call Advanced Copy's Make All Visible operator")
    else:
        try:
            bpy.ops.advanced_copy.make_all_visible()
            log.info("Triggered ADVCOPY Make All Visible.")
        except AttributeError:
            log.warning("Advanced Copy addon missing 'make_all_visible' operator. Please ensure version 2.6.3+ is installed.")

def _loc_reset_layout(context):
    """
    Step 4: Reset Window Layout
    - Make it default layout blender view
    - Reset/View All in 3D views
    """
    if context.scene.butcher_debug_mode:
        log.info("[DRY RUN] Would reset workspace to Layout and View All")
        return

    # Switch workspace to Layout
    layout_ws = bpy.data.workspaces.get("Layout")
    if layout_ws and getattr(context, 'window', None):
        context.window.workspace = layout_ws
        
    _reset_view(context)

def _loc_aggressive_purge(context):
    """
    Step 5: Purge Data
    - Forceably strips Fake Users from unlinked data 
    - Repeats Orphan Purge until perfectly clean
    """
    if context.scene.butcher_debug_mode:
        log.info("[DRY RUN] Would aggressively strip fake users and purge all orphaned data")
        return
        
    log.info("Starting Nuclear Purge")
    
    data_cats = [
        bpy.data.materials, bpy.data.images, bpy.data.meshes, 
        bpy.data.node_groups, bpy.data.actions, bpy.data.libraries,
        bpy.data.cameras, bpy.data.lights, bpy.data.curves,
        bpy.data.armatures, bpy.data.particles, bpy.data.texts
    ]
    
    # 1. Strip fake users rigidly
    for cat in data_cats:
        for item in list(cat):
            if item.users == 0 or (item.users == 1 and getattr(item, 'use_fake_user', False)):
                if hasattr(item, 'use_fake_user'):
                    item.use_fake_user = False
                    
    # Force view layer update before orphan purge to prevent Windows EXCEPTION_ACCESS_VIOLATION
    context.view_layer.update()
                    
    # 2. Run recursive orphan purge natively in a loop until fully scrubbed
    for _ in range(20):
        try:
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        except Exception as e:
            pass # Suppress minor warnings in deep purges
            
    # Force another update to ensure UI is completely clean
    context.view_layer.update()
    log.info("Nuclear Purge Complete.")

def _save_loc_hero(context):
    """
    Step 6: Save LOC Hero
    - Re-implemented per User Request to avoid jumping to Publisher
    - Copy the perfectly purged WORK file over to the `-HERO` directory.
    - Rename explicitly: 3212-loc-location_id-hero.blend
    """
    if not bpy.data.is_saved:
        raise Exception("File must be saved to create a hero!")

    filepath = bpy.data.filepath
    dir_path = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name_part, ext = os.path.splitext(filename)
    
    # 1. Translate Directory to HERO
    os_bridge = get_os_bridge(context)
    mac_root = os_bridge.get_mac_root(context) if os_bridge else None
    
    if mac_root:
        hero_dir = re.sub(r"-WORK", "-HERO", dir_path, flags=re.IGNORECASE)
    else:
        hero_dir = re.sub(r"-WORK", "-HERO", dir_path, flags=re.IGNORECASE)

    # 2. Format Filename exactly: project-loc-cleanAsset-hero.blend
    name_lower = name_part.lower()
    version_match = re.search(r'-v\d{3,}', name_lower)

    if version_match:
        base_name = name_lower[:version_match.start()]
    else:
        base_name = name_lower # Failsafe if formatting broke
        
    new_filename = f"{base_name}-hero{ext}"
    
    if context.scene.butcher_debug_mode:
        if not os.path.exists(hero_dir):
            log.info(f"[DRY RUN] Would create directory: {hero_dir}")
        log.info(f"[DRY RUN] Would save LOC HERO file to: {os.path.join(hero_dir, new_filename)}")
        return

    os.makedirs(hero_dir, exist_ok=True)
    hero_filepath = os.path.join(hero_dir, new_filename)

    # Save the current WORK file first to persist the butchered changes over the raw WORK file
    bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=False)

    try:
        if os_bridge:
            os_bridge.run_bridge_to_windows(context)

        log.info(f"Attempting to save LOC HERO to: {hero_filepath}")
        
        # FIX: Provide a window context override to prevent silent aborts during batch saving
        win = _get_safe_win(context)
        if win:
            with context.temp_override(window=win):
                bpy.ops.wm.save_as_mainfile(filepath=hero_filepath, copy=True)
        else:
            bpy.ops.wm.save_as_mainfile(filepath=hero_filepath, copy=True)
        
        # POST-SAVE VERIFICATION
        if os.path.exists(hero_filepath):
            log.info(f"Saved LOC HERO file: {hero_filepath}")
        else:
            log.error(f"Save Failure Verification: Hero file is missing at {hero_filepath} after blender save operation returned.")
            log.error("Check if the network drive is connected and writable.")

    finally:
        if os_bridge:
            os_bridge.run_bridge_to_mac(context, force=False)


def _check_save():
    if not bpy.data.is_saved:
        raise Exception("Please save the original file first!")


def _save_workflow_work(context, mode_tag, suffix_tag):
    if not bpy.data.is_saved:
        raise Exception("Please save the original file first!")

    filepath = bpy.data.filepath
    dir_path = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name_part, ext = os.path.splitext(filename)
    name_lower = name_part.lower()

    # Find and cleanly strip any old version number to get the pure base name
    version_match = re.search(r'-v\d{3,}', name_lower)
    if version_match:
        before_version_part = name_lower[:version_match.start()]
    else:
        before_version_part = name_lower

    sc, sh = get_active_shot_from_timeline(context.scene)
    if not sc or not sh:
        raise Exception("Could not detect active shot from Timeline Markers. Make sure the playhead is over a CAM-SC##-SH### marker.")
        
    project_match = re.match(r"^(\d+)-", name_lower)
    project_id = project_match.group(1) if project_match else "3212"

    sc_upper = sc.upper()
    sh_upper = sh.upper()
    
    # Always use the current artist detected by the system/configurator
    clean_user = get_current_user()

    master_sh_dir = get_production_scene_dir(context, sc_upper, sh_upper)
    
    if not master_sh_dir:
        raise Exception(
            f"CRITICAL SAVING ERROR: Could not resolve absolute Production path for '{sc_upper}-{sh_upper}'. "
            f"Ensure the SC folder exists inside 3212-PRODUCTION/ and that the network drive is connected. "
            f"Fallback relative saving has been disabled to prevent misplaced files."
        )

    target_dir = os.path.join(master_sh_dir, f"{sc_upper}-{sh_upper}-WORK", f"{sc_upper}-{sh_upper}-{mode_tag}-WORK")
    target_dir = os.path.normpath(target_dir)

    # --- Folder-Exclusive Versioning ---
    existing_files = glob.glob(os.path.join(target_dir, "*.blend"))
    highest_version = -1
    
    for f in existing_files:
        fname = os.path.basename(f).lower()
        v_match = re.search(r'-v(\d{3,})', fname)
        if v_match:
            highest_version = max(highest_version, int(v_match.group(1)))
                
    new_version_int = highest_version + 1
    # ------------------------------------

    new_filename = f"{project_id}-{sc_upper.lower()}-{sh_upper.lower()}-{mode_tag.lower()}-v{new_version_int:03d}-{clean_user}-{suffix_tag}{ext}"
    
    if context.scene.butcher_debug_mode:
        if not os.path.exists(target_dir):
            log.info(f"[DRY RUN] Would create directory: {target_dir}")
        log.info(f"[DRY RUN] Would save {mode_tag} WORK file to: {os.path.join(target_dir, new_filename)}")
        return
        
    expected_work_suffix = f"{sc_upper}-{sh_upper}-WORK/{sc_upper}-{sh_upper}-{mode_tag}-WORK"
    log.info(f"[DIAG] WORK target_dir resolved to: {target_dir}")
    log.info(f"[DIAG] Expecting suffix roughly matching: {expected_work_suffix}")
    
    if not target_dir.replace('\\', '/').endswith(expected_work_suffix):
        log.warning(f"[BRIDGE WARNING] Target directory '{target_dir}' does not cleanly end with the expected root structure. Bridge may resolve this correctly, proceeding anyway.")

    os.makedirs(target_dir, exist_ok=True)
    new_filepath = os.path.join(target_dir, new_filename)
    log.info(f"[DIAG] Calculated WORK filepath: {new_filepath}")

    os_bridge = get_os_bridge(context)
    try:
        if os_bridge: 
            log.info("[DIAG] Bridging to Windows for WORK save...")
            os_bridge.run_bridge_to_windows(context)
            
        log.info(f"Executing save_as_mainfile for {mode_tag} WORK...")
        
        # FIX: Provide a window context override to prevent silent aborts during batch saving
        win = _get_safe_win(context)
        if win:
            with context.temp_override(window=win):
                bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=False)
        else:
            bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=False)
        
        if os.path.exists(new_filepath):
            log.info(f"SUCCESS: Saved {mode_tag} WORK file: {new_filepath}")
        else:
            log.error(f"VERIFY FAILURE: WORK file missing after save: {new_filepath}")
    except Exception as e:
        log.error(f"EXCEPTION during WORK save: {e}")
    finally:
        if os_bridge: 
            log.info("[DIAG] Restoring Mac paths after WORK save...")
            os_bridge.run_bridge_to_mac(context, force=False)


def _save_workflow_hero(context, mode_tag, create_blocking=False):
    if not bpy.data.is_saved:
        raise Exception("File must be saved to create a hero!")

    filepath = bpy.data.filepath
    dir_path = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name_part, ext = os.path.splitext(filename)
    name_lower = name_part.lower()
    
    project_match = re.match(r"^(\d+)-", name_lower)
    project_id = project_match.group(1) if project_match else "3212"
    sc, sh = get_active_shot_from_timeline(context.scene)
    if not sc or not sh:
        raise Exception("Cannot determine SC/SH for hero save from timeline markers.")
        
    sc_upper = sc.upper()
    sh_upper = sh.upper()

    master_sh_dir = get_production_scene_dir(context, sc_upper, sh_upper)
    
    if not master_sh_dir:
        raise Exception(
            f"CRITICAL SAVING ERROR: Could not resolve absolute Production path for '{sc_upper}-{sh_upper}'. "
            f"Ensure the SC folder exists inside 3212-PRODUCTION/ and that the network drive is connected. "
            f"Fallback relative saving has been disabled to prevent misplaced files."
        )

    hero_dir = os.path.join(master_sh_dir, f"{sc_upper}-{sh_upper}-HERO", f"{sc_upper}-{sh_upper}-{mode_tag}-HERO")
    hero_dir = os.path.normpath(hero_dir)
    new_filename = f"{project_id}-{sc_upper.lower()}-{sh_upper.lower()}-{mode_tag.lower()}-hero{ext}"
    log.info(f"[DIAG] HERO target_dir resolved to: {hero_dir}")
    
    if context.scene.butcher_debug_mode:
        if not os.path.exists(hero_dir):
            log.info(f"[DRY RUN] Would create directory: {hero_dir}")
        log.info(f"[DRY RUN] Would save {mode_tag} HERO file to: {os.path.join(hero_dir, new_filename)}")
        return
        
    expected_hero_suffix = f"{sc_upper}-{sh_upper}-HERO/{sc_upper}-{sh_upper}-{mode_tag}-HERO"
    
    if not hero_dir.replace('\\', '/').endswith(expected_hero_suffix):
         log.warning(f"[BRIDGE WARNING] Hero target directory '{hero_dir}' does not cleanly end with the expected structure. Proceeding anyway.")

    os.makedirs(hero_dir, exist_ok=True)
    hero_filepath = os.path.join(hero_dir, new_filename)

    # 1. Update the currently open WORK version first (using Mac paths) before creating Hero copy
    log.info(f"[DIAG] Updating butchered WORK file (Mac): {filepath}")
    
    win = _get_safe_win(context)
    if win:
        with context.temp_override(window=win):
            bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=False)
    else:
        bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=False)

    # --- VERSION TRACKING HANDSHAKE & DIAGNOSTICS ---
    version_match = re.search(r'-v(\d{3,})', name_lower)
    target_col_name = f"+{mode_tag}+"
    target_col = bpy.data.collections.get(target_col_name)
    injected_prop = False

    # Force print to external trace file
    _debug_trace(f"--- HANDSHAKE DIAGNOSTICS FOR: {new_filename} ---")
    _debug_trace(f"  > name_lower: {name_lower}")
    _debug_trace(f"  > version_match: {'FOUND (v' + version_match.group(1) + ')' if version_match else 'NONE'}")
    _debug_trace(f"  > target_col_name expected: {target_col_name}")
    _debug_trace(f"  > target_col found in bpy.data: {'YES' if target_col else 'NO'}")

    if version_match and target_col:
        version_str = f"v{version_match.group(1)}"
        target_col["source_work_version"] = version_str
        injected_prop = True
        log.info(f"Injected 'source_work_version'='{version_str}' into '{target_col.name}' for HERO stamp.")
        _debug_trace("  > Result: INJECTION SUCCESSFUL")
    else:
        _debug_trace("  > Result: INJECTION SKIPPED (Condition Failed)")

    # 2. Save the HERO version (using Windows paths)
    os_bridge = get_os_bridge(context)
    try:
        if os_bridge: 
            log.info("[DIAG] Bridging to Windows for HERO save...")
            os_bridge.run_bridge_to_windows(context)
        
        log.info(f"Executing save_as_mainfile (copy=True) for {mode_tag} HERO: {hero_filepath}")
        
        if win:
            with context.temp_override(window=win):
                bpy.ops.wm.save_as_mainfile(filepath=hero_filepath, copy=True)
        else:
            bpy.ops.wm.save_as_mainfile(filepath=hero_filepath, copy=True)
        
        if os.path.exists(hero_filepath):
            log.info(f"SUCCESS: Saved {mode_tag} HERO file: {hero_filepath}")
        else:
            log.error(f"VERIFY FAILURE: HERO file missing after save: {hero_filepath}")
    except Exception as e:
        log.error(f"EXCEPTION during HERO save: {e}")
    finally:
        if os_bridge: 
            log.info("[DIAG] Restoring Mac paths after HERO save...")
            os_bridge.run_bridge_to_mac(context, force=False)
            
    # --- CLEANUP VERSION HANDSHAKE FROM LIVE WORK FILE ---
    if injected_prop and "source_work_version" in target_col:
        del target_col["source_work_version"]
        log.info(f"Cleaned up 'source_work_version' from '{target_col.name}' after HERO save.")

    # =========================================================
    # --- NEW: POST-HERO WORK INCREMENT (MATCHING PUBLISHER) ---
    # =========================================================
    
    # We only run this if triggered by the Relink operations
    if create_blocking:
        current_dir = os.path.dirname(filepath)
        current_filename = os.path.basename(filepath)
        name_part, ext = os.path.splitext(current_filename)
        name_lower = name_part.lower()

        # Strip existing version tags to get the clean base name
        version_match = re.search(r'-v\d{3,}', name_lower)
        if version_match:
            base_name = name_lower[:version_match.start()]
        else:
            base_name = name_lower

        # Scan for the highest version currently in the folder to prevent collisions
        existing_files = glob.glob(os.path.join(current_dir, "*.blend"))
        highest_version = -1
        for f in existing_files:
            v_match = re.search(r'-v(\d{3,})', os.path.basename(f), re.IGNORECASE)
            if v_match:
                highest_version = max(highest_version, int(v_match.group(1)))
                
        new_version_int = highest_version + 1
        new_version_str = f"-v{new_version_int:03d}"
        
        clean_user = get_current_user()
        
        # Select suffix based on mode: ANI line gets -blocking, ART line gets -setdress
        suffix = "blocking"
        if mode_tag == 'ART':
            suffix = "setdress"
            
        new_post_hero_filename = f"{base_name}{new_version_str}-{suffix}-{clean_user}{ext}"
        new_post_hero_filepath = os.path.join(current_dir, new_post_hero_filename)

        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would save POST-HERO Work file to: {new_post_hero_filepath}")
            return

        try:
            log.info(f"Executing save_as_mainfile for Post-Hero Increment: {new_post_hero_filepath}")
            
            # Use safe window context to prevent crashes during batch mode
            win = _get_safe_win(context)
            if win:
                with context.temp_override(window=win):
                    bpy.ops.wm.save_as_mainfile(filepath=new_post_hero_filepath, copy=False)
            else:
                bpy.ops.wm.save_as_mainfile(filepath=new_post_hero_filepath, copy=False)
                
            log.info(f"SUCCESS: Switched to clean Post-Hero Work file: {new_post_hero_filepath}")
        except Exception as e:
            log.error(f"EXCEPTION during Post-Hero increment save: {e}")


def _ani_save_work(context):
    _save_workflow_work(context, 'ANI', 'butch')

def _ani_save_hero(context):
    _save_workflow_hero(context, 'ANI')

def _art_save_work(context):
    _save_workflow_work(context, 'ART', 'butch')

def _art_extract_scene(context):
    scene = context.scene
    sc, sh = get_active_shot_from_timeline(scene)
    
    if not sc or not sh:
        log.warning(f"Could not identify active shot for scene extraction from markers.")

    # Rescue LGT-REFERENCE BEFORE any deletion logic
    for col in list(bpy.data.collections):
        try: cname = col.name
        except ReferenceError: continue
        if cname.upper().startswith("LGT-REFERENCE"):
            # Unlink from other parents safely
            for parent in list(bpy.data.collections):
                if col.name in parent.children.keys():
                    if getattr(parent, 'override_library', None):
                        continue
                    parent.children.unlink(col)
            if col.name not in context.scene.collection.children.keys():
                context.scene.collection.children.link(col)

    for s in list(bpy.data.scenes):
        if s != scene:
            if context.scene.butcher_debug_mode:
                log.info(f"[DRY RUN] Would delete scene {s.name}")
            else:
                s_name = s.name
                _safe_remove_scene(context, s)
                log.info(f"Removed scene: {s_name}")

    def _delete_hierarchy(coll):
        for child in list(coll.children): _delete_hierarchy(child)
        for obj in list(coll.objects):
            try:
                if not context.scene.butcher_debug_mode: 
                    _safe_remove_object(context, obj)
            except: pass
        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would delete hierarchy collection: {coll.name}")
        else:
            try: 
                cname = coll.name
                _safe_remove_collection(context, coll)
                survivor = bpy.data.collections.get(cname)
                if survivor:
                    log.warning(f"Collection {cname} survived deletion (likely an override lock). Hiding and renaming.")
                    survivor.hide_viewport = True
                    survivor.hide_render = True
                    import random
                    survivor.name = f"GARBAGE_{cname}_{random.randint(1000, 9999)}"
            except: pass

    hierarchy_delete_patterns = [
        re.compile(r"^ANI-REFERENCE-", re.IGNORECASE),
        re.compile(r"^\+ANI-", re.IGNORECASE),
        re.compile(r"^\+VFX-", re.IGNORECASE),
        re.compile(r"^\+ENV-", re.IGNORECASE),
        re.compile(r"^\+LOC-", re.IGNORECASE),
        re.compile(r"^ART-REFERENCE-", re.IGNORECASE),
        re.compile(r"^VFX-REFERENCE-", re.IGNORECASE)
    ]
    
    active_shot_model_col = f"MODEL-SC{sc[2:]}-SH{sh[2:]}".upper() if (sc and sh) else ""
    if active_shot_model_col and not bpy.data.collections.get(active_shot_model_col):
         active_shot_model_col = f"MODEL-{sc}-{sh}".upper()

    for col in list(bpy.data.collections):
        try: cname = col.name
        except ReferenceError: continue
        cname_upper = cname.upper()

        if cname_upper.startswith("LGT-REFERENCE"):
            continue # Safeguard pro ART workflow

        if any(p.search(cname) for p in hierarchy_delete_patterns):
            _delete_hierarchy(col)
            continue
            
        if cname_upper.startswith("MODEL-SC"):
            if "-SH" in cname_upper:
                # E.g. MODEL-SC11-SH040
                if active_shot_model_col and cname_upper != active_shot_model_col:
                    _delete_hierarchy(col)
            else:
                # General MODEL-SC11 -> delete it
                _delete_hierarchy(col)
            continue

        if cname_upper.startswith("+SC") or cname_upper.startswith("SHOT-ART-SC"):
            if context.scene.butcher_debug_mode:
                log.info(f"[DRY RUN] Would simple-delete collection: {col.name}")
            else:
                _simple_delete_collection(context, col)
            continue
                
    if context.scene.sequence_editor:
        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would clear non-active video sequencer content.")
        else:
            active_shot_str = f"{sc}-{sh}".upper() if (sc and sh) else ""
            active_sh_str = sh.upper() if sh else ""
            for strip in list(context.scene.sequence_editor.sequences):
                strip_name = strip.name.upper()
                filepath = getattr(strip, "filepath", "").upper()
                keep_strip = False
                
                # Check if the sequence belongs to our shot
                if active_shot_str and (active_shot_str in strip_name or active_shot_str in filepath):
                    keep_strip = True
                elif active_sh_str and (active_sh_str in strip_name or active_sh_str in filepath):
                    keep_strip = True
                # CRITICAL: Preserve VSE guides for RELINK
                elif "-guide-" in strip_name.lower() or "-guide-" in filepath.lower():
                    keep_strip = True
                    
                if not keep_strip:
                    context.scene.sequence_editor.sequences.remove(strip)

def _art_reorganize(context):
    if context.scene.butcher_debug_mode:
        log.info("[DRY RUN] Would reorganize ART collections")
        return
        
    scene = context.scene
    sc, sh = get_active_shot_from_timeline(scene)
    
    if sc and sh:
        scene.name = f"{sc.upper()}-{sh.upper()}-ART"
        
    art_root = None
    
    # PATCH: Constrain search to collections actively linked to the current scene's root
    # and enforce matching the current scene's SC prefix if available.
    for col in list(scene.collection.children):
        cname_upper = col.name.upper()
        
        # Determine if this collection is the correct ART root for this specific shot
        is_match = False
        if sc and cname_upper.startswith(f"+ART-{sc.upper()}"):
            is_match = True
        elif not sc and cname_upper.startswith("+ART-SC"):
            # Fallback if the timeline marker sc/sh parsing failed
            is_match = True

        if is_match:
            _debug_trace(f"    [_art_reorganize] Found and renaming ART root: {col.name}")
            
            # --- EXTRACT AND STORE ENV TARGET ---
            clean_name = cname_upper.strip('+')
            parts = clean_name.split('-')
            # Expecting e.g. ['ART', 'SC11', 'APOLLO_SITE']
            if len(parts) >= 3 and parts[0] == "ART" and parts[1].startswith("SC"):
                env_target = "-".join(parts[2:])
                context.scene["butcher_env_target"] = env_target
                _debug_trace(f"    [_art_reorganize] Stored env_target: {env_target}")
                
            _merge_collection_to_target(col, "+ART+")
            art_root = bpy.data.collections.get("+ART+")
            
            # Ensure the merged/renamed root is linked to the scene
            if art_root and art_root.name not in scene.collection.children:
                scene.collection.children.link(art_root)
                
            break
            
    if not art_root:
        art_root = bpy.data.collections.get("+ART+")
        
    active_shot_model_col_name = f"MODEL-SC{sc[2:]}-SH{sh[2:]}".upper() if (sc and sh) else ""
    if active_shot_model_col_name and not bpy.data.collections.get(active_shot_model_col_name):
         active_shot_model_col_name = f"MODEL-{sc}-{sh}".upper()
         
    local_col = bpy.data.collections.get(active_shot_model_col_name)
    if local_col:
        _debug_trace(f"    [_art_reorganize] Found local shot collection: {local_col.name}")
        
        is_linked = local_col.library is not None or getattr(local_col, 'override_library', None) is not None
        
        if not is_linked:
            _debug_trace(f"    [_art_reorganize] Renaming local collection to 'LOCAL'")
            local_col.name = "LOCAL"
        else:
            _debug_trace(f"    [_art_reorganize] SKIP RENAME for Linked/Override collection: {local_col.name}")
        
    if art_root:
        std_col = bpy.data.collections.get("STD")
        if not std_col:
            _debug_trace("    [_art_reorganize] Creating STD collection")
            std_col = bpy.data.collections.new("STD")
            art_root.children.link(std_col)
            
        linked_col = bpy.data.collections.get("LINKED")
        if not linked_col:
            _debug_trace("    [_art_reorganize] Creating LINKED collection")
            linked_col = bpy.data.collections.new("LINKED")
            std_col.children.link(linked_col)
            
        if local_col:
            for parent in list(bpy.data.collections):
                if local_col.name in parent.children.keys():
                    if getattr(parent, 'override_library', None):
                        _debug_trace(f"    [_art_reorganize] SKIP Unlink {local_col.name} from OVERRIDE parent: {parent.name}")
                        continue
                    _debug_trace(f"    [_art_reorganize] Unlinking {local_col.name} from parent: {parent.name}")
                    parent.children.unlink(local_col)
            
            if local_col.name in context.scene.collection.children.keys():
                _debug_trace(f"    [_art_reorganize] Unlinking {local_col.name} from Scene Root")
                context.scene.collection.children.unlink(local_col)
                
            if local_col.name not in std_col.children.keys():
                _debug_trace(f"    [_art_reorganize] Linking {local_col.name} to STD")
                std_col.children.link(local_col)
            
            _debug_trace(f"    [_art_reorganize] Recursively unhiding and enabling selection in {local_col.name}...")
            _make_visible_recursive(local_col)

            _debug_trace(f"    [_art_reorganize] Setting collection visibility: {local_col.name}")
            local_col.hide_viewport = False
            local_col.hide_render = False

            def recursive_unrestrict_layer(layer_col, target_name):
                if layer_col.name == target_name:
                    layer_col.exclude = False
                    layer_col.hide_viewport = False
                    return True
                for child in layer_col.children:
                    if recursive_unrestrict_layer(child, target_name):
                        layer_col.exclude = False
                        layer_col.hide_viewport = False
                        return True
                return False

            _debug_trace(f"    [_art_reorganize] Recursively un-excluding layer collection: {local_col.name}")
            recursive_unrestrict_layer(context.view_layer.layer_collection, local_col.name)
            _debug_trace(f"    [_art_reorganize] Finished reorganization for {local_col.name}")
            
        # Reparent LGT-REFERENCE correctly inside +ART+
        for col in list(bpy.data.collections):
            if col.name.upper().startswith("LGT-REFERENCE"):
                _debug_trace(f"    [_art_reorganize] Moving {col.name} to {art_root.name}")
                
                for parent in list(bpy.data.collections):
                    if col.name in parent.children.keys():
                        if getattr(parent, 'override_library', None):
                            continue
                        parent.children.unlink(col)
                
                if col.name in context.scene.collection.children.keys():
                    context.scene.collection.children.unlink(col)
                    
                if col.name not in art_root.children.keys():
                    art_root.children.link(col)

def _art_retime(context):
    _ani_retime(context)

def _art_project_settings(context):
    if context.scene.butcher_debug_mode:
        log.info("[DRY RUN] Would set project settings, ART config, DOPESHEET, SEQUENCER")
        return
        
    try:
        bpy.ops.ka.apply_config(config_type='ART')
    except Exception as e:
        log.warning(f"Could not apply KA ART config: {e}")
        
    _setup_project_ui(context)

def _art_purge_data(context):
    _loc_aggressive_purge(context)

def _art_save_hero(context):
    _save_workflow_hero(context, 'ART')


def _ani_extract_scene(context):
    scene = context.scene
    base_name = scene.name
    sc, sh = get_active_shot_from_timeline(scene)
    
    if not sc or not sh:
        log.warning(f"Could not identify active shot for scene extraction from markers.")

    # Delete other scenes safely using window override if available
    for s in list(bpy.data.scenes):
        if s != scene:
            if context.scene.butcher_debug_mode:
                log.info(f"[DRY RUN] Would delete scene {s.name}")
            else:
                s_name = s.name
                _safe_remove_scene(context, s)
                log.info(f"Removed scene: {s_name}")

    def _delete_hierarchy(coll):
        for child in list(coll.children): _delete_hierarchy(child)
        for obj in list(coll.objects):
            try:
                if not context.scene.butcher_debug_mode: 
                    _safe_remove_object(context, obj)
            except: pass
        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would delete hierarchy collection: {coll.name}")
        else:
            try: 
                cname = coll.name
                _safe_remove_collection(context, coll)
                survivor = bpy.data.collections.get(cname)
                if survivor:
                    survivor.hide_viewport = True
                    survivor.hide_render = True
                    import random
                    survivor.name = f"GARBAGE_{cname}_{random.randint(1000, 9999)}"
            except: pass

    # Delete collections matching patterns
    hierarchy_delete_patterns = [
        re.compile(r"^\+ART-", re.IGNORECASE),
        re.compile(r"^\+ENV-", re.IGNORECASE),
        re.compile(r"^\+LOC-", re.IGNORECASE)
    ]
    
    simple_delete_patterns = [
        re.compile(r"^SHOT-ANI-SC", re.IGNORECASE), # Removed trailing dash to catch SHOT-ANI-SC09-ON_MOON
        re.compile(r"^\+SC", re.IGNORECASE),
        re.compile(r"^SHOT-VFX-SC", re.IGNORECASE)
        # PATCH: Odstraněno plošné rozbalování (unzip) generického "VFX-SC...", bude se mazat kompletně
    ]
    
    cam_pattern = re.compile(r"^CAM-SC", re.IGNORECASE)
    active_cam_str = f"CAM-{sc}-{sh}".upper() if (sc and sh) else ""
    active_vfx_shot_col = f"VFX-{sc}-{sh}".upper() if (sc and sh) else ""
    active_shot_str = f"{sc}-{sh}".upper() if (sc and sh) else ""

    for col in list(bpy.data.collections):
        try: cname = col.name
        except ReferenceError: continue
        cname_upper = cname.upper()
        
        # Check standard hierarchy deletes
        if any(p.search(cname) for p in hierarchy_delete_patterns):
            _delete_hierarchy(col)
            continue
            
        # VFX Shot / Environment Handling
        if cname_upper.startswith("VFX-SC"):
            if "-SH" in cname_upper:
                # Shot Collection: Hierarchy delete if not active, KEEP (unzip) if active
                if active_vfx_shot_col and cname_upper.startswith(active_vfx_shot_col):
                    if context.scene.butcher_debug_mode:
                        log.info(f"[DRY RUN] Would simple-delete (unzip) active VFX shot: {cname}")
                    else:
                        _simple_delete_collection(context, col)
                else:
                    _delete_hierarchy(col)
            else:
                # PATCH: Generický "VFX-SC..." se nyní kompletně maže včetně obsahu (delete_hierarchy)
                if context.scene.butcher_debug_mode:
                    log.info(f"[DRY RUN] Would delete hierarchy generic VFX shot wrapper: {cname}")
                else:
                    _delete_hierarchy(col)
            continue

        if cname_upper.startswith("VFX-REFERENCE-SC"):
            if "-SH" in cname_upper:
                if active_shot_str and active_shot_str in cname_upper:
                    # PATCH: Rozbalení aktivní VFX reference (Simple Delete wrapperu)
                    if context.scene.butcher_debug_mode:
                        log.info(f"[DRY RUN] Would simple-delete active VFX reference wrapper: {col.name}")
                    else:
                        _simple_delete_collection(context, col)
                else:
                    _delete_hierarchy(col)
            continue

        # Check simple collection deletes (flatten out child collections)
        if any(p.search(cname) for p in simple_delete_patterns):
            if context.scene.butcher_debug_mode:
                log.info(f"[DRY RUN] Would simple-delete collection: {col.name}")
            else:
                _simple_delete_collection(context, col)
            continue
            
        # Check CAM deletes
        if cam_pattern.search(cname) and active_cam_str:
            # Preserve exact match OR sub-collections of the active shot
            if cname_upper == active_cam_str or cname_upper.startswith(active_cam_str + "-"):
                continue
            else:
                _delete_hierarchy(col)

    # Final cleanup of any other VFX-SC-SH markers that might have been nested
    for col in list(bpy.data.collections):
        try: cname = col.name
        except ReferenceError: continue
        cname_upper = cname.upper()
        if cname_upper.startswith("VFX-SC") and "-SH" in cname_upper:
             if active_vfx_shot_col and not cname_upper.startswith(active_vfx_shot_col):
                _delete_hierarchy(col)
                
    if context.scene.sequence_editor:
        if context.scene.butcher_debug_mode:
            log.info(f"[DRY RUN] Would clear non-active video sequencer content.")
        else:
            active_shot_str = f"{sc}-{sh}".upper() if (sc and sh) else ""
            active_sh_str = sh.upper() if sh else ""
            for strip in list(context.scene.sequence_editor.sequences):
                strip_name = strip.name.upper()
                filepath = getattr(strip, "filepath", "").upper()
                keep_strip = False
                
                # Check if the sequence belongs to our shot
                if active_shot_str and (active_shot_str in strip_name or active_shot_str in filepath):
                    keep_strip = True
                elif active_sh_str and (active_sh_str in strip_name or active_sh_str in filepath):
                    keep_strip = True
                # CRITICAL: Preserve VSE guides for RELINK
                elif "-guide-" in strip_name.lower() or "-guide-" in filepath.lower():
                    keep_strip = True
                    
                if not keep_strip:
                    context.scene.sequence_editor.sequences.remove(strip)

def _ani_reorganize(context):
    if context.scene.butcher_debug_mode:
        log.info("[DRY RUN] Would reorganize ANI collections")
        return
        
    scene = context.scene
    base_name = scene.name
    sc, sh = get_active_shot_from_timeline(scene)
    
    # Rename scene to SC##-SH###-ANI (if markers exist)
    if sc and sh:
        scene.name = f"{sc.upper()}-{sh.upper()}-ANI"
    
    # e.g. +ANI-SC11-APOLLO_SITE+ -> +ANI+
    ani_col = bpy.data.collections.get(f"+ANI-{base_name}+")
    if ani_col:
        _merge_collection_to_target(ani_col, "+ANI+")

    # Rename surviving VFX collection to +VFX+
    for col in list(bpy.data.collections):
        try: cname = col.name
        except ReferenceError: continue
        cname_upper = cname.upper()
        
        if cname_upper.startswith("+VFX-SC"):
            _merge_collection_to_target(col, "+VFX+")
            _debug_trace(f"    [_ani_reorganize] Ensuring visibility for +VFX+")
            tgt = bpy.data.collections.get("+VFX+")
            if tgt:
                _make_visible_recursive(tgt)

    # Strip -SC... suffix from key collections
    # Be careful not to mutate list mid-iteration and run into reference issues or loop forever
    cols_to_rename = [c for c in bpy.data.collections if c.name.endswith(f"-{base_name}")]
    for col in cols_to_rename:
        col.name = col.name.replace(f"-{base_name}", "")
        
    # Rename the active camera collection to 'CAM'
    if sc and sh:
        active_cam_name = f"CAM-{sc.upper()}-{sh.upper()}"
        cam_col = bpy.data.collections.get(active_cam_name)
        if cam_col:
            _force_free_name("CAM")
            cam_col.name = "CAM"

def _ani_retime(context):
    scene = context.scene
    sc, sh = get_active_shot_from_timeline(scene)
    if not sc or not sh:
        log.warning("Cannot retime: Active shot not found on timeline markers.")
        return
        
    target_marker_name = f"CAM-{sc}-{sh}".upper()
    markers = sorted(list(scene.timeline_markers), key=lambda m: m.frame)
    
    start_marker = None
    end_marker = None
    
    for i, m in enumerate(markers):
        if m.name.upper() == target_marker_name:
            start_marker = m
            if i + 1 < len(markers):
                end_marker = markers[i+1]
            break
            
    if not start_marker:
        log.warning(f"Could not find start marker {target_marker_name}")
        return
        
    if context.scene.butcher_debug_mode:
        log.info(f"[DRY RUN] Would retime scene to start at frame 1001 based on {target_marker_name}")
        return
        
    shift_amount = 1001 - start_marker.frame
    
    # 1. Delete all other markers
    safe_markers = [start_marker]
    if end_marker: safe_markers.append(end_marker)
    
    for m in list(scene.timeline_markers):
        if m not in safe_markers:
            scene.timeline_markers.remove(m)
            
    # 2. Shift markers
    for m in safe_markers:
        m.frame += shift_amount
        
    if end_marker:
        end_marker.name = "END"
        
    # 3. Shift Actions
    for action in bpy.data.actions:
        for fcurve in action.fcurves:
            for key in fcurve.keyframe_points:
                key.co[0] += shift_amount
                key.handle_left[0] += shift_amount
                key.handle_right[0] += shift_amount
                
    # 4. Shift Grease Pencil
    for gp in bpy.data.grease_pencils:
        for layer in gp.layers:
            for frame in layer.frames:
                frame.frame_number += shift_amount
                
    # 5. Shift Video Sequencer Strips (Guides)
    if scene.sequence_editor:
        for strip in scene.sequence_editor.sequences_all:
            try:
                strip.frame_start += shift_amount
            except Exception as e:
                log.warning(f"Could not shift VSE strip {strip.name}: {e}")
                
    # 6. Set scene range
    scene.frame_start = 1001
    if end_marker:
        scene.frame_end = end_marker.frame
    else:
        scene.frame_end = 1100
        
    # CRITICAL: Sync playhead to new start
    scene.frame_set(1001)
    if end_marker:
        scene.frame_end = end_marker.frame

def _setup_project_ui(context):
    """
    Sets up the consistent UI for projects:
    - Base from Layout Workspace
    - Dopesheet instead of Timeline
    - Abandons programmatic 3D viewport splitting as it proves unstable across varied display configurations.
    """
    layout_ws = bpy.data.workspaces.get("Layout")
    if layout_ws and getattr(context, 'window', None):
        context.window.workspace = layout_ws

    for window in context.window_manager.windows:
        for area in list(window.screen.areas):
            if area.type == 'TIMELINE':
                area.type = 'DOPESHEET_EDITOR'

def _ani_project_settings(context):
    if context.scene.butcher_debug_mode:
        log.info("[DRY RUN] Would change to DOPESHEET, set ANI config, lock ANI-REFERENCE")
        return
        
    # Apply render config
    try:
        bpy.ops.ka.apply_config(config_type='ANI')
    except Exception as e:
        log.warning(f"Could not apply KA ANI config: {e}")

    # Configure Output for preview export (náhledy) - Merged from VFX
    scene = context.scene
    scene.render.image_settings.file_format = 'FFMPEG'
    scene.render.ffmpeg.format = 'MPEG4'
    scene.render.ffmpeg.codec = 'H264'
    scene.render.ffmpeg.constant_rate_factor = 'MEDIUM'
    scene.render.ffmpeg.ffmpeg_preset = 'GOOD'
    scene.render.ffmpeg.gopsize = 12
        
    _setup_project_ui(context)
            
    # Lock selectability and visibility for all references
    reference_prefixes = ["ANI-REFERENCE", "ART-REFERENCE", "VFX-REFERENCE"]
    for col in bpy.data.collections:
        if any(col.name.upper().startswith(prefix) for prefix in reference_prefixes):
            col.hide_select = True
            col.hide_viewport = False
            col.hide_render = True
            
            # Disable exclusion in the active view layer (forces the checkmark ON)
            def recursive_unexclude(layer_col, target_name):
                if layer_col.name == target_name:
                    layer_col.exclude = False
                    return True
                for child in layer_col.children:
                    if recursive_unexclude(child, target_name):
                        return True
                return False

            recursive_unexclude(context.view_layer.layer_collection, col.name)

def _ani_purge_data(context):
    _loc_aggressive_purge(context)


def parse_shot_filename(filename):
    """
    Parses a filename to extract SC and SH numbers.
    Matches: ...SC04...SH010... (Case Insensitive)
    """
    match = re.search(r"(sc\d+).+?(sh\d+)", filename, re.IGNORECASE)
    if match:
        return match.group(1).upper(), match.group(2).upper()
    return None, None

def _get_dynamic_target_dir(dir_path, filename, mode, default_folder):
    """
    Determines the target directory based on the filename and mode.
    """
    if mode == 'LOC':
        # E.g. LOCATION/
        return os.path.join(dir_path, "LOCATION")
    
    if mode == 'ENV':
        # Extract environment name if possible, e.g. Props_ENV_City -> ENV_City
        match = re.search(r"ENV[_-]?([a-zA-Z0-9]+)", filename, re.IGNORECASE)
        env_folder = f"ENV_{match.group(1)}" if match else "ENV"
        return os.path.join(dir_path, env_folder)
        
    if mode == 'PREL':
        return os.path.join(dir_path, "PRELIGHT")
        
    sc, sh = parse_shot_filename(filename)
    
    if mode in ['ANI', 'ART'] and sc and sh:
        return os.path.join(dir_path, sc, sh, default_folder)
        
    # Fallback to default
    return os.path.join(dir_path, default_folder)

def _perform_save_as(context, folder_name, suffix_check, suffix_add, mode):
    filepath = bpy.data.filepath
    dir_path = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name_part, ext = os.path.splitext(filename)
    
    target_dir = _get_dynamic_target_dir(dir_path, filename, mode, folder_name)
    
    # Remove existing matching mode suffixes to prevent duplicates if ran multiple times
    clean_name = re.sub(f"[_-]{mode}$", "", name_part, flags=re.IGNORECASE)
    
    new_filename = clean_name
    if not new_filename.endswith(suffix_check):
        new_filename += suffix_add
    new_filepath = os.path.join(target_dir, new_filename + ext)
        
    if context.scene.butcher_debug_mode:
        if not os.path.exists(target_dir):
            log.info(f"[DRY RUN] Would create directory: {target_dir}")
        log.info(f"[DRY RUN] Would save {mode} file to: {new_filepath}")
        return

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        log.info(f"Created directory: {target_dir}")
    
    bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=False)
    log.info(f"Switched to {mode} file: {new_filepath}")

def _delete_other_scenes(context):
    for s in list(bpy.data.scenes):
        if s != context.scene:
            if context.scene.butcher_debug_mode:
                log.info(f"[DRY RUN] Would remove scene: {s.name}")
            else:
                s_name = s.name
                _safe_remove_scene(context, s)
                log.info(f"Removed scene: {s_name}")

def _clean_collections(context, keep_keywords, remove_regex_list):
    compiled_remove = [re.compile(p, re.IGNORECASE) for p in remove_regex_list]
    for col in list(bpy.data.collections):
        try:
            cname_original = col.name
            cname = cname_original.upper()
        except ReferenceError:
            continue
            
        should_keep = False
        for k in keep_keywords:
            if k in cname:
                should_keep = True
                break
        if should_keep:
            continue
            
        for pattern in compiled_remove:
            if pattern.search(cname):
                if context.scene.butcher_debug_mode:
                    log.info(f"[DRY RUN] Would remove collection: {cname_original}")
                else:
                    try:
                        _safe_remove_collection(context, col)
                        log.info(f"Removed collection: {cname_original}")
                    except Exception as e:
                        log.warning(f"Could not remove {cname_original}: {e}")
                break

def _clean_objects(context, remove_types, clear_animation=True):
    objs_to_remove = []
    for obj in bpy.data.objects:
        if obj.type in remove_types:
            objs_to_remove.append(obj)
            continue
        if clear_animation and obj.animation_data:
            if context.scene.butcher_debug_mode:
                 pass # Too noisy to log every animation clear
            else:
                 obj.animation_data_clear()
            
    for obj in objs_to_remove:
        if context.scene.butcher_debug_mode:
             pass # Will log summary
        else:
             try:
                 _safe_remove_object(context, obj)
             except: pass
             
    if context.scene.butcher_debug_mode:
        log.info(f"[DRY RUN] Would remove {len(objs_to_remove)} objects of types {remove_types}")
    else:
        log.info(f"Removed {len(objs_to_remove)} objects of types {remove_types}")

def _reset_view(context):
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            for region in area.regions:
                if region.type == 'WINDOW':
                    with context.temp_override(area=area, region=region):
                         bpy.ops.view3d.view_all(center=True)
                    break
    log.info("View Reset.")


# --- Step Definitions ---

def get_processing_steps(context, mode):
    """
    Returns a list of tuples: (Step Name, Callable Action)
    """
    steps = []
    
    # 1. Common Init
    steps.append(("Check Saved", _check_save))
    
    # 2. Mode Specifics
    if mode == 'LOC':
        # The new Miro LOC Pipeline Steps
        steps.append(("Save LOC Work", lambda: _save_loc_work(context)))
        steps.append(("Extract Scene", lambda: _loc_extract_scene(context)))
        steps.append(("Scene Models Visible", lambda: _loc_models_visible(context)))
        steps.append(("Reset Window Layout", lambda: _loc_reset_layout(context)))
        steps.append(("Purge Data", lambda: _loc_aggressive_purge(context)))
        steps.append(("Save LOC Hero (Native)", lambda: _save_loc_hero(context)))
        
        # LOC skips the generic finishers below
        return steps

    elif mode == 'ENV':
        steps.append(("CREATE FOLDER (SAVE AS ENVIRO)", lambda: _perform_save_as(context, "ENV", "_ENV", "_ENV", "ENV")))
        steps.append(("Delete Scenes", lambda: _delete_other_scenes(context)))
        steps.append(("Clean Collections", lambda: _clean_collections(context,
            ['ENVIRONMENT', 'ASSEMBLY', 'ENV-'], 
            [r"(^|[\W_])LOC([\W_]|$)", r"(^|[\W_])ANI([\W_]|$)", 
             r"(^|[\W_])VFX([\W_]|$)", r"(^|[\W_])ART([\W_]|$)", r"(^|[\W_])SC\d*([\W_]|$)"]
        )))
        steps.append(("Clean Objects", lambda: _clean_objects(context,
            {'LIGHT', 'CAMERA', 'SPEAKER', 'LIGHT_PROBE', 'VOLUME'}, clear_animation=True
        )))

    elif mode == 'ANI':
        # PATCH: Odstraněno automatické Prepare References. Bude se spouštět pouze manuálně tlačítkem.
        steps.append(("Save ANI Work", lambda: _ani_save_work(context)))
        steps.append(("Extract Scene", lambda: _ani_extract_scene(context)))
        steps.append(("Reorganize Collections", lambda: _ani_reorganize(context)))
        steps.append(("Retime Scene", lambda: _ani_retime(context)))
        steps.append(("Project Settings", lambda: _ani_project_settings(context)))
        steps.append(("Purge Data", lambda: _ani_purge_data(context)))
        steps.append(("Save ANI Hero", lambda: _ani_save_hero(context)))
        return steps

    elif mode == 'ART':
        steps.append(("Save ART Work", lambda: _art_save_work(context)))
        steps.append(("Extract Scene", lambda: _art_extract_scene(context)))
        steps.append(("Reorganize Collections", lambda: _art_reorganize(context)))
        steps.append(("Retime Scene", lambda: _art_retime(context)))
        steps.append(("Project Settings", lambda: _art_project_settings(context)))
        steps.append(("Purge Data", lambda: _art_purge_data(context)))
        steps.append(("Save ART Hero", lambda: _art_save_hero(context)))
        return steps

    else:
        log.warning(f"No specific steps for mode {mode}")
        return []

    # 3. Common Finishers
    steps.append(("Purge Data", lambda: log.info("[DRY RUN] Would recursive purge orphaned blocks") if context.scene.butcher_debug_mode else recursive_purge()))
    steps.append(("Reset View", lambda: _reset_view(context)))
    steps.append(("Create Hero", lambda: log.info("[DRY RUN] Would Publish Hero & Work files") if context.scene.butcher_debug_mode else bpy.ops.butcher.publish()))
    steps.append(("Create Hero", lambda: bpy.ops.butcher.publish()))
    
    return steps


def run_all_steps(context, mode):
    steps = get_processing_steps(context, mode)
    if not steps:
        return "No Steps Found"
        
    for name, action in steps:
        log.info(f"== EXEC STEP: {name} ==")
        
        # --- PATCH 2: Start Timer ---
        start_time = time.time()
        
        action()
        
        # --- PATCH 2: End Timer ---
        duration = time.time() - start_time
        log.info(f"[PROFILER] Completed '{name}' in {duration:.3f} seconds.")
        
    # Full redraw sync before yielding control back to Blender
    context.view_layer.update()
    
    # LOC specific: Return to original layout file
    if mode == 'LOC':
        orig = context.window_manager.get('butcher_original_file', "")
        if orig and os.path.exists(orig):
            log.info(f"Returning to original layout file via timer: {orig}")
            def open_orig():
                try:
                    bpy.ops.wm.open_mainfile(filepath=orig)
                except Exception as e:
                    log.error(f"Failed to reopen layout file: {e}")
                return None
            bpy.app.timers.register(open_orig, first_interval=1.0)
                
    return f"{mode} Process Complete"


# --- Operators ---

class BUTCHER_OT_cleanup(Operator):
    bl_idname = "butcher.cleanup"
    bl_label = "Butch"
    bl_description = "Runs the entire cleanup process for the detected context"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Save original filepath to return to if doing LOC
        context.window_manager['butcher_original_file'] = bpy.data.filepath
        
        selected_shots = [s.name for s in context.scene.butcher_shot_list if s.is_selected]
        if selected_shots:
            bpy.ops.butcher.batch_process()
            return {'FINISHED'}

        mode = get_current_mode(context)
        try:
            res = run_all_steps(context, mode)
            self.report({'INFO'}, res)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class BUTCHER_OT_run_step(Operator):
    bl_idname = "butcher.run_step"
    bl_label = "Run Step"
    bl_description = "Runs a single step of the cleanup process"
    bl_options = {'REGISTER', 'UNDO'}
    
    mode: StringProperty()
    step_index: IntProperty()
    step_name: StringProperty()

    def execute(self, context):
        steps = get_processing_steps(context, self.mode)
        if 0 <= self.step_index < len(steps):
            name, action = steps[self.step_index]
            log.info(f"== DEBUG STEP: {name} ==")
            try:
                action()
                self.report({'INFO'}, f"Completed: {name}")
            except Exception as e:
                self.report({'ERROR'}, f"Failed {name}: {e}")
                return {'CANCELLED'}
        return {'FINISHED'}


class BUTCHER_OT_publish(Operator):
    bl_idname = "butcher.publish"
    bl_label = "Publish"
    bl_description = "Save WORK and HERO versions"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        if not bpy.data.is_saved:
            self.report({'ERROR'}, "Save file first.")
            return {'CANCELLED'}

        mode = get_current_mode(context)
        filepath = bpy.data.filepath
        dir_path, filename = os.path.split(filepath)
        name_part, ext = os.path.splitext(filename)

        # Cleanup Name: Remove existing mode suffix to avoid duplicates (e.g. File_LOC-LOC-HERO)
        # Regex to strip -LOC, _LOC, -ENV, _ENV etc from end
        clean_name = re.sub(f"[_-]{mode}$", "", name_part, flags=re.IGNORECASE)
        
        work_path = os.path.join(dir_path, f"{clean_name}-{mode}-WORK{ext}")
        hero_path = os.path.join(dir_path, f"{clean_name}-{mode}-HERO{ext}")

        # Explicitly save the current open file BEFORE shifting OS paths
        bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=False)

        os_bridge = get_os_bridge(context)
        if os_bridge:
            os_bridge.run_bridge_to_windows(context)

        bpy.ops.wm.save_as_mainfile(filepath=work_path, copy=True)
        bpy.ops.wm.save_as_mainfile(filepath=hero_path, copy=True)
        
        if os_bridge:
            os_bridge.run_bridge_to_mac(context, force=False)

        self.report({'INFO'}, f"Published: {mode} HERO & WORK")
        return {'FINISHED'}


class BUTCHER_OT_prepare(Operator):
    bl_idname = "butcher.prepare"
    bl_label = "Prepare (Create References)"
    bl_description = "Creates reference collections and moves actors/props into them"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mode = get_current_mode(context)
        if mode != 'ANI':
            self.report({'WARNING'}, "Prepare is only applicable to ANI mode.")
            return {'CANCELLED'}
            
        try:
            _prepare_references(context, mode)
            self.report({'INFO'}, "References prepared.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to prepare references: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


# --- NEW RELINK LOGIC FUNCTIONS ---

def _clear_relink_log():
    txt = bpy.data.texts.get("BUTCHER_RELINK_LOG")
    if txt: txt.clear()
    else: bpy.data.texts.new("BUTCHER_RELINK_LOG")

def _log_relink(msg):
    txt = bpy.data.texts.get("BUTCHER_RELINK_LOG")
    if txt: txt.write(msg + "\n")
    log.info(msg)

def _extract_loc_name_from_vse(context):
    """
    Scans the VSE for the active guide strip and uses a two-step strategy
    to perfectly extract the root Location Name.
    """
    scene = context.scene
    if not scene.sequence_editor:
        _log_relink("  [VSE] Nenalezen Sequence Editor, hledání lokace selhalo.")
        return None
        
    for strip in scene.sequence_editor.sequences:
        filepath = getattr(strip, "filepath", "")
        if filepath and "-guide-" in filepath.lower():
            _log_relink(f"  [VSE] Nalezen GUIDE strip s cestou: {filepath}")
            
            # Normalize path for OS-agnostic splitting
            norm_path = os.path.normpath(filepath)
            path_parts = norm_path.split(os.sep)
            video_filename = os.path.basename(norm_path).lower()
            
            # --- STRATEGY 1: Explicit Folder Search ---
            # Walk backwards looking for LAYOUT- or LOC- folders
            for part in reversed(path_parts[:-1]):
                part_upper = part.upper()
                if part_upper.startswith("LAYOUT-") or part_upper.startswith("LOC-"):
                    # Ignore structural folders containing these keywords
                    if "GUIDES" in part_upper or "WORK" in part_upper or "HERO" in part_upper:
                        continue
                    
                    loc = part_upper.replace("LAYOUT-", "").replace("LOC-", "")
                    _log_relink(f"  [VSE] Extraktována Lokace (Primary Search): {loc}")
                    return loc

            # --- STRATEGY 2: Video Filename Heuristic (Fallback) ---
            # If explicit folder fails, check folders against the video filename
            for part in reversed(path_parts[:-1]):
                part_upper = part.upper()
                
                # Extract the core name of the folder (e.g., 'SC09-ALAN_IMPACT' -> 'alan_impact')
                base_name = part_upper.split('-')[-1].lower()
                
                # If the folder name is explicitly in the video filename, it's a sub-env/shot! Skip it.
                if base_name in video_filename:
                    _log_relink(f"  [VSE] Přeskakuji složku '{part}', protože '{base_name}' je obsaženo v názvu videa.")
                    continue
                
                # We found the first folder not mentioned in the video name!
                parts = part_upper.split('-')
                if len(parts) >= 2 and re.match(r"^SC\d+$", parts[0], re.IGNORECASE):
                    loc = "-".join(parts[1:]).upper()
                else:
                    loc = parts[-1].upper()
                    
                _log_relink(f"  [VSE] Extraktována Lokace (Heuristic Fallback): {loc}")
                return loc
                
    _log_relink("  [VSE] Na timeline není žádný strip s tagem '-guide-'. Nemám jak zjistit lokaci.")
    return None

def _find_workflow_hero_filepath(context, sc, sh, mode_tag, master_file_path=None):
    sc_upper = sc.upper()
    sh_upper = sh.upper()
    
    filename = os.path.basename(master_file_path if master_file_path else bpy.data.filepath)
    project_match = re.match(r"^(\d+)-", filename.lower())
    project_id = project_match.group(1) if project_match else "3212"
    
    master_sh_dir = get_production_scene_dir(context, sc_upper, sh_upper)
    if master_sh_dir:
        hero_dir = os.path.join(master_sh_dir, f"{sc_upper}-{sh_upper}-HERO", f"{sc_upper}-{sh_upper}-{mode_tag}-HERO")
    else:
        dir_path = os.path.dirname(master_file_path if master_file_path else bpy.data.filepath)
        hero_dir = dir_path.replace("-WORK", "-HERO").replace("_WORK", "_HERO")
        
    new_filename = f"{project_id}-{sc_upper.lower()}-{sh_upper.lower()}-{mode_tag.lower()}-hero.blend"
    return os.path.normpath(os.path.join(hero_dir, new_filename))

def _find_loc_hero_filepath(context, loc_name, master_file_path=None):
    if not loc_name: return None
    loc_name_upper = loc_name.upper()
    loc_name_lower = loc_name.lower()
    
    filename = os.path.basename(master_file_path if master_file_path else bpy.data.filepath)
    project_match = re.match(r"^(\d+)-", filename.lower())
    project_id = project_match.group(1) if project_match else "3212"
    
    os_bridge = get_os_bridge(context)
    mac_root = os_bridge.get_mac_root(context) if os_bridge else None
    
    if mac_root:
        from pathlib import Path
        shared_drives = Path(mac_root).parent
        preprod_root = shared_drives / f"{project_id}-PREPRODUCTION"
        hero_dir = preprod_root / "LIBRARY" / "LIBRARY-HERO" / "LOCATION-HERO" / f"LOC-{loc_name_upper}-HERO"
        hero_dir = str(hero_dir)
    else:
        dir_path = os.path.dirname(master_file_path if master_file_path else bpy.data.filepath)
        base_dir = dir_path
        for _ in range(6):
            bn = os.path.basename(base_dir).upper()
            if "PRODUCTION" in bn:
                base_dir = os.path.dirname(base_dir)
                break
            parent = os.path.dirname(base_dir)
            if parent == base_dir: break
            base_dir = parent
        
        hero_dir = os.path.join(base_dir, f"{project_id}-PREPRODUCTION", "LIBRARY", "LIBRARY-HERO", "LOCATION-HERO", f"LOC-{loc_name_upper}-HERO")
    
    new_filename = f"{project_id}-loc-{loc_name_lower}-hero.blend"
    return os.path.normpath(os.path.join(hero_dir, new_filename))

def _find_latest_work_filepath(context, sc, sh, mode_tag):
    sc_upper = sc.upper()
    sh_upper = sh.upper()

    master_sh_dir = get_production_scene_dir(context, sc_upper, sh_upper)
    if not master_sh_dir: return None

    target_dir = os.path.join(master_sh_dir, f"{sc_upper}-{sh_upper}-WORK", f"{sc_upper}-{sh_upper}-{mode_tag}-WORK")
    if not os.path.exists(target_dir): return None

    existing_files = glob.glob(os.path.join(target_dir, "*.blend"))
    highest_version = -1
    latest_file = None

    for f in existing_files:
        v_match = re.search(r'-v(\d{3,})', os.path.basename(f), re.IGNORECASE)
        if v_match:
            v_num = int(v_match.group(1))
            if v_num > highest_version:
                highest_version = v_num
                latest_file = f

    return latest_file

def _save_relink_version(context, sc, sh, mode_tag):
    """
    Saves the current open WORK file as a `-relink` increment before applying new links.
    """
    filepath = bpy.data.filepath
    dir_path = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name_part, ext = os.path.splitext(filename)

    # Find and cleanly strip any old version number to get the pure base name
    version_match = re.search(r'-v\d{3,}', name_part, re.IGNORECASE)
    if version_match:
        base_name = name_part[:version_match.start()]
    else:
        base_name = name_part

    # --- Folder-Exclusive Versioning ---
    existing_files = glob.glob(os.path.join(dir_path, "*.blend"))
    highest_version = -1
    
    for f in existing_files:
        v_match = re.search(r'-v(\d{3,})', os.path.basename(f), re.IGNORECASE)
        if v_match:
            highest_version = max(highest_version, int(v_match.group(1)))
    
    new_version_int = highest_version + 1
    # -----------------------------------
    
    clean_user = get_current_user()

    project_match = re.match(r"^(\d+)-", base_name)
    project_id = project_match.group(1) if project_match else "3212"
    
    new_filename = f"{project_id}-{sc.lower()}-{sh.lower()}-{mode_tag.lower()}-v{new_version_int:03d}-{clean_user}-butch_relink{ext}"
    new_filepath = os.path.join(dir_path, new_filename)

    bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=False)
    log.info(f"Saved relink version: {new_filepath}")

def _link_collection_from_hero(context, hero_filepath, col_name, target_parent, prefix_match=False):
    """
    Links a collection from a hero file into the target parent collection WITHOUT INSTANCING.
    Returns a LIST of linked collections so callers can handle multiple prefix matches properly.
    """
    if not hero_filepath or not os.path.exists(hero_filepath):
        _log_relink(f"  [CHYBA] Hero soubor nenalezen na disku: {hero_filepath}")
        return []
    
    # Failsafe: Check if it already exists in target_parent to avoid duplicates
    for child in target_parent.children:
        if prefix_match and child.name.upper().startswith(col_name.upper()):
            _log_relink(f"  [INFO] Kolekce začínající '{col_name}' už je v '{target_parent.name}' nalinkovaná jako '{child.name}'. Přeskakuji.")
            return [child]
        elif not prefix_match and child.name.upper() == col_name.upper():
            _log_relink(f"  [INFO] Kolekce '{col_name}' už je v '{target_parent.name}' nalinkovaná. Přeskakuji.")
            return [child]

    linked_cols = []
    try:
        with bpy.data.libraries.load(hero_filepath, link=True) as (data_from, data_to):
            if prefix_match:
                matched_cols = [c for c in data_from.collections if c.upper().startswith(col_name.upper())]
                data_to.collections = matched_cols
            else:
                if col_name in data_from.collections:
                    data_to.collections = [col_name]
                else:
                    available_cols = list(data_from.collections)
                    _log_relink(f"  [CHYBA] V souboru '{hero_filepath}' neexistuje kolekce s názvem '{col_name}'.")
                    return []
    except Exception as e:
        _log_relink(f"  [CHYBA] Selhalo načtení knihovny {hero_filepath}: {e}")
        return []

    for col in data_to.collections:
        if col and col.name not in target_parent.children:
            target_parent.children.link(col) # Direct native linking (no instancing empty)
            _log_relink(f"  [ÚSPĚCH] Nalinkována kolekce: {col.name}")
            linked_cols.append(col)
            
    return linked_cols

def _relink_art(context, sc=None, sh=None):
    _log_relink("\n====== SPUŠTĚN ART RELINK ======")
    scene = context.scene
    if not sc or not sh:
        sc, sh = get_active_shot_from_timeline(scene)
    if not (sc and sh): raise Exception("No SC/SH detected.")

    # Target: Root Scene Collection for workflow targets (+ANI+, +VFX+)
    root_col = scene.collection
    
    # Target: +ART+ collection for environments and assemblies
    art_col = bpy.data.collections.get("+ART+")
    if not art_col: art_col = root_col
    
    # 1. Link +ANI+ and +VFX+ into Root Collection
    _log_relink("--> Linkování +ANI+")
    ani_hero = _find_workflow_hero_filepath(context, sc, sh, "ANI", _relink_master_filepath if '_relink_master_filepath' in globals() else None)
    _link_collection_from_hero(context, ani_hero, "+ANI+", root_col, prefix_match=True)
    
    _log_relink("--> Linkování +VFX+")
    _link_collection_from_hero(context, ani_hero, "+VFX+", root_col, prefix_match=True)

    # 2. Setup LINKED structure strictly under +ART+
    std_col = art_col.children.get("STD")
    if not std_col:
        std_col = bpy.data.collections.new("STD")
        art_col.children.link(std_col)
    
    linked_col = std_col.children.get("LINKED")
    if not linked_col:
        linked_col = bpy.data.collections.new("LINKED")
        std_col.children.link(linked_col)
        
    # --- PRECISE LOC/ENV LINKING ---
    env_target = scene.get("butcher_env_target")
    if not env_target:
        error_msg = "  [KRITICKÁ CHYBA] Chybí property 'butcher_env_target' (Lokace nenalezena v názvu root kolekce během Butch). Přerušuji relink."
        _log_relink(error_msg)
        raise Exception(error_msg)
        
    _log_relink("--> Získávání jména lokace z VSE")
    loc_name = _extract_loc_name_from_vse(context)
    
    if not loc_name:
        error_msg = "  [KRITICKÁ CHYBA] Nelze získat lokaci z VSE. VSE guide je povinný pro ART relink! Přerušuji relink."
        _log_relink(error_msg)
        raise Exception(error_msg)
    else:
        _log_relink(f"--> Linkování LOC/ENV/SC... (Zdroj: {loc_name}, Cíl: {env_target})")
        loc_hero = _find_loc_hero_filepath(context, loc_name, _relink_master_filepath if '_relink_master_filepath' in globals() else None)
        if loc_hero:
            # Precise naming based on extracted target
            env_col_name = f"+ENV-{env_target}+"
            sc_col_name = f"+{sc.upper()}-{env_target}+"
            
            # Link exact collections
            loc_linked = _link_collection_from_hero(context, loc_hero, "+LOC-", linked_col, prefix_match=True)
            env_linked = _link_collection_from_hero(context, loc_hero, env_col_name, linked_col, prefix_match=False)
            sc_linked = _link_collection_from_hero(context, loc_hero, sc_col_name, linked_col, prefix_match=False)
            
            # --- FIX DUPLICATES: Unlink from root if they exist there ---
            _log_relink("--> Čištění duplikátů (Odlinkování z Root scény)")
            
            all_linked = []
            if loc_linked: all_linked.extend(loc_linked)
            if env_linked: all_linked.extend(env_linked)
            if sc_linked: all_linked.extend(sc_linked)
            
            for linked_item in all_linked:
                if linked_item and linked_item.name in root_col.children:
                    try:
                        root_col.children.unlink(linked_item)
                        _log_relink(f"  [CLEANUP] Odstraněn duplikát z rootu scény: {linked_item.name}")
                    except Exception as e:
                        _log_relink(f"  [CLEANUP CHYBA] Nelze odlinkovat z rootu: {e}")
        else:
            _log_relink(f"  [CHYBA] Nenalezen Hero soubor pro lokaci: {loc_name}")

def _relink_ani(context, sc=None, sh=None):
    _log_relink("\n====== SPUŠTĚN ANI RELINK ======")
    scene = context.scene
    if not sc or not sh:
        sc, sh = get_active_shot_from_timeline(scene)
    if not (sc and sh): raise Exception("No SC/SH detected.")

    # Target: Root Scene Collection
    root_col = scene.collection

    # DIRECT LINKING +ART+ TO ROOT LEVEL
    _log_relink("--> Linkování +ART+ do hlavní scény")
    art_hero = _find_workflow_hero_filepath(context, sc, sh, "ART", _relink_master_filepath if '_relink_master_filepath' in globals() else None)
    _link_collection_from_hero(context, art_hero, "+ART+", root_col, prefix_match=True)


# --- Batch Process Logic ---

def get_all_butcher_shots(context):
    scene = context.scene
    shot_markers = [m for m in scene.timeline_markers if re.match(r"CAM-SC\d+-SH\d+", m.name, re.IGNORECASE)]
    return sorted(shot_markers, key=lambda m: m.frame)

class BUTCHER_ShotListItem(bpy.types.PropertyGroup):
    name: StringProperty()
    display_name: StringProperty()
    is_selected: BoolProperty(name="", description="Include this shot in the batch preparation", default=True)
    frame: IntProperty()

class BUTCHER_UL_shot_list(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "is_selected", text=item.display_name)
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text=item.display_name)

class BUTCHER_OT_select_all_shots(Operator):
    bl_idname = "butcher.select_all_shots"
    bl_label = "Select All"
    bl_description = "Select or Deselect all shots"
    
    action: EnumProperty(
        items=[('SELECT', "Select", ""), ('DESELECT', "Deselect", "")],
        default='SELECT'
    )

    def execute(self, context):
        for shot in context.scene.butcher_shot_list:
            shot.is_selected = (self.action == 'SELECT')
        return {'FINISHED'}

class BUTCHER_OT_refresh_shot_list(Operator):
    bl_idname = "butcher.refresh_shot_list"
    bl_label = "Refresh Shot List"

    def execute(self, context):
        shot_list = context.scene.butcher_shot_list
        shot_list.clear()
        found_shots = get_all_butcher_shots(context)
        for marker in found_shots:
            item = shot_list.add()
            item.name = marker.name
            name_match = re.match(r"CAM-(SC\d+-SH\d+)", marker.name, re.IGNORECASE)
            if name_match:
                item.display_name = name_match.group(1)
            else:
                item.display_name = marker.name 
            item.frame = marker.frame

        context.scene.butcher_active_shot_index = 0
        log.info(f"Found and listed {len(found_shots)} shots for Butcher.")
        return {'FINISHED'}

_batch_queue = []
_batch_master_filepath = ""
_batch_processed_count = 0
_batch_total_count = 0
_batch_force_mode = "AUTO"

def _process_next_batch_step():
    global _batch_queue, _batch_master_filepath, _batch_processed_count, _batch_total_count, _batch_force_mode
    
    if not _batch_queue:
        msg = f"Batch Process complete. Successfully processed: {_batch_processed_count}/{_batch_total_count}."
        log.info(msg)
        if _batch_master_filepath:
            try:
                bpy.ops.wm.open_mainfile(filepath=_batch_master_filepath)
            except:
                pass
        _close_batch_file_logger()
        return None  # Stop timer
        
    shot_name = _batch_queue.pop(0)
    log.info(f"--- Batch Process [BUTCH]: {shot_name} ---")
    
    try:
        bpy.ops.wm.open_mainfile(filepath=_batch_master_filepath)
        current_context = bpy.context
        current_scene = current_context.scene
        
        shot_marker = current_scene.timeline_markers.get(shot_name)
        if not shot_marker:
            log.error(f"Marker '{shot_name}' missing after reload. Skipping.")
            return 0.1
            
        current_scene.frame_set(shot_marker.frame)
        
        mode = _batch_force_mode
        if mode == 'AUTO':
            mode = get_current_mode(current_context)
            
        if mode in ['AUTO', 'UNKNOWN']:
            log.error(f"Cannot determine valid Workflow Mode for {shot_name}. Skipping.")
            return 0.1
            
        log.info(f"Detected mode [{mode}] for {shot_name}")
        
        steps = get_processing_steps(current_context, mode)
        step_success = True
        for step_name, action in steps:
            log.info(f"  -> Executing: {step_name}")
            
            # --- PATCH 2: Profiling ---
            start_time = time.time()
            try: 
                action()
                duration = time.time() - start_time
                log.info(f"  -> [PROFILER] Completed '{step_name}' in {duration:.3f} seconds.")
            except Exception as e:
                log.error(f"FAILED {step_name} on {shot_name}: {e}")
                step_success = False
                break
                
        if step_success: 
            _batch_processed_count += 1
                
    except Exception as e:
        log.error(f"FAILED on {shot_name}: {e}")
        
    return 0.1

class BUTCHER_OT_batch_process(Operator):
    bl_idname = "butcher.batch_process"
    bl_label = "Batch Process Internal"
    bl_description = "Reloads the master file and processes each selected shot."
    
    def execute(self, context):
        global _batch_queue, _batch_master_filepath, _batch_processed_count, _batch_total_count, _batch_force_mode
        
        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the main project file first.")
            return {"CANCELLED"}
            
        if bpy.app.timers.is_registered(_process_next_batch_step):
            self.report({"ERROR"}, "A batch process is already running.")
            return {"CANCELLED"}
            
        _batch_master_filepath = bpy.data.filepath
        _setup_batch_file_logger(_batch_master_filepath)
        
        _batch_force_mode = get_current_mode(context)
        
        selected_shots = [s.name for s in context.scene.butcher_shot_list if s.is_selected]
        if not selected_shots:
            self.report({"WARNING"}, "No shots selected from the list.")
            return {"CANCELLED"}
            
        log.info(f"Starting batch Butcher for {len(selected_shots)} shots.")
        
        _batch_queue = selected_shots
        _batch_processed_count = 0
        _batch_total_count = len(selected_shots)
        
        bpy.app.timers.register(_process_next_batch_step, first_interval=0.1, persistent=True)
        self.report({'INFO'}, f"Batch Butcher started for {len(selected_shots)} shots.")
        return {'FINISHED'}


# === BATCH RELINK QUEUE LOGIC ===
_relink_queue = []
_relink_master_filepath = ""
_relink_processed_count = 0
_relink_total_count = 0
_relink_force_mode = "AUTO"

def _process_next_relink_step():
    global _relink_queue, _relink_master_filepath, _relink_processed_count, _relink_total_count, _relink_force_mode
    if not _relink_queue:
        log.info(f"Relink Batch complete. Processed: {_relink_processed_count}/{_relink_total_count}")
        if _relink_master_filepath:
            try: bpy.ops.wm.open_mainfile(filepath=_relink_master_filepath)
            except: pass
        return None

    shot_name = _relink_queue.pop(0)
    try:
        match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_name, re.IGNORECASE)
        if not match: return 0.1
        sc, sh = match.group(1).upper(), match.group(2).upper()
        mode = _relink_force_mode

        latest_work = _find_latest_work_filepath(bpy.context, sc, sh, mode)
        if not latest_work:
            log.warning(f"No WORK file found for {sc}-{sh} [{mode}]. Skipping.")
            return 0.1

        # Open the WORK file
        bpy.ops.wm.open_mainfile(filepath=latest_work)
        current_context = bpy.context

        # Save new -relink version
        _save_relink_version(current_context, sc, sh, mode)

        # Reload existing libraries
        for lib in bpy.data.libraries: lib.reload()

        # Perform actual relink
        if mode == 'ART': _relink_art(current_context, sc, sh)
        elif mode == 'ANI': _relink_ani(current_context, sc, sh)

        # Save the changes & Publish Hero automatically, triggering the Post-Hero Increment!
        bpy.ops.wm.save_mainfile()
        _save_workflow_hero(current_context, mode, create_blocking=True)
        
        _relink_processed_count += 1
    except Exception as e:
        log.error(f"Failed to Relink {shot_name}: {e}")

    return 0.1

class BUTCHER_OT_relink(Operator):
    bl_idname = "butcher.relink"
    bl_label = "Relink"
    bl_description = "Reload libraries and relink workflow targets (Batch capable)"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        global _relink_queue, _relink_master_filepath, _relink_processed_count, _relink_total_count, _relink_force_mode
        
        _clear_relink_log()

        selected_shots = [s.name for s in context.scene.butcher_shot_list if s.is_selected]

        # Single file manual relink
        if not selected_shots:
            mode = get_current_mode(context)
            try:
                for lib in bpy.data.libraries: lib.reload()
                if mode == 'ART': 
                    _relink_art(context)
                elif mode == 'ANI': 
                    _relink_ani(context)
                    
                # Auto Publish Hero for single files and trigger Post-Hero Increment!
                _save_workflow_hero(context, mode, create_blocking=True)
                self.report({'INFO'}, f"Relinked {mode} workflow targets & Published Hero.")
            except Exception as e: 
                self.report({'ERROR'}, f"Relink Failed: {e}")
            return {'FINISHED'}

        # Batch Relink
        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the master file first.")
            return {"CANCELLED"}

        _relink_master_filepath = bpy.data.filepath

        # Ensure explicit override for batch processing
        override_mode = context.scene.butcher_workflow_mode
        if override_mode in ['ANI', 'ART']:
            _relink_force_mode = override_mode
        else:
            _relink_force_mode = get_current_mode(context)
            if _relink_force_mode not in ['ANI', 'ART']:
                self.report({'ERROR'}, "Please specify 'ANI' or 'ART' in the Workflow dropdown for batch relinking.")
                return {'CANCELLED'}

        _relink_queue = selected_shots
        _relink_total_count = len(selected_shots)
        _relink_processed_count = 0

        bpy.app.timers.register(_process_next_relink_step, first_interval=0.1, persistent=True)
        self.report({'INFO'}, f"Batch Relink started for {len(selected_shots)} shots.")
        return {'FINISHED'}


# === NEW COMBO BATCH & RELINK LOGIC ===

_combo_batch_queue = []
_combo_master_filepath = ""
_combo_processed_count = 0
_combo_total_count = 0

_combo_relink_queue = []
_combo_relink_master_filepath = ""
_combo_relink_processed_count = 0
_combo_relink_total_count = 0

def _process_next_combo_batch_step():
    global _combo_batch_queue, _combo_master_filepath, _combo_processed_count, _combo_total_count

    if not _combo_batch_queue:
        msg = f"Combo Batch Process complete. Successfully processed: {_combo_processed_count}/{_combo_total_count}."
        log.info(msg)
        if _combo_master_filepath:
            try:
                bpy.ops.wm.open_mainfile(filepath=_combo_master_filepath)
            except:
                pass
        _close_batch_file_logger()
        return None  # Stop timer

    shot_name, mode = _combo_batch_queue.pop(0)
    log.info(f"--- Combo Batch Process [BUTCH]: {shot_name} [{mode}] ---")

    try:
        bpy.ops.wm.open_mainfile(filepath=_combo_master_filepath)
        current_context = bpy.context
        current_scene = current_context.scene

        shot_marker = current_scene.timeline_markers.get(shot_name)
        if not shot_marker:
            log.error(f"Marker '{shot_name}' missing after reload. Skipping.")
            return 0.1

        current_scene.frame_set(shot_marker.frame)

        log.info(f"Using forced combo mode [{mode}] for {shot_name}")

        steps = get_processing_steps(current_context, mode)
        step_success = True
        for step_name, action in steps:
            log.info(f"  -> Executing: {step_name}")
            
            # --- PATCH 2: Profiling ---
            start_time = time.time()
            try: 
                action()
                duration = time.time() - start_time
                log.info(f"  -> [PROFILER] Completed '{step_name}' in {duration:.3f} seconds.")
            except Exception as e:
                log.error(f"FAILED {step_name} on {shot_name} [{mode}]: {e}")
                step_success = False
                break
                
        if step_success: 
            _combo_processed_count += 1

    except Exception as e:
        log.error(f"FAILED on {shot_name} [{mode}]: {e}")

    return 0.1

def _process_next_combo_relink_step():
    global _combo_relink_queue, _combo_relink_master_filepath, _combo_relink_processed_count, _combo_relink_total_count

    if not _combo_relink_queue:
        log.info(f"Combo Relink Batch complete. Processed: {_combo_relink_processed_count}/{_combo_relink_total_count}")
        if _combo_relink_master_filepath:
            try: bpy.ops.wm.open_mainfile(filepath=_combo_relink_master_filepath)
            except: pass
        return None

    shot_name, mode = _combo_relink_queue.pop(0)
    log.info(f"--- Combo Relink Process: {shot_name} [{mode}] ---")
    try:
        match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_name, re.IGNORECASE)
        if not match: return 0.1
        sc, sh = match.group(1).upper(), match.group(2).upper()

        latest_work = _find_latest_work_filepath(bpy.context, sc, sh, mode)
        if not latest_work:
            log.warning(f"No WORK file found for {sc}-{sh} [{mode}]. Skipping.")
            return 0.1

        bpy.ops.wm.open_mainfile(filepath=latest_work)
        current_context = bpy.context

        _save_relink_version(current_context, sc, sh, mode)

        for lib in bpy.data.libraries: lib.reload()

        if mode == 'ART': _relink_art(current_context, sc, sh)
        elif mode == 'ANI': _relink_ani(current_context, sc, sh)

        # Save the changes & Publish Hero automatically, triggering the Post-Hero Increment!
        bpy.ops.wm.save_mainfile()
        _save_workflow_hero(current_context, mode, create_blocking=True)
        
        _combo_relink_processed_count += 1
    except Exception as e:
        log.error(f"Failed to Combo Relink {shot_name} [{mode}]: {e}")

    return 0.1

class BUTCHER_OT_combo_batch_process(Operator):
    bl_idname = "butcher.combo_batch_process"
    bl_label = "Combo Butch (ANI+ART)"
    bl_description = "Reloads the master file and processes both ANI and ART for each selected shot."

    def execute(self, context):
        global _combo_batch_queue, _combo_master_filepath, _combo_processed_count, _combo_total_count

        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the main project file first.")
            return {"CANCELLED"}

        if bpy.app.timers.is_registered(_process_next_combo_batch_step) or bpy.app.timers.is_registered(_process_next_batch_step):
            self.report({"ERROR"}, "A batch process is already running.")
            return {"CANCELLED"}

        # --- PATCH 1: Auto-Save the master file before starting the batch ---
        try:
            bpy.ops.wm.save_mainfile()
            log.info("Saved current file state before starting Combo Batch.")
        except Exception as e:
            self.report({"ERROR"}, f"Failed to auto-save file: {e}")
            return {"CANCELLED"}
        # -------------------------------------------------------------------

        _combo_master_filepath = bpy.data.filepath
        _setup_batch_file_logger(_combo_master_filepath)

        selected_shots = [s.name for s in context.scene.butcher_shot_list if s.is_selected]
        if not selected_shots:
            self.report({"WARNING"}, "No shots selected from the list.")
            return {"CANCELLED"}

        log.info(f"Starting Combo Batch Butcher for {len(selected_shots)} shots.")

        _combo_batch_queue = []
        for shot in selected_shots:
            _combo_batch_queue.append((shot, 'ANI'))
            _combo_batch_queue.append((shot, 'ART'))

        _combo_processed_count = 0
        _combo_total_count = len(_combo_batch_queue)

        bpy.app.timers.register(_process_next_combo_batch_step, first_interval=0.1, persistent=True)
        self.report({'INFO'}, f"Combo Batch Butcher started for {len(selected_shots)} shots (Total {len(_combo_batch_queue)} tasks).")
        return {'FINISHED'}

class BUTCHER_OT_combo_relink(Operator):
    bl_idname = "butcher.combo_relink"
    bl_label = "Combo Relink (ANI+ART)"
    bl_description = "Reload libraries and relink both ANI and ART workflow targets for selected shots."
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global _combo_relink_queue, _combo_relink_master_filepath, _combo_relink_processed_count, _combo_relink_total_count
        
        _clear_relink_log()

        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the main project file first.")
            return {"CANCELLED"}

        if bpy.app.timers.is_registered(_process_next_combo_relink_step) or bpy.app.timers.is_registered(_process_next_relink_step):
            self.report({"ERROR"}, "A batch relink process is already running.")
            return {"CANCELLED"}

        _combo_relink_master_filepath = bpy.data.filepath

        selected_shots = [s.name for s in context.scene.butcher_shot_list if s.is_selected]
        if not selected_shots:
            self.report({"WARNING"}, "No shots selected from the list.")
            return {"CANCELLED"}

        _combo_relink_queue = []
        for shot in selected_shots:
            _combo_relink_queue.append((shot, 'ANI'))
            _combo_relink_queue.append((shot, 'ART'))

        _combo_relink_total_count = len(_combo_relink_queue)
        _combo_relink_processed_count = 0

        bpy.app.timers.register(_process_next_combo_relink_step, first_interval=0.1, persistent=True)
        self.report({'INFO'}, f"Combo Batch Relink started for {len(selected_shots)} shots (Total {_combo_relink_total_count} tasks).")
        return {'FINISHED'}


from bpy.app.handlers import persistent

_last_butcher_scene_name = ""

@persistent
def auto_refresh_butcher_shot_list(dummy):
    global _last_butcher_scene_name
    
    if not bpy.context or not getattr(bpy.context, 'scene', None):
        return

    current_scene_name = bpy.context.scene.name
    is_load_post = dummy is None
    
    if is_load_post or current_scene_name != _last_butcher_scene_name:
        _last_butcher_scene_name = current_scene_name
        
        if getattr(bpy.context.screen, 'is_animation_playing', False):
            return

        shot_list = bpy.context.scene.butcher_shot_list
        shot_list.clear()
        found_shots = get_all_butcher_shots(bpy.context)
        for marker in found_shots:
            item = shot_list.add()
            item.name = marker.name
            name_match = re.match(r"CAM-(SC\d+-SH\d+)", marker.name, re.IGNORECASE)
            if name_match:
                item.display_name = name_match.group(1)
            else:
                item.display_name = marker.name 
            item.frame = marker.frame


# --- UI Panel ---

class VIEW3D_PT_butcher_panel(Panel):
    bl_label = "Butcher"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Butcher"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        mode = get_current_mode(context)
        label_text, icon_name = MODE_LABELS.get(mode, ("Unknown", "QUESTION"))

        # Header
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text=f"{label_text}", icon=icon_name)
        
        layout.separator()

        # Add Combo actions at the top of the panel if there are shots
        selected_count = sum(1 for s in scene.butcher_shot_list if s.is_selected)
        if mode in ['ANI', 'ART'] and getattr(scene, "butcher_workflow_mode", 'AUTO') != 'LOC':
            combo_box = layout.box()
            combo_box.label(text="Combo Actions (All Selected)", icon="FILE_BLEND")
            col = combo_box.column(align=True)
            col.scale_y = 1.2
            col.operator("butcher.combo_batch_process", text=f"Combo Butch ({selected_count} shots)", icon="BRUSH_DATA")
            col.operator("butcher.combo_relink", text=f"Combo Relink ({selected_count} shots)", icon="LINK_BLEND")
            layout.separator()
        
        # Override Dropdown (Only show for generic scenes that can be overridden)
        if mode in ['ANI', 'ART'] and getattr(scene, "butcher_workflow_mode", 'AUTO') != 'LOC':
            layout.prop(scene, "butcher_workflow_mode", text="Workflow")
            
        layout.separator()

        # Debug Toggle
        row = layout.row()
        row.prop(scene, "butcher_debug_mode", text="Debug Steps", toggle=True)
        
        # Batch Shot Selection UI
        if mode in ['ANI', 'ART'] and getattr(scene, "butcher_workflow_mode", 'AUTO') != 'LOC':
            box = layout.box()
            row = box.row(align=True)
            row.label(text="Shot Selection", icon="FILE_TICK")
            row.operator(BUTCHER_OT_refresh_shot_list.bl_idname, text="", icon="FILE_REFRESH")
            
            box.template_list("BUTCHER_UL_shot_list", "", scene, "butcher_shot_list", scene, "butcher_active_shot_index")
            
            row = box.row(align=True)
            op_sel = row.operator(BUTCHER_OT_select_all_shots.bl_idname, text="All")
            op_sel.action = 'SELECT'
            op_desel = row.operator(BUTCHER_OT_select_all_shots.bl_idname, text="None")
            op_desel.action = 'DESELECT'
            
            if len(scene.butcher_shot_list) == 0:
                box.label(text="No shots found on markers.", icon="INFO")
                
            layout.separator()

        # Main Actions
        col = layout.column(align=True)
        col.scale_y = 1.2
        
        batch_suffix = f" ({selected_count})" if selected_count > 0 else ""
        prefix = "Batch " if selected_count > 0 else ""

        if mode == 'ANI':
            col.operator("butcher.prepare", text="Prepare", icon="OUTLINER_COLLECTION")
            
        col.operator("butcher.cleanup", text=f"{prefix}Butch{batch_suffix}", icon="BRUSH_DATA")
        
        if mode != 'LOC':
            col.operator("butcher.relink", text="Relink", icon="LINK_BLEND")


# --- Registration ---

classes = (
    BUTCHER_OT_cleanup,
    BUTCHER_OT_run_step,
    BUTCHER_OT_publish,
    BUTCHER_OT_prepare,
    BUTCHER_OT_relink,
    BUTCHER_OT_combo_batch_process,
    BUTCHER_OT_combo_relink,
    BUTCHER_ShotListItem,
    BUTCHER_UL_shot_list,
    BUTCHER_OT_select_all_shots,
    BUTCHER_OT_refresh_shot_list,
    BUTCHER_OT_batch_process,
    VIEW3D_PT_butcher_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.butcher_debug_mode = BoolProperty(
        name="Debug Mode",
        description="Show individual processing steps",
        default=False
    )
    
    bpy.types.Scene.butcher_shot_list = CollectionProperty(type=BUTCHER_ShotListItem)
    bpy.types.Scene.butcher_active_shot_index = IntProperty(name="Active Shot Index", default=0)
    
    if auto_refresh_butcher_shot_list not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(auto_refresh_butcher_shot_list)
    if auto_refresh_butcher_shot_list not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(auto_refresh_butcher_shot_list)
    
    bpy.types.Scene.butcher_workflow_mode = EnumProperty(
        name="Workflow",
        description="Override Butcher workflow detection for this scene",
        items=[
            ('AUTO', "choose workflow", "choose"),
            ('ANI', "ANI", "Animation Workflow"),
            ('ART', "ART", "Art/Lighting Workflow"),
        ],
        default='AUTO'
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.butcher_debug_mode
    if hasattr(bpy.types.Scene, "butcher_workflow_mode"):
        del bpy.types.Scene.butcher_workflow_mode
    
    if hasattr(bpy.types.Scene, "butcher_shot_list"):
        del bpy.types.Scene.butcher_shot_list
    if hasattr(bpy.types.Scene, "butcher_active_shot_index"):
        del bpy.types.Scene.butcher_active_shot_index
        
    if auto_refresh_butcher_shot_list in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(auto_refresh_butcher_shot_list)
    if auto_refresh_butcher_shot_list in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(auto_refresh_butcher_shot_list)

if __name__ == "__main__":
    register()
    