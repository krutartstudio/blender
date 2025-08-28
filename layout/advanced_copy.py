bl_info = {
    "name": "Advanced Copy",
    "author": "Gemini, Krutart",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "Outliner & 3D Viewport > Right-Click Menu",
    "description": "Provides specific hierarchy traversal copy/move functionalities for collections, objects, and empties.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import re
import logging
from bpy.props import StringProperty, EnumProperty

# --- Configure Logging ---
# Set up a logger for clear feedback and debugging.
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)

# --- Helper Functions ---

def get_shot_collections():
    """
    Scans the scene for collections that match the shot naming convention 'MODEL-SC##-SH###' or 'VFX-SC##-SH###'.
    
    Returns:
        list: A list of collection names that are considered shots.
    """
    shot_collections = []
    for coll in bpy.data.collections:
        if re.match(r"^(MODEL|VFX)-SC\d+-SH\d+$", coll.name, re.IGNORECASE):
            shot_collections.append(coll.name)
    return sorted(shot_collections)

def get_timeline_markers():
    """
    Returns a sorted list of timeline markers.
    
    Returns:
        list: A sorted list of timeline markers.
    """
    return sorted(bpy.context.scene.timeline_markers, key=lambda m: m.frame)

def get_marker_range(marker_name):
    """
    Finds the start and end frame for a given marker.
    
    Args:
        marker_name (str): The name of the marker.
        
    Returns:
        tuple: A tuple containing the start and end frame of the marker's range.
    """
    markers = get_timeline_markers()
    for i, marker in enumerate(markers):
        if marker.name == marker_name:
            start_frame = marker.frame
            # If it's not the last marker, the end frame is the frame before the next marker
            if i + 1 < len(markers):
                end_frame = markers[i+1].frame - 1
            else:
                # Otherwise, it's the scene end
                end_frame = bpy.context.scene.frame_end
            return start_frame, end_frame
    return None, None

def set_visibility(obj, start_frame, end_frame, visible_in_range=True):
    """
    Sets the visibility of an object (render and viewport) based on a frame range.
    
    Args:
        obj (bpy.types.Object): The object to modify.
        start_frame (int): The start frame of the visibility range.
        end_frame (int): The end frame of the visibility range.
        visible_in_range (bool): If True, the object is visible inside the range and hidden outside. 
                                 If False, it's hidden inside and visible outside.
    """
    # Ensure the object has animation data
    if not obj.animation_data:
        obj.animation_data_create()

    # Viewport visibility
    if not obj.animation_data.action:
        obj.animation_data.action = bpy.data.actions.new(name=f"{obj.name}_VisibilityAction")
    
    hide_viewport_fcurve = obj.animation_data.action.fcurves.find('hide_viewport')
    if not hide_viewport_fcurve:
        hide_viewport_fcurve = obj.animation_data.action.fcurves.new(data_path='hide_viewport')

    hide_render_fcurve = obj.animation_data.action.fcurves.find('hide_render')
    if not hide_render_fcurve:
        hide_render_fcurve = obj.animation_data.action.fcurves.new(data_path='hide_render')

    # Keyframe the visibility
    hide_viewport_fcurve.keyframe_points.insert(start_frame - 1, not visible_in_range).interpolation = 'CONSTANT'
    hide_viewport_fcurve.keyframe_points.insert(start_frame, not visible_in_range).interpolation = 'CONSTANT'
    hide_viewport_fcurve.keyframe_points.insert(end_frame, not visible_in_range).interpolation = 'CONSTANT'
    hide_viewport_fcurve.keyframe_points.insert(end_frame + 1, visible_in_range).interpolation = 'CONSTANT'
    
    hide_render_fcurve.keyframe_points.insert(start_frame - 1, not visible_in_range).interpolation = 'CONSTANT'
    hide_render_fcurve.keyframe_points.insert(start_frame, not visible_in_range).interpolation = 'CONSTANT'
    hide_render_fcurve.keyframe_points.insert(end_frame, not visible_in_range).interpolation = 'CONSTANT'
    hide_render_fcurve.keyframe_points.insert(end_frame + 1, visible_in_range).interpolation = 'CONSTANT'

