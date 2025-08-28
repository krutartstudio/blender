bl_info = {
    "name": "Advanced Copy",
    "author": "Gemini",
    "version": (1, 0, 1),
    "blender": (4, 2, 0),
    "location": "Outliner > Right-Click Menu, 3D View > Right-Click Menu",
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
# Provides clear feedback in the system console for artists and developers.
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# --- Helper Functions ---

def get_active_datablock(context):
    """
    Determines the active datablock (object, collection, or empty) from the context.
    This is crucial for knowing what the user has right-clicked on.
    """
    # Priority is given to the Outliner's context
    if context.area.type == 'OUTLINER':
        selected = context.selected_ids
        if selected:
            # In Blender, the active item is often the last one selected.
            # We prioritize collections if both an object and its collection are selected.
            active_id = selected[-1]
            if isinstance(active_id, bpy.types.Collection):
                return active_id, 'COLLECTION'
            elif isinstance(active_id, bpy.types.Object):
                return active_id, 'OBJECT'
    
    # Fallback to the 3D Viewport's active object
    active_obj = context.active_object
    if active_obj:
        return active_obj, 'OBJECT'
        
    return None, None

def get_shot_collections(prefix="MODEL"):
    """
    Scans the entire blend file for collections that match the shot naming convention.
    Example: 'MODEL-SC01-SH001' or 'VFX-SC01-SH001'.
    """
    shot_collections = []
    pattern = re.compile(rf"^{prefix}-SC\d+-SH\d+$")
    for coll in bpy.data.collections:
        if pattern.match(coll.name):
            shot_collections.append(coll)
    # Sort alphabetically for a clean menu layout
    return sorted(shot_collections, key=lambda c: c.name)

def get_project_scenes():
    """
    Retrieves all scenes in the project that match the 'SC##-' naming convention.
    """
    scene_collections = []
    pattern = re.compile(r"^SC\d+-.*")
    for scene in bpy.data.scenes:
        if pattern.match(scene.name):
            scene_collections.append(scene)
    return sorted(scene_collections, key=lambda s: s.name)

def get_shot_frame_range(shot_name):
    """
    Finds the start and end frame for a shot based on timeline markers.
    The shot_name should correspond to a marker (e.g., 'CAM-SC01-SH001').
    """
    # Extract SC and SH from the collection name like 'MODEL-SC01-SH001'
    match = re.search(r"SC(\d+)-SH(\d+)", shot_name)
    if not match:
        log.warning(f"Could not parse shot name '{shot_name}' for frame range.")
        return None, None

    sc_id, sh_id = match.groups()
    marker_name = f"CAM-SC{sc_id.zfill(2)}-SH{sh_id.zfill(3)}"
    
    start_marker = bpy.context.scene.timeline_markers.get(marker_name)
    if not start_marker:
        log.warning(f"Start marker '{marker_name}' not found.")
        return None, None

    # Find the next marker to determine the end frame
    sorted_markers = sorted(bpy.context.scene.timeline_markers, key=lambda m: m.frame)
    next_marker = None
    for i, marker in enumerate(sorted_markers):
        if marker == start_marker and i + 1 < len(sorted_markers):
            next_marker = sorted_markers[i+1]
            break
    
    if not next_marker:
        log.warning(f"Could not find a subsequent marker to determine end frame for '{marker_name}'.")
        # Fallback to scene end if no next marker
        return start_marker.frame, bpy.context.scene.frame_end

    return start_marker.frame, next_marker.frame - 1


def set_visibility(item, frame, render_visible, viewport_visible):
    """Inserts visibility keyframes for an item."""
    item.hide_render = not render_visible
    item.keyframe_insert(data_path="hide_render", frame=frame)
    item.hide_viewport = not viewport_visible
    item.keyframe_insert(data_path="hide_viewport", frame=frame)

def animate_visibility(item, start_frame, end_frame, invert_original=None):
    """
    Animates the render and viewport visibility of an item to be visible
    only within a specific frame range.
    If 'invert_original' is provided, its visibility is set to the inverse.
    """
    log.info(f"Animating visibility for '{item.name}' from frame {start_frame} to {end_frame}.")
    # Set visibility for the copy
    set_visibility(item, start_frame - 1, False, False)
    set_visibility(item, start_frame, True, True)
    set_visibility(item, end_frame + 1, False, False)

    if invert_original:
        log.info(f"Inverting visibility for original '{invert_original.name}'.")
        # Set visibility for the original item
        set_visibility(invert_original, start_frame - 1, True, True)
        set_visibility(invert_original, start_frame, False, False)
        set_visibility(invert_original, end_frame + 1, True, True)

def get_source_collection(item):
    """Finds the collection an object or collection belongs to."""
    if isinstance(item, bpy.types.Object):
        # An object can be in multiple collections; we'll take the first one.
        if item.users_collection:
            return item.users_collection[0]
    elif isinstance(item, bpy.types.Collection):
        # Find the parent collection of the given collection.
        for coll in bpy.data.collections:
            if item.name in coll.children:
                return coll
    return bpy.context.scene.collection # Fallback to the scene's master collection.

# --- Main Operator Classes ---

class ADVCOPY_OT_copy_to_shot(bpy.types.Operator):
    """Copies the selected datablock to a specified shot collection and handles visibility."""
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

        # --- Duplication Logic ---
        new_datablock = None
        if datablock_type == 'OBJECT':
            # Deep copy the object and its data
            new_datablock = datablock.copy()
            if datablock.data:
                new_datablock.data = datablock.data.copy()
            # Link to the target collection
            target_coll.objects.link(new_datablock)
        elif datablock_type == 'COLLECTION':
            # Recursive copy for collections
            def copy_collection_recursive(original_coll, parent_target_coll):
                new_coll_name = f"{original_coll.name}-copy"
                new_coll = bpy.data.collections.new(name=new_coll_name)
                parent_target_coll.children.link(new_coll)
                
                for obj in original_coll.objects:
                    new_obj = obj.copy()
                    if obj.data:
                        new_obj.data = obj.data.copy()
                    new_coll.objects.link(new_obj)

                for child in original_coll.children:
                    copy_collection_recursive(child, new_coll)
                return new_coll

            new_datablock = copy_collection_recursive(datablock, target_coll)

        if not new_datablock:
            self.report({'ERROR'}, "Failed to create a copy.")
            return {'CANCELLED'}

        # --- Renaming ---
        scene_match = re.search(r"-SC(\d+)-", target_coll.name)
        shot_match = re.search(r"-SH(\d+)", target_coll.name)
        
        if scene_match and shot_match:
            scene_suffix = f"SC{scene_match.group(1)}"
            shot_suffix = f"SH{shot_match.group(1)}"
            new_datablock.name = f"{datablock.name}-{scene_suffix}-{shot_suffix}"
        else:
             new_datablock.name = f"{datablock.name}-copy"


        # --- Visibility Animation ---
        start_frame, end_frame = get_shot_frame_range(target_coll.name)
        if start_frame is not None and end_frame is not None:
            animate_visibility(new_datablock, start_frame, end_frame, invert_original=datablock)
        else:
            self.report({'WARNING'}, f"Could not determine frame range for '{target_coll.name}'. Visibility not animated.")

        self.report({'INFO'}, f"Copied '{datablock.name}' to '{new_datablock.name}' in '{target_coll.name}'.")
        return {'FINISHED'}

class ADVCOPY_OT_move_to_all_scenes(bpy.types.Operator):
    """Moves an item from an ENV collection to all SCENE collections across all scenes."""
    bl_idname = "advanced_copy.move_to_all_scenes"
    bl_label = "Move to All Scenes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, datablock_type = get_active_datablock(context)
        if not datablock or datablock_type != 'OBJECT': # This logic is simpler for objects
            self.report({'ERROR'}, "Operation requires an active Object.")
            return {'CANCELLED'}

        source_collection = get_source_collection(datablock)
        if not source_collection or not (source_collection.name.startswith("MODEL-ENV") or source_collection.name.startswith("VFX-ENV")):
            self.report({'ERROR'}, "Selected object must be in a 'MODEL-ENV...' or 'VFX-ENV...' collection.")
            return {'CANCELLED'}
        
        prefix = "MODEL" if source_collection.name.startswith("MODEL") else "VFX"
        log.info(f"Moving '{datablock.name}' from '{source_collection.name}' to all '{prefix}-SC...' collections.")

        scenes = get_project_scenes()
        if not scenes:
            self.report({'WARNING'}, "No scenes with 'SC##-' prefix found.")
            return {'CANCELLED'}

        moved_count = 0
        target_subcollection_name = f"{prefix}-{context.scene.name.split('-')[0]}"
        
        for scene in scenes:
            target_coll = scene.collection.children.get(f"+{prefix}-{scene.name}+")
            if target_coll:
                final_target_coll = target_coll.children.get(target_subcollection_name)
                if final_target_coll:
                    final_target_coll.objects.link(datablock)
                    moved_count += 1
                else:
                    log.warning(f"Could not find target sub-collection in scene '{scene.name}'.")

        if moved_count > 0:
            source_collection.objects.unlink(datablock)
            self.report({'INFO'}, f"Moved '{datablock.name}' to {moved_count} scene(s).")
        else:
            self.report({'ERROR'}, "Could not find any valid target collections in any scene.")

        return {'FINISHED'}

class ADVCOPY_OT_copy_to_scene(bpy.types.Operator):
    """Copies an item from an ENV collection to a specific SCENE collection."""
    bl_idname = "advanced_copy.copy_to_scene"
    bl_label = "Copy to Scene"
    bl_options = {'REGISTER', 'UNDO'}

    target_scene_name: StringProperty()

    def execute(self, context):
        datablock, datablock_type = get_active_datablock(context)
        if not datablock or datablock_type != 'OBJECT':
            self.report({'ERROR'}, "Operation requires an active Object.")
            return {'CANCELLED'}

        source_collection = get_source_collection(datablock)
        if not source_collection or not (source_collection.name.startswith("MODEL-ENV") or source_collection.name.startswith("VFX-ENV")):
            self.report({'ERROR'}, "Selected object must be in a 'MODEL-ENV...' or 'VFX-ENV...' collection.")
            return {'CANCELLED'}

        target_scene = bpy.data.scenes.get(self.target_scene_name)
        if not target_scene:
            self.report({'ERROR'}, f"Target scene '{self.target_scene_name}' not found.")
            return {'CANCELLED'}

        prefix = "MODEL" if source_collection.name.startswith("MODEL") else "VFX"
        
        target_coll_name = f"{prefix}-{target_scene.name}"
        target_coll = target_scene.collection.children.get(f"+{prefix}-{target_scene.name}+")
        if not target_coll:
            self.report({'ERROR'}, f"Could not find target collection '{target_coll_name}' in scene.")
            return {'CANCELLED'}

        log.info(f"Copying '{datablock.name}' to '{target_coll.name}' in scene '{target_scene.name}'.")

        # --- Duplicate and Link ---
        new_obj = datablock.copy()
        if datablock.data:
            new_obj.data = datablock.data.copy()
        
        # Find the correct sub-collection to link to.
        final_target_coll = target_coll.children.get(f"{prefix}-{target_scene.name.split('-')[0]}")
        if final_target_coll:
            final_target_coll.objects.link(new_obj)
        else:
            target_coll.objects.link(new_obj) # Fallback to parent

        # --- Renaming ---
        scene_suffix = target_scene.name.split('-')[0] # e.g., SC01
        new_obj.name = f"{datablock.name}-{scene_suffix}"

        # --- Visibility ---
        start_frame = target_scene.frame_start
        end_frame = target_scene.frame_end
        animate_visibility(new_obj, start_frame, end_frame) # No original inversion needed here

        self.report({'INFO'}, f"Copied '{datablock.name}' to '{target_coll.name}'.")
        return {'FINISHED'}


class ADVCOPY_OT_move_to_all_enviros(bpy.types.Operator):
    """Moves an item from a LOC collection to all ENV collections."""
    bl_idname = "advanced_copy.move_to_all_enviros"
    bl_label = "Move to All Enviros"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        datablock, datablock_type = get_active_datablock(context)
        if not datablock or datablock_type != 'OBJECT':
            self.report({'ERROR'}, "Operation requires an active Object.")
            return {'CANCELLED'}

        source_collection = get_source_collection(datablock)
        if not source_collection or not (source_collection.name.startswith("MODEL-LOC") or source_collection.name.startswith("VFX-LOC")):
            self.report({'ERROR'}, "Selected object must be in a 'MODEL-LOC...' or 'VFX-LOC...' collection.")
            return {'CANCELLED'}
            
        prefix = "MODEL" if source_collection.name.startswith("MODEL") else "VFX"
        
        env_collections = [c for c in bpy.data.collections if c.name.startswith(f"+{prefix}-ENV")]
        if not env_collections:
            self.report({'WARNING'}, f"No '{prefix}-ENV...' collections found to move to.")
            return {'CANCELLED'}

        log.info(f"Moving '{datablock.name}' to {len(env_collections)} ENV collections.")
        
        for env_coll in env_collections:
            # Find the actual target sub-collection, e.g. 'MODEL-ENV-...'
            target_sub_coll_name = f"{prefix}-{env_coll.name.strip('+').split('-', 1)[1]}"
            target_sub_coll = env_coll.children.get(target_sub_coll_name)
            if target_sub_coll:
                target_sub_coll.objects.link(datablock)
            else:
                log.warning(f"Could not find sub-collection '{target_sub_coll_name}' in '{env_coll.name}'")


        # Unlink from original after linking to all new ones
        source_collection.objects.unlink(datablock)
        
        self.report({'INFO'}, f"Moved '{datablock.name}' to {len(env_collections)} environment collections.")
        return {'FINISHED'}

# --- Dynamic Menus ---

class ADVCOPY_MT_copy_to_shot_menu(bpy.types.Menu):
    """Dynamically lists all available shot collections for copying."""
    bl_idname = "ADVCOPY_MT_copy_to_shot_menu"
    bl_label = "Copy to Shot"

    def draw(self, context):
        layout = self.layout
        datablock, _ = get_active_datablock(context)
        if not datablock:
            return

        source_collection = get_source_collection(datablock)
        if not source_collection:
            return
            
        prefix = "MODEL" if "MODEL" in source_collection.name else "VFX"
        
        shot_collections = get_shot_collections(prefix=prefix)
        if not shot_collections:
            layout.label(text="No Shot Collections Found")
            return

        for coll in shot_collections:
            op = layout.operator(ADVCOPY_OT_copy_to_shot.bl_idname, text=coll.name)
            op.target_shot_collection = coll.name

class ADVCOPY_MT_scene_operations_menu(bpy.types.Menu):
    """Menu for scene-level copy and move operations."""
    bl_idname = "ADVCOPY_MT_scene_operations_menu"
    bl_label = "Scene Operations"

    def draw(self, context):
        layout = self.layout
        layout.operator(ADVCOPY_OT_move_to_all_scenes.bl_idname)
        
        # Sub-menu for copying to a specific scene
        layout.menu("ADVCOPY_MT_copy_to_scene_submenu")

class ADVCOPY_MT_copy_to_scene_submenu(bpy.types.Menu):
    """Dynamically lists scenes for the 'Copy to Scene' operator."""
    bl_idname = "ADVCOPY_MT_copy_to_scene_submenu"
    bl_label = "Copy to Scene"

    def draw(self, context):
        layout = self.layout
        scenes = get_project_scenes()
        if not scenes:
            layout.label(text="No 'SC##-' Scenes Found")
            return
        
        for scene in scenes:
            op = layout.operator(ADVCOPY_OT_copy_to_scene.bl_idname, text=scene.name)
            op.target_scene_name = scene.name

# --- UI Integration ---

def add_context_menus(self, context):
    """Generic function to draw the menu items."""
    datablock, _ = get_active_datablock(context)
    if not datablock:
        return
    
    layout = self.layout
    layout.separator()
    # Draw the dynamic menus
    layout.menu(ADVCOPY_MT_copy_to_shot_menu.bl_idname, icon='COPYDOWN')
    layout.menu(ADVCOPY_MT_scene_operations_menu.bl_idname, icon='SCENE_DATA')
    layout.operator(ADVCOPY_OT_move_to_all_enviros.bl_idname, icon='CON_TRANSLIKE')
    layout.separator()


# --- Registration ---
classes = (
    ADVCOPY_OT_copy_to_shot,
    ADVCOPY_OT_move_to_all_scenes,
    ADVCOPY_OT_copy_to_scene,
    ADVCOPY_OT_move_to_all_enviros,
    ADVCOPY_MT_copy_to_shot_menu,
    ADVCOPY_MT_scene_operations_menu,
    ADVCOPY_MT_copy_to_scene_submenu,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # Add the menu to the Outliner and 3D Viewport right-click menus
    bpy.types.OUTLINER_MT_collection.append(add_context_menus)
    bpy.types.OUTLINER_MT_object.append(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.append(add_context_menus)


def unregister():
    # Remove the menu from all context menus
    bpy.types.OUTLINER_MT_collection.remove(add_context_menus)
    bpy.types.OUTLINER_MT_object.remove(add_context_menus)
    bpy.types.VIEW3D_MT_object_context_menu.remove(add_context_menus)
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
