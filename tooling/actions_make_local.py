bl_info = {
    "name": "krutart make linked actions local",
    "author": "iorisek",
    "version": (1, 3),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Localize Actions",
    "description": "Converts linked actions to local copies for selected objects",
    "category": "Animation",
}

import bpy

class OBJECT_OT_make_actions_local(bpy.types.Operator):
    bl_idname = "object.make_actions_local"
    bl_label = "Make Actions Local"
    bl_description = "Convert all linked actions to local copies for selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def execute(self, context):
        processed_actions = set()
        objects_processed = 0
        actions_localized = 0

        for obj in context.selected_objects:
            obj_processed = False
            # Process object animation data
            if obj.animation_data and obj.animation_data.action:
                if self.process_action(obj, obj.animation_data.action, processed_actions):
                    obj_processed = True
                    actions_localized += 1

            # Process shape keys
            shape_actions = self.process_shape_keys(obj, processed_actions)
            if shape_actions > 0:
                obj_processed = True
                actions_localized += shape_actions

            # Process NLA strips
            nla_actions = self.process_nla_strips(obj, processed_actions)
            if nla_actions > 0:
                obj_processed = True
                actions_localized += nla_actions

            if obj_processed:
                objects_processed += 1

        self.report({'INFO'}, f"Localized {actions_localized} actions on {objects_processed} objects")
        return {'FINISHED'}

    def process_shape_keys(self, obj, processed_actions):
        if not obj.data:
            return 0
            
        shape_keys = getattr(obj.data, 'shape_keys', None)
        if not shape_keys:
            return 0

        action_count = 0
        # Process shape key's active action
        if shape_keys.animation_data and shape_keys.animation_data.action:
            if self.process_action(shape_keys, shape_keys.animation_data.action, processed_actions):
                action_count += 1

        # Process shape key's NLA strips
        if shape_keys.animation_data and shape_keys.animation_data.nla_tracks:
            for track in shape_keys.animation_data.nla_tracks:
                for strip in track.strips:
                    if strip.action and strip.action not in processed_actions:
                        if self.process_action(shape_keys, strip.action, processed_actions):
                            action_count += 1
        return action_count

    def process_nla_strips(self, obj, processed_actions):
        if not obj.animation_data or not obj.animation_data.nla_tracks:
            return 0

        action_count = 0
        for track in obj.animation_data.nla_tracks:
            for strip in track.strips:
                if strip.action and strip.action not in processed_actions:
                    if self.process_action(obj, strip.action, processed_actions):
                        action_count += 1
        return action_count

    def process_action(self, owner, action, processed_actions):
        if not action.library or action in processed_actions:
            return False

        # Create local copy
        new_action = action.copy()
        new_action.name = f"{action.name}_Local"
        new_action.use_fake_user = False

        # Replace references
        if owner.animation_data:
            # Replace active action
            if owner.animation_data.action == action:
                owner.animation_data.action = new_action
            
            # Replace NLA strip references
            if owner.animation_data.nla_tracks:
                for track in owner.animation_data.nla_tracks:
                    for strip in track.strips:
                        if strip.action == action:
                            strip.action = new_action

        processed_actions.add(action)
        return True

class VIEW3D_PT_LocalizeActions(bpy.types.Panel):
    bl_label = "Localize Actions"
    bl_idname = "VIEW3D_PT_localize_actions"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Localize Actions"
    bl_context = "objectmode"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("object.make_actions_local", icon='ACTION')

def menu_func(self, context):
    self.layout.operator(OBJECT_OT_make_actions_local.bl_idname)

def register():
    bpy.utils.register_class(OBJECT_OT_make_actions_local)
    bpy.utils.register_class(VIEW3D_PT_LocalizeActions)
    bpy.types.VIEW3D_MT_object_context_menu.append(menu_func)
    bpy.types.NLA_MT_context_menu.append(menu_func)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_make_actions_local)
    bpy.utils.unregister_class(VIEW3D_PT_LocalizeActions)
    bpy.types.VIEW3D_MT_object_context_menu.remove(menu_func)
    bpy.types.NLA_MT_context_menu.remove(menu_func)

if __name__ == "__main__":
    register()