def copy_or_move_object(context, target_collection_name, copy=True):
    """
    The core logic for copying or moving selected objects to a target collection.
    
    Args:
        context (bpy.types.Context): The current Blender context.
        target_collection_name (str): The name of the destination collection.
        copy (bool): If True, performs a copy. If False, performs a move.
    """
    target_collection = bpy.data.collections.get(target_collection_name)
    if not target_collection:
        log.error(f"Target collection '{target_collection_name}' not found.")
        return

    selected_objects = context.selected_objects
    if not selected_objects:
        log.warning("No objects selected.")
        return

    for obj in selected_objects:
        original_collections = list(obj.users_collection)
        
        new_obj = obj
        if copy:
            new_obj = obj.copy()
            if obj.data:
                new_obj.data = obj.data.copy()
            
            # Suffix logic
            match = re.search(r"-(SC\d+)-(SH\d+)$", target_collection_name, re.IGNORECASE)
            if match:
                sc_id, sh_id = match.groups()
                new_obj.name = f"{obj.name}-{sc_id.upper()}-{sh_id.upper()}"
            else:
                 match_sc = re.search(r"-(SC\d+)", target_collection_name, re.IGNORECASE)
                 if match_sc:
                     sc_id = match_sc.groups()[0]
                     new_obj.name = f"{obj.name}-{sc_id.upper()}"


        # Link to new collection
        target_collection.objects.link(new_obj)

        # Unlink from original collections if moving
        if not copy:
            for coll in original_collections:
                coll.objects.unlink(obj)
        
        # Visibility logic for 'Copy to Shot'
        if "SH" in target_collection_name:
            match = re.search(r"CAM-(SC\d+)-(SH\d+)", f"CAM-{target_collection_name.split('-')[-2]}-{target_collection_name.split('-')[-1]}", re.IGNORECASE)
            if match:
                marker_name = match.string
                start_frame, end_frame = get_marker_range(marker_name)
                if start_frame is not None and end_frame is not None:
                    if copy:
                        set_visibility(new_obj, start_frame, end_frame, visible_in_range=True)
                        set_visibility(obj, start_frame, end_frame, visible_in_range=False)
                    else: # Move
                        set_visibility(new_obj, start_frame, end_frame, visible_in_range=True)

# --- Operators ---

class ADVANCEDCOPY_OT_copy_to_shot(bpy.types.Operator):
    """Copy selected objects to a specific shot collection"""
    bl_idname = "advancedcopy.copy_to_shot"
    bl_label = "Copy to Shot"
    bl_options = {'REGISTER', 'UNDO'}

    target_shot: StringProperty()

    def execute(self, context):
        log.info(f"Copying to shot: {self.target_shot}")
        copy_or_move_object(context, self.target_shot, copy=True)
        return {'FINISHED'}

class ADVANCEDCOPY_OT_move_to_all_scenes(bpy.types.Operator):
    """Move selected objects from ENV to SC collections in all scenes"""
    bl_idname = "advancedcopy.move_to_all_scenes"
    bl_label = "Move to All Scenes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        log.info("Moving to all scenes")
        selected = context.active_object or context.selected_objects[0]
        source_prefix = "MODEL-ENV" if "MODEL-ENV" in selected.users_collection[0].name else "VFX-ENV"
        
        for scene in bpy.data.scenes:
            for coll in scene.collection.children_recursive:
                if coll.name.startswith(source_prefix.replace("-ENV", "-SC")):
                    copy_or_move_object(context, coll.name, copy=False)
        return {'FINISHED'}

