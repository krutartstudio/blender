# <pep8 compliant>

bl_info = {
    "name": "Krutart empty resizer",
    "author": "iori, Krutart, Gemini",
    "version": (1, 1, 0),
    "blender": (4, 1, 0),  # Compatible with Blender 4.1 and newer
    "location": "View3D > UI > Resize Empties",
    "description": "Finds all empties in the scene and sets their display size.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy

class OBJECT_OT_resize_all_empties_small(bpy.types.Operator):
    """Operator to find all empties and set their display size to 0.01m"""
    bl_idname = "object.resize_all_empties_small"
    bl_label = "Resize to 0.01m"
    bl_description = "Sets the 'Display Size' of all Empty objects to 0.01 meters"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        """
        This method is called when the operator is executed.
        It iterates through all objects in the current scene.
        """
        empties_resized_count = 0
        scene_objects = context.scene.objects
        
        for obj in scene_objects:
            if obj.type == 'EMPTY':
                obj.empty_display_size = 0.01
                empties_resized_count += 1
        
        report_message = f"Finished: Resized {empties_resized_count} empty object(s) to 0.01m."
        self.report({'INFO'}, report_message)
        
        return {'FINISHED'}

class OBJECT_OT_resize_all_empties_large(bpy.types.Operator):
    """Operator to find all empties and set their display size to 1.0m"""
    bl_idname = "object.resize_all_empties_large"
    bl_label = "Resize to 1.0m"
    bl_description = "Sets the 'Display Size' of all Empty objects to 1.0 meters"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        """
        This method is called when the operator is executed.
        It iterates through all objects in the current scene.
        """
        empties_resized_count = 0
        scene_objects = context.scene.objects
        
        for obj in scene_objects:
            if obj.type == 'EMPTY':
                obj.empty_display_size = 1.0
                empties_resized_count += 1
        
        report_message = f"Finished: Resized {empties_resized_count} empty object(s) to 1.0m."
        self.report({'INFO'}, report_message)
        
        return {'FINISHED'}


class VIEW3D_PT_resize_empties_panel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport's UI sidebar"""
    bl_label = "Resize Empties"
    bl_idname = "VIEW3D_PT_resize_empties"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Tool'  # This sets the tab name in the sidebar

    def draw(self, context):
        """
        This method defines the layout of the panel.
        """
        layout = self.layout
        
        layout.label(text="Set All Empty Sizes:")
        
        # Button for resizing to 0.01m
        row_small = layout.row()
        row_small.scale_y = 1.5 # Make the button a bit taller
        row_small.operator(OBJECT_OT_resize_all_empties_small.bl_idname)

        # Button for resizing to 1.0m
        row_large = layout.row()
        row_large.scale_y = 1.5 # Make the button a bit taller
        row_large.operator(OBJECT_OT_resize_all_empties_large.bl_idname)


# A list of all classes that need to be registered with Blender
classes = (
    OBJECT_OT_resize_all_empties_small,
    OBJECT_OT_resize_all_empties_large,
    VIEW3D_PT_resize_empties_panel,
)

def register():
    """
    This function is called when the addon is enabled.
    It registers all of the addon's classes with Blender.
    """
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    """
    This function is called when the addon is disabled.
    It unregisters all of the addon's classes in reverse order.
    """
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

# This allows the script to be run directly in Blender's text editor
# for testing purposes, without having to install it.
if __name__ == "__main__":
    register()

