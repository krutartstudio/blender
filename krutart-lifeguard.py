bl_info = {
    "name": "Krutart Lifeguard",
    "author": "iori, Krutart, Claude",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "Top Bar (right side). Files are claimed automatically on open.",
    "description": "Shows who is working in a company .blend file and guards it: only the first artist in (the owner) can overwrite it. Saves by anyone else are redirected to a __CONFLICT copy and the shot is restored untouched.",
    "warning": "",
    "doc_url": "",
    "category": "System",
}

import bpy
import atexit
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import unicodedata
from pathlib import Path
from bpy.app.handlers import persistent
from bpy.props import BoolProperty
from bpy.types import AddonPreferences, Operator

# --- Constants ---

# A file is a "company file" when one of these folders is in its path.
ANCHORS = ("3212-PREPRODUCTION", "3212-PRODUCTION")
# Presence records always live in PREPRODUCTION, next to MISC/IDENTIFIER.
PRESENCE_HOME = "3212-PREPRODUCTION"
PRESENCE_SUBDIR = ("MISC", "LIFEGUARD")

SCAN_S = 10         # how often the worker re-reads who else is in the file
HEARTBEAT_S = 45    # how often we re-stamp our own record
STALE_S = 240       # a record with no heartbeat for this long is a crashed session
PURGE_S = 86400     # stale records older than this get deleted by whoever sees them
MTIME_SLACK_NS = 2_000_000_000  # SMB / FAT timestamps are only good to 2 s

# Headless Blender (render farm, batch scripts) never claims files. Tests set this to opt in.
TEST_ENV = "KRUTART_LIFEGUARD_TEST"

LOG = "[Krutart Lifeguard]"


# --- Identity (same source of truth as Publisher) ---

def get_hostname():
    hn = socket.gethostname().lower()
    if hn.endswith(".local"):
        hn = hn[:-6]
    return hn


def get_current_user():
    user_name = None
    configurator_mod = sys.modules.get('krutart-configurator')
    if configurator_mod is None:
        for mod in list(sys.modules.values()):
            info = getattr(mod, "bl_info", None)
            if isinstance(info, dict) and "Configurator" in info.get("name", ""):
                configurator_mod = mod
                break

    if configurator_mod:
        try:
            addon = bpy.context.preferences.addons.get(configurator_mod.__name__)
            if addon and addon.preferences.user_name_override.strip():
                user_name = addon.preferences.user_name_override.strip()
            else:
                cached_map = getattr(configurator_mod, "CACHED_IDENTITY_MAP", {})
                # Macs registered by Configurator < 2.0 are keyed with their ".local" suffix.
                host = get_hostname()
                user_name = cached_map.get(host) or cached_map.get(host + ".local")
        except Exception:
            pass

    if not user_name:
        user_name = get_hostname()
    return re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)


SESSION_ID = f"{get_hostname()}-{os.getpid()}-{int(time.time())}"


# --- Path helpers ---

def split_company_path(filepath):
    """
    Returns (presence_root: Path, rel: str) for a company file, else None.
    rel is the path from the anchor folder down, so S:\\3212-PRODUCTION\\x.blend and
    ~/Library/CloudStorage/.../Shared drives/3212-PRODUCTION/x.blend give the same rel.
    """
    if not filepath:
        return None
    parts = filepath.replace("\\", "/").split("/")
    for i, part in enumerate(parts):
        if part in ANCHORS:
            base = "/".join(parts[:i]) or "/"
            if base.endswith(":"):
                base += "/"
            home = Path(base) / PRESENCE_HOME
            rel = "/".join(parts[i:])
            return home.joinpath(*PRESENCE_SUBDIR), rel
    return None


def file_key(rel):
    """Folder name for one file's records. NFC + lowercase so Mac and Windows agree."""
    norm = unicodedata.normalize("NFC", rel).lower()
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
    base = re.sub(r'[^a-zA-Z0-9_.-]', '_', norm.rsplit("/", 1)[-1])[:60]
    return f"{digest}_{base}"


