bl_info = {
    "name": "Krutart Interface",
    "author": "iori, Krutart",
    "version": (1, 3, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Tool > Lighting",
    "description": "Brings the lighting UI into the 3D Viewport sidebar: Solo, Light Links, Light, Visibility and Shading of the active object.",
    "warning": "",
    "doc_url": "",
    "category": "Interface",
}

# Krutart Interface - the sidebar home for small, self-contained UI tools that would otherwise
# each be an add-on of their own. Pipeline add-ons keep their own panels.
#
# The Lighting panel (Tool > Lighting) has these categories, each with its details as nested,
# indented, collapsible titles (1.3.0: Blender indents only one level of registered sub-panels, so the
# titles inside a category are layout panels with their own indent; 1.1.0-1.2.0 used sub-panels, 1.0.0
# listed every mirrored panel flat under Lighting):
#   - Solo (1.2.0, from OPTIONAL/krutart-lgt-solo 1.0 by Jakub): Solo Lights hides every light except
#     the selected ones, Hide World disconnects the world. Saved files never contain either: both are
#     undone right before any save (farm copies included) and re-applied right after. The original
#     state lives in the scene, so restoring works after undo, reopen or a crash.
#   - Light Links: Krutart Light Link's LL groups; sub-panels Shadow Groups and Setup
#     (Light Link >= 2.7.4; older Light Link: its whole panel as one block, as in 1.0.0)
#   - Light: the active light's settings (Cycles or EEVEE); sub-panels Beam Shape, Settings, Nodes, ...
#   - Visibility: object visibility; sub-panels Ray Visibility, Culling
#   - Shading: object shading; sub-panels Light Linking, Shadow Linking, Shadow Terminator, ...
# The native panels are mirrored, not rebuilt: each mirror runs Blender's own draw code, so it draws
# exactly the same properties. Editing in the sidebar or in Properties is the same edit.
#
# Candidates discussed for later (2026-09-21), NOT part of this add-on yet:
#   - krutart-empty_resizer (live, Tool tab): empty display size 0.01 m / 1 m.
#     Retiring it needs a final empty 1.2.0 release - the configurator never removes add-ons.
#   - OPTIONAL/krutart-viewport_display_tool (Tool tab): Display As Solid/Textured, all/selected
#   - (OPTIONAL/krutart-lgt-solo is merged as Tool > Lighting > Solo since 1.2.0)
#   - OPTIONAL/krutart-replacer (Tool tab): replace node groups, textures, materials
#   - krutart-browser_filter (Asset Browser, not the 3D sidebar): proxy/master filter
#   Probably superseded already: OPTIONAL/krutart-camera_toggle (layout-suite has the shot camera
#   switcher), OPTIONAL/dope_sheet_custom_controls (animation_controls sets keying sets).
# Merged tools must use new krutart_interface.* ids so they cannot clash with the old add-on.

import bpy
import json
import logging
import types
from bpy.app.handlers import persistent
from bpy.props import BoolProperty
from bpy.types import AddonPreferences, Operator, Panel

log = logging.getLogger("KrutartInterface")

CATEGORY = "Tool"
LIGHTING_PANEL = "KRUTART_PT_iface_lighting"
LIGHT_LINK_PANEL = "KRUTART_PT_light_link_panel"  # krutart-light_link, optional

# Categories of the Lighting panel: (key, label, preference, main panels, extra panels).
# The category's own body draws the first MAIN native panel whose poll passes (Blender's polls pick
# the engine and object type, so Cycles and EEVEE variants can both be listed). The main panels'
# sub-panels and the EXTRA panels (with their own sub-panels) become nested sub-panels of the category.
SECTIONS = (
    ("light", "Light", "show_light",
     ("CYCLES_LIGHT_PT_light", "DATA_PT_EEVEE_light"),
     ("CYCLES_LIGHT_PT_settings", "CYCLES_LIGHT_PT_nodes")),
    ("visibility", "Visibility", "show_visibility",
     ("CYCLES_OBJECT_PT_visibility", "OBJECT_PT_visibility"), ()),
    ("shading", "Shading", "show_shading",
     ("OBJECT_PT_shading",), ()),
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


def light_link_panel():
    """Krutart Light Link's Properties panel class, or None when Light Link is not installed."""
    panel = getattr(bpy.types, LIGHT_LINK_PANEL, None)
    return panel if hasattr(panel, "draw_panel_content") else None


def light_link_split():
    """Light Link >= 2.7.4 draws its panel in parts; then Interface shows them as sub-panels."""
    panel = light_link_panel()
    return panel if panel is not None and hasattr(panel, "draw_light_groups") else None


INDENT = 3.0   # separator factor: how far the titles inside a category are indented


def nested_panel(layout, idname):
    """An indented, collapsible title inside `layout`: (header, body); body is None while closed."""
    row = layout.row()
    row.separator(factor=INDENT)
    return row.column().panel(idname, default_closed=True)


class KRUTART_PT_iface_light_links(Panel):
    """Krutart Light Link's LL groups (same data as Properties > Object > Krutart Light Links)."""
    bl_idname = "KRUTART_PT_iface_light_links"
    bl_label = "Light Links"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_parent_id = LIGHTING_PANEL
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return pref("show_light_links") and context.object is not None and light_link_panel() is not None

    def draw(self, context):
        layout, obj = self.layout, context.object
        split = light_link_split()
        if split is None:  # Light Link < 2.7.4: its whole panel, as in Interface 1.0.0
            light_link_panel().draw_panel_content(layout, context, obj)
            return
        split.draw_light_groups(layout, context, obj)
        if not split.object_ui_active(obj):
            return
        header, body = nested_panel(layout, "KRUTART_iface_ll_shadow")
        header.label(text="Shadow Groups")
        count = split.shadow_count(obj)
        if count:
            right = header.row()
            right.alignment = 'RIGHT'
            right.label(text=str(count))
        if body is not None:
            split.draw_shadow_body(body, obj)
        header, body = nested_panel(layout, "KRUTART_iface_ll_setup")
        header.label(text="Setup")
        if body is not None:
            split.draw_setup(body, context)


def panel_id(cls):
    """Many built-in panels (e.g. all of Cycles) have no bl_idname; the class name is their id."""
    return getattr(cls, "bl_idname", None) or cls.__name__


class NativePanelSelf:
    """
    Stands in for `self` when a native panel's draw code runs inside a mirror: layout and space
    come from the mirror, everything else (helper methods, class settings) from the native panel.
    """
    def __init__(self, mirror_panel, native, layout=None):
        self._panel = mirror_panel
        self._native = native
        self._layout = layout   # draw into this layout (a nested title's header or body) instead

    def __getattr__(self, name):
        if name == "layout" and self._layout is not None:
            return self._layout
        if name in ("layout", "bl_space_type", "bl_region_type", "is_popover"):
            return getattr(self._panel, name)
        try:
            attr = getattr(self._native, name)
        except AttributeError:
            return getattr(self._panel, name)
        if isinstance(attr, types.FunctionType):
            return types.MethodType(attr, self)
        return attr


def draw_nested(panel, layout, context, native):
    """
    Blender's own Properties panel `native` as an indented, collapsible title inside `layout`, drawn by
    its own code (composition, never a subclass: unregistering a subclass of a built-in panel strips the
    built-in's bl_label in 4.5.7, breaking the Properties editor). Its own sub-panels nest inside it.
    """
    if not _native_polls(native, context):
        return
    ctx = MirrorContext(context)
    header, body = nested_panel(layout, "KRUTART_iface_" + panel_id(native))
    native_header = getattr(native, "draw_header", None)
    if native_header is not None:
        try:
            native_header(NativePanelSelf(panel, native, header), ctx)
        except Exception as e:
            log.warning(f"Lighting panel: header of '{panel_id(native)}' failed: {e}")
    header.label(text=getattr(native, "bl_label", "") or panel_id(native))
    if body is None:
        return
    if ctx.light is not None:
        body.context_pointer_set("light", ctx.light)  # for buttons such as Use Nodes
    try:
        native.draw(NativePanelSelf(panel, native, body), ctx)
    except Exception as e:
        log.warning(f"Lighting panel: '{panel_id(native)}' failed to draw: {e}")
        body.label(text=f"Could not draw {getattr(native, 'bl_label', '') or panel_id(native)}", icon='ERROR')
    for kid in native_children(panel_id(native)):
        draw_nested(panel, body, context, kid)


def _native_polls(native, context):
    """A native panel's own poll, evaluated with the mirror context; never raises."""
    native_poll = getattr(native, "poll", None)
    try:
        return bool(native_poll(MirrorContext(context))) if native_poll else context.object is not None
    except Exception:
        return False


def make_section(key, label, pref_name, main_ids, extra_ids, order):
    """
    A category of the Lighting panel (Light, Visibility, Shading). Its body is the first main native
    panel that polls; below it, as indented collapsible titles, the main panels' own sub-panels and the
    extra panels (each with its sub-panels nested inside it).
    """
    idname = f"KRUTART_PT_iface_sec_{key}"

    def mains():
        return [n for n in (getattr(bpy.types, i, None) for i in main_ids) if n is not None]

    def extras():
        return [n for n in (getattr(bpy.types, i, None) for i in extra_ids) if n is not None]

    def active_main(context):
        return next((n for n in mains() if _native_polls(n, context)), None)

    def poll(cls, context):
        if not pref(pref_name) or context.object is None:
            return False
        return active_main(context) is not None or any(_native_polls(n, context) for n in extras())

    def nested(context):
        """The titles shown below the body, in draw order; each draws only when its own poll passes."""
        kids = [kid for main in mains() for kid in native_children(panel_id(main))]
        return kids + extras()

    def draw(self, context):
        native = active_main(context)
        if native is not None:
            ctx = MirrorContext(context)
            if ctx.light is not None:
                self.layout.context_pointer_set("light", ctx.light)
            native.draw(NativePanelSelf(self, native), ctx)
        for kid in nested(context):
            draw_nested(self, self.layout, context, kid)

    cls = type(idname, (Panel,), {
        "bl_idname": idname,
        "bl_label": label,
        "bl_space_type": 'VIEW_3D',
        "bl_region_type": 'UI',
        "bl_category": CATEGORY,
        "bl_parent_id": LIGHTING_PANEL,
        "bl_options": {'DEFAULT_CLOSED'},
        "bl_order": order,
        "poll": classmethod(poll),
        "draw": draw,
        "nested_panels": staticmethod(nested),
        "__doc__": f"Tool > Lighting > {label}",
    })
    return cls, mains(), extras()


def native_children(parent_idname):
    """Registered Properties panels whose parent is parent_idname, in registration order."""
    kids = []
    for cls in bpy.types.Panel.__subclasses__():
        if getattr(cls, "bl_parent_id", "") == parent_idname and getattr(cls, "bl_space_type", "") == 'PROPERTIES':
            kids.append(cls)
    return kids


_mirrors = []


def _register(cls):
    try:
        bpy.utils.register_class(cls)
    except Exception as e:
        log.warning(f"Lighting panel: could not register '{cls.__name__}': {e}")
        return False
    _mirrors.append(cls)
    return True


def build_mirrors():
    """
    Creates and registers the categories. Their inner titles are drawn, not registered (see draw_nested).
    Missing native panels (engine off, API change) are skipped and logged, never fatal.
    """
    for sec_order, (key, label, pref_name, main_ids, extra_ids) in enumerate(SECTIONS, start=2):
        section, mains, extras = make_section(key, label, pref_name, main_ids, extra_ids, sec_order)
        missing = [i for i in main_ids + extra_ids if getattr(bpy.types, i, None) is None]
        if missing:
            log.info(f"Lighting panel: {', '.join(missing)} not available here, skipped")
        if mains or extras:
            _register(section)


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



# --- Solo: Tool > Lighting > Solo -----------------------------------------------------------------
# Merged from OPTIONAL/krutart-lgt-solo 1.0, with its four problems fixed:
#  1. Saved files never contain a solo: every save (Ctrl+S, bRender's farm copies, ...) first puts the
#     lights and the world back, and the solo is re-applied right after (or after a failed save).
#  2. The original visibility is stored in the scene, not in Python memory, so the restore still
#     works after undo, reopening the file or a crash/autosave recovery.
#  3. Hide World records the exact link(s) into the World Output and restores exactly those (1.0
#     rewired "the first Background node", which broke worlds built with a Mix Shader).
#  4. Linked lights (not overrides) can't be changed here; they are skipped and named in a warning.
SOLO_KEY = "krutart_interface_solo"        # scene ID property: JSON with the lights' original state
WORLD_KEY = "krutart_interface_world_off"  # scene ID property: JSON with the removed world link(s)


def _get_state(scene, key):
    raw = scene.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _set_state(scene, key, data):
    if data:
        scene[key] = json.dumps(data)
    elif key in scene.keys():
        del scene[key]


def _editable(obj):
    """Local objects and editable library overrides; linked objects can't be changed in this file."""
    if obj.library is not None:
        return False
    override = obj.override_library
    return override is None or not getattr(override, "is_system_override", False)


def solo_apply(scene, keep):
    """Records every light's visibility, then shows the lights named in keep and hides the rest."""
    state, skipped = {"original": {}, "keep": sorted(keep)}, []
    for obj in scene.objects:
        if obj.type != 'LIGHT':
            continue
        if not _editable(obj):
            skipped.append(obj.name)
            continue
        state["original"][obj.name] = [obj.hide_viewport, obj.hide_render]
    failed = _solo_hide(state)
    for name in failed:
        state["original"].pop(name, None)
    return state, skipped + failed


def _solo_hide(state):
    """(Re)applies a recorded solo. Returns the lights that could not be changed."""
    failed = []
    keep = set(state.get("keep", ()))
    for name in state.get("original", {}):
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        show = name in keep
        try:
            obj.hide_viewport = not show
            obj.hide_render = not show
        except Exception:
            failed.append(name)
    return failed


def solo_restore(state):
    """Puts every recorded light back as it was. Returns the lights no longer found."""
    missing = []
    for name, (hide_viewport, hide_render) in state.get("original", {}).items():
        obj = bpy.data.objects.get(name)
        if obj is None:
            missing.append(name)
            continue
        try:
            obj.hide_viewport = bool(hide_viewport)
            obj.hide_render = bool(hide_render)
        except Exception:
            missing.append(name)
    return missing


def _world_output(world):
    outputs = [n for n in world.node_tree.nodes if n.type == 'OUTPUT_WORLD']
    return next((n for n in outputs if getattr(n, "is_active_output", False)), outputs[0] if outputs else None)


def world_hide(world):
    """Disconnects the World Output's Surface and records exactly what was connected: (state, error)."""
    if world is None or world.node_tree is None or not getattr(world, "use_nodes", True):
        return None, "The scene has no world with nodes."
    if world.library is not None:
        return None, "The world is linked from a library and can't be changed in this file."
    output = _world_output(world)
    surface = output.inputs.get("Surface") if output else None
    if surface is None:
        return None, "The world has no World Output node."
    links = [{"from_node": l.from_node.name, "from_socket": l.from_socket.identifier} for l in surface.links]
    if not links:
        return None, "Nothing is connected to the World Output; there is nothing to hide."
    for link in list(surface.links):
        world.node_tree.links.remove(link)
    return {"world": world.name, "output": output.name, "links": links}, None


def world_restore(state):
    """Reconnects exactly the recorded link(s). Returns a problem description or ''."""
    world = bpy.data.worlds.get(state.get("world", ""))
    if world is None or world.node_tree is None:
        return f"World '{state.get('world')}' not found."
    nodes = world.node_tree.nodes
    output = nodes.get(state.get("output", ""))
    surface = output.inputs.get("Surface") if output else None
    if surface is None:
        return f"World Output '{state.get('output')}' not found."
    lost = []
    for link in state.get("links", ()):
        node = nodes.get(link.get("from_node", ""))
        socket = next((s for s in node.outputs if s.identifier == link.get("from_socket")), None) if node else None
        if socket is None:
            lost.append(link.get("from_node", "?"))
            continue
        world.node_tree.links.new(socket, surface)
    return f"could not reconnect: {', '.join(lost)}" if lost else ""


_resume_after_save = []


@persistent
def _solo_save_pre(*_args):
    """Saved files never contain a solo: put everything back just before writing."""
    _resume_after_save.clear()
    for scene in bpy.data.scenes:
        solo, world = _get_state(scene, SOLO_KEY), _get_state(scene, WORLD_KEY)
        if not (solo or world):
            continue
        if solo:
            solo_restore(solo)
        if world:
            world_restore(world)
        _set_state(scene, SOLO_KEY, None)
        _set_state(scene, WORLD_KEY, None)
        _resume_after_save.append((scene.name, solo, world))


@persistent
def _solo_save_post(*_args):
    """Re-applies the solo in the open session after the save (or after a failed save)."""
    for scene_name, solo, world in _resume_after_save:
        scene = bpy.data.scenes.get(scene_name)
        if scene is None:
            continue
        if solo:
            _solo_hide(solo)
            _set_state(scene, SOLO_KEY, solo)
        if world:
            state, _ = world_hide(bpy.data.worlds.get(world.get("world", "")))
            _set_state(scene, WORLD_KEY, state)
    _resume_after_save.clear()


SAVE_HANDLERS = (("save_pre", _solo_save_pre), ("save_post", _solo_save_post), ("save_post_fail", _solo_save_post))


class KRUTART_INTERFACE_OT_solo_lights(Operator):
    """Hide every light except the selected ones, in the viewport and in renders. Click again to
restore them. Saved files always keep the original lights"""
    bl_idname = "krutart_interface.solo_lights"
    bl_label = "Solo Lights"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        state = _get_state(scene, SOLO_KEY)
        if state:
            missing = solo_restore(state)
            _set_state(scene, SOLO_KEY, None)
            if missing:
                self.report({'WARNING'}, f"Lights restored; not found any more: {', '.join(missing)}")
            else:
                self.report({'INFO'}, "Lights restored")
            return {'FINISHED'}
        keep = {o.name for o in context.selected_objects if o.type == 'LIGHT'}
        if not keep:
            self.report({'WARNING'}, "Select the light(s) to solo.")
            return {'CANCELLED'}
        state, skipped = solo_apply(scene, keep)
        _set_state(scene, SOLO_KEY, state)
        if skipped:
            self.report({'WARNING'}, f"Solo on. Linked lights can't be hidden in this file: {', '.join(skipped)}")
        else:
            self.report({'INFO'}, f"Solo on: {len(keep)} light(s)")
        return {'FINISHED'}


class KRUTART_INTERFACE_OT_hide_world(Operator):
    """Disconnect the world (background and its lighting) in the viewport and in renders. Click again
to reconnect it exactly as it was. Saved files always keep the world"""
    bl_idname = "krutart_interface.hide_world"
    bl_label = "Hide World"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        state = _get_state(scene, WORLD_KEY)
        if state:
            problem = world_restore(state)
            _set_state(scene, WORLD_KEY, None)
            if problem:
                self.report({'WARNING'}, f"World reconnected; {problem}")
            else:
                self.report({'INFO'}, "World reconnected")
            return {'FINISHED'}
        state, error = world_hide(scene.world)
        if error:
            self.report({'WARNING'}, error)
            return {'CANCELLED'}
        _set_state(scene, WORLD_KEY, state)
        self.report({'INFO'}, "World hidden")
        return {'FINISHED'}


class KRUTART_PT_iface_solo(Panel):
    """Solo the selected lights and hide the world, for looking at one light at a time."""
    bl_idname = "KRUTART_PT_iface_solo"
    bl_label = "Solo"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_parent_id = LIGHTING_PANEL
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return pref("show_solo") and context.scene is not None

    def draw(self, context):
        scene = context.scene
        solo, world = _get_state(scene, SOLO_KEY), _get_state(scene, WORLD_KEY)
        col = self.layout.column(align=True)
        if solo:
            shown = len(solo.get("keep", ()))
            hidden = len(solo.get("original", {})) - shown
            text = f"Solo: {shown} shown, {hidden} hidden"
        else:
            text = "Solo Selected Lights"
        col.operator(KRUTART_INTERFACE_OT_solo_lights.bl_idname, text=text, icon='LIGHT', depress=bool(solo))
        col.operator(KRUTART_INTERFACE_OT_hide_world.bl_idname, text="World Hidden" if world else "Hide World",
                     icon='WORLD', depress=bool(world))
        if solo or world:
            self.layout.label(text="Saved files keep all lights and the world", icon='INFO')


class KrutartInterfacePreferences(AddonPreferences):
    bl_idname = __name__

    show_solo: BoolProperty(name="Solo", default=True,
                            description="Solo Lights and Hide World buttons")
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
        for name in ("show_solo", "show_light_links", "show_light", "show_visibility", "show_shading"):
            row.prop(self, name)
        box.label(text=f"{len(_mirrors)} categories built from Blender's own panels", icon='INFO')


classes = (
    KrutartInterfacePreferences,
    KRUTART_PT_iface_lighting,
    KRUTART_PT_iface_light_links,
    KRUTART_INTERFACE_OT_solo_lights,
    KRUTART_INTERFACE_OT_hide_world,
    KRUTART_PT_iface_solo,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    for name, fn in SAVE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and fn not in handlers:
            handlers.append(fn)
    build_mirrors()
    if not _mirrors:
        bpy.app.timers.register(_deferred_build, first_interval=0.5)


def unregister():
    for name, fn in SAVE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and fn in handlers:
            handlers.remove(fn)
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
