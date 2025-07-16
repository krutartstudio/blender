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
            # Process object animation data
            obj_processed, obj_actions = self.process_animation_data(obj, processed_actions)
            if obj_processed:
                objects_processed += 1
                actions_localized += obj_actions

            # Process shape keys
            shape_processed, shape_actions = self.process_shape_keys(obj, processed_actions)
            if shape_processed:
                objects_processed += 1
                actions_localized += shape_actions

            # Process NLA strips
            nla_processed, nla_actions = self.process_nla_strips(obj, processed_actions)
            if nla_processed:
                objects_processed += 1
                actions_localized += nla_actions

            data_processed, data_actions = self.process_data_animation(obj, processed_actions)
            if data_processed:
                objects_processed += 1
                actions_localized += data_actions

        self.report({'INFO'}, f"Localized {actions_localized} actions on {objects_processed} objects")
        return {'FINISHED'}

    def process_data_animation(self, obj, processed_actions):
        if not obj.data:
            return False, 0

        data_block = obj.data
        if not hasattr(data_block, 'animation_data') or not data_block.animation_data:
            return False, 0

        changed = False
        action_count = 0

        # Process direct action
        if data_block.animation_data.action:
            action = data_block.animation_data.action
            if action.library and action not in processed_actions:
                self.make_action_local(data_block, action, processed_actions)
                changed = True
                action_count += 1

        # Process NLA strips
        if data_block.animation_data.nla_tracks:
            for track in data_block.animation_data.nla_tracks:
                for strip in track.strips:
                    if strip.type == 'CLIP' and strip.action and strip.action.library:
                        if strip.action not in processed_actions:
                            self.make_action_local(data_block, strip.action, processed_actions)
                            changed = True
                            action_count += 1

        return changed, action_count

    def process_animation_data(self, obj, processed_actions):
        if not obj.animation_data or not obj.animation_data.action:
            return False, 0

        action = obj.animation_data.action
        if action.library and action not in processed_actions:
            self.make_action_local(obj, action, processed_actions)
            return True, 1
        return False, 0

    def process_shape_keys(self, obj, processed_actions):
        if not obj.data or not obj.data.shape_keys:
            return False, 0

        shape_keys = obj.data.shape_keys
        if shape_keys.animation_data and shape_keys.animation_data.action:
            action = shape_keys.animation_data.action
            if action.library and action not in processed_actions:
                self.make_action_local(shape_keys, action, processed_actions)
                return True, 1
        return False, 0

    def process_nla_strips(self, obj, processed_actions):
        if not obj.animation_data or not obj.animation_data.nla_tracks:
            return False, 0

        changed = False
        action_count = 0
        for track in obj.animation_data.nla_tracks:
            for strip in track.strips:
                if strip.type == 'CLIP' and strip.action and strip.action.library:
                    if strip.action not in processed_actions:
                        self.make_action_local(obj, strip.action, processed_actions)
                        changed = True
                        action_count += 1
        return changed, action_count

    def make_action_local(self, owner, action, processed_actions):
        # Create a copy of the action
        new_action = action.copy()
        new_action.name = f"{action.name}_Local"
        new_action.use_fake_user = False

        # Replace references to the original action
        if hasattr(owner, 'animation_data') and owner.animation_data:
            if owner.animation_data.action == action:
                owner.animation_data.action = new_action

            # Update NLA strips
            for track in owner.animation_data.nla_tracks:
                for strip in track.strips:
                    if strip.action == action:
                        strip.action = new_action

        processed_actions.add(action)

# New panel class for the sidebar
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
