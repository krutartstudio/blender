bl_info = {
    "name": "Krutart Interface",
    "author": "iori, Krutart",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Tool > Lighting",
    "description": "Brings the lighting UI into the 3D Viewport sidebar: Light Links, Light, Visibility and Shading of the active object.",
    "warning": "",
    "doc_url": "",
    "category": "Interface",
}

# Krutart Interface - the sidebar home for small, self-contained UI tools that would otherwise
# each be an add-on of their own. Pipeline add-ons keep their own panels.
#
# 1.0.0 contains the Lighting panel only (Tool > Lighting):
#   - Light Links: Krutart Light Link's LL/SL panel (drawn by Light Link itself, when installed)
#   - Light: Blender's own Light / Settings / Nodes panels of the active light
#   - Visibility and Shading: Blender's own object panels, incl. Light/Shadow Linking
# The native panels are mirrored, not rebuilt: each mirror runs Blender's own draw code, so it draws
# exactly the same properties. Editing in the sidebar or in Properties is the same edit.
#
# Candidates discussed for later (2026-09-21), NOT part of this add-on yet:
#   - krutart-empty_resizer (live, Tool tab): empty display size 0.01 m / 1 m.
#     Retiring it needs a final empty 1.2.0 release - the configurator never removes add-ons.
#   - OPTIONAL/krutart-viewport_display_tool (Tool tab): Display As Solid/Textured, all/selected
#   - OPTIONAL/krutart-lgt-solo ("Separate Lights" tab): solo light, hide world - lighting-related,
#     would sit in Tool > Lighting
#   - OPTIONAL/krutart-replacer (Tool tab): replace node groups, textures, materials
#   - krutart-browser_filter (Asset Browser, not the 3D sidebar): proxy/master filter
#   Probably superseded already: OPTIONAL/krutart-camera_toggle (layout-suite has the shot camera
#   switcher), OPTIONAL/dope_sheet_custom_controls (animation_controls sets keying sets).
# Merged tools must use new krutart_interface.* ids so they cannot clash with the old add-on.

import bpy
import logging
import types
from bpy.props import BoolProperty
from bpy.types import AddonPreferences, Panel

log = logging.getLogger("KrutartInterface")

CATEGORY = "Tool"
LIGHTING_PANEL = "KRUTART_PT_iface_lighting"
LIGHT_LINK_PANEL = "KRUTART_PT_light_link_panel"  # krutart-light_link, optional

# Sections of the Lighting panel: (preference, native top-level panels in draw order).
# Blender's own polls decide what shows (engine, object type), so Cycles and EEVEE panels can both
# be listed. Their sub-panels are found and mirrored automatically.
SECTIONS = (
    ("show_light", ("CYCLES_LIGHT_PT_light", "CYCLES_LIGHT_PT_settings", "CYCLES_LIGHT_PT_nodes",
                    "DATA_PT_EEVEE_light")),
    ("show_visibility", ("CYCLES_OBJECT_PT_visibility", "OBJECT_PT_visibility")),
    ("show_shading", ("OBJECT_PT_shading",)),
)


def get_prefs():
    addon = bpy.context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


def pref(name):
    p = get_prefs()
    return True if p is None else getattr(p, name, True)


class MirrorContext:
    """
    The Properties editor hands its panels context.light; the 3D Viewport does not. Everything else
    these panels read (object, scene, view_layer, engine) is the same, so only the light is added.
    """
    def __init__(self, context):
        self._context = context
        obj = getattr(context, "object", None)
        self.light = obj.data if obj is not None and obj.type == 'LIGHT' else None

    def __getattr__(self, name):
        return getattr(self._context, name)


class KRUTART_PT_iface_lighting(Panel):
    """Tool > Lighting: the lighting UI of the active object, mirrored from Properties."""
    bl_idname = LIGHTING_PANEL
    bl_label = "Lighting"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        if context.object is None:
            self.layout.label(text="Select an object.", icon='INFO')


class KRUTART_PT_iface_light_links(Panel):
    """Krutart Light Link's panel; identical to Properties > Object > Krutart Light Links."""
    bl_idname = "KRUTART_PT_iface_light_links"
    bl_label = "Light Links"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_parent_id = LIGHTING_PANEL
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return (pref("show_light_links") and context.object is not None
                and hasattr(getattr(bpy.types, LIGHT_LINK_PANEL, None), "draw_panel_content"))

    def draw(self, context):
        getattr(bpy.types, LIGHT_LINK_PANEL).draw_panel_content(self.layout, context, context.object)


def panel_id(cls):
    """Many built-in panels (e.g. all of Cycles) have no bl_idname; the class name is their id."""
    return getattr(cls, "bl_idname", None) or cls.__name__


