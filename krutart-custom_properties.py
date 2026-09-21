bl_info = {
"name": "Krutart Custom Properties",
"author": "Jakub-ChatGPT",
"version": (1, 7),
"blender": (4, 5, 0),
"category": "Object",
}

import bpy
import os
import sys
import json
import time
import logging
import platform
import tempfile
import traceback
import functools

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("KrutartCustomProperties")


# =================================================
# CONSTANTS
# =================================================

DEFAULT_PATH = r"S:\3212-PREPRODUCTION\LIBRARY\LIBRARY-HERO\NODEGROUP-HERO\MAT-LIBRARY-HERO\3212-mat-library-hero.blend"
RNA_TEXT_BLOCK = "CPL_RNA_UI_CACHE"


# =================================================
# TEST REPORT (Info editor + clipboard)
# =================================================
# Every step and every caught exception goes through step(). Lines are kept for
# the whole Blender session so the artist can press "Copy Test Report" and paste
# the lot into Discord.

REPORT_LINES = []
_PENDING = []


def step(msg, level='INFO'):
    REPORT_LINES.append(f"{time.strftime('%H:%M:%S')} [{level}] {msg}")
    _PENDING.append((level, msg))
    {'INFO': log.info, 'WARNING': log.warning, 'ERROR': log.error}[level](msg)


