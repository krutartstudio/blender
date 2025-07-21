bl_info = {
    "name": "Make Linked Rigs Local",
    "author": "iori",
    "version": (1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Localize Rigs",
    "description": "Converts linked armatures and rig components to local copies",
    "category": "Rigging",
}

import bpy

class OBJECT_OT_make_rigs_local(bpy.types.Operator):
    bl_idname = "object.make_rigs_local"
    bl_label = "Make Rigs Local"
    bl_description = "Convert linked armatures to local copies for selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def execute(self, context):
        processed_armatures = set()
        objects_processed = 0
        rigs_localized = 0

        # Collect all relevant objects (armatures and their users)
        all_objects = set(context.selected_objects)
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                for mod in obj.modifiers:
                    if mod.type == 'ARMATURE' and mod.object:
                        all_objects.add(mod.object)

        for obj in all_objects:
            processed = False

            # Process armature objects
            if obj.type == 'ARMATURE':
                if self.process_armature(obj, processed_armatures):
                    processed = True
                    rigs_localized += 1

            # Process meshes with armature modifiers
            if obj.type == 'MESH':
                for mod in obj.modifiers:
                    if mod.type == 'ARMATURE' and mod.object:
                        if self.process_armature_reference(obj, mod, processed_armatures):
                            processed = True
                            rigs_localized += 1

            if processed:
                objects_processed += 1

        self.report({'INFO'}, f"Localized {rigs_localized} rig components on {objects_processed} objects")
        return {'FINISHED'}

    def process_armature(self, armature_obj, processed_armatures):
        """Handle armature objects directly"""
        if armature_obj.library and armature_obj not in processed_armatures:
            # Create local copy
            new_armature = armature_obj.copy()
            new_armature.data = armature_obj.data.copy()
            new_armature.name = f"{armature_obj.name}_Local"
            new_armature.data.name = f"{armature_obj.data.name}_Local"

            # Link to scene
            bpy.context.scene.collection.objects.link(new_armature)

            # Replace references
            self.replace_armature_references(armature_obj, new_armature)

            processed_armatures.add(armature_obj)
            return True
        return False

    def process_armature_reference(self, owner, modifier, processed_armatures):
        """Handle armature references in modifiers"""
        armature_obj = modifier.object
        if armature_obj.library and armature_obj not in processed_armatures:
            # Create local copy
            new_armature = armature_obj.copy()
            new_armature.data = armature_obj.data.copy()
            new_armature.name = f"{armature_obj.name}_Local"
            new_armature.data.name = f"{armature_obj.data.name}_Local"

            # Link to scene
            bpy.context.scene.collection.objects.link(new_armature)

            # Update modifier
            modifier.object = new_armature

            processed_armatures.add(armature_obj)
            return True
        return False

    def replace_armature_references(self, old_armature, new_armature):
        """Update all references to an armature"""
        # Update constraints
        for obj in bpy.data.objects:
            for constraint in obj.constraints:
                if constraint.target == old_armature:
                    constraint.target = new_armature

        # Update drivers
        for obj in bpy.data.objects:
            if obj.animation_data:
                for driver in obj.animation_data.drivers:
                    for var in driver.driver.variables:
                        for target in var.targets:
                            if target.id == old_armature:
                                target.id = new_armature

class VIEW3D_PT_LocalizeRigs(bpy.types.Panel):
    bl_label = "Localize Rigs"
    bl_idname = "VIEW3D_PT_localize_rigs"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Localize Rigs"
    bl_context = "objectmode"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("object.make_rigs_local", icon='ARMATURE_DATA')
        col.label(text="Converts linked armatures to local copies", icon='INFO')

def menu_func(self, context):
    self.layout.operator(OBJECT_OT_make_rigs_local.bl_idname)

def register():
    bpy.utils.register_class(OBJECT_OT_make_rigs_local)
    bpy.utils.register_class(VIEW3D_PT_LocalizeRigs)
    bpy.types.VIEW3D_MT_object_context_menu.append(menu_func)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_make_rigs_local)
    bpy.utils.unregister_class(VIEW3D_PT_LocalizeRigs)
    bpy.types.VIEW3D_MT_object_context_menu.remove(menu_func)

if __name__ == "__main__":
    register()