def same_path(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def fingerprint(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def same_fingerprint(a, b):
    if a is None or b is None:
        return a is b
    return a[0] == b[0] and abs(a[1] - b[1]) <= MTIME_SLACK_NS


# --- Session state (module level, never saved into the .blend) ---

class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()
        self.force_next = None     # path to claim by force on the next load
        self.pending_save = None   # save_pre -> save_post hand-off
        self.notice = None         # lines waiting to be shown as a popup
        self.flash_until = 0.0     # top bar alert highlight for the owner

    def reset(self):
        self.dir = None            # Path of this file's record folder
        self.path = None           # bpy.data.filepath we are registered for
        self.rel = None
        self.record = None         # our own record dict
        self.others = []           # live records of other sessions (worker-updated)
        self.loaded_fp = None      # disk fingerprint of the file as we last loaded/saved it
        self.error = None
        self.known_sessions = set()


S = _State()


def prefs():
    addon = bpy.context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


def is_allowed():
    if bpy.app.background and not os.environ.get(TEST_ENV):
        return False
    p = prefs()
    return p.enabled if p else True


# --- Record I/O (plain file access, safe to call from the worker thread) ---

def pid_alive(pid):
    """Is a process of THIS machine still running? Any doubt counts as alive."""
    try:
        pid = int(pid)
        if sys.platform.startswith("win"):
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such pid
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            return (not ok) or code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def write_record(dirpath, record):
    dirpath.mkdir(parents=True, exist_ok=True)
    record["heartbeat"] = time.time()
    with open(dirpath / f"{record['session']}.json", "w", encoding="utf-8") as f:
        json.dump(record, f)


def read_records(dirpath, skip_session=None):
    """Live records in one file's folder. Deletes records nobody will ever come back for."""
    live = []
    now = time.time()
    try:
        entries = list(os.scandir(dirpath))
    except FileNotFoundError:
        return live
    for entry in entries:
        if not entry.name.endswith(".json"):
            continue
        try:
            with open(entry.path, "r", encoding="utf-8") as f:
                rec = json.load(f)
            last_seen = max(rec.get("heartbeat", 0), entry.stat().st_mtime)
        except (OSError, ValueError):
            continue  # half-written or half-synced, catch it on the next scan
        if rec.get("session") == skip_session:
            continue
        if rec.get("host") == get_hostname() and rec.get("pid") and not pid_alive(rec["pid"]):
            # A crashed Blender on this very machine: no need to wait for the stale timeout,
            # the artist is most likely reopening the file right now.
            try:
                os.remove(entry.path)
            except OSError:
                pass
            continue
        age = now - last_seen
        if age > PURGE_S:
            try:
                os.remove(entry.path)
            except OSError:
                pass
        elif age <= STALE_S:
            live.append(rec)
    return live


def elect_owner(records):
    """
    Every session runs this on the same data and gets the same answer.
    A forced take-over beats a normal claim; otherwise the earliest claim wins.
    """
    owners = [r for r in records if r.get("role") == "owner"]
    if not owners:
        return None
    return min(owners, key=lambda r: (-r.get("force_at", 0), r.get("claimed_at", 0), r.get("session", "")))


def remove_record(dirpath, session):
    try:
        os.remove(dirpath / f"{session}.json")
    except OSError:
        pass
    try:
        dirpath.rmdir()  # only succeeds when we were the last one in
    except OSError:
        pass


# --- Entering / leaving a file ---

def enter_file(filepath, force=False):
    leave_file()
    if not is_allowed():
        return
    info = split_company_path(filepath)
    if not info:
        return
    root, rel = info
    # Set before anything can fail: even with the records folder unreachable, the
    # "file changed on disk behind our back" check in save_pre keeps working.
    S.path = filepath
    S.loaded_fp = fingerprint(filepath)
    if not root.parent.parent.exists():
        # PREPRODUCTION is not mounted here; do not invent a folder tree for it.
        S.error = f"{PRESENCE_HOME} not reachable"
        print(f"{LOG} {S.error}; presence disabled for this file.")
        return

    dirpath = root / file_key(rel)
    now = time.time()
    try:
        others = read_records(dirpath, skip_session=SESSION_ID)
        owner = elect_owner(others)
        record = {
            "session": SESSION_ID,
            "user": get_current_user(),
            "host": get_hostname(),
            "pid": os.getpid(),
            "file": rel,
            "role": "owner" if (force or owner is None) else "guest",
            "claimed_at": now,
            "force_at": now if force else 0,
            "blender": bpy.app.version_string,
        }
        write_record(dirpath, record)
    except OSError as e:
        S.error = str(e)
        print(f"{LOG} Could not register presence: {e}")
        return

    with S.lock:
        S.dir = dirpath
        S.path = filepath
        S.rel = rel
        S.record = record
        S.others = others
        S.loaded_fp = fingerprint(filepath)
        S.error = None
        S.known_sessions = {r["session"] for r in others}

    print(f"{LOG} {record['user']} entered {rel} as {record['role'].upper()}"
          + (f" (owner: {owner['user']})" if owner and not force else ""))

    if record["role"] == "guest":
        since = time.strftime("%H:%M", time.localtime(owner.get("claimed_at", now)))
        queue_notice([
            f"{owner['user']} is working in this file (since {since}).",
            "You are a GUEST here: look around freely.",
            "If you save, your version goes to a separate __CONFLICT file",
            "and the shot itself stays untouched.",
        ])


def leave_file():
    with S.lock:
        dirpath, had_record = S.dir, S.record is not None
        S.reset()
    if dirpath and had_record:
        remove_record(dirpath, SESSION_ID)


_io_lock = threading.Lock()


def flush_record():
    """
    Write our record to disk. S.lock is only held to copy the dict, never across network
    I/O, because the top bar draw takes S.lock and must not wait on a slow NAS.
    """
    with _io_lock:
        with S.lock:
            dirpath = S.dir
            record = dict(S.record) if S.record else None
        if not dirpath or not record:
            return
        try:
            write_record(dirpath, record)
            with S.lock:
                if S.record and S.dir == dirpath:
                    S.record["heartbeat"] = record["heartbeat"]
        except OSError as e:
            print(f"{LOG} Could not update record: {e}")


def set_role(role, force=False):
    now = time.time()
    with S.lock:
        if not S.record:
            return
        S.record["role"] = role
        S.record["claimed_at"] = now
        S.record["force_at"] = now if force else 0
    flush_record()


def current_owner():
    """The live owner other than us, or None."""
    with S.lock:
        return elect_owner(S.others)


# --- Worker thread: all periodic network I/O happens here, never on the UI thread ---

_stop_worker = threading.Event()
_worker = None


def refresh():
    """One scan + heartbeat if due. Called by the worker; tests call it directly."""
    with S.lock:
        dirpath = S.dir
        record = dict(S.record) if S.record else None
    if not dirpath or not record:
        return
    try:
        others = read_records(dirpath, skip_session=SESSION_ID)
        with S.lock:
            if S.dir != dirpath:
                return
            S.others = others
            due = bool(S.record) and time.time() - S.record.get("heartbeat", 0) >= HEARTBEAT_S
        if due:
            flush_record()
    except OSError as e:
        with S.lock:
            S.error = str(e)


def _worker_loop():
    while not _stop_worker.wait(SCAN_S):
        try:
            refresh()
        except Exception as e:
            print(f"{LOG} worker error: {e}")


# --- UI thread tick: turns scan results into role changes and notices ---

def apply_transitions():
    with S.lock:
        record = dict(S.record) if S.record else None
        others = list(S.others)
    if not record:
        return

    # The Configurator may load its identity map after we registered; follow it.
    user = get_current_user()
    if user != record["user"]:
        with S.lock:
            if S.record:
                S.record["user"] = user
        record["user"] = user
        flush_record()

    if record["role"] == "owner":
        winner = elect_owner(others + [record])
        if winner and winner["session"] != SESSION_ID:
            # Two of us claimed in the same moment (or they took over). Same rule on both
            # sides, so exactly one of us steps down.
            set_role("guest")
            how = "took over this file" if winner.get("force_at") else "opened this file at the same moment and holds it"
            queue_notice([
                f"{winner['user']} {how}.",
                "You are now a GUEST: your saves will go to a __CONFLICT file,",
                "the shot itself stays untouched. Talk to each other.",
            ])
        newcomers = [r for r in others if r["session"] not in S.known_sessions]
        if newcomers:
            S.flash_until = time.time() + 15
            print(f"{LOG} Also here now: {', '.join(r['user'] for r in newcomers)}")
    S.known_sessions = {r["session"] for r in others}


def queue_notice(lines):
    S.notice = lines
    for line in lines:
        print(f"{LOG} {line}")


_last_drawn = None


def redraw_topbar_if_changed():
    """
    The top bar is a global area: it is not in screen.areas, so it cannot be tag_redraw()n.
    Re-assigning a theme colour to itself sends a window-wide redraw instead. Only done when
    what we display actually changed, and without leaving the preferences marked dirty.
    """
    global _last_drawn
    with S.lock:
        role = S.record["role"] if S.record else None
        shown = (role, tuple(sorted((r["user"], r["role"]) for r in S.others)), time.time() < S.flash_until, S.error)
    if shown == _last_drawn:
        return
    _last_drawn = shown
    preferences = bpy.context.preferences
    was_dirty = preferences.is_dirty
    space = preferences.themes[0].view_3d.space
    space.header = space.header[:]
    if not was_dirty:
        preferences.is_dirty = False


def _ui_tick():
    try:
        apply_transitions()
        if S.notice and not bpy.app.background:
            p = prefs()
            wm = bpy.context.window_manager
            if (p is None or p.show_popups) and wm.windows:
                with bpy.context.temp_override(window=wm.windows[0]):
                    bpy.ops.krutart.lifeguard_notice('INVOKE_DEFAULT')
            else:
                S.notice = None
        redraw_topbar_if_changed()
    except Exception as e:
        print(f"{LOG} tick error: {e}")
    return 2.0


# --- Save guard ---

def snapshot(src, dst):
    """Hardlink when the volume allows it (instant, any file size), else copy."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def foreign_holder(target):
    """Name of whoever else holds `target`, or None when we may write to it."""
    if same_path(target, S.path):
        with S.lock:
            if not S.record or S.record["role"] != "guest":
                return None
            owner = elect_owner(S.others)
        return owner["user"] if owner else "Someone else"
    info = split_company_path(target)
    if not info:
        return None
    root, rel = info
    try:
        owner = elect_owner(read_records(root / file_key(rel), skip_session=SESSION_ID))
    except OSError:
        return None
    return owner["user"] if owner else None


@persistent
def on_save_pre(*args):
    S.pending_save = None
    if not is_allowed():
        return
    target = (args[0] if args and isinstance(args[0], str) else "") or bpy.data.filepath
    if not target or not os.path.exists(target):
        return  # nothing on disk to protect

    try:
        holder = foreign_holder(target)
        if holder:
            guard = f"{target}.guard_{SESSION_ID}"
            snapshot(target, guard)
            guard1 = None
            if os.path.exists(target + "1"):
                guard1 = f"{target}1.guard_{SESSION_ID}"
                snapshot(target + "1", guard1)
            S.pending_save = {"mode": "guest", "target": target, "guard": guard, "guard1": guard1, "holder": holder}

        elif same_path(target, S.path) and S.loaded_fp and not same_fingerprint(fingerprint(target), S.loaded_fp):
            # We own the file, yet it changed on disk since we loaded it: somebody without
            # the guard wrote to it. Keep their version before ours replaces it.
            stem, ext = os.path.splitext(target)
            foreign = f"{stem}__FOREIGN_{time.strftime('%Y%m%d-%H%M%S')}{ext}"
            snapshot(target, foreign)
            S.pending_save = {"mode": "foreign", "target": target, "foreign": foreign}
    except Exception as e:
        # Whatever went wrong, the artist's save itself must go ahead untouched.
        S.pending_save = None
        print(f"{LOG} SAVE GUARD could not snapshot {target}: {e!r}")


@persistent
def on_save_post(*args):
    pending, S.pending_save = S.pending_save, None
    target = (args[0] if args and isinstance(args[0], str) else "") or bpy.data.filepath

    if pending and pending["mode"] == "guest":
        target = pending["target"]
        stem, ext = os.path.splitext(target)
        conflict = f"{stem}__CONFLICT_{get_current_user()}_{time.strftime('%Y%m%d-%H%M%S')}{ext}"
        try:
            os.replace(target, conflict)
            os.replace(pending["guard"], target)
            if pending["guard1"]:
                os.replace(pending["guard1"], target + "1")
            queue_notice([
                f"{pending['holder']} holds {os.path.basename(target)}, so it was NOT overwritten.",
                "Your save is here instead:",
                os.path.basename(conflict),
            ])
        except Exception as e:
            queue_notice([
                "SAVE GUARD FAILED to restore the shot!",
                f"Original is kept as: {os.path.basename(pending['guard'])}",
                f"Tell the pipeline TD. ({e})",
            ])

    elif pending and pending["mode"] == "foreign":
        queue_notice([
            "This file was changed on disk by someone else while you had it open.",
            "Their version was kept as:",
            os.path.basename(pending["foreign"]),
        ])

    if not is_allowed():
        return
    try:
        if not same_path(bpy.data.filepath, S.path):
            # Save As / incremental save: our claim follows us to the new file.
            enter_file(bpy.data.filepath)
        elif same_path(target, S.path):
            S.loaded_fp = fingerprint(target)
    except Exception as e:
        print(f"{LOG} post-save bookkeeping failed: {e!r}")


@persistent
def on_load_pre(*args):
    try:
        leave_file()
    except Exception as e:
        print(f"{LOG} leave failed: {e!r}")


@persistent
def on_load_post(*args):
    try:
        force = same_path(S.force_next, bpy.data.filepath)
        S.force_next = None
        enter_file(bpy.data.filepath, force=force)
    except Exception as e:
        S.error = repr(e)
        print(f"{LOG} could not enter file: {e!r}")


# --- Operators ---

class KRUTART_OT_lifeguard_notice(Operator):
    bl_idname = "krutart.lifeguard_notice"
    bl_label = "Krutart Lifeguard"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self.lines = S.notice or []
        S.notice = None
        if not self.lines:
            return {'CANCELLED'}
        # A dialog, not invoke_popup: a popup closes as soon as the mouse drifts off it,
        # and this message must not be missed.
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        for i, line in enumerate(self.lines):
            row = col.row()
            ok = line.startswith("OK")
            row.alert = (i == 0 and not ok) or line.startswith(("FAIL", "WARN"))
            row.label(text=line, icon=('CHECKMARK' if ok else 'ERROR') if i == 0 else 'NONE')

    def execute(self, context):
        return {'FINISHED'}


class KRUTART_OT_lifeguard_status(Operator):
    bl_idname = "krutart.lifeguard_status"
    bl_label = "Who is in this file"
    bl_description = "Show everyone who has this file open"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=420)

    def draw(self, context):
        layout = self.layout
        with S.lock:
            record = dict(S.record) if S.record else None
            others = list(S.others)
        if not record:
            if S.error and S.path:
                layout.label(text="Lifeguard cannot reach its records, nobody can see you in this file.", icon='ERROR')
                layout.label(text=S.error)
                layout.operator("krutart.lifeguard_selftest", icon='CHECKMARK')
            else:
                layout.label(text="Not a company file.", icon='INFO')
            return

        col = layout.column(align=True)
        for rec in sorted([record] + others, key=lambda r: r.get("claimed_at", 0)):
            since = time.strftime("%H:%M", time.localtime(rec.get("claimed_at", 0)))
            you = "  (you)" if rec["session"] == SESSION_ID else ""
            icon = 'UNLOCKED' if rec["role"] == "owner" else 'HIDE_OFF'
            col.label(text=f"{rec['user']}{you} - {rec['role']} since {since} on {rec['host']}", icon=icon)

        if record["role"] == "guest":
            layout.separator()
            if elect_owner(others) is None:
                layout.label(text="The owner left. Reload to get their latest save and take the file.")
                layout.operator("krutart.lifeguard_reload_take", icon='FILE_REFRESH')
            else:
                layout.operator("krutart.lifeguard_take_over", icon='ERROR')
        layout.separator()
        layout.operator("krutart.lifeguard_whos_where", icon='COMMUNITY')

    def execute(self, context):
        return {'FINISHED'}


def _deferred_revert():
    bpy.ops.wm.revert_mainfile()
    return None


class KRUTART_OT_lifeguard_reload_take(Operator):
    bl_idname = "krutart.lifeguard_reload_take"
    bl_label = "Reload and Take File"
    bl_description = "Reload the file from disk and become its owner. Unsaved changes are lost"

    def execute(self, context):
        bpy.app.timers.register(_deferred_revert, first_interval=0.1)
        return {'FINISHED'}


class KRUTART_OT_lifeguard_take_over(Operator):
    bl_idname = "krutart.lifeguard_take_over"
    bl_label = "Take Over From Owner"
    bl_description = "Become the owner even though someone else holds the file. They are demoted to guest"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        owner = current_owner()
        col = self.layout.column()
        col.alert = True
        col.label(text=f"{owner['user'] if owner else 'The owner'} will lose the right to save this file.", icon='ERROR')
        col.label(text="Only do this if you have agreed on it with them.")
        if not same_fingerprint(fingerprint(S.path), S.loaded_fp):
            col.label(text="The file changed since you opened it: it will be reloaded first.")

    def execute(self, context):
        if same_fingerprint(fingerprint(S.path), S.loaded_fp):
            # What we have in memory is based on what is on disk, safe to promote in place.
            set_role("owner", force=True)
        else:
            S.force_next = S.path
            bpy.app.timers.register(_deferred_revert, first_interval=0.1)
        return {'FINISHED'}


class KRUTART_OT_lifeguard_whos_where(Operator):
    bl_idname = "krutart.lifeguard_whos_where"
    bl_label = "Who's Where (all files)"
    bl_description = "List every company file that is open right now and who is in it"

    def invoke(self, context, event):
        self.rows = []
        root = S.dir.parent if S.dir else None
        if root is None:
            info = split_company_path(bpy.data.filepath)
            root = info[0] if info else None
        if root is None:
            configurator = sys.modules.get('krutart-configurator')
            company_root = configurator.get_company_root() if configurator else None
            root = company_root.joinpath(*PRESENCE_SUBDIR) if company_root else None
        if root is None or not root.exists():
            self.report({'WARNING'}, "Lifeguard folder not found.")
            return {'CANCELLED'}
        try:
            for entry in os.scandir(root):
                if entry.is_dir():
                    for rec in read_records(Path(entry.path)):
                        self.rows.append(rec)
        except OSError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.rows.sort(key=lambda r: (r.get("file", ""), r.get("claimed_at", 0)))
        return context.window_manager.invoke_popup(self, width=640)

    def draw(self, context):
        col = self.layout.column(align=True)
        if not self.rows:
            col.label(text="Nobody has a company file open.", icon='INFO')
        for rec in self.rows:
            icon = 'UNLOCKED' if rec.get("role") == "owner" else 'HIDE_OFF'
            col.label(text=f"{rec.get('user', '?'):<12} {rec.get('role', '?'):<6} {rec.get('file', '?')}", icon=icon)

    def execute(self, context):
        return {'FINISHED'}


# --- Self-test: everything that differs from workstation to workstation ---

def run_selftest():
    """Returns report lines, each starting with OK / WARN / FAIL. Leaves nothing behind."""
    lines = []
    info = split_company_path(bpy.data.filepath)
    root = info[0] if info else None
    if root is None:
        configurator = sys.modules.get('krutart-configurator')
        get_root = getattr(configurator, "get_company_root", None)
        company_root = get_root() if get_root else None
        root = company_root.joinpath(*PRESENCE_SUBDIR) if company_root else None
    if root is None or not root.parent.parent.exists():
        return [f"FAIL company root not found ({root}). Open a company file and run again."]

    lines.append(f"OK   {sys.platform}, Blender {bpy.app.version_string}, records in {root}")
    user, host = get_current_user(), get_hostname()
    if user == re.sub(r'[^a-zA-Z0-9_-]', '_', host):
        lines.append(f"WARN identity: '{host}' is not registered, others will see the hostname. Register it in Configurator")
    else:
        lines.append(f"OK   identity: {user} on {host}")

    probe_dir = root / f"_selftest_{host}_{os.getpid()}"
    try:
        start = time.time()
        write_record(probe_dir, {"session": "selftest", "role": "guest", "user": user, "host": "selftest-host"})
        found = read_records(probe_dir)
        took = (time.time() - start) * 1000
        if len(found) == 1:
            lines.append(f"OK   records folder: write + read back in {took:.0f} ms")
        else:
            lines.append("FAIL records folder: wrote a record but could not read it back")

        probe = probe_dir / "selftest.json"
        skew = os.stat(probe).st_mtime - time.time()
        if abs(skew) > STALE_S / 2:
            lines.append(f"FAIL clock: this machine and the storage disagree by {skew:+.0f} s. Others will think you crashed. Fix time sync")
        elif abs(skew) > 30:
            lines.append(f"WARN clock: {skew:+.0f} s off from the storage. Works, but enable time sync")
        else:
            lines.append(f"OK   clock: {skew:+.1f} s from the storage")

        link = probe_dir / "selftest.link"
        try:
            os.link(probe, link)
            lines.append("OK   hardlinks: supported, protecting a save is instant at any file size")
        except OSError:
            shutil.copy2(probe, link)
            lines.append("WARN hardlinks: not supported here, a redirected save first COPIES the shot (slow on big files, still safe)")
        os.replace(link, probe)
        lines.append("OK   replace-over-existing file works")
    except OSError as e:
        lines.append(f"FAIL records folder: {e}")
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    try:
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        if pid_alive(os.getpid()) and not pid_alive(child.pid):
            lines.append("OK   crash detection: running / finished processes told apart")
        else:
            lines.append("WARN crash detection unreliable here: after a crash you wait out the 4 minute timeout (or use Take Over)")
    except Exception as e:
        lines.append(f"WARN crash detection could not be tested: {e}")

    problems = [l for l in lines if not l.startswith("OK")]
    lines.insert(0, "OK - Lifeguard self-test passed" if not problems else f"Lifeguard self-test: {len(problems)} thing(s) to look at")
    return lines


class KRUTART_OT_lifeguard_selftest(Operator):
    bl_idname = "krutart.lifeguard_selftest"
    bl_label = "Run Self-Test"
    bl_description = "Check this workstation: records folder, clock, hardlinks, crash detection, identity. The report is copied to the clipboard"

    def execute(self, context):
        lines = run_selftest()
        context.window_manager.clipboard = "\n".join(lines)
        queue_notice(lines)
        return {'FINISHED'}


# --- Top bar indicator ---

def draw_topbar(self, context):
    if context.region.alignment != 'RIGHT':
        return
    with S.lock:
        record = S.record
        role = record["role"] if record else None
        others = list(S.others)
        error = S.error
    if not role:
        if error and S.path:
            row = self.layout.row(align=True)
            row.alert = True
            row.operator("krutart.lifeguard_status", text="Lifeguard offline", icon='ERROR')
        return

    row = self.layout.row(align=True)
    if role == "guest":
        owner = elect_owner(others)
        row.alert = True
        text = f"GUEST - {owner['user']} has this file" if owner else "GUEST - owner left, click to take the file"
        row.operator("krutart.lifeguard_status", text=text, icon='LOCKED')
    elif others:
        row.alert = time.time() < S.flash_until
        names = ", ".join(sorted({r["user"] for r in others}))
        row.operator("krutart.lifeguard_status", text=f"Also here: {names}", icon='COMMUNITY')
    else:
        row.operator("krutart.lifeguard_status", text="", icon='UNLOCKED')


# --- Preferences ---

class KrutartLifeguardPreferences(AddonPreferences):
    bl_idname = __name__

    enabled: BoolProperty(
        name="Claim and guard company files",
        default=True,
        description="Register in MISC/LIFEGUARD when opening a company file and protect files held by others",
    )
    show_popups: BoolProperty(
        name="Show popups",
        default=True,
        description="Pop up a notice when you enter a held file or a save was redirected. The top bar label is always shown",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "enabled")
        layout.prop(self, "show_popups")
        layout.label(text=f"You are: {get_current_user()} on {get_hostname()}", icon='USER')
        row = layout.row()
        row.operator("krutart.lifeguard_whos_where", icon='COMMUNITY')
        row.operator("krutart.lifeguard_selftest", icon='CHECKMARK')


# --- Registration ---

classes = (
    KrutartLifeguardPreferences,
    KRUTART_OT_lifeguard_notice,
    KRUTART_OT_lifeguard_status,
    KRUTART_OT_lifeguard_reload_take,
    KRUTART_OT_lifeguard_take_over,
    KRUTART_OT_lifeguard_whos_where,
    KRUTART_OT_lifeguard_selftest,
)


def _enter_current_file():
    if bpy.data.filepath and S.record is None:
        enter_file(bpy.data.filepath)
    return None


def register():
    global _worker
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.app.handlers.load_pre.append(on_load_pre)
    bpy.app.handlers.load_post.append(on_load_post)
    bpy.app.handlers.save_pre.append(on_save_pre)
    bpy.app.handlers.save_post.append(on_save_post)
    bpy.types.TOPBAR_HT_upper_bar.append(draw_topbar)

    _stop_worker.clear()
    _worker = threading.Thread(target=_worker_loop, name="krutart-lifeguard", daemon=True)
    _worker.start()

    bpy.app.timers.register(_ui_tick, first_interval=1.0, persistent=True)
    # Enabled mid-session with a file already open: claim it once Blender is ready.
    bpy.app.timers.register(_enter_current_file, first_interval=0.5)
    atexit.register(leave_file)


def unregister():
    _stop_worker.set()
    if bpy.app.timers.is_registered(_ui_tick):
        bpy.app.timers.unregister(_ui_tick)
    leave_file()
    atexit.unregister(leave_file)

    bpy.types.TOPBAR_HT_upper_bar.remove(draw_topbar)
    for handler_list, fn in (
        (bpy.app.handlers.load_pre, on_load_pre),
        (bpy.app.handlers.load_post, on_load_post),
        (bpy.app.handlers.save_pre, on_save_pre),
        (bpy.app.handlers.save_post, on_save_post),
    ):
        if fn in handler_list:
            handler_list.remove(fn)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
