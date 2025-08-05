bl_info = {
    "name": "Advanced Copy V2",
    "author": "iori, krutart, Gemini",
    "version": (2, 2, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Object Context Menu",
    "description": "Advanced copy/move operations based on timeline shots and a structured collection hierarchy.",
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
    # Create a set of all collections that are children of another collection
    nested_collections = set()
    for coll in bpy.data.collections:
        for child in coll.children:
            nested_collections.add(child.name)

    # Iterate all collections again
    for coll in bpy.data.collections:
        # A top-level scene collection matches the name and is not in the nested set
        if coll.name.startswith("+SC") and coll.name.endswith("+") and coll.name not in nested_collections:
             # Extra check to avoid matching sub-collections like `+SC17-LOCATION-ART+`
            if '-ART' not in coll.name and '-MODEL' not in coll.name and '-SHOT' not in coll.name:
                scene_colls.append(coll)
    return scene_colls

def find_top_level_scene_collection_by_str(scene_str):
    """
    Finds a top-level scene collection from all .blend file data that matches the scene string, e.g., "SC17".
    """
    # Use the more robust find_all_scene_collections to ensure we only get top-level ones
    all_scenes = find_all_scene_collections()
    for coll in all_scenes:
        if coll.name.startswith(f"+{scene_str}-"):
            return coll
    return None

def find_shot_model_collection(context, scene_str, shot_str):
    """
    Finds or creates the target MODEL collection for a specific shot.
    Target Path: +SC##-LOCATION+/+SC##-LOCATION-ART+/SC##-LOCATION-ART-SHOT/SC##-SH###-ART/MODEL-SC##-SH###/
    """
    top_level_scene_coll = find_top_level_scene_collection_by_str(scene_str)

    if not top_level_scene_coll:
        print(f"AdvCopy Error: Could not find a top-level scene collection for '{scene_str}'")
        return None
        
    location_name = '-'.join(top_level_scene_coll.name.strip('+').split('-')[1:])
    if not location_name:
        return None

    art_coll = get_or_create_collection(top_level_scene_coll, f"+{scene_str}-{location_name}-ART+")
    art_shot_coll = get_or_create_collection(art_coll, f"{scene_str}-{location_name}-ART-SHOT")
    shot_art_coll = get_or_create_collection(art_shot_coll, f"{scene_str}-{shot_str}-ART")
    model_coll = get_or_create_collection(shot_art_coll, f"MODEL-{scene_str}-{shot_str}")

    return model_coll

def find_scene_model_collection(top_level_scene_coll):
    """
    Finds or creates the scene-level MODEL collection for a given top-level scene collection.
    Target Path: +SC##-LOCATION+/+SC##-LOCATION-ART+/SC##-LOCATION-MODEL/
    """
    if not top_level_scene_coll:
        return None
        
    try:
        parts = top_level_scene_coll.name.strip('+').split('-')
        scene_str = parts[0]
        location_name = '-'.join(parts[1:])
    except IndexError:
        print(f"AdvCopy Error: Could not parse scene/location from '{top_level_scene_coll.name}'.")
        return None

    art_coll = get_or_create_collection(top_level_scene_coll, f"+{scene_str}-{location_name}-ART+")
    model_coll = get_or_create_collection(art_coll, f"{scene_str}-{location_name}-MODEL")
    
    return model_coll

def find_source_loc_model_collection(obj):
    """
    Finds the source collection of an object if it matches the `+LOC-.../LOC-...-MODEL` structure.
    Returns the collection object or None.
    """
    for coll in obj.users_collection:
        if coll.name.endswith("-MODEL") and coll.name.startswith("LOC-"):
            # This is a potential `LOC-...-MODEL` collection. Now check its parent.
            for parent_coll in bpy.data.collections:
                if coll.name in parent_coll.children:
                    if parent_coll.name.startswith("+LOC-") and parent_coll.name.endswith("+"):
                        return coll
    return None

def find_all_env_model_collections():
    """
    Finds all collections that match the `+ENV-.../ENV-...-MODEL` structure.
    Returns a list of collection objects.
    """
    env_model_colls = []
    for parent_coll in bpy.data.collections:
        if parent_coll.name.startswith("+ENV-") and parent_coll.name.endswith("+"):
            # Found a potential parent, now look for the child MODEL collection.
            env_name = parent_coll.name.strip('+').replace('ENV-', '', 1)
            expected_model_coll_name = f"ENV-{env_name}-MODEL"
            if expected_model_coll_name in parent_coll.children:
                env_model_colls.append(parent_coll.children[expected_model_coll_name])
    return env_model_colls


def toggle_object_visibility(obj, frame_range, hide):
    """
    Keys the visibility of an object to be on or off for a specific frame range.
    """
    start_frame, end_frame = frame_range

    for prop in ["hide_viewport", "hide_render"]:
        setattr(obj, prop, not hide)
        obj.keyframe_insert(data_path=prop, frame=start_frame - 1)

        setattr(obj, prop, hide)
        obj.keyframe_insert(data_path=prop, frame=start_frame)

        setattr(obj, prop, not hide)
        obj.keyframe_insert(data_path=prop, frame=end_frame + 1)


# --- Operators ---

class ADVCOPY_OT_copy_to_current_shot(bpy.types.Operator):
    """Copies an object to the MODEL collection for the current timeline shot.
Hides the original and shows the copy only during the shot's frame range"""
    bl_idname = "object.advcopy_copy_to_current_shot"
    bl_label = "Copy to Current Shot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and get_current_shot_info(context) is not None

    def execute(self, context):
        shot_info = get_current_shot_info(context)
        original_obj = context.active_object
        target_collection = find_shot_model_collection(context, shot_info['scene_str'], shot_info['shot_str'])
        if not target_collection:
            self.report({'ERROR'}, "Could not find or create the target shot collection.")
            return {'CANCELLED'}

        new_obj = original_obj.copy()
        if original_obj.data:
            new_obj.data = original_obj.data.copy()
            
        new_obj.animation_data_clear()
        new_obj.name = f"{original_obj.name}.{shot_info['scene_str']}.{shot_info['shot_str']}"
        
        target_collection.objects.link(new_obj)

        frame_range = (shot_info['start'], shot_info['end'])
        toggle_object_visibility(original_obj, frame_range, hide=True)
        toggle_object_visibility(new_obj, frame_range, hide=False)

        self.report({'INFO'}, f"Copied '{original_obj.name}' to shot '{shot_info['name']}'")
        return {'FINISHED'}

class ADVCOPY_OT_copy_to_current_scene_model(bpy.types.Operator):
    """Copies an object to the current scene's MODEL collection, based on timeline markers.
Hides the original and shows the copy only during that scene's entire frame range"""
    bl_idname = "object.advcopy_copy_to_current_scene"
    bl_label = "Copy to Current Scene (with Visibility Toggle)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Enable if an object is selected and we are in a defined shot,
        # which tells us what the "current scene" is.
        return context.active_object is not None and get_current_shot_info(context) is not None

    def execute(self, context):
        original_obj = context.active_object
        shot_info = get_current_shot_info(context)
        scene_str = shot_info['scene_str']
        
        # 1. Find the top-level scene collection using the scene string from the marker.
        top_level_coll = find_top_level_scene_collection_by_str(scene_str)
        if not top_level_coll:
            self.report({'ERROR'}, f"Could not find top-level scene collection for '{scene_str}'.")
            return {'CANCELLED'}
            
        # 2. Find the target MODEL collection for that scene.
        target_collection = find_scene_model_collection(top_level_coll)
        if not target_collection:
            self.report({'ERROR'}, "Could not find the scene's MODEL collection.")
            return {'CANCELLED'}
            
        # 3. Find the frame range for the entire scene.
        frame_range = get_scene_frame_range(context, scene_str)
        if not frame_range:
            self.report({'WARNING'}, f"No shot markers found for scene '{scene_str}'. Cannot toggle visibility.")
            return {'CANCELLED'}

        # 4. Duplicate the object and link it.
        new_obj = original_obj.copy()
        if original_obj.data:
            new_obj.data = original_obj.data.copy()
        new_obj.animation_data_clear()
        new_obj.name = f"{original_obj.name}.{scene_str}"
        
        target_collection.objects.link(new_obj)
        
        # 5. Toggle visibility for the entire scene's duration.
        toggle_object_visibility(original_obj, frame_range, hide=True)
        toggle_object_visibility(new_obj, frame_range, hide=False)
        
        self.report({'INFO'}, f"Copied '{original_obj.name}' to '{target_collection.name}' for scene '{scene_str}'")
        return {'FINISHED'}

class ADVCOPY_OT_move_to_all_scenes(bpy.types.Operator):
    """Moves an object by creating a unique copy in every scene's MODEL collection and then removing the original"""
    bl_idname = "object.advcopy_move_to_all_scenes"
    bl_label = "Move Unique Copies to Each Scene"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.active_object is not None and len(find_all_scene_collections()) > 0

    def execute(self, context):
        original_obj = context.active_object
        
        scene_collections = find_all_scene_collections()
        if not scene_collections:
            self.report({'WARNING'}, "No top-level scene collections (+SC##-...) found in the file.")
            return {'CANCELLED'}
            
        copies_made = 0
        for scene_coll in scene_collections:
            target_model_coll = find_scene_model_collection(scene_coll)
            if not target_model_coll:
                self.report({'WARNING'}, f"Skipping scene '{scene_coll.name}', could not find its MODEL collection.")
                continue

            new_obj = original_obj.copy()
            if original_obj.data:
                new_obj.data = original_obj.data.copy()
            
            scene_str = scene_coll.name.strip('+').split('-')[0]
            new_obj.name = f"{original_obj.name}.{scene_str}"
            
            target_model_coll.objects.link(new_obj)
            copies_made += 1
            
        if copies_made > 0:
            bpy.data.objects.remove(original_obj, do_unlink=True)
            self.report({'INFO'}, f"Moved '{original_obj.name}' into {copies_made} scene(s). Original removed.")
        else:
            self.report({'ERROR'}, "Failed to create any copies. Original object was not removed.")
            return {'CANCELLED'}
            
        return {'FINISHED'}

class ADVCOPY_OT_copy_to_env(bpy.types.Operator):
    """Copies an object from a LOC-MODEL collection to all ENV-MODEL collections, then removes the original from the LOC collection."""
    bl_idname = "object.advcopy_copy_to_env"
    bl_label = "Copy to All Enviros"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not context.active_object:
            return False
        # Check if the object is in a valid source collection and if there are valid targets.
        return find_source_loc_model_collection(context.active_object) is not None and len(find_all_env_model_collections()) > 0

    def execute(self, context):
        original_obj = context.active_object
        
        # 1. Find the source collection.
        source_loc_model_coll = find_source_loc_model_collection(original_obj)
        if not source_loc_model_coll:
            self.report({'ERROR'}, f"Source object '{original_obj.name}' is not in a valid '+LOC-.../LOC-...-MODEL' collection.")
            return {'CANCELLED'}

        # 2. Find all target ENV collections.
        target_env_model_colls = find_all_env_model_collections()
        if not target_env_model_colls:
            self.report({'WARNING'}, "No '+ENV-.../ENV-...-MODEL' collections found to copy to.")
            return {'CANCELLED'}
            
        copies_made = 0
        for env_coll in target_env_model_colls:
            # Create a unique copy of the object and its data.
            new_obj = original_obj.copy()
            if original_obj.data:
                new_obj.data = original_obj.data.copy()
            
            # Generate a new name for the copy.
            try:
                env_name = env_coll.name.replace("-MODEL", "").replace("ENV-", "")
                new_obj.name = f"{original_obj.name}.{env_name}"
            except Exception:
                # Fallback name if parsing fails
                new_obj.name = f"{original_obj.name}.ENV_COPY"

            # Link the new object to the target environment collection.
            env_coll.objects.link(new_obj)
            copies_made += 1
        
        # 4. If copies were successfully made, unlink the original from its source collection.
        if copies_made > 0:
            source_loc_model_coll.objects.unlink(original_obj)
            self.report({'INFO'}, f"Copied '{original_obj.name}' to {copies_made} ENV collection(s) and removed from '{source_loc_model_coll.name}'.")
        else:
            self.report({'ERROR'}, "Failed to create any copies. Original object was not moved.")
            return {'CANCELLED'}

        return {'FINISHED'}


# --- Menus ---

class ADVCOPY_MT_copy_to_scene_menu(bpy.types.Menu):
    bl_label = "Scene Operations"
    bl_idname = "OBJECT_MT_advcopy_copy_to_scene_submenu"

    def draw(self, context):
        layout = self.layout
        layout.operator(ADVCOPY_OT_copy_to_current_scene_model.bl_idname, icon='VIS_SEL_11')
        layout.separator()
        layout.operator(ADVCOPY_OT_move_to_all_scenes.bl_idname, icon='COPY_ID')

def draw_main_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator(ADVCOPY_OT_copy_to_current_shot.bl_idname, icon='SEQUENCE')
    layout.menu(ADVCOPY_MT_copy_to_scene_menu.bl_idname, icon='SCENE_DATA')
    layout.operator(ADVCOPY_OT_copy_to_env.bl_idname, icon='WORLD')


# --- Registration ---

classes = [
    ADVCOPY_OT_copy_to_current_shot,
    ADVCOPY_OT_copy_to_current_scene_model,
    ADVCOPY_OT_move_to_all_scenes,
    ADVCOPY_OT_copy_to_env, # Replaced the placeholder
    ADVCOPY_MT_copy_to_scene_menu,
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

