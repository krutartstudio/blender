bl_info = {
    "name": "Asset Browser Quick Filters (-P- / -M-)",
    "author": "ChatGPT",
    "version": (1, 0, 1),
    "blender": (4, 5, 0),
    "location": "Asset Browser header & N-panel",
    "description": "Adds 'proxy' and 'master' buttons that filter assets by name containing -P- / -M-",
    "category": "User Interface",
}

import bpy


def _ensure_assets_mode(space):
    # Make sure the File Browser is in Asset mode
    try:
        if getattr(space, "browse_mode", None) != 'ASSETS':
            space.browse_mode = 'ASSETS'
    except Exception:
        pass


def _set_search(space, text: str):
    params = getattr(space, "params", None)
    if params and hasattr(params, "filter_search"):
        try:
            params.use_filter = True
        except Exception:
            pass
        try:
            params.filter_search = text
        except Exception:
            pass

    # Best-effort: switch to "All" library so the filter spans everything
    ap = getattr(space, "asset_params", None)
    if ap and hasattr(ap, "asset_library_ref"):
        for candidate in ("ALL", "ALL_LIBRARIES", "ALL_LIBRARY"):
            try:
                ap.asset_library_ref = candidate
                break
            except Exception:
                continue


def _apply_to_all_asset_browsers(context, text: str) -> int:
    count = 0
    wm = context.window_manager
    for win in wm.windows:
        scr = win.screen
        if not scr:
            continue
        for area in scr.areas:
            if area.type != 'FILE_BROWSER':
                continue
            space = area.spaces.active
            if not space:
                continue
            _ensure_assets_mode(space)
            if getattr(space, "browse_mode", None) != 'ASSETS':
                continue
            _set_search(space, text)
            try:
                area.tag_redraw()
            except Exception:
                pass
            count += 1
    return count


class ABQF_OT_set(bpy.types.Operator):
    """Set Asset Browser search filter text"""
    bl_idname = "abqf.set"
    bl_label = "Set Asset Filter"
    bl_options = {'INTERNAL'}

    substring: bpy.props.StringProperty(default="")

    def execute(self, context):
        n = _apply_to_all_asset_browsers(context, self.substring)
        self.report({'INFO'}, f"Applied filter '{self.substring}' to {n} Asset Browser view(s).")
        return {'FINISHED'}


def _draw_header(self, context):
    space = context.space_data
    if not space or space.type != 'FILE_BROWSER':
        return
    layout = self.layout
    layout.separator()
    row = layout.row(align=True)
    row.label(text="Quick:")
    row.operator("abqf.set", text="proxy").substring = "-P-"
    row.operator("abqf.set", text="master").substring = "-M-"
    row.operator("abqf.set", text="", icon='X').substring = ""


class FILEBROWSER_PT_abqf(bpy.types.Panel):
    bl_label = "Quick Filters"
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'UI'
    bl_category = "Filter"

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return sp and sp.type == 'FILE_BROWSER'

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("abqf.set", text="proxy").substring = "-P-"
        row.operator("abqf.set", text="master").substring = "-M-"
        col.operator("abqf.set", text="Clear", icon='X').substring = ""


classes = (
    ABQF_OT_set,
    FILEBROWSER_PT_abqf,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.FILEBROWSER_HT_header.append(_draw_header)


def unregister():
    try:
        bpy.types.FILEBROWSER_HT_header.remove(_draw_header)
    except Exception:
        pass
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