def plain(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


def describe_prop(target, key):
    try:
        desc = target.id_properties_ui(key).as_dict().get("description")
    except Exception as e:
        desc = f"<{type(e).__name__}>"
    try:
        overridable = target.is_property_overridable_library(f'["{key}"]')
    except Exception as e:
        overridable = f"<{type(e).__name__}>"
    return f"{key}={plain(target[key])!r} description={desc!r} overridable={overridable}"


def id_name(target):
    return f"{type(target).__name__} '{getattr(target, 'name', target)}'"


def reported(execute):
    """Wraps Operator.execute: header, catch-all, and pushes the steps to the Info editor."""
    @functools.wraps(execute)
    def wrapper(self, context):
        _PENDING.clear()
        version = ".".join(str(v) for v in bl_info["version"])
        step(f"===== {self.bl_idname} | CPL {version} | Blender {bpy.app.version_string} | {platform.system()} =====")
        step(f"blend file: {bpy.data.filepath or '<unsaved>'}")

        try:
            result = execute(self, context)
        except Exception as e:
            step(f"UNHANDLED {type(e).__name__}: {e}", 'ERROR')
            REPORT_LINES.append(traceback.format_exc().rstrip())
            result = {'CANCELLED'}

        step(f"----- {self.bl_idname} result: {', '.join(sorted(result))}")

        verbose = getattr(getattr(context.scene, "cpl_settings", None), "verbose", True)
        while _PENDING:
            level, msg = _PENDING.pop(0)
            if level == 'INFO' and not verbose:
                continue
            self.report({level}, f"CPL: {msg}")

        return result
    return wrapper


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

    verbose: bpy.props.BoolProperty(
        name="Verbose Report",
        description="Print every step into the Info editor (warnings and errors are always printed)",
        default=True
    )


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
        except Exception as e:
            step(f"{RNA_TEXT_BLOCK} text block is not valid JSON ({type(e).__name__}: {e}), starting a new cache", 'WARNING')
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
    except Exception as e:
        step(f"{RNA_TEXT_BLOCK} text block is not valid JSON ({type(e).__name__}: {e})", 'WARNING')
        return None


# =================================================
# COPY HELPERS
# =================================================

def set_property_overridable(target, key, overridable=True):
    """
    Ensures that a custom property on a target data-block (Object, Scene, ViewLayer)
    is configured as Library Overridable (is_overridable_library=True) and tagged with description="cp".
    """
    if hasattr(target, "property_overridable_library_set"):
        try:
            target.property_overridable_library_set(f'["{key}"]', overridable)
        except Exception as e:
            step(f"property_overridable_library_set('{key}') on {id_name(target)} raised {type(e).__name__}: {e}", 'WARNING')

    if hasattr(target, "id_properties_ui"):
        try:
            target.id_properties_ui(key).update(description="cp", is_overridable_library=overridable)
        except Exception as e:
            step(f"id_properties_ui('{key}').update(description, is_overridable_library) on {id_name(target)} raised {type(e).__name__}: {e}", 'WARNING')

    # Remove any stray legacy _RNA_UI dict property on target to keep Custom Properties UI panel clean
    if "_RNA_UI" in target:
        try:
            del target["_RNA_UI"]
        except Exception as e:
            step(f"deleting _RNA_UI on {id_name(target)} raised {type(e).__name__}: {e}", 'WARNING')


def copy_rna_ui(source_name, target):
    step(f"Loading RNA UI for '{source_name}'")
    ui_data = load_rna_ui(source_name)

    if not ui_data:
        step(f"No RNA UI data found for '{source_name}'")
        return

    copied_keys = []
    for k, v in ui_data.items():
        if k in target:
            if hasattr(target, "id_properties_ui") and isinstance(v, dict):
                try:
                    kwargs = {k_ui: v_ui for k_ui, v_ui in v.items() if k_ui != "default"}
                    kwargs["description"] = "cp"
                    kwargs["is_overridable_library"] = True
                    target.id_properties_ui(k).update(**kwargs)
                except Exception as e:
                    step(f"id_properties_ui('{k}').update(cached RNA UI) on {id_name(target)} raised {type(e).__name__}: {e}", 'WARNING')

            set_property_overridable(target, k, overridable=True)
            copied_keys.append(k)

    # Clean up legacy _RNA_UI dict on target
    if "_RNA_UI" in target:
        try:
            del target["_RNA_UI"]
        except Exception as e:
            step(f"deleting _RNA_UI on {id_name(target)} raised {type(e).__name__}: {e}", 'WARNING')

    step(f"Copied RNA UI properties: {copied_keys}")


def is_custom_prop(source, key):
    # Check if the property has a description equal to "cp"
    try:
        if hasattr(source, "id_properties_ui"):
            ui_data = source.id_properties_ui(key)
            if ui_data.as_dict().get("description") == "cp":
                return True
    except Exception as e:
        step(f"reading id_properties_ui('{key}') on {id_name(source)} raised {type(e).__name__}: {e}", 'WARNING')

    try:
        if "_RNA_UI" in source:
            rna_ui = source["_RNA_UI"]
            if key in rna_ui and "description" in rna_ui[key]:
                if rna_ui[key]["description"] == "cp":
                    return True
    except Exception as e:
        step(f"reading _RNA_UI['{key}'] on {id_name(source)} raised {type(e).__name__}: {e}", 'WARNING')

    return False


def copy_props(source, target, source_name):
    step(f"Copying custom properties from '{source_name}' to object '{getattr(target, 'name', str(target))}'")
    step(f"source '{source.name}' keys: {sorted(source.keys())}")
    copied_keys = []
    skipped_keys = []
    for key, value in source.items():

        if key.startswith("_"):
            continue

        if key in {"cycles", "rna_type"}:
            continue

        if not is_custom_prop(source, key):
            skipped_keys.append(key)
            continue

        try:
            target[key] = value
            set_property_overridable(target, key, overridable=True)
            copied_keys.append(key)
        except Exception as e:
            step(f"copying '{key}' to {id_name(target)} raised {type(e).__name__}: {e}", 'ERROR')

    if copied_keys:
        step(f"Successfully copied: {copied_keys}")
    if skipped_keys:
        step(f"Skipped non-cp properties: {skipped_keys}")

    copy_rna_ui(source_name, target)

    for key in copied_keys:
        if key in target:
            step(f"  result on '{getattr(target, 'name', target)}': {describe_prop(target, key)}")
        else:
            step(f"  result on '{getattr(target, 'name', target)}': '{key}' is MISSING after copy", 'ERROR')


# =================================================
# LOAD
# =================================================

class CPL_OT_LoadItems(bpy.types.Operator):
    bl_idname = "cpl.load_items"
    bl_label = "Load Custom Properties"

    @reported
    def execute(self, context):
        s = context.scene.cpl_settings
        step(f"cpl.load_items: Loading properties from file path: '{s.file_path}'")

        s.items.clear()
        s.view_items.clear()
        s.scene_items.clear()

        if not os.path.exists(s.file_path):
            step(f"cpl.load_items: File not found at '{s.file_path}' (absolute: '{bpy.path.abspath(s.file_path)}')", 'ERROR')
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

        step(f"cpl.load_items: Found {len(obj_list)} Object props, {len(view_list)} View Layer props, {len(scene_list)} Scene props.")

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
    bl_options = {'REGISTER', 'UNDO'}

    item_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.selected_objects is not None and len(context.selected_objects) > 0

    @reported
    def execute(self, context):
        if not context.selected_objects:
            step("cpl.apply_properties: Attempted execution with no selected objects.", 'WARNING')
            self.report({'ERROR'}, "No objects selected to apply properties to.")
            return {'CANCELLED'}

        f = context.scene.cpl_settings.file_path
        name = f"OBJECT_PROP-{self.item_name}"
        targets = list(context.selected_objects)
        step(f"cpl.apply_properties: Applying '{self.item_name}' to {len(targets)} selected object(s) from '{f}'")
        step(f"library absolute path: '{bpy.path.abspath(f)}' exists={os.path.exists(f)}")
        step(f"selected: {[o.name for o in targets]}")

        with bpy.data.libraries.load(f, link=False) as (d_from, d_to):
            d_to.objects = [name]

        src = d_to.objects[0]
        step(f"appended template object: {src.name if src else src!r}")

        for obj in targets:
            copy_props(src, obj, name)

        bpy.data.objects.remove(src, do_unlink=True)
        step("cpl.apply_properties: Finished applying object properties and cleaned up temporary library object.")
        self.report({'INFO'}, f"Applied '{self.item_name}' to {len(targets)} object(s)")
        return {'FINISHED'}


# =================================================
# VIEW LAYER APPLY
# =================================================

class CPL_OT_ApplyViewLayerProperties(bpy.types.Operator):
    bl_idname = "cpl.apply_view_layer_properties"
    bl_label = "Apply View Layer Properties"

    item_name: bpy.props.StringProperty()

    @reported
    def execute(self, context):
        f = context.scene.cpl_settings.file_path
        name = f"VIEW_LAYER_PROP-{self.item_name}"
        step(f"cpl.apply_view_layer_properties: Applying '{self.item_name}' to View Layer from '{f}'")

        with bpy.data.libraries.load(f, link=False) as (d_from, d_to):
            d_to.objects = [name]

        src = d_to.objects[0]

        copied_keys = []
        skipped_keys = []
        for k, v in src.items():
            if k.startswith("_") or k in {"cycles", "rna_type"}:
                continue

            if not is_custom_prop(src, k):
                skipped_keys.append(k)
                continue

            try:
                context.view_layer[k] = v
                set_property_overridable(context.view_layer, k, overridable=True)
                copied_keys.append(k)
            except Exception as e:
                step(f"copying '{k}' to view layer raised {type(e).__name__}: {e}", 'ERROR')

        if copied_keys:
            step(f"Successfully copied view layer properties: {copied_keys}")
        if skipped_keys:
            step(f"Skipped non-cp view layer properties: {skipped_keys}")

        copy_rna_ui(name, context.view_layer)

        bpy.data.objects.remove(src, do_unlink=True)
        step("cpl.apply_view_layer_properties: Finished applying view layer properties and cleaned up temporary library object.")
        return {'FINISHED'}


# =================================================
# SCENE APPLY
# =================================================

class CPL_OT_ApplySceneProperties(bpy.types.Operator):
    bl_idname = "cpl.apply_scene_properties"
    bl_label = "Apply Scene Properties"

    item_name: bpy.props.StringProperty()

    @reported
    def execute(self, context):
        f = context.scene.cpl_settings.file_path
        name = f"SCENE_PROP-{self.item_name}"
        step(f"cpl.apply_scene_properties: Applying '{self.item_name}' to Scene from '{f}'")

        with bpy.data.libraries.load(f, link=False) as (d_from, d_to):
            d_to.objects = [name]

        src = d_to.objects[0]

        copied_keys = []
        skipped_keys = []
        for k, v in src.items():
            if k.startswith("_") or k in {"cycles", "rna_type"}:
                continue

            if not is_custom_prop(src, k):
                skipped_keys.append(k)
                continue

            try:
                context.scene[k] = v
                set_property_overridable(context.scene, k, overridable=True)
                copied_keys.append(k)
            except Exception as e:
                step(f"copying '{k}' to scene raised {type(e).__name__}: {e}", 'ERROR')

        if copied_keys:
            step(f"Successfully copied scene properties: {copied_keys}")
        if skipped_keys:
            step(f"Skipped non-cp scene properties: {skipped_keys}")

        copy_rna_ui(name, context.scene)

        bpy.data.objects.remove(src, do_unlink=True)
        step("cpl.apply_scene_properties: Finished applying scene properties and cleaned up temporary library object.")
        return {'FINISHED'}


# =================================================
# DELETE OBJECT PROPS
# =================================================

class CPL_OT_DeleteProperties(bpy.types.Operator):
    bl_idname = "cpl.delete_properties"
    bl_label = "Delete Object Properties"

    @reported
    def execute(self, context):
        step(f"cpl.delete_properties: Deleting custom properties from {len(context.selected_objects)} selected objects")
        for obj in context.selected_objects:
            deleted_keys = []
            for k in list(obj.keys()):
                if not k.startswith("_"):
                    del obj[k]
                    deleted_keys.append(k)
            if "_RNA_UI" in obj:
                try:
                    del obj["_RNA_UI"]
                except Exception as e:
                    step(f"deleting _RNA_UI on {id_name(obj)} raised {type(e).__name__}: {e}", 'WARNING')
            step(f"  Deleted properties from '{obj.name}': {deleted_keys}, remaining keys: {sorted(obj.keys())}")
        return {'FINISHED'}


# =================================================
# DELETE VIEW LAYER PROPS
# =================================================

class CPL_OT_DeleteViewLayerProperties(bpy.types.Operator):
    bl_idname = "cpl.delete_view_layer_properties"
    bl_label = "Delete View Layer Properties"

    @reported
    def execute(self, context):
        vl = context.view_layer
        step(f"cpl.delete_view_layer_properties: Deleting properties from view layer '{vl.name}'")
        deleted_keys = []
        for k in list(vl.keys()):
            if not k.startswith("_"):
                del vl[k]
                deleted_keys.append(k)
        if "_RNA_UI" in vl:
            try:
                del vl["_RNA_UI"]
            except Exception as e:
                step(f"deleting _RNA_UI on {id_name(vl)} raised {type(e).__name__}: {e}", 'WARNING')
        step(f"  Deleted view layer properties: {deleted_keys}")
        return {'FINISHED'}


# =================================================
# DELETE SCENE PROPS (FIXED)
# =================================================

class CPL_OT_DeleteSceneProperties(bpy.types.Operator):
    bl_idname = "cpl.delete_scene_properties"
    bl_label = "Delete Scene Properties"

    @reported
    def execute(self, context):
        sc = context.scene
        step(f"cpl.delete_scene_properties: Deleting properties from scene '{sc.name}'")
        deleted_keys = []

        for k in list(sc.keys()):
            # ONLY real custom properties
            if k.startswith("_"):
                continue

            if k in {"cycles", "rna_type"}:
                continue

            del sc[k]
            deleted_keys.append(k)

        if "_RNA_UI" in sc:
            try:
                del sc["_RNA_UI"]
            except Exception as e:
                step(f"deleting _RNA_UI on {id_name(sc)} raised {type(e).__name__}: {e}", 'WARNING')

        step(f"  Deleted scene properties: {deleted_keys}")
        return {'FINISHED'}


# =================================================
# TEST REPORT OPERATORS
# =================================================

class CPL_OT_CopyReport(bpy.types.Operator):
    bl_idname = "cpl.copy_report"
    bl_label = "Copy Test Report"
    bl_description = "Copy every Custom Properties step from this session to the clipboard, ready to paste into Discord"

    def execute(self, context):
        s = context.scene.cpl_settings
        version = ".".join(str(v) for v in bl_info["version"])
        header = [
            "Krutart Custom Properties test report",
            f"add-on {version} | Blender {bpy.app.version_string} | {platform.platform()} | Python {sys.version.split()[0]}",
            f"blend file: {bpy.data.filepath or '<unsaved>'}",
            f"library: {s.file_path} (exists={os.path.exists(s.file_path)})",
            f"selected now: {[o.name for o in context.selected_objects]}",
            "",
        ]
        text = "\n".join(header + (REPORT_LINES or ["<no steps recorded yet - press the add-on buttons first>"]))
        context.window_manager.clipboard = text

        path = os.path.join(tempfile.gettempdir(), "krutart_cpl_test_report.txt")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            saved = f", also saved to {path}"
        except Exception as e:
            saved = f", could not save a copy ({type(e).__name__}: {e})"

        self.report({'INFO'}, f"CPL: copied {len(REPORT_LINES)} report lines to the clipboard{saved}. Paste it into Discord.")
        return {'FINISHED'}


class CPL_OT_ClearReport(bpy.types.Operator):
    bl_idname = "cpl.clear_report"
    bl_label = "Clear Test Report"
    bl_description = "Forget the recorded steps, e.g. before starting a new test"

    def execute(self, context):
        REPORT_LINES.clear()
        self.report({'INFO'}, "CPL: test report cleared")
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

        layout.separator()
        box = layout.box()
        box.label(text=f"Test Report ({len(REPORT_LINES)} lines)", icon='INFO')
        box.prop(s, "verbose")
        row = box.row(align=True)
        row.operator("cpl.copy_report", icon='COPYDOWN')
        row.operator("cpl.clear_report", text="", icon='X')


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

    CPL_OT_CopyReport,
    CPL_OT_ClearReport,

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