class NativePanelSelf:
    """
    Stands in for `self` when a native panel's draw code runs inside a mirror: layout and space
    come from the mirror, everything else (helper methods, class settings) from the native panel.
    """
    def __init__(self, mirror_panel, native):
        self._panel = mirror_panel
        self._native = native

    def __getattr__(self, name):
        if name in ("layout", "bl_space_type", "bl_region_type", "is_popover"):
            return getattr(self._panel, name)
        try:
            attr = getattr(self._native, name)
        except AttributeError:
            return getattr(self._panel, name)
        if isinstance(attr, types.FunctionType):
            return types.MethodType(attr, self)
        return attr


def make_mirror(native, parent_idname, order, pref_name):
    """
    A sidebar panel that runs a native Properties panel's draw code. Composition, never a subclass:
    unregistering a subclass of a built-in panel strips the built-in's own bl_label (seen in 4.5.7),
    which would break the Properties editor after this add-on is disabled or updated.
    """
    idname = "KRUTART_PT_iface_" + panel_id(native)
    native_poll = getattr(native, "poll", None)
    native_header = getattr(native, "draw_header", None)

    def poll(cls, context):
        if not pref(pref_name):
            return False
        try:
            return bool(native_poll(MirrorContext(context))) if native_poll else context.object is not None
        except Exception:
            return False

    def draw(self, context):
        ctx = MirrorContext(context)
        if ctx.light is not None:
            self.layout.context_pointer_set("light", ctx.light)  # for buttons such as Use Nodes
        native.draw(NativePanelSelf(self, native), ctx)

    attrs = {
        "bl_idname": idname,
        "bl_label": getattr(native, "bl_label", "") or panel_id(native),
        "bl_space_type": 'VIEW_3D',
        "bl_region_type": 'UI',
        "bl_category": CATEGORY,
        "bl_parent_id": parent_idname,
        "bl_options": {'DEFAULT_CLOSED'},
        "bl_order": order,
        "poll": classmethod(poll),
        "draw": draw,
        "__doc__": f"Sidebar mirror of {panel_id(native)}",
    }
    if native_header is not None:
        def draw_header(self, context):
            native_header(NativePanelSelf(self, native), MirrorContext(context))
        attrs["draw_header"] = draw_header
    return type(idname, (Panel,), attrs)


def native_children(parent_idname):
    """Registered Properties panels whose parent is parent_idname, in registration order."""
    kids = []
    for cls in bpy.types.Panel.__subclasses__():
        if getattr(cls, "bl_parent_id", "") == parent_idname and getattr(cls, "bl_space_type", "") == 'PROPERTIES':
            kids.append(cls)
    return kids


_mirrors = []


def build_mirrors():
    """Creates and registers the mirrors. Missing native panels (engine off, API change) are skipped."""
    order = 1
    for pref_name, natives in SECTIONS:
        for native_id in natives:
            native = getattr(bpy.types, native_id, None)
            if native is None:
                log.info(f"Lighting panel: '{native_id}' is not available here, skipped")
                continue
            stack = [(native, LIGHTING_PANEL)]
            while stack:
                cls, parent = stack.pop(0)
                try:
                    mirror = make_mirror(cls, parent, order, pref_name)
                    bpy.utils.register_class(mirror)
                except Exception as e:
                    log.warning(f"Lighting panel: could not mirror '{panel_id(cls)}': {e}")
                    continue
                _mirrors.append(mirror)
                order += 1
                stack.extend((kid, mirror.bl_idname) for kid in native_children(panel_id(cls)))


def remove_mirrors():
    for mirror in reversed(_mirrors):
        try:
            bpy.utils.unregister_class(mirror)
        except Exception:
            pass
    _mirrors.clear()


def _deferred_build():
    """Cycles registers its panels at startup too; build once everything is in place."""
    if not _mirrors:
        build_mirrors()
    return None


class KrutartInterfacePreferences(AddonPreferences):
    bl_idname = __name__

    show_light_links: BoolProperty(name="Light Links", default=True,
                                   description="Krutart Light Link's LL/SL groups (needs Krutart Light Link)")
    show_light: BoolProperty(name="Light", default=True, description="Light, Settings and Nodes of the active light")
    show_visibility: BoolProperty(name="Visibility", default=True, description="Object visibility and ray visibility")
    show_shading: BoolProperty(name="Shading", default=True,
                               description="Object shading, incl. Blender's Light Linking and Shadow Linking")

    def draw(self, context):
        box = self.layout.box()
        box.label(text="Tool > Lighting sections", icon='LIGHT')
        row = box.row()
        for name in ("show_light_links", "show_light", "show_visibility", "show_shading"):
            row.prop(self, name)
        box.label(text=f"{len(_mirrors)} Blender panels mirrored", icon='INFO')


classes = (
    KrutartInterfacePreferences,
    KRUTART_PT_iface_lighting,
    KRUTART_PT_iface_light_links,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    build_mirrors()
    if not _mirrors:
        bpy.app.timers.register(_deferred_build, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(_deferred_build):
        bpy.app.timers.unregister(_deferred_build)
    remove_mirrors()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
