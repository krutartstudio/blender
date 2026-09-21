bl_info = {
    "name": "Krutart Light Link",
    "author": "iori, Krutart, Gemini",
    "version": (2, 7, 1),
    "blender": (4, 2, 0),
    "location": "Properties > Object & Collection > Light Linking",
    "description": "Manages default light (LL-) and shadow (SL-) link groups for assets via a cascading lookup system linked with Google Sheets preproduction data.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import json
import logging
import urllib.request
import urllib.error
import csv
import io
import re
import os
import threading
import subprocess
import tempfile
import shutil
from pathlib import Path
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty
from bpy.types import PropertyGroup, Panel, Operator, AddonPreferences
from bpy_extras.io_utils import ExportHelper, ImportHelper
from bpy.app.handlers import persistent
from mathutils import Vector

# --- Logging Setup ---
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("KrutartLightLink")

# --- Constants ---
DATA_BLOCK_NAME = "__krutart_light_link_data.json"
CUSTOM_PROP_OVERRIDE = "krutart_light_override"
CUSTOM_PROP_DELTA = "krutart_light_delta"
SCENE_PROP_SOURCE = "krutart_ll_source"
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/KRUTART_REDACTED_SHEET_ID_1/export?format=csv&gid=1343757578"
DEFAULT_HERO_LGT_PATH = "S:/3212-PREPRODUCTION/LIBRARY/LIBRARY-HERO/LGT-HERO/LGT-LLCOL-HERO/3212-lgt-llcol-hero.blend"

STANDARD_LIGHT_GROUPS = (
    "LL-LOCATION", "LL-TERRAIN", "LL-HILL", "LL-ROCK", "LL-PROP",
    "LL-ACTOR", "LL-ALAN", "LL-OSMA", "LL-CHARLIE", "LL-FRED",
    "LL-RAT_SIDE", "LL-ONLYVOLUME", "LL-ALLBUTVOLUME", "LL-FG"
)

STANDARD_LIGHT_GROUPS_SET = {g.upper() for g in STANDARD_LIGHT_GROUPS}

class LinkChannel:
    """
    One kind of link group. Light and shadow linking work the same way in Blender - a light points
    at a collection, the collection's members are affected - so everything in this add-on is shared
    and the group name's prefix says which channel a group belongs to.
    """
    def __init__(self, key, prefix, label, section, light_prop, sheet_header, standard, lightgroups):
        self.key = key                    # 'LIGHT' / 'SHADOW'
        self.prefix = prefix              # 'LL-' / 'SL-'
        self.label = label                # UI word
        self.section = section            # panel section title
        self.light_prop = light_prop      # the light's pointer in Object.light_linking
        self.sheet_header = sheet_header  # ASL column
        self.standard = standard          # groups every ART file gets up front
        self.lightgroups = lightgroups    # register as View Layer lightgroups (render passes)

LIGHT = LinkChannel("LIGHT", "LL-", "Light", "LL GROUPS", "receiver_collection", "LIGHT LINK",
                    STANDARD_LIGHT_GROUPS, True)
# Shadow links are the exception, not the rule: no groups up front, no lightgroups, default none.
SHADOW = LinkChannel("SHADOW", "SL-", "Shadow", "SL GROUPS", "blocker_collection", "SHADOW LINK",
                     (), False)
CHANNELS = (LIGHT, SHADOW)
CHANNELS_BY_KEY = {c.key: c for c in CHANNELS}
LINK_GROUP_PREFIXES = tuple(c.prefix for c in CHANNELS)
LGT_CONTAINER_NAME = "+LGT+"
LGT_OLD_CONTAINER_NAMES = ("+LGT+", "LGT-REFERENCE")  # replaced wholesale by Append LGT File
ART_CONTAINER_NAME = "+ART+"                          # +LGT+ lives inside it
VIEW_LAYER_PROP_PREFIX = "lgt"  # Append LGT File copies the source view layer's custom properties starting with this (lowercase)

# --- Utility Functions ---

def resolve_krutart_path(path_str):
    """
    Resolves canonical S:/ drive paths or relative Blender paths to valid local filesystem paths.
    Compatible with Windows S: drive and macOS Google Drive mount locations.
    """
    if not path_str:
        return ""
    abs_p = bpy.path.abspath(path_str)
    if os.path.exists(abs_p):
        return abs_p
    normalized = path_str.replace('\\', '/')
    if os.path.exists(normalized):
        return normalized
    if normalized.upper().startswith("S:/") or normalized.upper().startswith("S:\\"):
        rel_subpath = normalized[3:]
        user_home = os.path.expanduser("~")
        cloud_storage = os.path.join(user_home, "Library", "CloudStorage")
        if os.path.exists(cloud_storage):
            for entry in os.listdir(cloud_storage):
                if entry.startswith("GoogleDrive-"):
                    candidate = os.path.join(cloud_storage, entry, "Shared drives", rel_subpath)
                    if os.path.exists(candidate):
                        return candidate
        if bpy.data.filepath:
            curr = Path(bpy.data.filepath).resolve()
            for parent in [curr] + list(curr.parents):
                if parent.name in ("3212-PREPRODUCTION", "3212-PRODUCTION"):
                    target_root = parent if parent.name == "3212-PREPRODUCTION" else (parent.parent / "3212-PREPRODUCTION")
                    if rel_subpath.startswith("3212-PREPRODUCTION/"):
                        sub_rel = rel_subpath[len("3212-PREPRODUCTION/"):]
                    else:
                        sub_rel = rel_subpath
                    candidate = target_root / sub_rel
                    if candidate.exists():
                        return str(candidate)
    return abs_p

def get_base_asset_name(name, strip_lod=True):
    """Strips duplicate suffixes, proxy/master flags, pipeline prefixes/suffixes, and wrapper symbols."""
    if not name:
        return ""
    base = name.strip()
    
    # 0. Strip leading/trailing wrapper symbols (+, *, #, @) and whitespace
    base = re.sub(r'^[\+\*\#\@\s]+|[\+\*\#\@\s]+$', '', base)
    
    # 1. Strip standard Blender duplicate suffixes (e.g. '.003')
    base = re.sub(r'\.\d+$', '', base)
    
    # 2. Strip proxy/master middle flags (e.g. '-P-', '-M-', '-p-', '-m-')
    base = re.sub(r'-[PpMm]-', '-', base, flags=re.IGNORECASE)
    
    # 3. Strip proxy/master trailing flags (e.g. '-P', '-M', '-p', '-m')
    base = re.sub(r'-[PpMm]$', '', base, flags=re.IGNORECASE)
    
    # 4. Strip standard pipeline prefixes case-insensitively
    prefixes = (
        r'^GRMOD-', r'^\*act-', r'^ACT-', r'^ACTOR-',
        r'^PRP\s*-\s*', r'^PRP-', r'^PROP\s*-\s*', r'^PROP-',
        r'^MOD\s*-\s*', r'^MOD-', r'^MODEL\s*-\s*', r'^MODEL-',
        r'^LOC\s*-\s*', r'^LOC-', r'^LOCATION\s*-\s*', r'^LOCATION-',
        r'^ENV\s*-\s*', r'^ENV-', r'^ENVIRONMENT\s*-\s*', r'^ENVIRONMENT-',
        r'^GNO-', r'^MAT-', r'^CAM-'
    )
    for pattern in prefixes:
        base = re.sub(pattern, '', base, flags=re.IGNORECASE)
        
    # 5. Strip standard armature/rig/helper/lod suffixes case-insensitively
    suffixes = [r'-arm$', r'-armature$', r'-rig$', r'-emp$', r'-mesh$']
    if strip_lod:
        suffixes.append(r'-lod\d+$')
        
    for pattern in suffixes:
        base = re.sub(pattern, '', base, flags=re.IGNORECASE)
        
    return base.strip()

def get_asset_version(name):
    """
    Determines if the asset is PROXY or MASTER based on naming conventions.
    Checks for the presence of -p/-P or -m/-M flags.
    """
    clean_name = re.sub(r'\.\d+$', '', name.strip()) # Strip Blender duplicates like .001
    
    # Check for Proxy flag (middle: '-P-' or trailing: '-P')
    if re.search(r'-[Pp]-', clean_name) or re.search(r'-[Pp]$', clean_name):
        return "PROXY"
    # Check for Master flag (middle: '-M-' or trailing: '-M')
    if re.search(r'-[Mm]-', clean_name) or re.search(r'-[Mm]$', clean_name):
        return "MASTER"
        
    return "PROXY" # Fallback default

def get_object_delta(obj):
    """
    Returns the delta dict of an object or collection. Objects keep it in their overridable
    krutart_light_link settings, because Blender does not save plain custom properties on library
    overrides; the custom property is the fallback for older files and for collections.
    """
    raw = None
    settings = getattr(obj, "krutart_light_link", None) if isinstance(obj, bpy.types.Object) else None
    if settings is not None and settings.delta_json:
        raw = settings.delta_json
    elif CUSTOM_PROP_DELTA in obj:
        raw = obj[CUSTOM_PROP_DELTA]
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                if "added" not in data: data["added"] = []
                if "removed" not in data: data["removed"] = []
                return data
        except Exception:
            pass
    return {"added": [], "removed": []}

def save_object_delta(obj, delta):
    """Saves the delta to the object's overridable settings and to the custom property the pipeline sync reads."""
    payload = json.dumps(delta)
    settings = getattr(obj, "krutart_light_link", None) if isinstance(obj, bpy.types.Object) else None
    if settings is not None:
        settings.delta_json = payload
    try:
        obj[CUSTOM_PROP_DELTA] = payload
    except Exception:
        return
    # Register with metadata 'cp' description so krutart-custom_properties.py can sync it
    if hasattr(obj, "id_properties_ui"):
        try:
            obj.id_properties_ui(CUSTOM_PROP_DELTA).update(description="cp")
        except Exception:
            pass

def get_prefs():
    """Returns this add-on's preferences, or None if it is not registered under __name__."""
    try:
        addon = bpy.context.preferences.addons.get(__name__)
        return addon.preferences if addon else None
    except Exception:
        return None

ART_FLAG = "art"
ANI_FLAG = "ani"

def filename_flags():
    """
    Department flags in the open file's name, e.g. 3212-sc11-sh050-art-v002-comment.blend gives
    {'3212', 'sc11', 'sh050', 'art', 'v002', 'comment'}. Empty for an unsaved file.
    """
    path = bpy.data.filepath
    if not path:
        return set()
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return set(re.split(r"[-_. ]+", stem))

def is_art_file():
    """
    True for ART shot files. Everything this add-on does on its own is limited to those: elsewhere
    it must not create LL- collections, register lightgroups or write its cache.
    """
    return ART_FLAG in filename_flags()

def is_ani_file():
    """True for ANI files - the ones polluted by earlier versions, where the cleanup button belongs."""
    return ANI_FLAG in filename_flags()

def group_channel(name):
    """The LinkChannel a (possibly '.001'-suffixed) group name belongs to, or None for other names."""
    if not name:
        return None
    base = name.split('.')[0]
    if base.upper() in STANDARD_LIGHT_GROUPS_SET or base.startswith(LIGHT.prefix):
        return LIGHT
    if base.startswith(SHADOW.prefix):
        return SHADOW
    return None

def is_light_group_name(name, channel=None):
    """
    True if a (possibly '.001'-suffixed) collection name is a link group: LL- (light) or SL- (shadow).
    Everything that hides, protects, transfers or cleans groups treats both alike; pass channel to
    ask about one of them only.
    """
    ch = group_channel(name)
    return ch is not None and (channel is None or ch is channel)

def normalize_group_name(raw, channel=None):
    """Turns user input into a canonical custom group name: 'bridge rim' -> 'LL-BRIDGE_RIM' (or 'SL-...')."""
    channel = channel or LIGHT
    name = re.sub(r'[^A-Za-z0-9_]+', '_', (raw or "").strip()).strip('_').upper()
    name = re.sub(r'^(LL|SL)_+', '', name)
    return f"{channel.prefix}{name}" if name else ""

def get_group_pool_names(channel=None):
    """Names of every canonical (non '.001'-suffixed) link group collection in the current file."""
    return {c.name for c in bpy.data.collections
            if is_light_group_name(c.name, channel) and c.name == c.name.split('.')[0]}

def _library_has_linked_ids(lib):
    """True if any datablock in the file is still linked from lib."""
    for attr in dir(bpy.data):
        if attr.startswith("_") or attr == "libraries":
            continue
        coll = getattr(bpy.data, attr, None)
        if not isinstance(coll, bpy.types.bpy_prop_collection):
            continue
        try:
            for idb in coll:
                if getattr(idb, "library", None) == lib:
                    return True
        except Exception:
            continue
    return False

def remove_orphan_libraries(before):
    """
    Removes Library datablocks created by an append-mode libraries.load. Appended data is made
    local, so these are dead references - unless something is still linked from them.
    """
    for lib in list(bpy.data.libraries):
        if lib in before or _library_has_linked_ids(lib):
            continue
        try:
            bpy.data.libraries.remove(lib)
        except Exception as e:
            log.warning(f"Could not remove library reference '{lib.name}': {e}")

def _library_keys(lib):
    """Ways a library path can be written: as stored, and resolved. Either one matching means same file."""
    keys = set()
    raw = (lib.filepath or "").replace("\\", "/")
    if raw:
        keys.add(os.path.normcase(raw))
        try:
            keys.add(os.path.normcase(os.path.normpath(bpy.path.abspath(lib.filepath)).replace("\\", "/")))
        except Exception:
            pass
    return keys

def merge_duplicate_libraries(before):
    """
    An append can add a second entry for a library this file already links ('x.blend.001') when the
    two files spell its path differently. Points everything from such an entry at the same-named
    datablock of the existing entry; the leftovers are then orphans for purge_new_orphans and
    remove_orphan_libraries. Returns the number of datablocks redirected.
    """
    by_key = {}
    for lib in before:
        for k in _library_keys(lib):
            by_key.setdefault(k, lib)
    merged = 0
    for lib in list(bpy.data.libraries):
        if lib in before:
            continue
        orig = next((by_key[k] for k in _library_keys(lib) if k in by_key), None)
        if orig is None:
            continue
        for attr in set(_ID_COLLECTION_NAMES.values()):
            coll = getattr(bpy.data, attr, None)
            if coll is None:
                continue
            twins = {i.name: i for i in coll if i.library == orig}
            for idb in [i for i in coll if i.library == lib]:
                twin = twins.get(idb.name)
                if twin is None:
                    # Not linked here yet: link it through the existing entry first.
                    try:
                        with bpy.data.libraries.load(bpy.path.abspath(orig.filepath), link=True) as (_src, dst):
                            setattr(dst, attr, [idb.name])
                        twin = next((i for i in coll if i.library == orig and i.name == idb.name), None)
                    except Exception:
                        twin = None
                if twin is None:
                    log.warning(f"'{idb.name}' exists only in the duplicate library entry '{lib.name}'")
                    continue
                try:
                    idb.user_remap(twin)
                    merged += 1
                except Exception as e:
                    log.warning(f"Could not redirect '{idb.name}' to '{orig.name}': {e}")
    return merged

def _local_collection(name):
    """The file's own (not linked, not overridden) collection with exactly this name."""
    for c in bpy.data.collections:
        if c.name == name and not c.library and not c.override_library:
            return c
    return None

def _id_collection(idb):
    """bpy.data collection holding idb. Walks the type hierarchy: a light is a PointLight/SunLight,
    a node group a GeometryNodeTree, which the plain type name never matched."""
    for cls in type(idb).__mro__:
        attr = _ID_COLLECTION_NAMES.get(cls.__name__)
        if attr:
            return getattr(bpy.data, attr, None)
    return None

def _remove_id(idb):
    coll = _id_collection(idb)
    if coll is None or not hasattr(coll, "remove"):
        return False
    try:
        coll.remove(idb)
        return True
    except Exception:
        return False

def purge_freed_orphans(orphans_before):
    """
    Removes datablocks that lost their last user since orphans_before (as_pointer ids) was taken.
    Orphans that already existed are left alone. One ID at a time (batch_remove crashed 4.5 on Windows).
    """
    removed = 0
    for _ in range(20):
        orphans = [i for i in _id_snapshot()
                   if i.users == 0 and not i.use_fake_user and i.as_pointer() not in orphans_before]
        freed = sum(1 for i in orphans if _remove_id(i))
        if not freed:
            break
        removed += freed
    return removed

def remove_lgt_containers():
    """
    Deletes the file's +LGT+ and LGT-REFERENCE collections (any '.001' copies too), their
    sub-collections and every object that lives only there, then purges what that freed -
    including linked assets and library entries nothing uses any more. LL- groups are never touched.
    Linked or overridden containers are left alone. Returns (collections, objects, purged) counts.
    """
    doomed = set()
    for c in bpy.data.collections:
        if c.library or c.override_library or is_light_group_name(c.name):
            continue
        if c.name.split(".")[0] in LGT_OLD_CONTAINER_NAMES:
            doomed.add(c)
            doomed.update(ch for ch in c.children_recursive
                          if not ch.library and not ch.override_library and not is_light_group_name(ch.name))
    if not doomed:
        return 0, 0, 0

    ids_before = _id_snapshot()
    orphans_before = {i.as_pointer() for i in ids_before if i.users == 0}
    libs_used_before = {i.library for i in ids_before if i.library is not None}
    objects = set()
    for c in doomed:
        objects.update(c.objects)

    removed_objs = 0
    for o in objects:
        others = [u for u in o.users_collection if u not in doomed and not is_light_group_name(u.name)]
        in_scene = any(o.name in s.collection.objects for s in bpy.data.scenes)
        if others or in_scene or o.library:
            for c in doomed:  # used elsewhere (or linked): only take it out of the old containers
                if o in set(c.objects):
                    try:
                        c.objects.unlink(o)
                    except Exception:
                        pass
            continue
        try:
            bpy.data.objects.remove(o, do_unlink=True)
            removed_objs += 1
        except Exception as e:
            log.warning(f"Could not remove '{o.name}': {e}")

    removed_cols = 0
    for c in doomed:
        try:
            bpy.data.collections.remove(c, do_unlink=True)
            removed_cols += 1
        except Exception as e:
            log.warning(f"Could not remove collection: {e}")

    purged = purge_freed_orphans(orphans_before)
    for lib in list(bpy.data.libraries):
        if lib in libs_used_before and not _library_has_linked_ids(lib):
            try:
                bpy.data.libraries.remove(lib)
            except Exception:
                pass
    return removed_cols, removed_objs, purged

_SNAPSHOT_SKIP = {"libraries", "window_managers", "screens", "workspaces"}

# bpy.data collection for a datablock type, so single IDs can be removed without batch_remove.
_ID_COLLECTION_NAMES = {
    "Object": "objects", "Mesh": "meshes", "Material": "materials", "Collection": "collections",
    "Image": "images", "Light": "lights", "LightProbe": "lightprobes", "Curve": "curves",
    "Armature": "armatures", "Action": "actions", "NodeTree": "node_groups", "Text": "texts",
    "Texture": "textures", "World": "worlds", "Camera": "cameras", "GreasePencil": "grease_pencils",
    "Lattice": "lattices", "MetaBall": "metaballs", "Volume": "volumes", "PointCloud": "pointclouds",
    "Speaker": "speakers", "Sound": "sounds", "ParticleSettings": "particles", "Brush": "brushes",
    "Palette": "palettes", "LineStyle": "linestyles", "FreestyleLineStyle": "linestyles",
    "Scene": "scenes", "Key": "shape_keys", "VectorFont": "fonts", "CacheFile": "cache_files",
}

def _id_snapshot():
    """Every datablock currently in bpy.data (UI data excluded), for exact before/after diffs."""
    ids = set()
    for attr in dir(bpy.data):
        if attr.startswith("_") or attr in _SNAPSHOT_SKIP:
            continue
        coll = getattr(bpy.data, attr, None)
        if isinstance(coll, bpy.types.bpy_prop_collection):
            try:
                ids.update(coll)
            except Exception:
                pass
    return ids

POSITION_TOLERANCE = 0.01  # metres; placements closer than this count as the same spot

def object_world_position(obj):
    """
    World position from the saved transforms and the parent chain. matrix_world is not usable for
    the source side: it reads (0, 0, 0) on overrides linked in from another file, because nothing
    evaluates them. Constraints and drivers are ignored, the same way on both sides.
    """
    mw = obj.matrix_basis.copy()
    node = obj
    while node.parent:
        mw = node.parent.matrix_basis @ node.matrix_parent_inverse @ mw
        node = node.parent
    return mw.translation.copy()

def asset_identity(obj):
    """
    Cross-file identity of a library override: the asset object it overrides plus that asset's
    .blend file. Local override names depend on instancing order, so they differ between files.
    Returns None for objects that are not overrides.
    """
    ol = obj.override_library
    ref = ol.reference if ol else None
    if not ref:
        return None
    lib = bpy.path.basename(ref.library.filepath).lower() if ref.library else ""
    return (ref.name, lib)

def purge_new_orphans(before_ids):
    """
    Removes datablocks an append brought in that nothing uses any more - mostly the receiver
    geometry that light linking drags along (and its linked materials, meshes, node groups),
    which the append deletes again. Repeats until nothing more frees up. Only touches IDs that
    did not exist before the append; fake-user IDs (the LL- groups) are kept.
    """
    removed = 0
    for _ in range(10):
        orphans = [i for i in (_id_snapshot() - before_ids) if i.users == 0 and not i.use_fake_user]
        if not orphans:
            break
        # One at a time: bpy.data.batch_remove crashed Blender 4.5 on Windows
        # (BKE_idtype_index_to_idfilter, "code marked as unreachable").
        freed = 0
        for idb in orphans:
            if _remove_id(idb):
                freed += 1
        if not freed:
            break
        removed += freed
    return removed

# Read in a SEPARATE Blender: opening another shot's overrides inside this file made Blender
# repoint local data to duplicate library entries, which corrupted the file (sh050 v006).
# The child process writes what we need plus a stripped copy holding only lights + empty LL groups.
SOURCE_EXTRACTOR = r"""
import bpy, sys, json, os

argv = sys.argv[sys.argv.index("--") + 1:]
out_json, out_blend = argv[0], argv[1]

def world_pos(o):
    mw = o.matrix_basis.copy()
    node = o
    while node.parent:
        mw = node.parent.matrix_basis @ node.matrix_parent_inverse @ mw
        node = node.parent
    return list(mw.translation)

def descriptor(idb):
    ol = idb.override_library
    ref = ol.reference if ol else None
    if ref is not None:
        lib = os.path.basename(bpy.path.abspath(ref.library.filepath)).lower() if ref.library else ""
        return "asset", [ref.name, lib]
    if idb.library:
        return "linked", [idb.name, os.path.basename(bpy.path.abspath(idb.library.filepath)).lower()]
    return "local", [idb.name, ""]

def raw_settings(o):
    pg = o.get("krutart_light_link")
    groups, delta_raw, flag = [], "", False
    if pg is not None:
        try:
            groups = json.loads(pg.get("groups_json", "[]"))
        except Exception:
            groups = []
        delta_raw = pg.get("delta_json", "") or ""
        flag = bool(pg.get("is_override_active", 0))
    if not delta_raw:
        delta_raw = o.get("krutart_light_delta", "") or ""
    return groups, delta_raw, flag

records = {}
def rec(o):
    r = records.get(o.as_pointer())
    if r is None:
        kind, key = descriptor(o)
        r = {"kind": kind, "key": key, "name": o.name, "position": world_pos(o),
             "groups": [], "props_groups": None, "delta": None, "customised": False}
        records[o.as_pointer()] = r
    return r

def is_override(idb):
    return bool(idb.override_library and idb.override_library.reference is not None)

# +LGT+ content travels with the append as it is: local items get appended, linked items stay
# linked. Overrides are left out - they are what corrupted sh050 v006.
source_container = None
for cname in ("+LGT+", "LGT-REFERENCE"):  # older shots keep their lights in LGT-REFERENCE
    c = bpy.data.collections.get(cname)
    if c is not None and not c.library and not is_override(c):
        source_container = c
        break
if source_container is not None:
    source_container.name = "+LGT+"
content_cols, content_objs, skipped = set(), set(), []
def gather(col):
    for o in col.objects:
        if is_override(o):
            skipped.append(o.name)
        else:
            content_objs.add(o)
    for c in col.children:
        if is_override(c):
            skipped.append(c.name)
        elif c not in content_cols:
            content_cols.add(c)
            gather(c)
if source_container is not None:
    gather(source_container)

collections_out = []
GROUP_PREFIXES = ("LL-", "SL-")  # light and shadow link groups

for col in bpy.data.collections:
    if not col.name.startswith(GROUP_PREFIXES):
        continue
    group = col.name.split(".")[0]
    for o in col.objects:
        if o in content_objs:  # its membership rides along with the append
            continue
        r = rec(o)
        if group not in r["groups"]:
            r["groups"].append(group)
    for child in col.children:
        if child in content_cols:
            continue
        kind, key = descriptor(child)
        collections_out.append({"group": group, "kind": kind, "key": key, "name": child.name})

asset_counts = {}
for o in bpy.data.objects:
    kind, key = descriptor(o)
    if kind == "asset":
        k = key[0] + "|" + key[1]
        asset_counts[k] = asset_counts.get(k, 0) + 1
    groups, delta_raw, flag = raw_settings(o)
    delta = None
    if delta_raw:
        try:
            d = json.loads(delta_raw)
            if d.get("added") or d.get("removed"):
                delta = {"added": list(d.get("added", [])), "removed": list(d.get("removed", []))}
        except Exception:
            delta = None
    if (delta or flag) and o not in content_objs:
        r = rec(o)
        r["customised"] = True
        r["props_groups"] = groups
        r["delta"] = delta

# Custom properties of the view layer the file was saved with as active (a background Blender has
# no window of its own, but the saved one is still there). Only plain values named lgt*.
def active_view_layer():
    try:
        vl = bpy.data.window_managers[0].windows[0].view_layer
        if vl is not None:
            return vl
    except Exception:
        pass
    return bpy.context.view_layer

view_layer_props, view_layer_skipped = [], []
vl = active_view_layer()
for k in vl.keys():
    if k.startswith("_") or k == "cycles":  # Blender's own data
        continue
    if not k.startswith("lgt"):
        view_layer_skipped.append(k)
        continue
    v = vl[k]
    if hasattr(v, "to_list"):
        v = v.to_list()
        if not all(isinstance(x, (int, float, bool)) for x in v):
            view_layer_skipped.append(k)
            continue
    elif not isinstance(v, (int, float, str, bool)):
        view_layer_skipped.append(k)  # nested groups, datablock pointers
        continue
    try:
        ui = vl.id_properties_ui(k).as_dict()
    except Exception:
        ui = None
    try:
        overridable = vl.is_property_overridable_library('["%s"]' % k)
    except Exception:
        overridable = False
    view_layer_props.append({"name": k, "value": v, "ui": ui, "overridable": overridable})

with open(out_json, "w", encoding="utf-8") as f:
    json.dump({"objects": list(records.values()), "collections": collections_out,
               "asset_counts": asset_counts, "skipped_overrides": skipped,
               "view_layer": {"name": vl.name, "props": view_layer_props, "skipped": view_layer_skipped}}, f, default=str)

# Strip to lights, the +LGT+ content and LL-/SL- groups (which keep only +LGT+ content members).
# Deliberately one ID at a time: batch_remove and the recursive purge crashed Blender 4.5 on
# Windows (BKE_idtype_index_to_idfilter).
for col in bpy.data.collections:
    if col.name.startswith(GROUP_PREFIXES) and not col.library:
        for o in list(col.objects):
            if o not in content_objs:
                col.objects.unlink(o)
        for child in list(col.children):
            if child not in content_cols:
                col.children.unlink(child)
        col.use_fake_user = True

scene = bpy.context.scene
container = source_container
if container is None:
    container = bpy.data.collections.new("+LGT+")
if container.name not in scene.collection.children:
    scene.collection.children.link(container)
# Collections a content object instances (local or linked, e.g. MOD-BACKGROUND_SPACE) must survive
# too, with their objects - otherwise the instancer comes over empty.
keep_cols, keep_objs = set(content_cols), set(content_objs)
for o in content_objs:
    ic = o.instance_collection
    if ic is not None:
        keep_cols.add(ic)
        keep_cols.update(ic.children_recursive)
        keep_objs.update(ic.all_objects)
for o in list(bpy.data.objects):
    if o in keep_objs:
        continue
    if o.type in {'LIGHT', 'LIGHT_PROBE'} and not o.library:
        if o.name not in container.objects:
            try:
                container.objects.link(o)
            except Exception:
                pass
    else:
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:
            pass

for col in list(bpy.data.collections):
    if col == container or col in keep_cols or col.name.startswith(GROUP_PREFIXES):
        continue
    try:
        bpy.data.collections.remove(col)
    except Exception:
        pass

# The copy lives in a temp folder: relative library paths would point nowhere from there.
for lib in bpy.data.libraries:
    try:
        lib.filepath = bpy.path.abspath(lib.filepath)
    except Exception:
        pass
bpy.ops.wm.save_as_mainfile(filepath=out_blend, copy=True, relative_remap=False)
print("KRUTART_EXTRACT_OK")
"""

def extract_source_light_data(src_path, timeout=900):
    """
    Runs a second Blender on src_path. Returns (source dict, path of a stripped .blend with only its
    lights and empty LL- groups, temp directory to clean up); the first two are None on failure.
    """
    tempdir = tempfile.mkdtemp(prefix="krutart_ll_")
    script_path = os.path.join(tempdir, "extract.py")
    json_path = os.path.join(tempdir, "light_data.json")
    blend_path = os.path.join(tempdir, "lights_only.blend")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(SOURCE_EXTRACTOR)

    cmd = [bpy.app.binary_path, "-b", "--factory-startup", src_path,
           "--python", script_path, "--", json_path, blend_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        log.warning(f"Could not run the source reader: {e}")
        return None, None, tempdir
    if "KRUTART_EXTRACT_OK" not in (proc.stdout or "") or not os.path.exists(json_path) or not os.path.exists(blend_path):
        log.warning(f"Source reader failed (exit {getattr(proc, 'returncode', '?')}): "
                    f"{(proc.stdout or '')[-400:]} {(proc.stderr or '')[-400:]}")
        return None, None, tempdir

    try:
        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log.warning(f"Could not read the extracted light data: {e}")
        return None, None, tempdir

    source = {"objects": [], "collections": raw.get("collections", []), "asset_counts": {},
              "skipped_overrides": raw.get("skipped_overrides", []),
              "view_layer": raw.get("view_layer") or {}}
    for key, count in raw.get("asset_counts", {}).items():
        name, _, lib = key.partition("|")
        source["asset_counts"][(name, lib)] = count
    for entry in raw.get("objects", []):
        entry["key"] = entry["key"][0] if entry["kind"] == "local" else tuple(entry["key"])
        entry["position"] = Vector(entry["position"])
        entry["groups"] = set(entry.get("groups") or ())
        source["objects"].append(entry)
    for entry in source["collections"]:
        entry["key"] = entry["key"][0] if entry["kind"] == "local" else tuple(entry["key"])
    return source, blend_path, tempdir

def _same_file(a, b):
    try:
        return os.path.normcase(os.path.abspath(bpy.path.abspath(a))) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False

def _source_descriptor(idb, src_libs):
    """
    How to find a source datablock again in this file:
      ('asset', (name, file))  - a library override: the asset datablock it overrides
      ('linked', (name, file)) - data the source linked straight from an asset library
      ('local', name)          - the source file's own data
    """
    ol = idb.override_library
    if ol and ol.reference:
        ref = ol.reference
        return "asset", (ref.name, bpy.path.basename(ref.library.filepath).lower() if ref.library else "")
    if idb.library and idb.library not in src_libs:
        return "linked", (idb.name, bpy.path.basename(idb.library.filepath).lower())
    return "local", idb.name

def read_source_light_assignments(src_path):  # unused since 2.5.7 - the extractor above replaced it
    """
    Reads everything in the source file that puts datablocks into LL- groups:
      - LL- collection membership: objects of any kind and nested collections. This is what light
        linking renders from, and the only record of members added outside the panel or of linked
        objects, which cannot hold per-object settings.
      - per-object settings of customised objects, which also carry explicit removals.
    Every entry carries what is needed to find the same datablock in this file. A throwaway LINK
    pass (linked data keeps exact source names and state) that removes everything it brought in.
    """
    before = _id_snapshot()
    libs_before = set(bpy.data.libraries)
    result = {"objects": [], "collections": [], "asset_counts": {}}
    try:
        with bpy.data.libraries.load(src_path, link=True) as (data_from, data_to):
            data_to.objects = list(data_from.objects)
            data_to.collections = [n for n in data_from.collections if is_light_group_name(n)]
        src_libs = {lib for lib in bpy.data.libraries if _same_file(lib.filepath, src_path)}
        records = {}

        def record(obj):
            rec = records.get(obj.as_pointer())
            if rec is None:
                kind, key = _source_descriptor(obj, src_libs)
                rec = {"kind": kind, "key": key, "name": obj.name, "position": object_world_position(obj),
                       "groups": set(), "props_groups": None, "delta": None, "customised": False}
                records[obj.as_pointer()] = rec
            return rec

        for col in data_to.collections:
            if not col:
                continue
            group = col.name.split('.')[0]
            for obj in col.objects:
                record(obj)["groups"].add(group)
            for child in col.children:
                kind, key = _source_descriptor(child, src_libs)
                result["collections"].append({"group": group, "kind": kind, "key": key, "name": child.name})

        for obj in data_to.objects:
            if not obj:
                continue
            kind, key = _source_descriptor(obj, src_libs)
            if kind == "asset":
                result["asset_counts"][key] = result["asset_counts"].get(key, 0) + 1
            settings = getattr(obj, "krutart_light_link", None)
            if settings is None:
                continue
            delta = get_object_delta(obj)
            has_delta = bool(delta["added"] or delta["removed"])
            if not (has_delta or settings.is_override_active):
                continue
            rec = record(obj)
            rec["customised"] = True
            rec["props_groups"] = get_groups(settings)
            rec["delta"] = {"added": list(delta["added"]), "removed": list(delta["removed"])} if has_delta else None
        result["objects"] = list(records.values())
    except Exception as e:
        log.warning(f"Could not read light group assignments from '{src_path}': {e}")
    finally:
        new_ids = _id_snapshot() - before
        if new_ids:
            try:
                bpy.data.batch_remove(list(new_ids))
            except Exception as e:
                log.warning(f"Could not clean up the assignment read pass: {e}")
        remove_orphan_libraries(libs_before)
    return result

def _resolve_collection(entry, eligible_cols):
    """The collection in this file that corresponds to a nested source collection, or None."""
    cands = []
    for col in bpy.data.collections:
        if col not in eligible_cols or is_light_group_name(col.name) or col.name == LGT_CONTAINER_NAME:
            continue
        if entry["kind"] == "asset":
            if asset_identity(col) == entry["key"]:
                cands.append(col)
        elif entry["kind"] == "linked":
            if col.library and not col.override_library and (col.name, bpy.path.basename(col.library.filepath).lower()) == entry["key"]:
                cands.append(col)
        elif not col.library and not col.override_library and col.name == entry["key"]:
            cands.append(col)
    return cands[0] if len(cands) == 1 else (None if not cands else "ambiguous")

def build_target_index(scene, eligible_objs):
    """Lookup tables for matching: by overridden asset, by local name, linked by (name, library), and by any name."""
    index = {"by_asset": {}, "by_name": {}, "linked": {}, "any_name": {}}
    for obj in scene.objects:
        if obj not in eligible_objs:
            continue
        index["any_name"].setdefault(obj.name, []).append(obj)
        if obj.library and not obj.override_library:
            index["linked"][(obj.name, bpy.path.basename(obj.library.filepath).lower())] = obj
            continue
        ident = asset_identity(obj)
        if ident:
            index["by_asset"].setdefault(ident, []).append(obj)
        elif not obj.library:
            index["by_name"].setdefault(obj.name, []).append(obj)
    return index

def match_source_entry(entry, index, source, used):
    """
    Finds the datablock in this file that corresponds to one source entry.
    Rules in order: the overridden asset (plus position when that asset appears more than once),
    the linked original, then the name - the last resort for files that reach the same objects
    through another chain, e.g. a shot's ANI hero. Returns (object or None, rule, reason).
    """
    def at_position(cands):
        return [c for c in cands if (object_world_position(c) - entry["position"]).length <= POSITION_TOLERANCE]

    if entry["kind"] == "local":
        rule = "name+position"
        cands = at_position(index["by_name"].get(entry["name"], []))
    else:
        rule = "asset"
        cands = index["by_asset"].get(entry["key"], [])
        if len(cands) > 1 or source["asset_counts"].get(entry["key"], 0) > 1:
            cands = at_position(cands)
            rule = "asset+position"
        if not cands and entry["key"] in index["linked"]:
            cands, rule = [index["linked"][entry["key"]]], "linked asset"
    cands = [c for c in cands if c.as_pointer() not in used]

    if not cands:
        # Name fallback, for overrides and local objects alike.
        names = {entry["name"]}
        if isinstance(entry["key"], (list, tuple)) and entry["key"]:
            names.add(entry["key"][0])
        pool = []
        for name in names:
            for obj in index["any_name"].get(name, ()):
                if obj.as_pointer() not in used and all(obj != c for c in pool):
                    pool.append(obj)
        rule = "name"
        if len(pool) > 1:
            narrowed = at_position(pool)
            if not narrowed:
                return None, None, f"{len(pool)} objects share this name, none at the same position"
            pool = narrowed
        cands = pool

    if not cands:
        return None, None, "no object with this asset or name in the scene"
    if len(cands) > 1:
        return None, None, f"{len(cands)} candidates, none unique at this position"
    return cands[0], rule, ""

def apply_source_assignments(source, scene, eligible_objs, eligible_cols):
    """
    Puts the same datablocks into the same LL- groups in this file.
    Matching:
      - override: same asset datablock + asset file; when either file holds that asset more than
        once, also the same position
      - data the source linked from an asset: this file's override of that same linked object,
        or the linked object itself when it is in the scene
      - local object: same name AND same position
      - nested collection: same asset / linked collection / local name
    Editable objects get the groups written into their settings, so the panel shows them and later
    syncs keep them; read-only (linked) objects only gain membership. Explicit removals travel only
    from objects customised in the source. Unmatched and ambiguous datablocks are reported, never guessed.
    """
    report = {"applied": 0, "membership_only": 0, "collections": 0, "by_asset": 0, "by_name": 0,
              "missing": [], "ambiguous": []}
    index = build_target_index(scene, eligible_objs)
    used = set()
    for entry in source["objects"]:
        src_groups = {g for g in set(entry["groups"]) | set(entry["props_groups"] or []) if is_light_group_name(g)}
        if not src_groups and not entry["customised"]:
            continue
        obj, rule, reason = match_source_entry(entry, index, source, used)
        if obj is None:
            bucket = "ambiguous" if ("candidates" in reason or "share this name" in reason) else "missing"
            report[bucket].append(entry["name"])
            continue
        used.add(obj.as_pointer())
        report["by_name" if rule.startswith("name") else "by_asset"] += 1

        if obj.library and not obj.override_library:
            # Every group, not just the first: any() short-circuits and would drop the rest.
            linked_any = False
            for group in sorted(src_groups):
                if link_to_group(obj, group):
                    linked_any = True
            if linked_any:
                report["membership_only"] += 1
            continue

        settings = obj.krutart_light_link
        current = get_groups(settings)
        if not entry["customised"]:
            missing = [g for g in sorted(src_groups) if g not in current]
            if not missing:
                continue
            set_groups(settings, current + missing)
            sync_delta_from_ui(obj)
            report["applied"] += 1
            continue

        base = get_base_defaults(obj)
        src_added = [g for g in sorted(src_groups) if g not in base]
        src_removed = [g for g in base if g not in src_groups]
        cur = get_object_delta(obj)
        added = [g for g in cur["added"] if g not in src_removed]
        added += [g for g in src_added if g not in added]
        removed = [g for g in cur["removed"] if g not in src_added]
        removed += [g for g in src_removed if g not in removed]
        groups = [g for g in base if g not in removed]
        groups += [g for g in added if g not in groups]
        if groups != current:
            set_groups(settings, groups)
            sync_delta_from_ui(obj)
            report["applied"] += 1

    for entry in source["collections"]:
        target = _resolve_collection(entry, eligible_cols)
        if target == "ambiguous":
            report["ambiguous"].append(entry["name"])
        elif target is None:
            report["missing"].append(entry["name"])
        elif link_to_group(target, entry["group"]):
            report["collections"] += 1

    log.info(f"Light group transfer: matched {report['by_asset']} by asset, {report['by_name']} by name; "
             f"{report['applied']} objects updated, {report['membership_only']} linked, {report['collections']} collections.")
    for key, label in (("missing", "not found in this file"), ("ambiguous", "ambiguous")):
        if report[key]:
            log.info(f"Light group transfer: {len(report[key])} {label}, e.g. {report[key][:5]}")
    return report

# Names of the LL- collections in the hero LLCOL library, read from disk without appending.
# None until first read; refreshed on every file load.
_hero_llcol_names = None

def get_llcol_names(channel=None):
    """LLCOL section of the Add Group menu: hero library names plus the built-in standard list."""
    channel = channel or LIGHT
    return sorted({n for n in set(channel.standard) | set(_hero_llcol_names or ())
                   if is_light_group_name(n, channel)})

def group_icon(name):
    """LLCOL groups show the bulb, custom groups the collection icon."""
    return 'LIGHT' if name in get_llcol_names(group_channel(name)) else 'OUTLINER_COLLECTION'

# Google Drive can leave the hero file as an online-only placeholder; reading it then blocks until
# it is downloaded. Load handlers run on Blender's main thread, so that read must never happen there.
_hero_ready = threading.Event()
_hero_thread = None
_hero_retry_count = 0

def _hero_file_ready(src_path, wait=1.5):
    """True once the hero file is fully readable. Downloads it on a background thread; blocks the UI at most `wait` seconds."""
    global _hero_thread
    if _hero_ready.is_set():
        return True
    if _hero_thread is None or not _hero_thread.is_alive():
        def _read():
            try:
                with open(src_path, 'rb') as f:
                    while f.read(1 << 20):
                        pass
                _hero_ready.set()
            except Exception as e:
                log.warning(f"Could not read hero LLCOL library '{src_path}': {e}")
        _hero_thread = threading.Thread(target=_read, daemon=True)
        _hero_thread.start()
    return _hero_ready.wait(wait)

def _retry_hero_llcols():
    """Timer: once the hero file has finished downloading, top up the LLCOL groups. Gives up after ~5 minutes."""
    global _hero_retry_count
    _hero_retry_count += 1
    if _hero_ready.is_set():
        try:
            ensure_light_groups_initialized(force=True)
        except Exception as e:
            log.warning(f"Could not top up hero LLCOL groups: {e}")
        return None
    return 5.0 if _hero_retry_count < 60 else None

def _schedule_hero_retry():
    global _hero_retry_count
    if not bpy.app.timers.is_registered(_retry_hero_llcols):
        _hero_retry_count = 0
        bpy.app.timers.register(_retry_hero_llcols, first_interval=5.0)

def append_missing_hero_llcols(refresh=False):
    """
    Appends every LL- collection of the hero LLCOL library that the current file does not have yet.
    Only bpy.data is touched - nothing is linked into a scene. Also refreshes the cached name list.
    """
    global _hero_llcol_names
    src_path = resolve_krutart_path(DEFAULT_HERO_LGT_PATH)
    if not src_path or not os.path.exists(src_path):
        if _hero_llcol_names is None:
            _hero_llcol_names = []
        return 0

    if bpy.data.filepath and os.path.normcase(os.path.abspath(bpy.data.filepath)) == os.path.normcase(os.path.abspath(src_path)):
        _hero_llcol_names = sorted(get_group_pool_names())
        return 0

    if not _hero_file_ready(src_path):
        log.info("Hero LLCOL library is still downloading; using the standard groups until it is available.")
        if _hero_llcol_names is None:
            _hero_llcol_names = []
        _schedule_hero_retry()
        return 0

    appended = 0
    libs_before = set(bpy.data.libraries)
    try:
        with bpy.data.libraries.load(src_path, link=False) as (data_from, data_to):
            hero_names = [n for n in data_from.collections if is_light_group_name(n)]
            missing = [n for n in hero_names if n not in bpy.data.collections]
            data_to.collections = missing
        _hero_llcol_names = sorted(hero_names)
        for col in data_to.collections:
            if col:
                col.use_fake_user = True
                appended += 1
    except Exception as e:
        log.warning(f"Could not read hero LLCOL library '{src_path}': {e}")
        if _hero_llcol_names is None:
            _hero_llcol_names = []
    remove_orphan_libraries(libs_before)
    return appended

def apply_view_layer_props(view_layer, props):
    """
    Writes the source view layer's lgt* custom properties onto view_layer: value, UI settings and
    library-override flag. A property the target already has takes the source value (replaced when
    the type differs); everything else on the view layer is left alone. Returns the names written.
    """
    written = []
    for p in props or ():
        name = p.get("name") or ""
        if not name.startswith(VIEW_LAYER_PROP_PREFIX):
            continue
        try:
            if name in view_layer.keys():
                del view_layer[name]
            view_layer[name] = p.get("value")
        except Exception as e:
            log.warning(f"View layer property '{name}' not copied: {e}")
            continue
        ui = dict(p.get("ui") or {})
        if ui:
            try:
                view_layer.id_properties_ui(name).update(**ui)
            except Exception:
                ui.pop("subtype", None)
                try:
                    view_layer.id_properties_ui(name).update(**ui)
                except Exception as e:
                    log.warning(f"View layer property '{name}': settings not copied: {e}")
        try:
            view_layer.property_overridable_library_set(f'["{name}"]', bool(p.get("overridable")))
        except Exception:
            pass
        written.append(name)
    return written

def place_lgt_container(scene, container):
    """
    Puts +LGT+ inside the scene's +ART+ collection and nowhere else. +ART+ is never created: without
    one (or when it is linked/overridden) +LGT+ goes to the scene root. Returns the parent used.
    """
    art = next((c for c in scene.collection.children_recursive if c.name == ART_CONTAINER_NAME), None)
    if art is not None and (art.library or art.override_library):
        log.warning(f"{ART_CONTAINER_NAME} is linked/overridden here; {container.name} goes to the scene root")
        art = None
    parent = art if art is not None else scene.collection
    if container.name not in parent.children:
        parent.children.link(container)
    others = [scene.collection] + [c for c in bpy.data.collections if not c.library and not c.override_library]
    for other in others:
        if other is parent:
            continue
        if container.name in other.children and other.children[container.name] == container:
            try:
                other.children.unlink(container)
            except Exception:
                pass
    return parent

def purge_light_groups_from_outliner():
    """
    Unlinks every LL- light group collection from all scenes and from any parent collection.
    The groups stay in the file as fake-user datablocks so they remain selectable in the light
    linking pool, but only the +LGT+ container is ever visible in the outliner.
    """
    targets = {c for c in bpy.data.collections if is_light_group_name(c.name)}
    if not targets:
        return

    for scene in bpy.data.scenes:
        for child in list(scene.collection.children):
            if child in targets:
                try:
                    scene.collection.children.unlink(child)
                except Exception:
                    pass

    for parent in bpy.data.collections:
        if parent in targets:
            continue
        for child in list(parent.children):
            if child in targets:
                try:
                    parent.children.unlink(child)
                except Exception:
                    pass

    for col in targets:
        col.use_fake_user = True

_ll_groups_ready = False

def ensure_light_groups_initialized(scene=None, view_layer=None, force=False):
    """
    Ensures every LLCOL group (hero library + the 14 standard names) exists as a protected
    (fake-user) collection datablock, registers the whole LL- pool - custom groups included - in
    view_layer.lightgroups, then keeps all of them out of the outliner.

    Cached behind _ll_groups_ready so the per-object sync path stays cheap; pass force=True
    after a file load, a scene change or an append to rebuild unconditionally.
    """
    global _ll_groups_ready
    if _ll_groups_ready and not force:
        return

    if not view_layer and hasattr(bpy.context, "view_layer"):
        view_layer = bpy.context.view_layer

    append_missing_hero_llcols()

    if hasattr(bpy.data, "collections"):
        for g_name in STANDARD_LIGHT_GROUPS:
            col = bpy.data.collections.get(g_name)
            if not col:
                try:
                    col = bpy.data.collections.new(g_name)
                except Exception:
                    pass
            if col:
                col.use_fake_user = True

    # view_layer.lightgroups is a read-only collection - the only way to add an entry is the
    # scene operator, which acts on the view layer in the given context.
    #
    # TODO(future): this registers every LL- group as a View Layer lightgroup in EVERY file opened
    # with the add-on (ANI, layout, ...), not only lighting files. In Cycles each lightgroup is a
    # render pass, so those files also carry ~15 extra passes (EXR size, compositor setup).
    # Decide whether to gate this on lighting files (e.g. a +LGT+ container being present) or to
    # move lightgroup registration back to the manual "Init Light Groups" dev tool.
    if view_layer and hasattr(view_layer, "lightgroups"):
        if not scene:
            scene = getattr(bpy.context, "scene", None)
        existing = {lg.name for lg in view_layer.lightgroups}
        missing = sorted(get_group_pool_names(LIGHT) - existing)  # SL- groups are never render passes
        if missing:
            try:
                with bpy.context.temp_override(scene=scene, view_layer=view_layer):
                    for g_name in missing:
                        bpy.ops.scene.view_layer_add_lightgroup(name=g_name)
            except Exception as e:
                log.warning(f"Could not register view layer lightgroups: {e}")

    purge_light_groups_from_outliner()
    _ll_groups_ready = True

def sync_light_collection_membership(obj, assigned_groups):
    """
    Silently links/unlinks obj to/from every LL- group collection in the pool (LLCOL and custom).
    Assigned LL- groups without a collection yet get one, created as a fake-user datablock outside
    the outliner. Non LL- names in the list stay list-only, as before.
    """
    if not obj or not hasattr(bpy.data, "collections"):
        return
        
    ensure_light_groups_initialized()

    assigned_ll = {g for g in assigned_groups if is_light_group_name(g)}
    # Membership by identity, never by name: a scene override and the linked original it overrides
    # share the same name, and name checks mistook one for the other.
    member_of = {c.as_pointer() for c in obj.users_collection}
    created = False
    for group_name in sorted(get_group_pool_names() | assigned_ll):
        col = bpy.data.collections.get(group_name)
        if not col and group_name in assigned_ll:
            col = bpy.data.collections.new(group_name)
            created = True
            
        if col:
            col.use_fake_user = True
            
            if group_name in assigned_groups:
                if col.as_pointer() not in member_of:
                    try:
                        col.objects.link(obj)
                    except Exception:
                        pass
            else:
                if col.as_pointer() in member_of:
                    try:
                        col.objects.unlink(obj)
                    except Exception:
                        pass

    if created:
        ensure_light_groups_initialized(force=True)

# --- Data Management ---

class LibraryDataManager:
    """Manages reading and writing the internal JSON text block."""

    @staticmethod
    def load_data_from_text_block(text_block):
        """Helper to safely load JSON from a text block object."""
        if not text_block: return {}
        try:
            return json.loads(text_block.as_string())
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def load_local_data():
        """Loads data from the CURRENT file's text block."""
        return LibraryDataManager.load_data_from_text_block(bpy.data.texts.get(DATA_BLOCK_NAME))

    @staticmethod
    def save_local_data(data):
        """Saves dictionary data to the CURRENT file's text block."""
        text_block = bpy.data.texts.get(DATA_BLOCK_NAME)
        if not text_block:
            text_block = bpy.data.texts.new(DATA_BLOCK_NAME)
        
        try:
            json_str = json.dumps(data, indent=2)
            text_block.clear()
            text_block.write(json_str)
        except Exception as e:
            log.error(f"Failed to save data: {e}")

    @staticmethod
    def get_local_cache_defaults(asset_name, base_asset_name):
        """Loads defaults for asset_name from the current file's local text block cache with ASL / PROXY / MASTER fallbacks."""
        local_json = LibraryDataManager.load_local_data()
        if not local_json:
            return None
            
        asset_version = get_asset_version(asset_name)
        
        # Test base_asset_name with and without LOD suffix
        base_with_lod = get_base_asset_name(asset_name, strip_lod=False).upper()
        base_without_lod = get_base_asset_name(asset_name, strip_lod=True).upper()
        asset_upper = asset_name.upper()

        test_keys = []
        for k in (base_with_lod, base_without_lod, asset_upper):
            if k and k not in test_keys:
                test_keys.append(k)

        # 1. Check unified ASL sheet
        if "ASL" in local_json:
            asl_defaults = local_json.get("ASL", {})
            for tk in test_keys:
                val = asl_defaults.get(tk)
                if val:
                    return val

        # 2. Check legacy PROXY / MASTER sheets
        if "PROXY" in local_json and "MASTER" in local_json:
            proxy_defaults = local_json.get("PROXY", {})
            master_defaults = local_json.get("MASTER", {})
            
            primary = master_defaults if asset_version == "MASTER" else proxy_defaults
            secondary = proxy_defaults if asset_version == "MASTER" else master_defaults
            
            for tk in test_keys:
                val = primary.get(tk)
                if val:
                    return val
                    
            for tk in test_keys:
                val = secondary.get(tk)
                if val:
                    return val
                    
            for tk in test_keys:
                if tk in primary: return primary.get(tk)
                if tk in secondary: return secondary.get(tk)
        else:
            target_defaults = local_json
            for tk in test_keys:
                if tk in target_defaults:
                    return target_defaults[tk]
            
        return None

# --- Google Sheets Ingestor ---

class GoogleSheetsIngestor:
    """Fetches light linking defaults from Google Sheets and parses them, automatically finding the header row."""

    @staticmethod
    def get_urls_for_preproduction(base_url):
        """
        Returns target CSV URL for the unified ASL sheet.
        """
        if "gid=" in base_url:
            return {"ASL": base_url}
            
        match = re.search(r'/d/([a-zA-Z0-9-_]+)', base_url)
        if match:
            spreadsheet_id = match.group(1)
            return {"ASL": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid=1343757578"}
            
        return {"ASL": base_url}

    @staticmethod
    def fetch_defaults(url):
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read().decode('utf-8')
        except Exception as e:
            raise ConnectionError(f"Failed to fetch spreadsheet: {e}")

        f_raw = io.StringIO(data)
        raw_reader = csv.reader(f_raw)
        raw_rows = list(raw_reader)
        
        if not raw_rows:
            raise ValueError("Spreadsheet data is empty.")

        header_index = 0
        for idx, row in enumerate(raw_rows):
            row_upper = [cell.strip().upper() for cell in row]
            if "COLLECTION" in row_upper or "LIGHT LINK" in row_upper:
                header_index = idx
                break
                
        header_row = raw_rows[header_index]
        data_rows = raw_rows[header_index + 1:]
        headers_clean = [h.strip() for h in header_row]
        
        col_key_idx = None
        ll_key_idx = None
        sl_key_idx = None
        
        for idx, h in enumerate(headers_clean):
            h_upper = h.upper()
            if h_upper == "COLLECTION":
                col_key_idx = idx
            elif h_upper == LIGHT.sheet_header:
                ll_key_idx = idx
            elif h_upper == SHADOW.sheet_header:
                sl_key_idx = idx

        if col_key_idx is None:
            for idx, h in enumerate(headers_clean):
                h_upper = h.upper()
                if h_upper in ("NAME", "ASSET", "ASSET NAME", "GEO"):
                    col_key_idx = idx
                    break
            if col_key_idx is None:
                col_key_idx = 0

        if ll_key_idx is None:
            for idx, h in enumerate(headers_clean):
                h_upper = h.upper()
                if "LIGHT" in h_upper:
                    ll_key_idx = idx
                    break

        if ll_key_idx is None:
            return {}

        defaults = {}
        for row in data_rows:
            if len(row) > col_key_idx:
                asset = row[col_key_idx]
                col_val = row[col_key_idx].strip() if len(row) > col_key_idx else ""
                ll_val = row[ll_key_idx].strip() if (ll_key_idx is not None and len(row) > ll_key_idx) else ""
                sl_val = row[sl_key_idx].strip() if (sl_key_idx is not None and len(row) > sl_key_idx) else ""
                
                if col_val:
                    asset_key = col_val.upper()
                    groups = [g.strip() for g in ll_val.split(',') if g.strip()]
                    # SHADOW LINK is sparse and defaults to nothing; its names always carry SL-.
                    for g in sl_val.split(','):
                        g = g.strip()
                        if not g or g.upper() in ("NONE", "-"):
                            continue
                        g = g if g.startswith(SHADOW.prefix) else normalize_group_name(g, SHADOW)
                        if g and g not in groups:
                            groups.append(g)
                    key_with_lod = get_base_asset_name(asset_key, strip_lod=False).upper()
                    key_without_lod = get_base_asset_name(asset_key, strip_lod=True).upper()
                    
                    defaults[key_with_lod] = groups
                    defaults[key_without_lod] = groups
                    
        return defaults

# --- Async Background Fetch & Auto-Sync Engine ---

_async_fetch_thread = None
AUTO_FETCH_TIMER_DELAY = 15.0

def sync_scene_objects_from_defaults(scene, asl_defaults):
    """Efficiently syncs all objects in scene against asl_defaults in batch mode."""
    if not scene or not asl_defaults:
        return 0
    updated_count = 0
    log.info("--- [Krutart Light Link] Async Scene Objects Sync Started ---")
    ensure_light_groups_initialized(scene, force=True)
    for obj in scene.objects:
        if hasattr(obj, "krutart_light_link"):
            candidates = get_candidate_asset_names(obj)
            match_found = False
            matched_key = ""
            for cand in candidates:
                base_with_lod = get_base_asset_name(cand, strip_lod=False).upper()
                base_without_lod = get_base_asset_name(cand, strip_lod=True).upper()
                cand_upper = cand.upper()
                
                for test_key in (base_with_lod, base_without_lod, cand_upper):
                    if test_key and test_key in asl_defaults:
                        match_found = True
                        matched_key = test_key
                        break
                if match_found:
                    break
                    
            if match_found:
                groups = asl_defaults.get(matched_key, [])
                settings = obj.krutart_light_link
                if obj.library:
                    # Read-only: can only gain membership.
                    for g in groups:
                        if is_light_group_name(g):
                            link_to_group(obj, g)
                    continue
                adopt_native_memberships(obj)
                delta = get_object_delta(obj)
                
                # Customised objects keep their groups. is_override_active also protects files saved
                # before 2.5.3, whose deltas on library overrides were lost on save.
                if not delta["added"] and not delta["removed"] and not settings.is_override_active:
                    set_groups(settings, groups)
                    settings.resolved_source = 'ASSET'
                    sync_light_collection_membership(obj, groups)
                    updated_count += 1

    log.info(f"--- [Krutart Light Link] Async Scene Sync Complete ({updated_count} objects updated) ---")
    return updated_count

def apply_fetched_sheet_defaults(asl_defaults, error_msg):
    """Callback executed safely on Blender's main thread after background fetch finishes."""
    if error_msg or not asl_defaults:
        log.warning(f"Could not update sheet defaults: {error_msg}")
        return
        
    if not is_art_file():
        log.info("No ART flag in the file name. Fetched sheet defaults are not written into this file.")
        return

    combined_defaults = {"ASL": asl_defaults}
    LibraryDataManager.save_local_data(combined_defaults)
    log.info(f"Saved {len(asl_defaults)} ASL defaults to local cache via background sync.")
    
    try:
        if bpy.context and hasattr(bpy.context, "scene") and bpy.context.scene:
            sync_scene_objects_from_defaults(bpy.context.scene, asl_defaults)
    except Exception as e:
        log.warning(f"Could not sync scene objects after background fetch: {e}")

def fetch_sheets_in_background(url, on_complete_callback):
    """Target function for background thread. Fetches spreadsheet CSV safely without freezing Blender UI."""
    try:
        urls = GoogleSheetsIngestor.get_urls_for_preproduction(url)
        asl_url = urls.get("ASL", url)
        log.info(f"Background thread fetching unified ASL defaults from: {asl_url}")
        asl_defaults = GoogleSheetsIngestor.fetch_defaults(asl_url)
        
        def main_thread_callback():
            on_complete_callback(asl_defaults, None)
            return None # Run once for timer

        bpy.app.timers.register(main_thread_callback, first_interval=0.0)
    except Exception as e:
        log.warning(f"Background sheet fetch encountered an error: {e}")
        def main_thread_error():
            on_complete_callback(None, str(e))
            return None
        bpy.app.timers.register(main_thread_error, first_interval=0.0)

def _auto_fetch_timer():
    """Timer callback triggered 15.0 seconds after Blender start, add-on registration or file load."""
    prefs = get_prefs()
    if prefs and not prefs.auto_sync:
        log.info("Automatic sync disabled in preferences. Skipping auto-fetch.")
        return None

    if not is_art_file():
        log.info("No ART flag in the file name. Skipping sheet auto-fetch.")
        return None

    log.info(f"Auto-sync timer fired ({AUTO_FETCH_TIMER_DELAY}s delay). Initiating background sheet fetch...")
    global _async_fetch_thread
    if _async_fetch_thread and _async_fetch_thread.is_alive():
        log.info("Background sheet fetch thread already running. Skipping auto-fetch.")
        return None
        
    url = DEFAULT_SHEET_URL
    try:
        if bpy.context and hasattr(bpy.context, "preferences") and bpy.context.preferences:
            addon_prefs = bpy.context.preferences.addons.get(__name__)
            if addon_prefs and hasattr(addon_prefs, "preferences"):
                pref_url = getattr(addon_prefs.preferences, "sheet_url", "")
                if pref_url:
                    url = pref_url
    except Exception:
        pass

    _async_fetch_thread = threading.Thread(
        target=fetch_sheets_in_background,
        args=(url, apply_fetched_sheet_defaults),
        daemon=True
    )
    _async_fetch_thread.start()
    return None # Run timer once

def schedule_auto_fetch():
    """Schedules the 15-second delayed background auto-fetch timer."""
    if not bpy.app.timers.is_registered(_auto_fetch_timer):
        bpy.app.timers.register(_auto_fetch_timer, first_interval=AUTO_FETCH_TIMER_DELAY)

def link_to_group(datablock, group_name):
    """Adds an object or collection to the canonical LL- group collection (created if missing). Never removes anything."""
    col = bpy.data.collections.get(group_name)
    if col is None:
        col = bpy.data.collections.new(group_name)
    col.use_fake_user = True
    try:
        if isinstance(datablock, bpy.types.Collection):
            if datablock == col or any(ch == datablock for ch in col.children) or col in datablock.children_recursive:
                return False
            col.children.link(datablock)
        else:
            if any(o == datablock for o in col.objects):
                return False
            col.objects.link(datablock)
        return True
    except Exception:
        return False

def current_ll_memberships(obj, channel=None):
    """Canonical link groups (LL- and SL-, or one channel) whose collection currently contains obj."""
    return {c.name for c in obj.users_collection
            if is_light_group_name(c.name, channel) and c.name == c.name.split('.')[0]}

def adopt_native_memberships(obj):
    """
    An editable object that sits in an LL- collection its group list does not mention was put there
    outside the panel (Blender's own light linking UI, drag and drop). Write that group into its list
    so later syncs keep the membership instead of stripping it. Returns True when something changed.
    """
    if obj.library:
        return False
    settings = getattr(obj, "krutart_light_link", None)
    if settings is None:
        return False
    groups = get_groups(settings)
    extra = [g for g in sorted(current_ll_memberships(obj)) if g not in groups]
    if not extra:
        return False
    set_groups(settings, groups + extra)
    sync_delta_from_ui(obj)
    return True

def sync_existing_assignments(scene):
    """
    Load-time pass over every object:
      - adopts memberships added outside the panel (see adopt_native_memberships)
      - rebuilds the delta of objects flagged as customised whose delta was lost (library overrides
        saved before 2.5.3)
      - re-applies each object's group list to the LL- collections, creating collections for groups
        that have none yet (e.g. custom groups saved by versions that never created them)
    Linked objects are read-only: they only gain membership, never lose it.
    """
    if not scene:
        return 0
    count = 0
    for obj in scene.objects:
        settings = getattr(obj, "krutart_light_link", None)
        if settings is None:
            continue
        if obj.library:
            for g in get_groups(settings):
                if is_light_group_name(g):
                    link_to_group(obj, g)
            continue
        try:
            if adopt_native_memberships(obj):
                count += 1
                continue
        except Exception as e:
            log.warning(f"Could not adopt light group memberships of '{obj.name}': {e}")
        groups = get_groups(settings)
        delta = get_object_delta(obj)
        if settings.is_override_active and not (delta["added"] or delta["removed"]):
            try:
                sync_delta_from_ui(obj)
                count += 1
            except Exception as e:
                log.warning(f"Could not rebuild the light group delta of '{obj.name}': {e}")
            continue
        if not groups:
            continue
        sync_light_collection_membership(obj, groups)
        count += 1
    return count

def _apply_local_cache_to_scene():
    """Timer callback: applies the file's cached ASL defaults, then re-applies every object's existing groups."""
    if not is_art_file():
        return None
    scene = getattr(bpy.context, "scene", None)
    try:
        data = LibraryDataManager.load_local_data()
        asl = data.get("ASL") if isinstance(data, dict) else None
        if asl and scene:
            sync_scene_objects_from_defaults(scene, asl)
    except Exception as e:
        log.warning(f"Auto-sync from local cache failed: {e}")
    try:
        count = sync_existing_assignments(scene)
        log.info(f"Load-time membership pass re-applied groups on {count} objects.")
    except Exception as e:
        log.warning(f"Load-time membership pass failed: {e}")
    return None

@persistent
def _on_load_post(dummy):
    """Re-arms light group initialisation, local-cache sync and the background sheet fetch for each opened file."""
    global _ll_groups_ready, _hero_llcol_names
    _ll_groups_ready = False
    _hero_llcol_names = None

    if not is_art_file():
        log.info(f"'{bpy.path.basename(bpy.data.filepath) or 'unsaved file'}' has no ART flag. Light Link stays idle.")
        return

    prefs = get_prefs()
    if prefs and not prefs.auto_sync:
        return

    try:
        ensure_light_groups_initialized(force=True)
    except Exception as e:
        log.warning(f"Could not initialize light groups on load: {e}")

    if not bpy.app.timers.is_registered(_apply_local_cache_to_scene):
        bpy.app.timers.register(_apply_local_cache_to_scene, first_interval=1.0)

    schedule_auto_fetch()

# --- Core Logic: Link Traversal & Candidate Resolution ---

def build_collection_parent_map():
    """Builds a map from child collection to parent collection."""
    parent_map = {}
    for col in bpy.data.collections:
        for child in col.children:
            parent_map[child] = col
    return parent_map

def get_candidate_asset_names(target):
    """
    Builds an ordered list of candidate asset names for target (Object or Collection)
    from closest identity to root collection.
    """
    candidates = []
    if not target:
        return candidates

    if isinstance(target, bpy.types.Collection):
        parent_map = build_collection_parent_map()
        current = target
        while current:
            candidates.append(current.name)
            if hasattr(current, "override_library") and current.override_library:
                try:
                    ref = current.override_library.reference
                    if ref:
                        candidates.append(ref.name)
                except Exception:
                    pass
            current = parent_map.get(current)
    else:
        candidates.append(target.name)
        if getattr(target, "data", None):
            candidates.append(target.data.name)
        if getattr(target, "instance_collection", None):
            candidates.append(target.instance_collection.name)
            
        if hasattr(target, "override_library") and target.override_library:
            try:
                ref = target.override_library.reference
                if ref:
                    candidates.append(ref.name)
            except Exception:
                pass

        if hasattr(target, "users_collection") and target.users_collection:
            parent_map = build_collection_parent_map()
            for col in target.users_collection:
                current = col
                while current:
                    candidates.append(current.name)
                    if hasattr(current, "override_library") and current.override_library:
                        try:
                            ref = current.override_library.reference
                            if ref:
                                candidates.append(ref.name)
                        except Exception:
                            pass
                    current = parent_map.get(current)
                
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result

class LinkTraverser:
    """
    Handles the logic of finding where an asset comes from
    and fetching the 'best' version of its light data.
    """

    @staticmethod
    def get_source_data(obj):
        """
        Determines the 'Source Data Block' and 'Source Library Path' for an object.
        Returns: (source_data_block_name, library_path_or_None)
        """
        if hasattr(obj, "users_collection") and obj.users_collection:
            parent_map = build_collection_parent_map()
            for col in obj.users_collection:
                current = col
                root_col = None
                while current:
                    if current.library or current.override_library:
                        root_col = current
                    current = parent_map.get(current)
                
                if root_col:
                    if root_col.override_library:
                        try:
                            ref = root_col.override_library.reference
                            if ref and ref.library:
                                return ref.name, ref.library.filepath
                        except Exception:
                            pass
                    elif root_col.library:
                        return root_col.name, root_col.library.filepath

        if obj.override_library:
            try:
                reference = obj.override_library.reference
                if reference and reference.library:
                    return reference.name, reference.library.filepath
            except Exception:
                pass
        
        if obj.instance_collection and obj.instance_collection.library:
             return obj.instance_collection.name, obj.instance_collection.library.filepath

        if obj.data and obj.data.library:
             return obj.data.name, obj.data.library.filepath

        return obj.name, None

    @staticmethod
    def fetch_cascaded_data(obj):
        """
        Performs the Cascading Lookup resolving the base defaults
        and applying local delta changes.
        
        Returns: (resolved_groups_list, source_type_enum)
        """
        candidates = get_candidate_asset_names(obj)
        defaults = None
        source_type = 'NONE'

        # 1. Try local cache for each candidate
        for cand in candidates:
            base_cand = get_base_asset_name(cand)
            found = LibraryDataManager.get_local_cache_defaults(cand, base_cand)
            if found is not None:
                defaults = found
                source_type = 'ASSET'
                break

        # 2. Fallback to library files if not in local cache
        if defaults is None:
            asset_name, library_path = LinkTraverser.get_source_data(obj)
            base_asset_name = get_base_asset_name(asset_name)
            
            if library_path:
                found_data = LinkTraverser._read_json_from_library(library_path, base_asset_name)
                if found_data is None:
                    found_data = LinkTraverser._read_json_from_library(library_path, asset_name)
                    
                if found_data is not None:
                    defaults = found_data
                    source_type = 'LAYOUT'
                else:
                    real_asset_source = LinkTraverser._find_real_source_of_asset(library_path, asset_name)
                    if real_asset_source:
                        deep_data = LinkTraverser._read_json_from_library(real_asset_source, base_asset_name)
                        if deep_data is None:
                            deep_data = LinkTraverser._read_json_from_library(real_asset_source, asset_name)
                        if deep_data is not None:
                            defaults = deep_data
                            source_type = 'ASSET'

        if defaults is None:
            defaults = []

        # Backwards Compatibility Migration
        if CUSTOM_PROP_OVERRIDE in obj and CUSTOM_PROP_DELTA not in obj and not getattr(getattr(obj, "krutart_light_link", None), "delta_json", ""):
            try:
                old_list = json.loads(obj[CUSTOM_PROP_OVERRIDE])
                if isinstance(old_list, list):
                    added = [g for g in old_list if g not in defaults]
                    removed = [g for g in defaults if g not in old_list]
                    save_object_delta(obj, {"added": added, "removed": removed})
                    del obj[CUSTOM_PROP_OVERRIDE]
            except Exception:
                pass

        # Apply Deltas
        delta = get_object_delta(obj)
        resolved_source = source_type
        
        if delta["added"] or delta["removed"]:
            resolved_source = 'LOCAL'

        resolved_groups = [g for g in defaults if g not in delta["removed"]]
        for g in delta["added"]:
            if g not in resolved_groups:
                resolved_groups.append(g)

        return resolved_groups, resolved_source

    @staticmethod
    def _read_json_from_library(lib_path, asset_name):
        """Opens lib_path, looks for __krutart_light_link_data.json, returns dict.get(asset_name)"""
        try:
            abs_path = bpy.path.abspath(lib_path)
            if not abs_path: return None
            
            with bpy.data.libraries.load(abs_path, link=False) as (data_from, data_to):
                if DATA_BLOCK_NAME in data_from.texts:
                     pass 
            
            with bpy.data.libraries.load(abs_path, link=False) as (data_from, data_to):
                if DATA_BLOCK_NAME in data_from.texts:
                    data_to.texts = [DATA_BLOCK_NAME]
            
            if data_to.texts:
                txt = data_to.texts[0]
                content = txt.as_string()
                bpy.data.texts.remove(txt)
                
                data = json.loads(content)
                return data.get(asset_name)
                
        except Exception:
            pass
        return None

    @staticmethod
    def _find_real_source_of_asset(intermediate_lib_path, asset_name):
        """Finds where the intermediate library links the asset from."""
        try:
            abs_path = bpy.path.abspath(intermediate_lib_path)
            chain_path = None
            
            with bpy.data.libraries.load(abs_path, link=True) as (data_from, data_to):
                if asset_name in data_from.collections:
                    data_to.collections = [asset_name]
                elif asset_name in data_from.objects:
                    data_to.objects = [asset_name]
            
            loaded_item = None
            if data_to.collections: loaded_item = data_to.collections[0]
            elif data_to.objects: loaded_item = data_to.objects[0]
            
            if loaded_item and loaded_item.library:
                chain_path = loaded_item.library.filepath
            
            return chain_path
        except Exception:
            return None

def get_base_defaults(obj):
    """Resolves the raw default groups of the asset without applying any local deltas."""
    candidates = get_candidate_asset_names(obj)
    for cand in candidates:
        base_cand = get_base_asset_name(cand)
        found = LibraryDataManager.get_local_cache_defaults(cand, base_cand)
        if found is not None:
            return found

    asset_name, library_path = LinkTraverser.get_source_data(obj)
    base_asset_name = get_base_asset_name(asset_name)
    
    if library_path:
        found_data = LinkTraverser._read_json_from_library(library_path, base_asset_name)
        if found_data is None:
            found_data = LinkTraverser._read_json_from_library(library_path, asset_name)
        if found_data is not None:
            return found_data
        else:
            real_asset_source = LinkTraverser._find_real_source_of_asset(library_path, asset_name)
            if real_asset_source:
                deep_data = LinkTraverser._read_json_from_library(real_asset_source, base_asset_name)
                if deep_data is None:
                    deep_data = LinkTraverser._read_json_from_library(real_asset_source, asset_name)
                if deep_data is not None:
                    return deep_data
    return []

def get_groups(settings):
    """Safely decodes active groups from JSON string."""
    try:
        data = json.loads(settings.groups_json)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []

def set_groups(settings, groups_list):
    """Safely encodes active groups to JSON string."""
    settings.groups_json = json.dumps(groups_list)

def sync_delta_from_ui(obj):
    """Compares the current UI group list against sheet defaults to compute and save the delta."""
    defaults = get_base_defaults(obj)
    
    settings = obj.krutart_light_link
    ui_groups = get_groups(settings)
    
    added = [g for g in ui_groups if g not in defaults]
    removed = [g for g in defaults if g not in ui_groups]
    
    delta = {"added": added, "removed": removed}
    save_object_delta(obj, delta)
    
    # Sync membership in standard protected LL- collections
    sync_light_collection_membership(obj, ui_groups)
    
    if added or removed:
        settings.resolved_source = 'LOCAL'
        settings.is_override_active = True
    else:
        _, source_type = LinkTraverser.fetch_cascaded_data(obj)
        settings.resolved_source = source_type
        settings.is_override_active = False

# --- Preferences ---

class KrutartLightLinkPreferences(AddonPreferences):
    bl_idname = __name__

    sheet_url: StringProperty(
        name="Google Sheet URL",
        default=DEFAULT_SHEET_URL,
        description="Public CSV export URL of the Preproduction sheet"
    )

    auto_sync: BoolProperty(
        name="Automatic Sync",
        default=True,
        description="Fetch sheet defaults and sync light groups automatically on file load, with no manual Fetch/Sync step"
    )

    show_dev_tools: BoolProperty(
        name="Show Developer Tools",
        default=False,
        description="Show the manual sync, cache, sheet fetch and JSON import/export buttons in the UI panels"
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Google Sheets Preproduction Connection", icon='URL')
        box.prop(self, "sheet_url")

        box_dev = layout.box()
        box_dev.label(text="Developer", icon='TOOL_SETTINGS')
        box_dev.prop(self, "auto_sync")
        box_dev.prop(self, "show_dev_tools")

# --- Property Groups ---

class KrutartObjectLightSettings(PropertyGroup):
    """Attached to Objects to manage local UI state."""
    groups_json: StringProperty(
        name="Groups JSON",
        default="[]",
        override={'LIBRARY_OVERRIDABLE'}
    )

    delta_json: StringProperty(
        name="Delta JSON",
        description="Local edits over the sheet defaults. Kept here as well as in the krutart_light_delta custom property, which Blender drops from library overrides on save",
        default="",
        override={'LIBRARY_OVERRIDABLE'}
    )
    
    is_override_active: BoolProperty(
        name="Is Local Override",
        description="If True, this object has local edits over the sheet defaults.",
        default=False,
        override={'LIBRARY_OVERRIDABLE'}
    )
    
    resolved_source: EnumProperty(
        items=[
            ('NONE', "None", "No data found"),
            ('ASSET', "Asset Library", "Data linked from original asset file"),
            ('LAYOUT', "Intermediate (Layout)", "Data linked from intermediate file"),
            ('LOCAL', "Local Override", "Data defined locally or customized by delta"),
        ],
        name="Source",
        default='NONE',
        override={'LIBRARY_OVERRIDABLE'}
    )

# --- Operators ---

class KRUTART_OT_save_asset_data(Operator):
    """Saves the current object's light groups to the internal JSON configuration and updates object custom property."""
    bl_idname = "krutart.save_asset_data"
    bl_label = "Save to Local Cache"
    bl_description = "Saves this asset's active light groups to __krutart_light_link_data.json and syncs object custom properties"

    def execute(self, context):
        obj = context.active_object
        if not obj: return {'CANCELLED'}
        
        settings = obj.krutart_light_link
        current_groups = get_groups(settings)
        
        db = LibraryDataManager.load_local_data()
        if "ASL" not in db:
            db["ASL"] = {}
        db["ASL"][obj.name] = current_groups
        base_clean = get_base_asset_name(obj.name, strip_lod=True)
        if base_clean:
            db["ASL"][base_clean] = current_groups
        LibraryDataManager.save_local_data(db)
        
        settings.resolved_source = 'LOCAL' 
        settings.is_override_active = True 
        
        # Both-Way Sync: Sync membership and preserve delta custom property on object
        sync_delta_from_ui(obj)
        
        self.report({'INFO'}, f"Saved {len(current_groups)} groups for '{obj.name}' (Both-Way Sync)")
        return {'FINISHED'}

class KRUTART_OT_fetch_light_links(Operator):
    """Fetches and applies light links from the source library hierarchy."""
    bl_idname = "krutart.fetch_light_links"
    bl_label = "Sync Light Links"
    
    target_object_name: StringProperty(default="")
    
    def execute(self, context):
        if self.target_object_name:
            obj = context.scene.objects.get(self.target_object_name)
            selected = [obj] if obj else []
        else:
            selected = context.selected_objects
            if not selected: selected = [context.active_object]
        
        count = 0
        for obj in selected:
            if not obj: continue
            
            groups, source_type = LinkTraverser.fetch_cascaded_data(obj)
            
            settings = obj.krutart_light_link
            set_groups(settings, groups)
            settings.resolved_source = source_type
            
            # Sync membership in standard protected LL- collections
            sync_light_collection_membership(obj, groups)
            
            delta = get_object_delta(obj)
            settings.is_override_active = bool(delta["added"] or delta["removed"])
            
            count += 1
            
        self.report({'INFO'}, f"Synced {count} objects.")
        return {'FINISHED'}

class KRUTART_OT_fetch_sheet_defaults(Operator):
    """Downloads default light group definitions from Google Sheets in the background."""
    bl_idname = "krutart.fetch_sheet_defaults"
    bl_label = "Fetch Sheet Defaults"
    bl_description = "Downloads light group defaults from Preproduction Google Sheet in the background"
    
    def execute(self, context):
        prefs = get_prefs()
        url = prefs.sheet_url if (prefs and getattr(prefs, "sheet_url", "")) else DEFAULT_SHEET_URL
        
        self.report({'INFO'}, "Connecting to Google Sheets in background...")
        global _async_fetch_thread
        _async_fetch_thread = threading.Thread(
            target=fetch_sheets_in_background,
            args=(url, apply_fetched_sheet_defaults),
            daemon=True
        )
        _async_fetch_thread.start()
        return {'FINISHED'}

class KRUTART_OT_export_light_link_data(Operator, ExportHelper):
    """Exports light linking sheet defaults, active overrides, and custom deltas to a JSON file."""
    bl_idname = "krutart.export_light_link_data"
    bl_label = "Export Light Links"
    bl_description = "Exports light linking data, active overrides, and local custom deltas to a JSON file"
    
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            export_data = {
                "version": "2.2.1",
                "defaults": LibraryDataManager.load_local_data(),
                "overrides": {},
                "deltas": {}
            }
            
            # Export scene object active groups and deltas
            for obj in context.scene.objects:
                settings = getattr(obj, "krutart_light_link", None)
                if settings:
                    groups = get_groups(settings)
                    if groups:
                        export_data["overrides"][obj.name] = groups
                        base_clean = get_base_asset_name(obj.name, strip_lod=True)
                        if base_clean:
                            export_data["overrides"][base_clean] = groups

                delta = get_object_delta(obj)
                if delta["added"] or delta["removed"]:
                    export_data["deltas"][obj.name] = delta
                    base_clean = get_base_asset_name(obj.name, strip_lod=True)
                    if base_clean:
                        export_data["deltas"][base_clean] = delta

            # Export collection deltas
            for col in bpy.data.collections:
                delta = get_object_delta(col)
                if delta["added"] or delta["removed"]:
                    export_data["deltas"][col.name] = delta

            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2)

            self.report({'INFO'}, f"Exported light links to {self.filepath}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export light links: {e}")
            return {'CANCELLED'}

class KRUTART_OT_import_light_link_data(Operator, ImportHelper):
    """Imports and non-destructively merges light linking sheet defaults, overrides, and deltas from a JSON file."""
    bl_idname = "krutart.import_light_link_data"
    bl_label = "Import Light Links"
    bl_description = "Imports light linking defaults, overrides, and merges custom deltas from a JSON file"
    
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                raise ValueError("Invalid JSON format.")

            # 1. Merge defaults into local cache if present
            imported_defaults = data.get("defaults", {})
            if isinstance(imported_defaults, dict) and imported_defaults:
                current_cache = LibraryDataManager.load_local_data()
                if "ASL" in imported_defaults:
                    if "ASL" not in current_cache: current_cache["ASL"] = {}
                    current_cache["ASL"].update(imported_defaults.get("ASL", {}))
                else:
                    current_cache.update(imported_defaults)
                LibraryDataManager.save_local_data(current_cache)

            # 2. Merge overrides and deltas into matching scene objects and collections
            imported_overrides = data.get("overrides", {})
            imported_deltas = data.get("deltas", {})
            updated_count = 0

            for obj in context.scene.objects:
                match_delta = None
                keys = (obj.name, get_base_asset_name(obj.name, strip_lod=False), get_base_asset_name(obj.name, strip_lod=True))
                
                # Check explicit deltas first
                for key in keys:
                    if key and key in imported_deltas:
                        match_delta = imported_deltas[key]
                        break
                        
                # Check overrides if no explicit delta present
                if not match_delta:
                    for key in keys:
                        if key and key in imported_overrides:
                            imp_groups = imported_overrides[key]
                            base_defs = get_base_defaults(obj)
                            added = [g for g in imp_groups if g not in base_defs]
                            removed = [g for g in base_defs if g not in imp_groups]
                            if added or removed:
                                match_delta = {"added": added, "removed": removed}
                            break

                if match_delta:
                    existing = get_object_delta(obj)
                    new_added = list(dict.fromkeys(existing["added"] + match_delta.get("added", [])))
                    new_removed = list(dict.fromkeys(existing["removed"] + match_delta.get("removed", [])))
                    merged_delta = {"added": new_added, "removed": new_removed}
                    save_object_delta(obj, merged_delta)
                    
                    settings = obj.krutart_light_link
                    resolved_groups, _ = LinkTraverser.fetch_cascaded_data(obj)
                    set_groups(settings, resolved_groups)
                    sync_delta_from_ui(obj)
                    updated_count += 1

            for col in bpy.data.collections:
                match_delta = None
                keys = (col.name, get_base_asset_name(col.name, strip_lod=False), get_base_asset_name(col.name, strip_lod=True))
                
                for key in keys:
                    if key and key in imported_deltas:
                        match_delta = imported_deltas[key]
                        break
                        
                if not match_delta:
                    for key in keys:
                        if key and key in imported_overrides:
                            imp_groups = imported_overrides[key]
                            match_delta = {"added": imp_groups, "removed": []}
                            break

                if match_delta:
                    existing = get_object_delta(col)
                    new_added = list(dict.fromkeys(existing["added"] + match_delta.get("added", [])))
                    new_removed = list(dict.fromkeys(existing["removed"] + match_delta.get("removed", [])))
                    merged_delta = {"added": new_added, "removed": new_removed}
                    save_object_delta(col, merged_delta)
                    updated_count += 1

            self.report({'INFO'}, f"Imported light links ({updated_count} items updated).")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to import light links: {e}")
            return {'CANCELLED'}

class KRUTART_OT_make_local_override(Operator):
    """Converts the current state into a Local Override."""
    bl_idname = "krutart.make_local_override"
    bl_label = "Customize Locally"
    
    def execute(self, context):
        obj = context.active_object
        settings = obj.krutart_light_link
        
        delta = get_object_delta(obj)
        save_object_delta(obj, delta)
        
        settings.is_override_active = True
        settings.resolved_source = 'LOCAL'
        
        return {'FINISHED'}

class KRUTART_OT_revert_to_library(Operator):
    """Removes Local Override delta and re-syncs to library defaults."""
    bl_idname = "krutart.revert_to_library"
    bl_label = "Revert to Library Defaults"
    
    def execute(self, context):
        obj = context.active_object
        
        if CUSTOM_PROP_DELTA in obj:
            del obj[CUSTOM_PROP_DELTA]
            if "_RNA_UI" in obj and CUSTOM_PROP_DELTA in obj["_RNA_UI"]:
                del obj["_RNA_UI"][CUSTOM_PROP_DELTA]
        
        if CUSTOM_PROP_OVERRIDE in obj:
            del obj[CUSTOM_PROP_OVERRIDE]
            
        settings = obj.krutart_light_link
        settings.delta_json = ""
        settings.is_override_active = False
        
        bpy.ops.krutart.fetch_light_links(target_object_name=obj.name) 
        
        return {'FINISHED'}

def is_read_only_object(obj):
    """True for linked objects that are not overrides: they cannot store per-object settings."""
    return bool(obj.library and not obj.override_library)

def object_group_rows(obj, channel=None):
    """
    What the panel shows: the groups the object is actually in, plus any it lists but whose
    collection does not exist yet. Membership is what Blender renders from, and it is the only
    record a linked object can carry. With channel, only that channel's groups (the LL- list
    keeps any non-prefixed names it always showed).
    """
    settings = getattr(obj, "krutart_light_link", None)
    stored = get_groups(settings) if settings else []
    rows = set(stored) | current_ll_memberships(obj)
    if channel is None:
        return sorted(rows)
    if channel is LIGHT:
        return sorted(g for g in rows if group_channel(g) is not SHADOW)
    return sorted(g for g in rows if group_channel(g) is channel)

def add_group_to_object(obj, group_name):
    """Appends group_name to the object's list and syncs delta + collection membership. Returns False if already present."""
    if is_read_only_object(obj):
        # Linked object: membership is all it can be given.
        return link_to_group(obj, group_name)
    settings = obj.krutart_light_link
    groups = get_groups(settings)
    if group_name in groups:
        return False
    groups.append(group_name)
    set_groups(settings, groups)
    sync_delta_from_ui(obj)
    return True

class KRUTART_OT_add_group_item(Operator):
    """Adds an existing LLCOL or custom light group to the active object."""
    bl_idname = "krutart.add_group_item"
    bl_label = "Add Group"
    bl_options = {'REGISTER', 'UNDO'}
    
    group_name: StringProperty(name="Group Name", default="")
    
    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}
        if not self.group_name:
            return {'CANCELLED'}
        if not add_group_to_object(obj, self.group_name):
            self.report({'WARNING'}, f"Group '{self.group_name}' already assigned.")
            return {'CANCELLED'}
        return {'FINISHED'}

class KRUTART_OT_new_light_group(Operator):
    """Creates a new custom LL- (or SL-) group in this file and adds it to the active object."""
    bl_idname = "krutart.new_light_group"
    bl_label = "New Group"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: StringProperty(name="Name", default="", description="Stored as LL-<NAME> / SL-<NAME>, e.g. 'bridge rim' becomes LL-BRIDGE_RIM")
    channel: EnumProperty(
        name="Channel",
        items=[('LIGHT', "Light", "LL- light link group"), ('SHADOW', "Shadow", "SL- shadow link group")],
        default='LIGHT',
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "group_name")
        final = normalize_group_name(self.group_name, CHANNELS_BY_KEY[self.channel])
        layout.label(text=f"Creates: {final}" if final else "Enter a name.", icon='OUTLINER_COLLECTION')

    def invoke(self, context, event):
        self.group_name = ""
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}
        channel = CHANNELS_BY_KEY[self.channel]
        name = normalize_group_name(self.group_name, channel)
        if not name:
            self.report({'WARNING'}, "Group name is empty.")
            return {'CANCELLED'}
        existed = name in bpy.data.collections
        if channel is SHADOW and obj.type == 'LIGHT':
            set_light_group(obj, channel, name)
            self.report({'INFO'}, f"'{obj.name}' now takes shadows from '{name}'.")
            return {'FINISHED'}
        if not add_group_to_object(obj, name):
            self.report({'WARNING'}, f"Group '{name}' already assigned.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{'Added existing' if existed else 'Created'} {channel.label.lower()} group '{name}'.")
        return {'FINISHED'}

class KRUTART_MT_add_light_group(bpy.types.Menu):
    """Add Group dropdown: hero LLCOL groups, this file's custom LL- groups, and New Group."""
    bl_idname = "KRUTART_MT_add_light_group"
    bl_label = "Add Light Group"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        assigned = set(object_group_rows(obj, LIGHT)) if obj else set()

        llcol = get_llcol_names(LIGHT)
        llcol_set = set(llcol)
        custom = sorted(get_group_pool_names(LIGHT) - llcol_set)

        layout.label(text="LLCOL")
        shown = [g for g in llcol if g not in assigned]
        for g_name in shown:
            layout.operator("krutart.add_group_item", text=g_name, icon='LIGHT').group_name = g_name
        if not shown:
            layout.label(text="All assigned", icon='CHECKMARK')

        layout.separator()
        layout.label(text="CUSTOM")
        shown = [g for g in custom if g not in assigned]
        for g_name in shown:
            layout.operator("krutart.add_group_item", text=g_name, icon='OUTLINER_COLLECTION').group_name = g_name
        if not shown:
            layout.label(text="None in this file" if not custom else "All assigned", icon='INFO')

        layout.separator()
        layout.operator("krutart.new_light_group", text="New Group...", icon='ADD')

def set_light_group(light_obj, channel, group_name):
    """Points a light's receiver (LL-) or blocker (SL-) collection at group_name; '' clears it."""
    ll = getattr(light_obj, "light_linking", None)
    if ll is None:
        return False
    if not group_name:
        setattr(ll, channel.light_prop, None)
        return True
    col = bpy.data.collections.get(group_name)
    if col is None:
        col = bpy.data.collections.new(group_name)
    col.use_fake_user = True
    setattr(ll, channel.light_prop, col)
    purge_light_groups_from_outliner()
    return True

class KRUTART_OT_set_light_group(Operator):
    """Sets which group this light affects: its light link receivers (LL-) or shadow blockers (SL-)."""
    bl_idname = "krutart.set_light_group"
    bl_label = "Set Light Group"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: StringProperty(default="", description="Empty clears it")
    channel: EnumProperty(
        name="Channel",
        items=[('LIGHT', "Light", "LL- light link group"), ('SHADOW', "Shadow", "SL- shadow link group")],
        default='LIGHT',
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'LIGHT':
            return {'CANCELLED'}
        set_light_group(obj, CHANNELS_BY_KEY[self.channel], self.group_name)
        return {'FINISHED'}

class KRUTART_MT_add_shadow_group(bpy.types.Menu):
    """Add Shadow Group dropdown: this file's SL- groups and New Group. On a light it sets the blocker."""
    bl_idname = "KRUTART_MT_add_shadow_group"
    bl_label = "Add Shadow Group"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        is_light = bool(obj and obj.type == 'LIGHT')
        pool = sorted(get_group_pool_names(SHADOW))
        if is_light:
            current = getattr(obj.light_linking, SHADOW.light_prop, None)
            layout.label(text="SHADOWS FROM")
            for g_name in pool:
                op = layout.operator("krutart.set_light_group", text=g_name, icon='OUTLINER_COLLECTION')
                op.channel, op.group_name = 'SHADOW', g_name
            if current is not None:
                op = layout.operator("krutart.set_light_group", text="None (all objects)", icon='X')
                op.channel, op.group_name = 'SHADOW', ""
        else:
            assigned = set(object_group_rows(obj, SHADOW)) if obj else set()
            layout.label(text="SL GROUPS")
            shown = [g for g in pool if g not in assigned]
            for g_name in shown:
                layout.operator("krutart.add_group_item", text=g_name, icon='OUTLINER_COLLECTION').group_name = g_name
            if not shown:
                layout.label(text="None in this file" if not pool else "All assigned", icon='INFO')
        layout.separator()
        layout.operator("krutart.new_light_group", text="New Shadow Group...", icon='ADD').channel = 'SHADOW'

# Open/closed state of the SL section per object, for this session only. Unset = open only when
# the object already has shadow data.
_section_open = {}

class KRUTART_OT_toggle_section(Operator):
    """Show or hide this section"""
    bl_idname = "krutart.toggle_section"
    bl_label = "Toggle Section"
    bl_options = {'INTERNAL'}

    key: StringProperty(default="")
    is_open: BoolProperty(default=False)

    def execute(self, context):
        _section_open[self.key] = not self.is_open
        return {'FINISHED'}

class KRUTART_OT_remove_group_item(Operator):
    """Takes the object out of this light group."""
    bl_idname = "krutart.remove_group_item"
    bl_label = "Remove"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty(default=-1)
    group_name: StringProperty(default="")

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}
        settings = obj.krutart_light_link
        groups = get_groups(settings)
        name = self.group_name
        if not name and 0 <= self.index < len(groups):
            name = groups[self.index]
        if not name:
            return {'CANCELLED'}

        if not is_read_only_object(obj):
            set_groups(settings, [g for g in groups if g != name])
            sync_delta_from_ui(obj)

        # Membership is the part that renders, and the only part a linked object has.
        col = bpy.data.collections.get(name)
        if col and any(o == obj for o in col.objects):
            try:
                col.objects.unlink(obj)
            except Exception as e:
                self.report({'WARNING'}, f"Could not remove '{obj.name}' from '{name}': {e}")
                return {'CANCELLED'}
        return {'FINISHED'}

# --- UI Panel ---

class KRUTART_PT_light_link_panel(Panel):
    """Main UI Panel for Light Linking Tools in Properties > Object."""
    bl_label = "Krutart Light Links"
    bl_idname = "KRUTART_PT_light_link_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    @staticmethod
    def draw_non_art(layout):
        """Outside ART files the add-on is idle; offer the cleanup for files it polluted earlier."""
        box = layout.box()
        box.label(text="Not an ART file", icon='INFO')
        box.label(text="Light groups stay off outside ART shots.")
        if is_ani_file():
            box.operator("krutart.cleanup_light_groups", icon='TRASH', text="Clean Up LL Light Groups")
        box.operator("krutart.initialize_light_groups", icon='LIGHT_DATA', text="Init Light Groups Anyway")

    @classmethod
    def draw_panel_content(cls, layout, context, obj):
        if not is_art_file():
            cls.draw_non_art(layout)
            return
        if not obj:
            layout.label(text="Select an object.")
            return

        settings = obj.krutart_light_link
        delta = get_object_delta(obj)
        groups = object_group_rows(obj, LIGHT)
        read_only = is_read_only_object(obj)

        box_list = layout.box()
        box_list.label(text=(context.scene.get(SCENE_PROP_SOURCE) if context.scene else None) or "LL GROUPS")
        if read_only:
            box_list.label(text="Linked object: group membership only", icon='LINKED')
        if not groups:
            box_list.label(text="No active light groups.", icon='INFO')
        else:
            for g_name in groups:
                row = box_list.row(align=True)
                row.label(text=g_name, icon=group_icon(g_name))
                rem_op = row.operator("krutart.remove_group_item", icon='REMOVE', text="")
                rem_op.group_name = g_name

        row_actions = layout.row(align=True)
        row_actions.menu("KRUTART_MT_add_light_group", text="Add Group", icon='ADD')
        if delta["added"] or delta["removed"]:
            row_actions.operator("krutart.revert_to_library", icon='LOOP_BACK', text="Revert to Defaults")

        cls.draw_shadow_section(layout, obj, read_only)

        layout.operator("krutart.append_lgt_structure", icon='FILE_BLEND', text="Append LGT File...")

        cls.draw_dev_tools(layout, context)

    @staticmethod
    def draw_shadow_section(layout, obj, read_only):
        """SL GROUPS: always there, collapsed unless the object (or light) already has shadow data."""
        is_light = obj.type == 'LIGHT'
        blocker = getattr(obj.light_linking, SHADOW.light_prop, None) if is_light else None
        rows = [] if is_light else object_group_rows(obj, SHADOW)
        has_content = bool(rows or blocker)
        key = obj.name_full
        is_open = _section_open.get(key, has_content)

        box = layout.box()
        header = box.row(align=True)
        op = header.operator("krutart.toggle_section", text="", emboss=False,
                             icon='DOWNARROW_HLT' if is_open else 'RIGHTARROW')
        op.key, op.is_open = key, is_open
        count = 1 if blocker else len(rows)
        header.label(text=SHADOW.section + (f" ({count})" if count else ""))
        if not is_open:
            return

        if is_light:
            row = box.row(align=True)
            row.label(text=f"Shadows from: {blocker.name if blocker else 'all objects'}",
                      icon='OUTLINER_COLLECTION' if blocker else 'INFO')
            if blocker:
                clr = row.operator("krutart.set_light_group", icon='REMOVE', text="")
                clr.channel, clr.group_name = 'SHADOW', ""
            box.menu("KRUTART_MT_add_shadow_group", text="Set Shadow Group", icon='ADD')
            return

        if read_only and rows:
            box.label(text="Linked object: group membership only", icon='LINKED')
        if not rows:
            box.label(text="No shadow groups (default).", icon='INFO')
        for g_name in rows:
            row = box.row(align=True)
            row.label(text=g_name, icon=group_icon(g_name))
            row.operator("krutart.remove_group_item", icon='REMOVE', text="").group_name = g_name
        box.menu("KRUTART_MT_add_shadow_group", text="Add Shadow Group", icon='ADD')

    @staticmethod
    def draw_dev_tools(layout, context):
        """Manual counterparts of the automatic sync, shown only when the dev toggle is on."""
        prefs = get_prefs()
        if not (prefs and getattr(prefs, "show_dev_tools", False)):
            return

        box_dev = layout.box()
        box_dev.label(text="Developer Tools:", icon='TOOL_SETTINGS')

        row_sync = box_dev.row(align=True)
        row_sync.operator("krutart.fetch_light_links", icon='FILE_REFRESH', text="Sync Links")
        row_sync.operator("krutart.save_asset_data", icon='EXPORT', text="Save to Local Cache")

        row_tools = box_dev.row(align=True)
        row_tools.operator("krutart.fetch_sheet_defaults", icon='URL', text="Fetch Sheets")
        row_tools.operator("krutart.initialize_light_groups", icon='LIGHT_DATA', text="Init Light Groups")
        row_tools.operator("krutart.cleanup_light_groups", icon='TRASH', text="Clean Up LL Groups")

        row_io = box_dev.row(align=True)
        row_io.operator("krutart.export_light_link_data", icon='FILE_BACKUP', text="Export JSON")
        row_io.operator("krutart.import_light_link_data", icon='IMPORT', text="Import JSON")

    def draw(self, context):
        self.draw_panel_content(self.layout, context, context.object)

class KRUTART_PT_light_link_collection_panel(Panel):
    """UI Panel for Collection Light Linking in Properties > Collection."""
    bl_label = "Krutart Light Links"
    bl_idname = "KRUTART_PT_light_link_collection_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "collection"

    def draw(self, context):
        layout = self.layout
        col = context.collection

        if not is_art_file():
            KRUTART_PT_light_link_panel.draw_non_art(layout)
            return
        if not col:
            layout.label(text="Select a collection.")
            return

        candidates = get_candidate_asset_names(col)
        groups = None
        
        for cand in candidates:
            base_cand = get_base_asset_name(cand)
            found = LibraryDataManager.get_local_cache_defaults(cand, base_cand)
            if found is not None:
                groups = found
                break

        groups = groups or []
        ll_groups = [g for g in groups if group_channel(g) is not SHADOW]
        sl_groups = [g for g in groups if group_channel(g) is SHADOW]
        box_list = layout.box()
        box_list.label(text=(context.scene.get(SCENE_PROP_SOURCE) if context.scene else None) or "LL GROUPS")
        if not ll_groups:
            box_list.label(text="No active light groups.", icon='INFO')
        else:
            for g_name in ll_groups:
                row = box_list.row(align=True)
                row.label(text=g_name, icon=group_icon(g_name))
        if sl_groups:
            box_sl = layout.box()
            box_sl.label(text=SHADOW.section)
            for g_name in sl_groups:
                box_sl.label(text=g_name, icon=group_icon(g_name))

        layout.operator("krutart.append_lgt_structure", icon='FILE_BLEND', text="Append LGT File...")

        KRUTART_PT_light_link_panel.draw_dev_tools(layout, context)

class KRUTART_OT_append_lgt_structure(Operator, ImportHelper):
    """Appends lighting structure and lights from a source blend file without appending linked receiver geometry assets."""
    bl_idname = "krutart.append_lgt_structure"
    bl_label = "Append LGT Structure"
    bl_description = "Appends lighting setup from a source file and safely re-binds light linking without pulling in geometry objects"

    filename_ext = ".blend"
    filter_glob: StringProperty(default="*.blend", options={'HIDDEN'})
    
    target_collection_name: StringProperty(
        name="Target Collection",
        description="Name of the collection to append or merge into (e.g. +LGT+ or LGT-REFERENCE)",
        default="+LGT+"
    )
    
    def execute(self, context):
        src_path = resolve_krutart_path(self.filepath)
        if not src_path or not os.path.exists(src_path):
            self.report({'ERROR'}, f"Source file not found: {self.filepath}")
            return {'CANCELLED'}

        target_scene = context.scene

        # 0. A separate Blender reads the source and hands back its light group assignments plus a
        #    stripped copy with only the lights, the +LGT+ content and LL- groups. The source's own
        #    overrides never enter this file, so nothing here can be repointed at a duplicate library.
        self.report({'INFO'}, "Reading the source file in a separate Blender...")
        source_assignments, append_path, tempdir = extract_source_light_data(src_path)
        if not source_assignments or not append_path:
            shutil.rmtree(tempdir, ignore_errors=True)
            self.report({'ERROR'}, f"Could not read '{os.path.basename(src_path)}'. See the system console.")
            return {'CANCELLED'}

        # The source is readable: the file's old +LGT+ / LGT-REFERENCE go first, with everything
        # only they used, so the source's lighting replaces them instead of piling up next to them.
        old_cols, old_objs, old_purged = remove_lgt_containers()
        if old_cols:
            log.info(f"Removed the old light containers: {old_cols} collections, {old_objs} objects, "
                     f"{old_purged} freed datablocks")

        existing_objs = set(bpy.data.objects)
        existing_cols = set(bpy.data.collections)
        existing_libs = set(bpy.data.libraries)
        existing_ids = _id_snapshot()
        existing_ll_members = {
            c.as_pointer(): ({o.as_pointer() for o in c.objects}, {ch.as_pointer() for ch in c.children})
            for c in bpy.data.collections if is_light_group_name(c.name)
        }

        # 1. Append the stripped copy (lights, +LGT+ content, LL- groups)
        try:
            with bpy.data.libraries.load(append_path, link=False) as (data_from, data_to):
                requested = self.target_collection_name.strip()
                match_col = None
                if requested and requested in data_from.collections:
                    match_col = requested
                else:
                    for c_name in ("+LGT+", "LGT-REFERENCE", "LGT", "LIGHTS"):
                        if c_name in data_from.collections:
                            match_col = c_name
                            break
                            
                wanted = [match_col] if match_col else list(data_from.collections)
                # Every LL- group of the source file comes along too, into bpy.data only.
                for c_name in data_from.collections:
                    if is_light_group_name(c_name) and c_name not in wanted:
                        wanted.append(c_name)
                data_to.collections = wanted
        except Exception as e:
            shutil.rmtree(tempdir, ignore_errors=True)
            self.report({'ERROR'}, f"Failed to load library: {e}")
            return {'CANCELLED'}

        appended_objs = set(bpy.data.objects) - existing_objs
        appended_cols = set(bpy.data.collections) - existing_cols

        # The source's +LGT+ as it arrived ('+LGT+' or '+LGT+.001'). Its content stays as it is:
        # appended items are local now, linked items (e.g. an instanced asset collection) stay linked.
        loaded = [c for c in getattr(data_to, "collections", []) if c is not None]
        src_container = loaded[0] if match_col and loaded and not is_light_group_name(loaded[0].name) else None
        content_objs, content_cols = set(), set()
        if src_container is not None:
            content_objs = set(src_container.all_objects)
            content_cols = set(src_container.children_recursive)
        # Collections the content instances, and what they hold, are kept as well.
        kept_objs, kept_cols = set(content_objs), set()
        for o in content_objs:
            ic = o.instance_collection
            if ic is not None:
                kept_cols.add(ic)
                kept_cols.update(ic.children_recursive)
                kept_objs.update(ic.all_objects)

        if not appended_objs and not appended_cols:
            shutil.rmtree(tempdir, ignore_errors=True)
            self.report({'WARNING'}, f"No lights or light groups found in '{os.path.basename(src_path)}'")
            return {'CANCELLED'}

        # 2. Guarantee the canonical LL- group datablocks exist BEFORE we resolve any appended names.
        ensure_light_groups_initialized(target_scene, getattr(context, "view_layer", None), force=True)

        # Appended LL- collections arrive suffixed ('LL-ROCK.001') because the canonical name is
        # already taken. Map every such duplicate onto its canonical datablock so light linking
        # rebinds to the collection the rest of the pipeline actually uses.
        ll_dup_map = {}
        for col in appended_cols:
            if not is_light_group_name(col.name):
                continue
            base_name = col.name.split('.')[0]
            canonical = bpy.data.collections.get(base_name)
            if canonical and canonical is not col:
                ll_dup_map[col] = canonical
            else:
                col.use_fake_user = True

        # 3. The +LGT+ container (inside +ART+) is the only collection this operator puts in the outliner.
        target_container = _local_collection(LGT_CONTAINER_NAME)
        if target_container is None:
            target_container = bpy.data.collections.new(LGT_CONTAINER_NAME)
        target_container.use_fake_user = True
        place_lgt_container(target_scene, target_container)

        # Duplicates only hold appended copies and the source's linked originals; the transfer
        # below re-creates the real membership, so nothing is handed over here.
        for canonical in ll_dup_map.values():
            canonical.use_fake_user = True

        # The source's +LGT+ content moves into this file's container, hierarchy included.
        if src_container is not None and src_container is not target_container:
            for o in list(src_container.objects):
                if o not in set(target_container.objects):
                    target_container.objects.link(o)
                src_container.objects.unlink(o)
            for ch in list(src_container.children):
                if ch not in set(target_container.children):
                    target_container.children.link(ch)
                src_container.children.unlink(ch)

        # Gather every other appended light/probe into the container, wherever it came in.
        for obj in appended_objs:
            if obj.type not in {'LIGHT', 'LIGHT_PROBE'} or obj in kept_objs:
                continue
            for col in list(obj.users_collection):
                if col is not target_container:
                    try:
                        col.objects.unlink(obj)
                    except Exception:
                        pass
            if obj.name not in target_container.objects:
                try:
                    target_container.objects.link(obj)
                except Exception:
                    pass

        # 4. Re-bind light linking by pointer identity, falling back to a canonical name lookup.
        rebound_count = 0
        for obj in list(appended_objs):
            if obj.type != 'LIGHT':
                continue
            ll = getattr(obj, "light_linking", None)
            if not ll:
                continue
            for prop in ('receiver_collection', 'blocker_collection'):
                rec = getattr(ll, prop, None)
                if not rec:
                    continue
                local_col = ll_dup_map.get(rec)
                if local_col is None:
                    base_name = rec.name.split('.')[0]
                    local_col = bpy.data.collections.get(base_name)
                    if not local_col:
                        local_col = bpy.data.collections.new(base_name)
                if local_col is rec:
                    continue
                local_col.use_fake_user = True
                setattr(ll, prop, local_col)
                rebound_count += 1

        # 5. Clean up non-light appended geometry objects that are not +LGT+ content.
        cleaned_obj_count = 0
        for obj in list(appended_objs):
            if obj.type not in {'LIGHT', 'LIGHT_PROBE'} and obj not in kept_objs:
                cleaned_obj_count += 1
                bpy.data.objects.remove(obj, do_unlink=True)

        # +LGT+ content keeps the LL- membership it had in the source: hand it from the duplicate
        # over to the canonical group before the duplicate goes.
        for dup, canonical in ll_dup_map.items():
            for o in list(dup.objects):
                if o in content_objs and o not in set(canonical.objects):
                    canonical.objects.link(o)
            for ch in list(dup.children):
                if ch in content_cols and ch not in set(canonical.children):
                    canonical.children.link(ch)

        # 6. Drop the now-empty duplicates and any leftover appended scaffolding collections.
        #    Canonical LL- collections are never removed here, even when empty - they are the pool.
        for col in list(appended_cols):
            if col not in set(bpy.data.collections):
                continue
            # Linked collections are never scaffolding: an instanced asset can look empty when its
            # library is unreachable (e.g. S:\ on a Mac) and would be lost for good.
            if col is target_container or col in content_cols or col.library or col in kept_cols:
                continue
            if col in ll_dup_map:
                bpy.data.collections.remove(col, do_unlink=True)
                continue
            if is_light_group_name(col.name):
                col.use_fake_user = True
                continue
            if not col.objects and not col.children:
                bpy.data.collections.remove(col, do_unlink=True)

        # 6a. Membership that only rode along with the append (the source's linked originals, which are
        #     stale there as well) is dropped; the transfer below re-creates the real one.
        for col in list(bpy.data.collections):
            if not is_light_group_name(col.name) or col.name != col.name.split('.')[0]:
                continue
            keep_objs, keep_children = existing_ll_members.get(col.as_pointer(), (set(), set()))
            for o in list(col.objects):
                if o.as_pointer() not in keep_objs and o not in content_objs:
                    try:
                        col.objects.unlink(o)
                    except Exception:
                        pass
            for ch in list(col.children):
                if ch.as_pointer() not in keep_children and ch not in content_cols:
                    try:
                        col.children.unlink(ch)
                    except Exception:
                        pass

        # 6b. Put the same datablocks into the same LL- groups in this file.
        transfer = apply_source_assignments(source_assignments, target_scene, existing_objs, existing_cols)

        # 6c. The source view layer's lgt* custom properties go onto the active view layer.
        src_vl = source_assignments.get("view_layer") or {}
        vl_written = apply_view_layer_props(context.view_layer, src_vl.get("props"))
        if vl_written:
            log.info(f"View layer '{context.view_layer.name}': copied {vl_written} from source layer '{src_vl.get('name')}'")
        if src_vl.get("skipped"):
            log.info(f"View layer properties not copied (not {VIEW_LAYER_PROP_PREFIX}*): {src_vl['skipped']}")

        # 7. Nothing but +LGT+ may show up in the outliner, and no dead library reference stays behind.
        purge_light_groups_from_outliner()
        merged_libs = merge_duplicate_libraries(existing_libs)
        if merged_libs:
            log.info(f"Redirected {merged_libs} datablocks from duplicate library entries")
        purged = purge_new_orphans(existing_ids)
        remove_orphan_libraries(existing_libs)

        alive = set(bpy.data.objects)
        appended_light_count = sum(1 for o in appended_objs if o in alive and o.type in {'LIGHT', 'LIGHT_PROBE'})
        content_count = sum(1 for o in content_objs if o in alive and o.type not in {'LIGHT', 'LIGHT_PROBE'})
        skipped = source_assignments.get("skipped_overrides") or []
        if skipped:
            log.warning(f"+LGT+ overrides not appended ({len(skipped)}): {skipped[:5]}")
        transfer_msg = f"groups placed on {transfer['applied']} objects"
        if transfer["membership_only"]:
            transfer_msg += f" + {transfer['membership_only']} linked objects"
        if transfer["collections"]:
            transfer_msg += f" + {transfer['collections']} collections"
        for key, label in (("missing", "not found"), ("ambiguous", "ambiguous")):
            if transfer[key]:
                transfer_msg += f", {len(transfer[key])} {label}"
        target_scene[SCENE_PROP_SOURCE] = os.path.basename(src_path)
        shutil.rmtree(tempdir, ignore_errors=True)
        if skipped:
            self.report({'WARNING'}, f"{len(skipped)} library overrides in the source +LGT+ were not appended "
                                     f"(e.g. '{skipped[0]}'). Link or override them here instead.")
        if vl_written:
            self.report({'INFO'}, f"Copied {len(vl_written)} view layer properties: {', '.join(vl_written)}.")
        if old_cols:
            self.report({'INFO'}, f"Replaced the old light setup ({old_cols} collections, {old_objs} objects removed).")
        self.report({'INFO'}, f"Appended {appended_light_count} lights and {content_count} other +LGT+ objects from '{os.path.basename(src_path)}' ({transfer_msg}, rebound {rebound_count} links, merged {len(ll_dup_map)} LL- groups, cleaned {cleaned_obj_count} objects + {purged} leftover datablocks).")
        return {'FINISHED'}

class KRUTART_OT_initialize_light_groups(Operator):
    """Registers the 14 standard light groups on the active View Layer and scene collections."""
    bl_idname = "krutart.initialize_light_groups"
    bl_label = "Init Light Groups"
    bl_description = "Ensures all 14 standard light groups are registered on the View Layer and available in native light group menus"

    def execute(self, context):
        ensure_light_groups_initialized(context.scene, context.view_layer, force=True)
        self.report({'INFO'}, "Initialized standard light groups on View Layer.")
        return {'FINISHED'}

class KRUTART_OT_cleanup_light_groups(Operator):
    """Removes the LL-/SL- groups this add-on added to a file that should not have them."""
    bl_idname = "krutart.cleanup_light_groups"
    bl_label = "Clean Up LL Light Groups"
    bl_description = "Removes every LL- and SL- collection and LL- view layer lightgroup from this file. Objects and their custom properties are left untouched"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        removed_cols = []
        for col in list(bpy.data.collections):
            if not col.name.startswith(LINK_GROUP_PREFIXES) or col.library:
                continue
            removed_cols.append(col.name)
            col.use_fake_user = False
            try:
                bpy.data.collections.remove(col)
            except Exception as e:
                log.warning(f"Could not remove collection '{col.name}': {e}")

        removed_groups = 0
        for scene in bpy.data.scenes:
            for view_layer in scene.view_layers:
                for _ in range(200):
                    idx = next((i for i, lg in enumerate(view_layer.lightgroups) if lg.name.startswith("LL-")), None)
                    if idx is None:
                        break
                    view_layer.active_lightgroup_index = idx
                    try:
                        with context.temp_override(scene=scene, view_layer=view_layer):
                            bpy.ops.scene.view_layer_remove_lightgroup()
                        removed_groups += 1
                    except Exception as e:
                        log.warning(f"Could not remove lightgroup '{view_layer.lightgroups[idx].name}': {e}")
                        break

        leftover = sum(1 for o in bpy.data.objects
                       if o.get(CUSTOM_PROP_DELTA) or (getattr(o, "krutart_light_link", None) and get_groups(o.krutart_light_link)))
        cache = bpy.data.texts.get(DATA_BLOCK_NAME)
        msg = f"Removed {len(removed_cols)} LL-/SL- collections and {removed_groups} view layer lightgroups."
        if leftover or cache:
            msg += f" Left untouched: {leftover} objects with light group properties"
            msg += " and the cached sheet data text block." if cache else "."
        self.report({'INFO'}, msg)
        log.info(msg + (f" Collections: {removed_cols[:10]}" if removed_cols else ""))
        return {'FINISHED'}

class KRUTART_PT_light_link_data_panel(Panel):
    """UI Panel for Light Data in Properties > Light (Data context)."""
    bl_label = "Krutart Light Links"
    bl_idname = "KRUTART_PT_light_link_data_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.light is not None

    def draw(self, context):
        obj = context.object
        if obj:
            KRUTART_PT_light_link_panel.draw_panel_content(self.layout, context, obj)
        else:
            self.layout.label(text="Select a light object.")

# --- Registration ---

classes = (
    KrutartObjectLightSettings,
    KrutartLightLinkPreferences,
    KRUTART_OT_save_asset_data,
    KRUTART_OT_fetch_light_links,
    KRUTART_OT_fetch_sheet_defaults,
    KRUTART_OT_export_light_link_data,
    KRUTART_OT_import_light_link_data,
    KRUTART_OT_initialize_light_groups,
    KRUTART_OT_cleanup_light_groups,
    KRUTART_OT_make_local_override,
    KRUTART_OT_revert_to_library,
    KRUTART_OT_add_group_item,
    KRUTART_OT_new_light_group,
    KRUTART_MT_add_light_group,
    KRUTART_OT_set_light_group,
    KRUTART_MT_add_shadow_group,
    KRUTART_OT_toggle_section,
    KRUTART_OT_remove_group_item,
    KRUTART_OT_append_lgt_structure,
    KRUTART_PT_light_link_panel,
    KRUTART_PT_light_link_collection_panel,
    KRUTART_PT_light_link_data_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Object.krutart_light_link = bpy.props.PointerProperty(type=KrutartObjectLightSettings, override={'LIBRARY_OVERRIDABLE'})
    
    # Re-arm the automatic sync for every file that gets opened
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)

    # Schedule background auto-fetch 15 seconds after registration
    schedule_auto_fetch()

def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)

    for timer in (_auto_fetch_timer, _apply_local_cache_to_scene, _retry_hero_llcols):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
        
    del bpy.types.Object.krutart_light_link
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

