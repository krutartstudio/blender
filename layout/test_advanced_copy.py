bl_info = {
    "name": "Advanced Copy V2",
    "author": "iori, krutart, Gemini",
    "version": (2, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Object Context Menu",
    "description": "Advanced copy/move operations based on timeline shots and a structured collection hierarchy.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import re

# --- Helper Functions: Shot and Collection Management ---

def get_current_shot_info(context):
    """
    Determines the current shot from timeline markers bound to cameras.
    Parses marker names like 'CAM-SC17-SH180-FLAT' to extract shot details.
    
    Args:
        context (bpy.types.Context): The current Blender context.

    Returns:
        dict: A dictionary with shot info ('name', 'start', 'end', 'scene_str', 'shot_str'),
              or None if not in a defined shot.
    """
    current_frame = context.scene.frame_current
    # Filter for markers that have a camera assigned, as these define shots.
    markers = sorted([m for m in context.scene.timeline_markers if m.camera], key=lambda m: m.frame)

    if not markers:
        return None

    current_shot_marker = None
    shot_end_frame = context.scene.frame_end

    # Find which shot the current frame is in.
    for i, marker in enumerate(markers):
        start_frame = marker.frame
        # The shot ends one frame before the next marker, or at the end of the scene.
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

    # Use regex to parse the marker name for Scene and Shot numbers.
    # Expected format: CAM-SC##-SH###...
    match = re.match(r"CAM-(SC\d+)-(SH\d+)", current_shot_marker.name, re.IGNORECASE)
    if not match:
        # This marker doesn't follow the naming convention.
        return None

    scene_str = match.group(1).upper()  # e.g., "SC17"
    shot_str = match.group(2).upper()   # e.g., "SH180"

    return {
        "name": current_shot_marker.name,
        "start": current_shot_marker.frame,
        "end": shot_end_frame,
        "scene_str": scene_str,
        "shot_str": shot_str
    }

def get_or_create_collection(parent_collection, child_name):
    """
    Gets a child collection by name from a parent. If it doesn't exist,
    it creates and links it. This ensures the hierarchy is always present.

    Args:
        parent_collection (bpy.types.Collection): The parent collection.
        child_name (str): The name of the child collection to find or create.

    Returns:
        bpy.types.Collection: The found or newly created collection.
    """
    if child_name in parent_collection.children:
        return parent_collection.children[child_name]
    else:
        new_coll = bpy.data.collections.new(name=child_name)
        parent_collection.children.link(new_coll)
        return new_coll

def find_shot_model_collection(context, scene_str, shot_str):
    """
    Finds or creates the target MODEL collection for a specific shot based on the
    expected naming convention and hierarchy.

    The target path is assumed to be:
    +SC##-LOCATION+/
     └── +SC##-LOCATION-ART+/
          └── SC##-LOCATION-ART-SHOT/
               └── SC##-SH###-ART/
                    └── MODEL-SC##-SH###/

    Args:
        context (bpy.types.Context): The current Blender context.
        scene_str (str): The scene identifier (e.g., "SC17").
        shot_str (str): The shot identifier (e.g., "SH180").

    Returns:
        bpy.types.Collection: The target collection for the shot, or None if the
                              top-level scene collection couldn't be found.
    """
    # 1. Find the top-level Scene collection (e.g., `+SC17-APOLLO_CRASH+`)
    #    and extract the location name (e.g., "APOLLO_CRASH").
    location_name = None
    top_level_scene_coll = None
    for coll in context.scene.collection.children:
        # Match the scene string and the expected prefix/suffix format.
        if coll.name.startswith(f"+{scene_str}-") and coll.name.endswith("+"):
            top_level_scene_coll = coll
            try:
                # Extract the part between the first and last '-'.
                location_name = '-'.join(coll.name.split('-')[1:-1])
                if not location_name: # Handle names like "+SC17-ART+"
                    location_name = coll.name.strip('+').split('-')[1]

            except IndexError:
                continue
            break

    if not top_level_scene_coll or not location_name:
        print(f"AdvCopy Error: Could not find a top-level scene collection like '+{scene_str}-LOCATION+'")
        return None

    # 2. Traverse the hierarchy, creating collections as needed.
    art_coll = get_or_create_collection(top_level_scene_coll, f"+{scene_str}-{location_name}-ART+")
    art_shot_coll = get_or_create_collection(art_coll, f"{scene_str}-{location_name}-ART-SHOT")
    shot_art_coll = get_or_create_collection(art_shot_coll, f"{scene_str}-{shot_str}-ART")
    model_coll = get_or_create_collection(shot_art_coll, f"MODEL-{scene_str}-{shot_str}")

    return model_coll

def find_scene_model_collection(context):
    """
    Finds or creates the scene-level MODEL collection for the active object.
    This is for the "Copy to Current Scene" operator.
    
    Target Path:
    +SC##-LOCATION+/
     └── +SC##-LOCATION-ART+/
          └── SC##-LOCATION-MODEL/
    """
    active_obj = context.active_object
    if not active_obj: return None

    # 1. Find the object's top-level scene collection by checking its ancestry.
    top_level_scene_coll = None
    
    def find_ancestor(coll):
        # Check if the collection is a top-level scene collection.
        if coll.name.startswith("+SC") and coll in context.scene.collection.children:
            return coll
        # Recurse up the hierarchy.
        for p in bpy.data.collections:
            if coll.name in p.children:
                ancestor = find_ancestor(p)
                if ancestor:
                    return ancestor
        return None

    # Start search from the object's immediate collections.
    for coll in active_obj.users_collection:
        top_level_scene_coll = find_ancestor(coll)
        if top_level_scene_coll:
            break
            
    if not top_level_scene_coll:
        print(f"AdvCopy Error: Could not determine the parent Scene Collection for '{active_obj.name}'.")
        return None

    # 2. Extract identifiers and build path.
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


def toggle_object_visibility(obj, frame_range, hide):
    """
    Keys the visibility of an object to be on or off for a specific frame range.
    It sets keyframes for both viewport and render visibility.
    
    Args:
        obj (bpy.types.Object): The object to keyframe.
        frame_range (tuple): A (start_frame, end_frame) tuple.
        hide (bool): True to hide the object during the range, False to show it.
    """
    start_frame, end_frame = frame_range

    # --- Keyframe Viewport and Render Visibility ---
    for prop in ["hide_viewport", "hide_render"]:
        # Set initial state before the shot. The object should have the opposite visibility.
        setattr(obj, prop, not hide)
        obj.keyframe_insert(data_path=prop, frame=start_frame - 1)

        # Set the desired visibility for the duration of the shot.
        setattr(obj, prop, hide)
        obj.keyframe_insert(data_path=prop, frame=start_frame)

        # Revert to the opposite state after the shot.
        setattr(obj, prop, not hide)
        obj.keyframe_insert(data_path=prop, frame=end_frame + 1)


# --- Operators ---

class ADVCOPY_OT_copy_to_current_shot(bpy.types.Operator):
    """Copies an object to the correct MODEL collection for the current timeline shot.
Hides the original during the shot and makes the copy visible only during the shot."""
    bl_idname = "object.advcopy_copy_to_current_shot"
    bl_label = "Copy to Current Shot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Only show operator if an object is selected and we are in a valid shot.
        return context.active_object is not None and get_current_shot_info(context) is not None

    def execute(self, context):
        shot_info = get_current_shot_info(context)
        if not shot_info:
            self.report({'WARNING'}, "Not in a defined shot. Check timeline markers (e.g., CAM-SC##-SH###).")
            return {'CANCELLED'}

        original_obj = context.active_object

        # Find the destination collection using our traversal logic.
        target_collection = find_shot_model_collection(context, shot_info['scene_str'], shot_info['shot_str'])
        if not target_collection:
            self.report({'ERROR'}, "Could not find or create the target shot collection.")
            return {'CANCELLED'}

        # Duplicate the object.
        new_obj = original_obj.copy()
        # If the object has data (e.g., it's a mesh, not an Empty), copy the data too.
        if original_obj.data:
            new_obj.data = original_obj.data.copy()
            
        new_obj.animation_data_clear() # Clear any existing animation
        new_obj.name = f"{original_obj.name}.{shot_info['scene_str']}.{shot_info['shot_str']}"
        
        # Link the new object to the target collection.
        target_collection.objects.link(new_obj)

        # Toggle visibility for the shot's duration.
        frame_range = (shot_info['start'], shot_info['end'])
        toggle_object_visibility(original_obj, frame_range, hide=True)
        toggle_object_visibility(new_obj, frame_range, hide=False)

        self.report({'INFO'}, f"Copied '{original_obj.name}' to collection '{target_collection.name}'")
        return {'FINISHED'}

class ADVCOPY_OT_copy_to_current_scene_model(bpy.types.Operator):
    """Copies object to the scene-level MODEL collection"""
    bl_idname = "object.advcopy_copy_to_current_scene"
    bl_label = "Copy to Current Scene"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        original_obj = context.active_object
        
        target_collection = find_scene_model_collection(context)
        if not target_collection:
            self.report({'ERROR'}, "Could not find the scene's MODEL collection.")
            return {'CANCELLED'}
            
        # Duplicate the object.
        new_obj = original_obj.copy()
        # If the object has data (e.g., it's a mesh, not an Empty), copy the data too.
        if original_obj.data:
            new_obj.data = original_obj.data.copy()

        new_obj.name = f"{original_obj.name}.scene_copy"
        
        # Link to target collection and unlink from all of original's collections
        # This assumes the new copy is only in the active collection, which is typical.
        current_collections = list(new_obj.users_collection)
        for coll in current_collections:
            coll.objects.unlink(new_obj)
            
        target_collection.objects.link(new_obj)
        
        self.report({'INFO'}, f"Copied '{original_obj.name}' to '{target_collection.name}'")
        return {'FINISHED'}


# --- Placeholder Operators ---

class ADVCOPY_OT_placeholder_move_all_scenes(bpy.types.Operator):
    """Placeholder for future functionality"""
    bl_idname = "object.advcopy_placeholder_move_all"
    bl_label = "Move Copies to All Scenes"
    
    def execute(self, context):
        self.report({'INFO'}, "This functionality is not yet implemented.")
        return {'CANCELLED'}

class ADVCOPY_OT_placeholder_copy_enviro(bpy.types.Operator):
    """Placeholder for future functionality"""
    bl_idname = "object.advcopy_placeholder_copy_enviro"
    bl_label = "Copy to Current Enviro"
    
    def execute(self, context):
        self.report({'INFO'}, "This functionality is not yet implemented.")
        return {'CANCELLED'}


# --- Menus ---

class ADVCOPY_MT_copy_to_scene_menu(bpy.types.Menu):
    bl_label = "Copy to Current Scene"
    bl_idname = "OBJECT_MT_advcopy_copy_to_scene_submenu"

    def draw(self, context):
        layout = self.layout
        layout.operator(ADVCOPY_OT_placeholder_move_all_scenes.bl_idname)
        layout.operator(ADVCOPY_OT_copy_to_current_scene_model.bl_idname)

def draw_main_menu(self, context):
    layout = self.layout
    layout.separator()
    # Main menu with the new operators
    layout.operator(ADVCOPY_OT_copy_to_current_shot.bl_idname, icon='SEQUENCE')
    layout.menu(ADVCOPY_MT_copy_to_scene_menu.bl_idname, icon='SCENE_DATA')
    layout.operator(ADVCOPY_OT_placeholder_copy_enviro.bl_idname, icon='WORLD')


# --- Registration ---

classes = [
    ADVCOPY_OT_copy_to_current_shot,
    ADVCOPY_OT_copy_to_current_scene_model,
    ADVCOPY_OT_placeholder_move_all_scenes,
    ADVCOPY_OT_placeholder_copy_enviro,
    ADVCOPY_MT_copy_to_scene_menu,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # Add our main menu to the object context menu (right-click).
    bpy.types.VIEW3D_MT_object_context_menu.append(draw_main_menu)

def unregister():
    # Remove the menu first.
    bpy.types.VIEW3D_MT_object_context_menu.remove(draw_main_menu)
    # Unregister classes in reverse order.
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