class ADVANCEDCOPY_OT_copy_to_scene(bpy.types.Operator):
    """Copy selected objects from ENV to SC collections in a specific scene"""
    bl_idname = "advancedcopy.copy_to_scene"
    bl_label = "Copy to Scene"
    bl_options = {'REGISTER', 'UNDO'}

    target_scene: StringProperty()

    def execute(self, context):
        log.info(f"Copying to scene: {self.target_scene}")
        scene = bpy.data.scenes.get(self.target_scene)
        if not scene:
            return {'CANCELLED'}
            
        selected = context.active_object or context.selected_objects[0]
        source_prefix = "MODEL-ENV" if "MODEL-ENV" in selected.users_collection[0].name else "VFX-ENV"
        
        for coll in scene.collection.children_recursive:
            if coll.name.startswith(source_prefix.replace("-ENV", "-SC")):
                copy_or_move_object(context, coll.name, copy=True)
                # Visibility for the scene's frame range
                for obj in context.selected_objects:
                    set_visibility(obj, scene.frame_start, scene.frame_end, visible_in_range=True)

        return {'FINISHED'}

class ADVANCEDCOPY_OT_move_to_all_enviros(bpy.types.Operator):
    """Move selected objects from LOC to all ENV collections"""
    bl_idname = "advancedcopy.move_to_all_enviros"
    bl_label = "Move to All Enviros"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        log.info("Moving to all enviros")
        selected = context.active_object or context.selected_objects[0]
        source_prefix = "MODEL-LOC" if "MODEL-LOC" in selected.users_collection[0].name else "VFX-LOC"
        target_prefix = source_prefix.replace("-LOC", "-ENV")

        for coll in bpy.data.collections:
            if coll.name.startswith(target_prefix):
                copy_or_move_object(context, coll.name, copy=False)
                
        return {'FINISHED'}

# --- Menus ---

class ADVANCEDCOPY_MT_copy_to_shot_menu(bpy.types.Menu):
    bl_label = "Copy to Shot"
    bl_idname = "ADVANCEDCOPY_MT_copy_to_shot_menu"

    def draw(self, context):
        layout = self.layout
        shot_collections = get_shot_collections()
        for shot in shot_collections:
            op = layout.operator(ADVANCEDCOPY_OT_copy_to_shot.bl_idname, text=shot)
            op.target_shot = shot

class ADVANCEDCOPY_MT_scene_operations_menu(bpy.types.Menu):
    bl_label = "Scene Operations"
    bl_idname = "ADVANCEDCOPY_MT_scene_operations_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator(ADVANCEDCOPY_OT_move_to_all_scenes.bl_idname)
        
        # Submenu for 'Copy to Scene'
        layout.menu("ADVANCEDCOPY_MT_copy_to_scene_submenu")

class ADVANCEDCOPY_MT_copy_to_scene_submenu(bpy.types.Menu):
    bl_label = "Copy to Scene"
    bl_idname = "ADVANCEDCOPY_MT_copy_to_scene_submenu"

    def draw(self, context):
        layout = self.layout
        for scene in bpy.data.scenes:
            op = layout.operator(ADVANCEDCOPY_OT_copy_to_scene.bl_idname, text=scene.name)
            op.target_scene = scene.name

def draw_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.menu(ADVANCEDCOPY_MT_copy_to_shot_menu.bl_idname)
    layout.menu(ADVANCEDCOPY_MT_scene_operations_menu.bl_idname)
    layout.operator(ADVANCEDCOPY_OT_move_to_all_enviros.bl_idname)

# --- Registration ---

classes = (
    ADVANCEDCOPY_OT_copy_to_shot,
    ADVANCEDCOPY_OT_move_to_all_scenes,
    ADVANCEDCOPY_OT_copy_to_scene,
    ADVANCEDCOPY_OT_move_to_all_enviros,
    ADVANCEDCOPY_MT_copy_to_shot_menu,
    ADVANCEDCOPY_MT_scene_operations_menu,
    ADVANCEDCOPY_MT_copy_to_scene_submenu,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object_context_menu.append(draw_menu)
    bpy.types.OUTLINER_MT_collection.append(draw_menu)
    bpy.types.OUTLINER_MT_object.append(draw_menu)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.types.VIEW3D_MT_object_context_menu.remove(draw_menu)
    bpy.types.OUTLINER_MT_collection.remove(draw_menu)
    bpy.types.OUTLINER_MT_object.remove(draw_menu)


if __name__ == "__main__":
    register()
