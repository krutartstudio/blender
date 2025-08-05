bl_info = {
    "name": "Advanced Copy V4",
    "author": "iori, krutart, Gemini",
    "version": (4, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Object Context Menu",
    "description": "Automatically performs MODEL or VFX copy/move operations based on object context and a structured collection hierarchy.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import re

# --- Helper Functions: Shot, Scene, and Collection Management ---

def get_current_shot_info(context):
    """
    Determines the current shot from timeline markers bound to cameras.
    Parses marker names like 'CAM-SC17-SH180-FLAT' to extract shot details.
    """
    current_frame = context.scene.frame_current
    markers = sorted([m for m in context.scene.timeline_markers if m.camera], key=lambda m: m.frame)

    if not markers:
        return None

    current_shot_marker = None
    shot_end_frame = context.scene.frame_end

    for i, marker in enumerate(markers):
        start_frame = marker.frame
        if i + 1 < len(markers):
            end_frame = markers[i+1].frame - 1
        else:
            end_frame = context.scene.frame_end

        if start_frame <= current_frame <= end_frame:
            current_shot_marker = marker
            shot_end_frame = end_frame
            break

    if not current_shot_marker:
        return None

    match = re.match(r"CAM-(SC\d+)-(SH\d+)", current_shot_marker.name, re.IGNORECASE)
    if not match:
        return None

    scene_str = match.group(1).upper()
    shot_str = match.group(2).upper()

    return {
        "name": current_shot_marker.name,
        "start": current_shot_marker.frame,
        "end": shot_end_frame,
        "scene_str": scene_str,
        "shot_str": shot_str
    }

def get_scene_frame_range(context, scene_str):
    """
    Calculates the full frame range for a given scene by finding the
    earliest start and latest end frame among all its associated shot markers.
    """
    scene_markers = []
    for marker in context.scene.timeline_markers:
        if marker.camera and f"-{scene_str.upper()}-" in marker.name.upper():
            scene_markers.append(marker)

    if not scene_markers:
        return None

    scene_markers.sort(key=lambda m: m.frame)
    all_markers = sorted([m for m in context.scene.timeline_markers if m.camera], key=lambda m: m.frame)

    start_frame = scene_markers[0].frame
    last_shot_marker = scene_markers[-1]
    end_frame = context.scene.frame_end

    try:
        last_marker_index = all_markers.index(last_shot_marker)
        if last_marker_index + 1 < len(all_markers):
            end_frame = all_markers[last_marker_index + 1].frame - 1
    except ValueError:
        pass

    return (start_frame, end_frame)

def get_or_create_collection(parent_collection, child_name):
    """
    Gets a child collection by name from a parent. If it doesn't exist,
    it creates and links it.
    """
    if child_name in parent_collection.children:
        return parent_collection.children[child_name]
    else:
        new_coll = bpy.data.collections.new(name=child_name)
        parent_collection.children.link(new_coll)
        return new_coll

def find_all_scene_collections():
    """
    Finds all top-level scene collections in the entire .blend file data.
    A scene collection is identified by the naming convention `+SC##-...`
    and having no collection parents.
    """
    scene_colls = []
    nested_collections = set()
    for coll in bpy.data.collections:
        for child in coll.children:
            nested_collections.add(child.name)

    for coll in bpy.data.collections:
        if coll.name.startswith("+SC") and coll.name.endswith("+") and coll.name not in nested_collections:
            if all(sub not in coll.name for sub in ['-ART', '-MODEL', '-SHOT', '-VFX']):
                scene_colls.append(coll)
    return scene_colls

def find_top_level_scene_collection_by_str(scene_str):
    """
    Finds a top-level scene collection from all .blend file data that matches the scene string, e.g., "SC17".
    """
    all_scenes = find_all_scene_collections()
    for coll in all_scenes:
        if coll.name.startswith(f"+{scene_str}-"):
            return coll
    return None

def toggle_object_visibility(obj, frame_range, hide):
    """
    Keys the visibility of an object to be on or off for a specific frame range.
    """
    start_frame, end_frame = frame_range
    if not obj.animation_data:
        obj.animation_data_create()

    for prop in ["hide_viewport", "hide_render"]:
        setattr(obj, prop, not hide)
        obj.keyframe_insert(data_path=prop, frame=start_frame - 1)
        setattr(obj, prop, hide)
        obj.keyframe_insert(data_path=prop, frame=start_frame)
        obj.keyframe_insert(data_path=prop, frame=end_frame)
        setattr(obj, prop, not hide)
        obj.keyframe_insert(data_path=prop, frame=end_frame + 1)

# --- Collection Finders & Auto-Diagnosis ---

_parent_cache = {}

def find_parent_collection(child_coll, collections):
    """Helper to find the parent of a collection with caching."""
    child_name = child_coll.name
    if child_name in _parent_cache:
        return _parent_cache[child_name]
    for parent_coll in collections:
        if child_name in parent_coll.children:
            _parent_cache[child_name] = parent_coll
            return parent_coll
    _parent_cache[child_name] = None
    return None

def get_contextual_op_type(obj):
    """
    Auto-diagnoses if an object is in a MODEL or VFX context by checking its collections.
    Defaults to 'MODEL' if no context can be found.
    """
    _parent_cache.clear()
    all_colls = bpy.data.collections
    for coll in obj.users_collection:
        current_coll = coll
        for _ in range(32):  # Safety break for deep or recursive hierarchies
            if not current_coll:
                break
            name = current_coll.name.upper()
            if 'VFX' in name:
                return 'VFX'
            if 'MODEL' in name or 'ART' in name:
                return 'MODEL'
            current_coll = find_parent_collection(current_coll, all_colls)
    return 'MODEL'

def find_shot_collection(context, scene_str, shot_str, op_type):
    """
    Finds or creates the target collection for a specific shot (MODEL or VFX).
    """
    top_level_scene_coll = find_top_level_scene_collection_by_str(scene_str)
    if not top_level_scene_coll:
        print(f"AdvCopy Error: Could not find top-level scene collection for '{scene_str}'")
        return None
    location_name = '-'.join(top_level_scene_coll.name.strip('+').split('-')[1:])
    if not location_name: return None

    if op_type == 'MODEL':
        art_coll = get_or_create_collection(top_level_scene_coll, f"+{scene_str}-{location_name}-ART+")
        art_shot_coll = get_or_create_collection(art_coll, f"{scene_str}-{location_name}-ART-SHOT")
        shot_art_coll = get_or_create_collection(art_shot_coll, f"{scene_str}-{shot_str}-ART")
        return get_or_create_collection(shot_art_coll, f"MODEL-{scene_str}-{shot_str}")
    elif op_type == 'VFX':
        vfx_coll = get_or_create_collection(top_level_scene_coll, f"+{scene_str}-{location_name}-VFX+")
        vfx_shot_coll = get_or_create_collection(vfx_coll, f"{scene_str}-{location_name}-VFX-SHOT")
        return get_or_create_collection(vfx_shot_coll, f"{scene_str}-{shot_str}-VFX")
    return None

def find_scene_collection(top_level_scene_coll, op_type):
    """
    Finds or creates the scene-level collection (MODEL or VFX).
    """
    if not top_level_scene_coll: return None
    try:
        parts = top_level_scene_coll.name.strip('+').split('-')
        scene_str, location_name = parts[0], '-'.join(parts[1:])
    except IndexError:
        return None

    if op_type == 'MODEL':
        art_coll = get_or_create_collection(top_level_scene_coll, f"+{scene_str}-{location_name}-ART+")
        return get_or_create_collection(art_coll, f"{scene_str}-{location_name}-MODEL")
    elif op_type == 'VFX':
        vfx_parent = get_or_create_collection(top_level_scene_coll, f"+{scene_str}-{location_name}-VFX+")
        return get_or_create_collection(vfx_parent, f"{scene_str}-{location_name}-VFX")
    return None

def find_source_loc_collection(obj, op_type):
    """Finds the source `+LOC-.../LOC-...-[TYPE]` collection of an object."""
    suffix = f"-{op_type}"
    for coll in obj.users_collection:
        if coll.name.endswith(suffix) and coll.name.startswith("LOC-"):
            for parent_coll in bpy.data.collections:
                if coll.name in parent_coll.children and parent_coll.name.startswith("+LOC-"):
                    return coll
    return None

def find_all_env_collections(op_type):
    """Finds all `+ENV-.../ENV-...-[TYPE]` collections."""
    suffix = f"-{op_type}"
    env_colls = []
    for parent_coll in bpy.data.collections:
        if parent_coll.name.startswith("+ENV-"):
            env_name = parent_coll.name.strip('+').replace('ENV-', '', 1)
            expected_coll_name = f"ENV-{env_name}{suffix}"
            if expected_coll_name in parent_coll.children:
                env_colls.append(parent_coll.children[expected_coll_name])
    return env_colls

# --- Operators ---

class ADVCOPY_OT_copy_to_current_shot(bpy.types.Operator):
    """Copies object to the collection for the current shot, auto-detecting MODEL/VFX context"""
    bl_idname = "object.advcopy_copy_to_current_shot"
    bl_label = "Copy to Current Shot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and get_current_shot_info(context) is not None

    def execute(self, context):
        shot_info = get_current_shot_info(context)
        original_obj = context.active_object
        op_type = get_contextual_op_type(original_obj)
        
        target_collection = find_shot_collection(context, shot_info['scene_str'], shot_info['shot_str'], op_type)
        if not target_collection:
            self.report({'ERROR'}, f"Could not find target {op_type} shot collection.")
            return {'CANCELLED'}

        new_obj = original_obj.copy()
        if original_obj.data: new_obj.data = original_obj.data.copy()
        new_obj.animation_data_clear()
        new_obj.name = f"{original_obj.name}.{shot_info['scene_str']}.{shot_info['shot_str']}"
        target_collection.objects.link(new_obj)

        frame_range = (shot_info['start'], shot_info['end'])
        toggle_object_visibility(original_obj, frame_range, hide=True)
        toggle_object_visibility(new_obj, frame_range, hide=False)

        self.report({'INFO'}, f"({op_type}) Copied '{original_obj.name}' to shot '{shot_info['name']}'")
        return {'FINISHED'}

class ADVCOPY_OT_copy_to_current_scene(bpy.types.Operator):
    """Copies object to the current scene's collection, auto-detecting MODEL/VFX context"""
    bl_idname = "object.advcopy_copy_to_current_scene"
    bl_label = "Copy to Current Scene"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and get_current_shot_info(context) is not None

    def execute(self, context):
        original_obj = context.active_object
        shot_info = get_current_shot_info(context)
        scene_str = shot_info['scene_str']
        op_type = get_contextual_op_type(original_obj)
        
        top_level_coll = find_top_level_scene_collection_by_str(scene_str)
        if not top_level_coll:
            self.report({'ERROR'}, f"Could not find top-level scene collection for '{scene_str}'.")
            return {'CANCELLED'}
            
        target_collection = find_scene_collection(top_level_coll, op_type)
        if not target_collection:
            self.report({'ERROR'}, f"Could not find the scene's {op_type} collection.")
            return {'CANCELLED'}
            
        frame_range = get_scene_frame_range(context, scene_str)
        if not frame_range:
            self.report({'WARNING'}, f"No markers for scene '{scene_str}'. Cannot toggle visibility.")
            return {'CANCELLED'}

        new_obj = original_obj.copy()
        if original_obj.data: new_obj.data = original_obj.data.copy()
        new_obj.animation_data_clear()
        new_obj.name = f"{original_obj.name}.{scene_str}"
        target_collection.objects.link(new_obj)
        
        toggle_object_visibility(original_obj, frame_range, hide=True)
        toggle_object_visibility(new_obj, frame_range, hide=False)
        
        self.report({'INFO'}, f"({op_type}) Copied '{original_obj.name}' to '{target_collection.name}'")
        return {'FINISHED'}

class ADVCOPY_OT_move_to_all_scenes(bpy.types.Operator):
    """Creates a copy in every scene's collection and removes original, auto-detecting MODEL/VFX"""
    bl_idname = "object.advcopy_move_to_all_scenes"
    bl_label = "Move Unique Copies to Each Scene"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.active_object is not None and len(find_all_scene_collections()) > 0

    def execute(self, context):
        original_obj = context.active_object
        op_type = get_contextual_op_type(original_obj)
        scene_collections = find_all_scene_collections()
        if not scene_collections:
            self.report({'WARNING'}, "No top-level scene collections (+SC##-...) found.")
            return {'CANCELLED'}
            
        copies_made = 0
        for scene_coll in scene_collections:
            target_coll = find_scene_collection(scene_coll, op_type)
            if not target_coll:
                self.report({'WARNING'}, f"Skipping '{scene_coll.name}', no {op_type} collection.")
                continue

            new_obj = original_obj.copy()
            if original_obj.data: new_obj.data = original_obj.data.copy()
            scene_str = scene_coll.name.strip('+').split('-')[0]
            new_obj.name = f"{original_obj.name}.{scene_str}"
            target_coll.objects.link(new_obj)
            copies_made += 1
            
        if copies_made > 0:
            bpy.data.objects.remove(original_obj, do_unlink=True)
            self.report({'INFO'}, f"({op_type}) Moved '{original_obj.name}' into {copies_made} scene(s).")
        else:
            self.report({'ERROR'}, "Failed to create any copies. Original not removed.")
            return {'CANCELLED'}
        return {'FINISHED'}

class ADVCOPY_OT_copy_to_env(bpy.types.Operator):
    """Copies object from a LOC collection to all ENV collections, auto-detecting MODEL/VFX"""
    bl_idname = "object.advcopy_copy_to_env"
    bl_label = "Copy to All Enviros"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not obj: return False
        
        source_model = find_source_loc_collection(obj, 'MODEL')
        source_vfx = find_source_loc_collection(obj, 'VFX')
        
        if not (source_model or source_vfx): return False
        
        op_type = 'MODEL' if source_model else 'VFX'
        return len(find_all_env_collections(op_type)) > 0

    def execute(self, context):
        original_obj = context.active_object
        
        source_model_coll = find_source_loc_collection(original_obj, 'MODEL')
        source_vfx_coll = find_source_loc_collection(original_obj, 'VFX')
        
        if source_model_coll:
            op_type = 'MODEL'
            source_coll = source_model_coll
        elif source_vfx_coll:
            op_type = 'VFX'
            source_coll = source_vfx_coll
        else:
            self.report({'ERROR'}, "Source object not in a valid LOC-MODEL or LOC-VFX collection.")
            return {'CANCELLED'}

        target_env_colls = find_all_env_collections(op_type)
        if not target_env_colls:
            self.report({'WARNING'}, f"No ENV-{op_type} collections found.")
            return {'CANCELLED'}
            
        copies_made = 0
        for env_coll in target_env_colls:
            new_obj = original_obj.copy()
            if original_obj.data: new_obj.data = original_obj.data.copy()
            try:
                env_name = env_coll.name.replace(f"-{op_type}", "").replace("ENV-", "")
                new_obj.name = f"{original_obj.name}.{env_name}"
            except Exception:
                new_obj.name = f"{original_obj.name}.ENV_COPY"

            env_coll.objects.link(new_obj)
            copies_made += 1
        
        if copies_made > 0:
            source_coll.objects.unlink(original_obj)
            self.report({'INFO'}, f"({op_type}) Copied '{original_obj.name}' to {copies_made} ENV collection(s).")
        else:
            self.report({'ERROR'}, "Failed to create any copies. Original not moved.")
            return {'CANCELLED'}
        return {'FINISHED'}

# --- Menus ---

class ADVCOPY_MT_scene_menu(bpy.types.Menu):
    bl_label = "Scene Operations"
    bl_idname = "OBJECT_MT_advcopy_scene_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator(ADVCOPY_OT_copy_to_current_scene.bl_idname, icon='SCENE_DATA')
        layout.operator(ADVCOPY_OT_move_to_all_scenes.bl_idname, icon='COPY_ID')

def draw_main_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator(ADVCOPY_OT_copy_to_current_shot.bl_idname, icon='SEQUENCE')
    layout.menu(ADVCOPY_MT_scene_menu.bl_idname, icon='OUTLINER_COLLECTION')
    layout.operator(ADVCOPY_OT_copy_to_env.bl_idname, icon='WORLD')

# --- Registration ---

classes = [
    ADVCOPY_OT_copy_to_current_shot,
    ADVCOPY_OT_copy_to_current_scene,
    ADVCOPY_OT_move_to_all_scenes,
    ADVCOPY_OT_copy_to_env,
    ADVCOPY_MT_scene_menu,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object_context_menu.append(draw_main_menu)

def unregister():
    bpy.types.VIEW3D_MT_object_context_menu.remove(draw_main_menu)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

