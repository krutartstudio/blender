bl_info = {
    "name": "Outliner Move Logger (Timer Based)",
    "author": "Gemini",
    "version": (1, 2),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > View Tab",
    "description": "Logs details when assets are moved using a robust 1-second timer check.",
    "category": "System",
}

import bpy
import datetime

# Global storage
_hierarchy_snapshot = {}
_is_logging_active = False
_timer_handle = None

def get_timestamp():
    return datetime.datetime.now().strftime("%H:%M:%S")

def write_log(message):
    timestamp = get_timestamp()
    formatted_msg = f"[{timestamp}] {message}"
    print(formatted_msg)
    
    log_name = "Outliner_Activity_Log.txt"
    text_block = bpy.data.texts.get(log_name)
    if not text_block:
        text_block = bpy.data.texts.new(log_name)
    text_block.write(formatted_msg + "\n")

def get_scene_snapshot():
    """Snapshot current parent and collection memberships."""
    snapshot = {}
    # Check all objects in the file
    for obj in bpy.data.objects:
        parent_name = obj.parent.name if obj.parent else "None"
        # Use set for collections to make comparison order-independent
        collection_names = tuple(sorted([c.name for c in obj.users_collection]))
        
        snapshot[obj.name] = {
            'parent': parent_name,
            'collections': collection_names
        }
    return snapshot

def check_hierarchy_timer():
    """
    Timer function that runs every 1.0 second to check for changes.
    """
    global _hierarchy_snapshot, _is_logging_active
    
    if not _is_logging_active:
        return 1.0 # Keep timer running but do nothing

    # Safety: Ensure we are in a valid context to read data
    if not bpy.data:
        return 1.0

    current_snapshot = get_scene_snapshot()
    
    # Initialize if empty
    if not _hierarchy_snapshot:
        _hierarchy_snapshot = current_snapshot
        return 1.0

    # Compare
    for obj_name, current_data in current_snapshot.items():
        if obj_name in _hierarchy_snapshot:
            previous_data = _hierarchy_snapshot[obj_name]
            
            # 1. Hierarchy (Parent) Change
            if current_data['parent'] != previous_data['parent']:
                write_log(f"HIERARCHY MOVE: '{obj_name}' moved from parent '{previous_data['parent']}' to '{current_data['parent']}'")
            
            # 2. Collection Change
            if current_data['collections'] != previous_data['collections']:
                old_cols = set(previous_data['collections'])
                new_cols = set(current_data['collections'])
                added = new_cols - old_cols
                removed = old_cols - new_cols
                
                details = []
                if added: details.append(f"Added to {list(added)}")
                if removed: details.append(f"Removed from {list(removed)}")
                
                if details:
                    write_log(f"COLLECTION MOVE: '{obj_name}' changes: {', '.join(details)}")
        
        # Note: We ignore new object creation to focus strictly on 'moves' 
        # for existing assets, but you can add an 'else' block here to log creation.

    # Update Snapshot
    _hierarchy_snapshot = current_snapshot
    
    return 1.0 # Run again in 1.0 second

class OUTLINERLOG_PT_Panel(bpy.types.Panel):
    bl_label = "Outliner Logger"
    bl_idname = "OUTLINERLOG_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"

    def draw(self, context):
        layout = self.layout
        
        global _is_logging_active
        status = "Active (Timer Running)" if _is_logging_active else "Inactive"
        icon = 'TIME' if _is_logging_active else 'PAUSE'
        
        layout.label(text=f"Status: {status}")
        
        op_text = "Stop Logging" if _is_logging_active else "Start Logging"
        layout.operator("outlinerlog.toggle", text=op_text, icon=icon)
        
        if "Outliner_Activity_Log.txt" in bpy.data.texts:
            layout.separator()
            layout.operator("outlinerlog.open_log", text="Open Log", icon='FILE_TEXT')

class OUTLINERLOG_OT_Toggle(bpy.types.Operator):
    bl_idname = "outlinerlog.toggle"
    bl_label = "Toggle Logger"

    def execute(self, context):
        global _is_logging_active, _hierarchy_snapshot
        
        _is_logging_active = not _is_logging_active
        
        if _is_logging_active:
            _hierarchy_snapshot = get_scene_snapshot()
            self.report({'INFO'}, "Logger Started (Polling every 1s)")
        else:
            self.report({'INFO'}, "Logger Stopped")
            
        return {'FINISHED'}

class OUTLINERLOG_OT_OpenLog(bpy.types.Operator):
    bl_idname = "outlinerlog.open_log"
    bl_label = "Open Log"

    def execute(self, context):
        txt = bpy.data.texts.get("Outliner_Activity_Log.txt")
        if txt:
            for area in context.screen.areas:
                if area.type == 'TEXT_EDITOR':
                    area.spaces.active.text = txt
                    return {'FINISHED'}
        return {'FINISHED'}

classes = (
    OUTLINERLOG_PT_Panel,
    OUTLINERLOG_OT_Toggle,
    OUTLINERLOG_OT_OpenLog,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # Register the timer
    # We use 'persistent' logic manually by checking if it's already running
    if not bpy.app.timers.is_registered(check_hierarchy_timer):
        bpy.app.timers.register(check_hierarchy_timer)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
    
    if bpy.app.timers.is_registered(check_hierarchy_timer):
        bpy.app.timers.unregister(check_hierarchy_timer)

if __name__ == "__main__":
    register()