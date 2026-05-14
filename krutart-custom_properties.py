bl_info = {
"name": "Krutart Custom Properties",
"author": "Jakub-ChatGPT",
"version": (1, 2, 1),
"blender": (4, 5, 0),
"category": "Object",
}

import bpy
import os
import json


# =================================================
# CONSTANTS
# =================================================

DEFAULT_PATH = r"S:\3212-PREPRODUCTION\LIBRARY\LIBRARY-HERO\NODEGROUP-HERO\MAT-LIBRARY-HERO\3212-mat-library-hero.blend"
RNA_TEXT_BLOCK = "CPL_RNA_UI_CACHE"


# =================================================
# PROPERTIES
# =================================================

class CPL_Item(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()


class CPL_ViewLayerItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()


class CPL_SceneItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()


class CPL_Settings(bpy.types.PropertyGroup):
    file_path: bpy.props.StringProperty(
        name="Library File",
        subtype='FILE_PATH',
        default=DEFAULT_PATH
    )

    items: bpy.props.CollectionProperty(type=CPL_Item)
    view_items: bpy.props.CollectionProperty(type=CPL_ViewLayerItem)
    scene_items: bpy.props.CollectionProperty(type=CPL_SceneItem)


# =================================================
# RNA_UI STORAGE (TEXT BLOCK CACHE)
# =================================================

def store_rna_ui(name, data):
    txt = bpy.data.texts.get(RNA_TEXT_BLOCK)
    if not txt:
        txt = bpy.data.texts.new(RNA_TEXT_BLOCK)

    cache = {}

    if txt.as_string():
        try:
            cache = json.loads(txt.as_string())
        except:
            cache = {}

    cache[name] = data

    txt.clear()
    txt.write(json.dumps(cache))


def load_rna_ui(name):
    txt = bpy.data.texts.get(RNA_TEXT_BLOCK)
    if not txt:
        return None

    try:
        cache = json.loads(txt.as_string())
        return cache.get(name)
    except:
        return None


# =================================================
# COPY HELPERS
# =================================================

def is_cpl_property(obj, key):
    if key.startswith("_") or key in {"cycles", "rna_type"}:
        return False
        
    try:
        rna_ui = obj.get("_RNA_UI", {})
        if key in rna_ui:
            meta = rna_ui[key]
            meta_dict = meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)
            # Check if 'cp' is anywhere in the description (case-insensitive)
            desc = meta_dict.get("description", "").lower()
            return "cp" in desc
    except:
        pass
    return False


def copy_rna_ui(source_obj, target):
    source_rna = source_obj.get("_RNA_UI", {})
    if not source_rna:
        return

    if "_RNA_UI" not in target:
        target["_RNA_UI"] = {}

    for k in source_obj.keys():
        if is_cpl_property(source_obj, k):
            if k in source_rna:
                try:
                    meta = source_rna[k]
                    meta_dict = meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)
                    target["_RNA_UI"][k] = meta_dict
                except:
                    pass


def copy_props(source, target):
    for key, value in source.items():
        if not is_cpl_property(source, key):
            continue

        try:
            target[key] = value
        except:
            pass

    copy_rna_ui(source, target)


# =================================================
# LOAD
# =================================================

class CPL_OT_LoadItems(bpy.types.Operator):
    bl_idname = "cpl.load_items"
    bl_label = "Load Custom Properties"

    def execute(self, context):
        s = context.scene.cpl_settings

        s.items.clear()
        s.view_items.clear()
        s.scene_items.clear()

        if not os.path.exists(s.file_path):
            self.report({'ERROR'}, "File not found")
            return {'CANCELLED'}

        obj_list, view_list, scene_list = [], [], []

        with bpy.data.libraries.load(s.file_path, link=False) as (d_from, d_to):

            for n in d_from.objects:

                if n.startswith("OBJECT_PROP-"):
                    obj_list.append(n.split("-", 1)[1])

                elif n.startswith("VIEW_LAYER_PROP-"):
                    view_list.append(n.split("-", 1)[1])

                elif n.startswith("SCENE_PROP-"):
                    scene_list.append(n.split("-", 1)[1])

        for n in obj_list:
            s.items.add().name = n

        for n in view_list:
            s.view_items.add().name = n

        for n in scene_list:
            s.scene_items.add().name = n

        return {'FINISHED'}


# =================================================
# OBJECT APPLY
# =================================================

class CPL_OT_ApplyProperties(bpy.types.Operator):
    bl_idname = "cpl.apply_properties"
    bl_label = "Apply Object Properties"

    item_name: bpy.props.StringProperty()

    def execute(self, context):
        f = context.scene.cpl_settings.file_path
        name = f"OBJECT_PROP-{self.item_name}"

        with bpy.data.libraries.load(f, link=False) as (d_from, d_to):
            d_to.objects = [name]

        src = d_to.objects[0]

        copy_props(src, context.selected_objects[0])

        bpy.data.objects.remove(src, do_unlink=True)
        return {'FINISHED'}


# =================================================
# VIEW LAYER APPLY
# =================================================

class CPL_OT_ApplyViewLayerProperties(bpy.types.Operator):
    bl_idname = "cpl.apply_view_layer_properties"
    bl_label = "Apply View Layer Properties"

    item_name: bpy.props.StringProperty()

    def execute(self, context):
        f = context.scene.cpl_settings.file_path
        name = f"VIEW_LAYER_PROP-{self.item_name}"

        with bpy.data.libraries.load(f, link=False) as (d_from, d_to):
            d_to.objects = [name]

        src = d_to.objects[0]

        for k, v in src.items():
            if not is_cpl_property(src, k):
                continue

            try:
                context.view_layer[k] = v
            except:
                pass

        copy_rna_ui(src, context.view_layer)

        bpy.data.objects.remove(src, do_unlink=True)
        return {'FINISHED'}


# =================================================
# SCENE APPLY
# =================================================

class CPL_OT_ApplySceneProperties(bpy.types.Operator):
    bl_idname = "cpl.apply_scene_properties"
    bl_label = "Apply Scene Properties"

    item_name: bpy.props.StringProperty()

    def execute(self, context):
        f = context.scene.cpl_settings.file_path
        name = f"SCENE_PROP-{self.item_name}"

        with bpy.data.libraries.load(f, link=False) as (d_from, d_to):
            d_to.objects = [name]

        src = d_to.objects[0]

        for k, v in src.items():
            if not is_cpl_property(src, k):
                continue

            try:
                context.scene[k] = v
            except:
                pass

        copy_rna_ui(src, context.scene)

        bpy.data.objects.remove(src, do_unlink=True)
        return {'FINISHED'}


# =================================================
# DELETE OBJECT PROPS
# =================================================

class CPL_OT_DeleteProperties(bpy.types.Operator):
    bl_idname = "cpl.delete_properties"
    bl_label = "Delete Object Properties"

    def execute(self, context):
        for obj in context.selected_objects:
            for k in list(obj.keys()):
                if is_cpl_property(obj, k):
                    del obj[k]
                    if "_RNA_UI" in obj and k in obj["_RNA_UI"]:
                        del obj["_RNA_UI"][k]
        return {'FINISHED'}


# =================================================
# DELETE VIEW LAYER PROPS
# =================================================

class CPL_OT_DeleteViewLayerProperties(bpy.types.Operator):
    bl_idname = "cpl.delete_view_layer_properties"
    bl_label = "Delete View Layer Properties"

    def execute(self, context):
        vl = context.view_layer
        for k in list(vl.keys()):
            if is_cpl_property(vl, k):
                del vl[k]
                if "_RNA_UI" in vl and k in vl["_RNA_UI"]:
                    del vl["_RNA_UI"][k]
        return {'FINISHED'}


# =================================================
# DELETE SCENE PROPS (FIXED)
# =================================================

class CPL_OT_DeleteSceneProperties(bpy.types.Operator):
    bl_idname = "cpl.delete_scene_properties"
    bl_label = "Delete Scene Properties"

    def execute(self, context):
        sc = context.scene

        for k in list(sc.keys()):
            if is_cpl_property(sc, k):
                del sc[k]
                if "_RNA_UI" in sc and k in sc["_RNA_UI"]:
                    del sc["_RNA_UI"][k]

        return {'FINISHED'}


# =================================================
# PROPERTY TAGGER
# =================================================

class CPL_PropTaggerItem(bpy.types.PropertyGroup):
    prop_name: bpy.props.StringProperty()
    sync: bpy.props.BoolProperty(default=False)

class CPL_OT_TagPropertiesManager(bpy.types.Operator):
    bl_idname = "cpl.tag_properties_manager"
    bl_label = "sort and clean up"
    bl_description = "Select which custom properties should be synced (Tags them with 'cp')"
    bl_options = {'REGISTER', 'UNDO'}

    target_type: bpy.props.EnumProperty(
        name="Target",
        items=[
            ('OBJECT', "Object", "Tag properties on the active object"),
            ('VIEW_LAYER', "View Layer", "Tag properties on the active view layer"),
            ('SCENE', "Scene", "Tag properties on the current scene"),
        ],
        default='OBJECT'
    )

    do_cleanup: bpy.props.BoolProperty(
        name="Cleanup Untagged",
        description="Delete all custom properties on this target that are not tagged with 'cp' when clicking OK",
        default=True
    )

    def _get_target_id(self, context):
        if self.target_type == 'OBJECT':
            return context.active_object
        elif self.target_type == 'VIEW_LAYER':
            return context.view_layer
        elif self.target_type == 'SCENE':
            return context.scene
        return None

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        
        layout.prop(self, "target_type", expand=True)
        layout.separator()

        target = self._get_target_id(context)
        if not target:
            layout.label(text="No valid target selected.", icon='ERROR')
            return

        # Dynamically refresh list in UI (Blender 4.0+ style)
        # Note: In a real production script, we might refresh this in an 'update' callback
        # but for this utility, we'll just show the properties currently in the ID block
        
        layout.label(text=f"Properties on {target.name if hasattr(target, 'name') else 'View Layer'}:")
        box = layout.box()
        
        found_any = False
        rna_ui = target.get("_RNA_UI", {})
        
        for k in target.keys():
            if k.startswith("_") or k in {"cycles", "rna_type"}:
                continue
            
            found_any = True
            # Check if it's currently tagged in the actual data
            is_tagged = False
            try:
                meta = rna_ui.get(k, {})
                meta_dict = meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)
                is_tagged = (meta_dict.get("description", "") == "cp")
            except:
                pass
            
            row = box.row()
            icon = 'CHECKBOX_HLT' if is_tagged else 'CHECKBOX_DEHLT'
            op = row.operator("cpl.toggle_tag", text=k, icon=icon)
            op.prop_name = k
            op.target_type = self.target_type

        if not found_any:
            box.label(text="No custom properties found.")

        layout.separator()
        layout.prop(self, "do_cleanup", toggle=True, icon='TRASH')

    def execute(self, context):
        if not self.do_cleanup:
            return {'FINISHED'}

        target = self._get_target_id(context)
        if not target:
            return {'FINISHED'}

        # Cleanup untagged properties
        count = 0
        for k in list(target.keys()):
            if k.startswith("_") or k in {"cycles", "rna_type"}:
                continue
                
            if not is_cpl_property(target, k):
                del target[k]
                if "_RNA_UI" in target and k in target["_RNA_UI"]:
                    del target["_RNA_UI"][k]
                count += 1
        
        if count > 0:
            self.report({'INFO'}, f"Cleaned up {count} untagged properties.")
            
        return {'FINISHED'}

class CPL_OT_ToggleTag(bpy.types.Operator):
    bl_idname = "cpl.toggle_tag"
    bl_label = "Toggle Tag"
    bl_options = {'INTERNAL'}
    
    prop_name: bpy.props.StringProperty()
    target_type: bpy.props.StringProperty()

    def execute(self, context):
        if self.target_type == 'OBJECT': target = context.active_object
        elif self.target_type == 'VIEW_LAYER': target = context.view_layer
        else: target = context.scene

        if not target: return {'CANCELLED'}

        if "_RNA_UI" not in target:
            target["_RNA_UI"] = {}
        
        k = self.prop_name
        if k not in target["_RNA_UI"]:
            target["_RNA_UI"][k] = {}
            
        try:
            meta = target["_RNA_UI"][k]
            meta_dict = meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)
            
            # Toggle the "cp" description
            if meta_dict.get("description") == "cp":
                meta_dict["description"] = ""
            else:
                meta_dict["description"] = "cp"
                    
            target["_RNA_UI"][k] = meta_dict
        except Exception as e:
            self.report({'ERROR'}, f"Failed to toggle tag: {e}")
            
        return {'FINISHED'}


# =================================================
# UI PANEL
# =================================================

class CPL_PT_Panel(bpy.types.Panel):
    bl_label = "Custom Properties"
    bl_idname = "CPL_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Tool'

    def draw(self, context):
        s = context.scene.cpl_settings
        layout = self.layout

        layout.prop(s, "file_path")
        layout.operator("cpl.load_items")
        layout.operator("cpl.tag_properties_manager", icon='MODIFIER_ON')

        layout.separator()
        layout.label(text="Object Properties")

        for i in s.items:
            op = layout.operator("cpl.apply_properties", text=i.name)
            op.item_name = i.name

        layout.operator("cpl.delete_properties", icon='TRASH')

        layout.separator()
        layout.label(text="View Layer Properties")

        for i in s.view_items:
            op = layout.operator("cpl.apply_view_layer_properties", text=i.name)
            op.item_name = i.name

        layout.operator("cpl.delete_view_layer_properties", icon='TRASH')

        layout.separator()
        layout.label(text="Scene Properties")

        for i in s.scene_items:
            op = layout.operator("cpl.apply_scene_properties", text=i.name)
            op.item_name = i.name

        layout.operator("cpl.delete_scene_properties", icon='TRASH')


# =================================================
# REGISTER
# =================================================

classes = (
    CPL_Item,
    CPL_ViewLayerItem,
    CPL_SceneItem,
    CPL_Settings,

    CPL_OT_LoadItems,

    CPL_OT_ApplyProperties,
    CPL_OT_ApplyViewLayerProperties,
    CPL_OT_ApplySceneProperties,

    CPL_OT_DeleteProperties,
    CPL_OT_DeleteViewLayerProperties,
    CPL_OT_DeleteSceneProperties,

    CPL_OT_TagPropertiesManager,
    CPL_OT_ToggleTag,

    CPL_PT_Panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)

    bpy.types.Scene.cpl_settings = bpy.props.PointerProperty(type=CPL_Settings)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

    del bpy.types.Scene.cpl_settings


if __name__ == "__main__":
    register()
