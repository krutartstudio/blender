import os
import sys
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

bl_info = {
    "name": "Krutart OS Bridge",
    "author": "Krutart, iori, gemini",
    "version": (1, 7, 9),
    "blender": (4, 5, 0),
    "location": "Edit > Preferences > Addons > Krutart OS Bridge",
    "description": "Cross-OS path bridge. Forces canonical Windows paths and auto-fixes Ghost Files.",
    "category": "System",
}

# ------------------------------------------------------------------------
#   CONSTANTS
# ------------------------------------------------------------------------

WIN_PATH_KEY = "krutart_win_source"
PROJECT_NAME = "3212-PREPRODUCTION"
DEFAULT_WIN_DRIVE = "S"

# ------------------------------------------------------------------------
#   UTILITIES
# ------------------------------------------------------------------------


def get_prefs(context):
    addon_id = __name__.partition(".")[0]
    if addon_id in context.preferences.addons:
        return context.preferences.addons[addon_id].preferences
    return None


def get_mac_root(context):
    """
    Finds the local folder ending in '3212-PREPRODUCTION'.
    """
    # 1. Context Aware (Best)
    if context and context.blend_data.filepath:
        curr = Path(context.blend_data.filepath).resolve()
        for p in [curr] + list(curr.parents):
            if p.name == PROJECT_NAME:
                return p
            if p.name == "3212-PRODUCTION":
                candidate = p.parent / PROJECT_NAME
                if candidate.exists():
                    return candidate

    # 2. Manual Override
    prefs = get_prefs(context) if context else None
    if prefs and prefs.mac_root_path:
        p = Path(prefs.mac_root_path).expanduser().resolve()
        if p.exists():
            return p

    # 3. Dynamic CloudStorage Search
    home = Path.home()
    cloud_storage_dir = home / "Library/CloudStorage"
    if cloud_storage_dir.exists():
        for item in cloud_storage_dir.iterdir():
            if item.is_dir() and item.name.startswith("GoogleDrive-"):
                candidate = item / "Shared drives" / PROJECT_NAME
                if candidate.exists():
                    return candidate

    # 4. Standard Volumes Check
    candidates = [
        Path(f"/Volumes/GoogleDrive/Shared drives/{PROJECT_NAME}"),
    ]
    for cand in candidates:
        if cand.exists():
            return cand
            
    return None


def get_win_config(context):
    prefs = get_prefs(context)
    char = (
        prefs.win_drive_char
        if (prefs and hasattr(prefs, "win_drive_char"))
        else DEFAULT_WIN_DRIVE
    )
    return char.upper().replace(":", "").strip() + ":"


def get_win_adaptive_root(context):
    """
    Finds the true local Windows root for fallback, mirroring Mac's context-aware logic.
    """
    if context and context.blend_data.filepath:
        curr = Path(context.blend_data.filepath).resolve()
        for p in [curr] + list(curr.parents):
            if p.name == PROJECT_NAME:
                return p
            if p.name == "3212-PRODUCTION":
                candidate = p.parent / PROJECT_NAME
                if candidate.exists():
                    return candidate
    return None


# ------------------------------------------------------------------------
#   PATH LOGIC
# ------------------------------------------------------------------------

def sanitize_windows_absolute(dirty_path, context):
    """
    Sanitizes any dirty Windows path (IP addresses, network shares, double slashes)
    and forces it to the canonical local Windows drive (e.g. S:\)
    """
    if not dirty_path:
        return None
        
    clean_str = dirty_path.replace("/", "\\")
    win_drive = get_win_config(context)
    
    anchors = [PROJECT_NAME, "3212-PRODUCTION"]
    
    for anchor in anchors:
        idx = clean_str.find(anchor)
        if idx != -1:
            relative_part = clean_str[idx:]
            # e.g., S:\3212-PREPRODUCTION\assets\texture.png
            return f"{win_drive}\\{relative_part}"
            
    return dirty_path


def to_win_adaptive(dirty_path, context):
    """Fallback logic to map a broken path to the true local Windows root."""
    if not dirty_path:
        return None
    root = get_win_adaptive_root(context)
    if not root:
        return None
    
    clean_str = dirty_path.replace("/", "\\")
    anchors = [PROJECT_NAME, "3212-PRODUCTION"]
    
    for anchor in anchors:
        idx = clean_str.find(anchor)
        if idx != -1:
            relative_part = clean_str[idx:]
            target = root.parent / relative_part
            return str(target).replace("/", "\\")
    return None


def to_win_absolute(item_path, context):
    """Local Mac -> S:\3212-PREPRODUCTION\..."""
    if not item_path:
        return None
    if item_path.startswith("//"):
        try:
            item_path = bpy.path.abspath(item_path)
        except:
            return None

    mac_root = get_mac_root(context)
    win_drive = get_win_config(context)

    p = Path(item_path).expanduser().resolve()
    p_str = str(p)

    if p_str.upper().startswith(win_drive):
        return p_str.replace("/", "\\")

    if mac_root:
        # We know mac_root points to 3212-PREPRODUCTION. Its parent is the Shared drives root.
        shared_drives_root = mac_root.parent
        
        # Check for PREPRODUCTION or PRODUCTION
        for anchor in [PROJECT_NAME, "3212-PRODUCTION"]:
            try:
                rel = p.relative_to(shared_drives_root / anchor)
                clean_rel = str(rel).replace("/", "\\")
                return f"{win_drive}\\{anchor}\\{clean_rel}"
            except ValueError:
                pass
                
    return None


def to_mac_absolute(dirty_path, context, force=False):
    """
    Repairs paths by anchoring to the PROJECT_NAME or PRODUCTION_NAME.
    If 'force' is True, returns the calculated path even if file is missing (Ghost Files).
    """
    if not dirty_path:
        return None

    mac_root = get_mac_root(context)  # e.g. /Users/.../3212-PREPRODUCTION
    if not mac_root:
        return dirty_path  # Failsafe fallback

    # Normalize slashes for searching
    clean_str = dirty_path.replace("\\", "/")

    # THE ANCHOR: Find where the project root starts (Preproduction or Production)
    anchors = [PROJECT_NAME, "3212-PRODUCTION"]
    relative_part = None
    
    for anchor in anchors:
        idx = clean_str.find(anchor)
        if idx != -1:
            relative_part = clean_str[idx:]
            break

    if relative_part:
        # Join with PARENT of root (the Volume level)
        target = mac_root.parent / relative_part

        # If we are on Windows, ensure we return a Windows-styled path pointing to the drive
        if sys.platform.startswith("win"):
            return str(target).replace("/", "\\")
        
        # On Mac, ensure we return the absolute path if it exists or if force is true
        if target.exists() or force:
            return str(target)

    return dirty_path  # Final failsafe fallback if anchor not found or file missing on Mac


# ------------------------------------------------------------------------
#   ITERATOR
# ------------------------------------------------------------------------


def iter_external_data():
    for lib in bpy.data.libraries:
        yield lib, "filepath"
    for img in bpy.data.images:
        if img.source in {"FILE", "SEQUENCE", "MOVIE"}:
            yield img, "filepath"
    for cache in bpy.data.cache_files:
        yield cache, "filepath"
    for snd in bpy.data.sounds:
        yield snd, "filepath"
    for font in bpy.data.fonts:
        yield font, "filepath"
    for clip in bpy.data.movieclips:
        yield clip, "filepath"
    for vol in bpy.data.volumes:
        yield vol, "filepath"

    for scene in bpy.data.scenes:
        if not scene.sequence_editor:
            continue
        strips = getattr(
            scene.sequence_editor,
            "sequences_all",
            getattr(scene.sequence_editor, "sequences", []),
        )
        for strip in strips:
            if hasattr(strip, "filepath"):
                yield strip, "filepath"
            if hasattr(strip, "directory"):
                yield strip, "directory"


# ------------------------------------------------------------------------
#   OPERATIONS
# ------------------------------------------------------------------------


def run_bridge_to_mac(context, force=False):
    if sys.platform.startswith("win"):
        return 0
    count = 0
    for item, prop in iter_external_data():
        current_path = getattr(item, prop)
        # We pass 'force' here to bypass exists() check
        new_path = to_mac_absolute(current_path, context, force=force)

        if new_path and new_path != current_path:
            item[WIN_PATH_KEY] = current_path
            
            if isinstance(item, bpy.types.Library):
                try:
                    # 1. Update filepath string directly (crucial for red stubs)
                    item.filepath = new_path
                    
                    # 2. Try the UI operator (handles internal cleanups/syncs)
                    try:
                        bpy.ops.wm.lib_reload(library=item.name)
                    except:
                        pass
                    
                    # 3. Force internal reload if still missing or operator failed
                    if item.is_missing:
                        item.reload()
                        
                except Exception as e:
                    print(f"[Krutart Bridge] Failed to reload library {item.name}: {e}")
            else:
                setattr(item, prop, new_path)
                if hasattr(item, "reload"):
                    try:
                        item.reload()
                    except:
                        pass
                        
            count += 1
    return count


def run_bridge_to_windows(context):
    is_win = sys.platform.startswith("win")
    count = 0
    
    for item, prop in iter_external_data():
        current_path = getattr(item, prop)
        
        if is_win:
            # On Windows, we simply sanitize dirty paths to the canonical drive
            win_path = sanitize_windows_absolute(current_path, context)
        else:
            # On Mac, we map the Mac path to the Windows equivalent before saving
            win_path = to_win_absolute(current_path, context)
            if not win_path and item.get(WIN_PATH_KEY):
                win_path = item[WIN_PATH_KEY]

        if win_path and win_path != current_path:
            setattr(item, prop, win_path)
            count += 1
            
    return count


def run_windows_adaptive_fallback(context):
    if not sys.platform.startswith("win"):
        return 0
    count = 0
    for item, prop in iter_external_data():
        current_path = getattr(item, prop)
        if not current_path:
            continue
            
        try:
            abs_path = bpy.path.abspath(current_path)
        except:
            abs_path = current_path
            
        # Only fallback if the current path is actively broken
        if Path(abs_path).exists():
            continue
            
        new_path = to_win_adaptive(current_path, context)
        if new_path and new_path != current_path and Path(new_path).exists():
            if isinstance(item, bpy.types.Library):
                try:
                    item.filepath = new_path
                    try:
                        bpy.ops.wm.lib_reload(library=item.name)
                    except:
                        pass
                    if item.is_missing:
                        item.reload()
                except Exception as e:
                    print(f"[Krutart Bridge] Failed to reload library {item.name}: {e}")
            else:
                setattr(item, prop, new_path)
                if hasattr(item, "reload"):
                    try:
                        item.reload()
                    except:
                        pass
            count += 1
    return count


# ------------------------------------------------------------------------
#   HANDLERS & UI
# ------------------------------------------------------------------------


@persistent
def on_save_pre(dummy):
    prefs = get_prefs(bpy.context)
    if prefs and prefs.auto_manage:
        c = run_bridge_to_windows(bpy.context)
        if sys.platform.startswith("win"):
            print(f"[Krutart Bridge] Pre-Save: Sanitized {c} paths to canonical Windows drive.")
        else:
            print(f"[Krutart Bridge] Pre-Save: Mapped {c} paths to Windows (S:).")


@persistent
def on_save_post(dummy):
    prefs = get_prefs(bpy.context)
    if sys.platform.startswith("win"):
        if prefs and prefs.auto_manage:
            c = run_windows_adaptive_fallback(bpy.context)
            if c > 0:
                print(f"[Krutart Bridge] Post-Save: Windows adaptive fallback restored {c} broken paths to local root.")
        return
        
    if prefs and prefs.auto_manage:
        # Auto-save uses Safe Mode (force=False)
        c = run_bridge_to_mac(bpy.context, force=False)
        print(f"[Krutart Bridge] Post-Save: Restored {c} paths to Mac")


@persistent
def on_load_post(dummy):
    bpy.app.timers.register(lambda: delayed_load_fix(), first_interval=0.5)


def delayed_load_fix():
    if not bpy.context:
        return None
        
    prefs = get_prefs(bpy.context)
    if prefs and prefs.auto_manage:
        # Load uses Safe Mode (force=False) on Mac, Canonical strict on Win
        if sys.platform.startswith("win"):
            c = run_bridge_to_windows(bpy.context)
            c += run_windows_adaptive_fallback(bpy.context)
        else:
            c = run_bridge_to_mac(bpy.context, force=False)
            
        if c > 0:

            def draw_msg(self, context):
                self.layout.label(text=f"Krutart Bridge: Fixed {c} paths")

            try:
                bpy.context.window_manager.popup_menu(
                    draw_msg, title="OS Bridge", icon="INFO"
                )
            except:
                pass
    return None


class KRUTART_OT_FixPathsMac(bpy.types.Operator):
    """Force fix broken paths (Ignores 'File Not Found' checks)"""
    bl_idname = "krutart.fix_paths_mac"
    bl_label = "Force Fix Paths"

    def execute(self, context):
        # Manual Button uses Force Mode (force=True)
        count = run_bridge_to_mac(context, force=True)
        self.report({"INFO"}, f"Force Fixed {count} paths.")
        return {"FINISHED"}


class KRUTART_OT_FixPathsWin(bpy.types.Operator):
    """Sanitize all messy IP/Network paths to Canonical Windows Drive"""
    bl_idname = "krutart.fix_paths_win"
    bl_label = "Force Canonical Paths"

    def execute(self, context):
        count = run_bridge_to_windows(context)
        self.report({"INFO"}, f"Sanitized {count} paths to Canonical Windows Drive.")
        return {"FINISHED"}


class KRUTART_OT_FixPathsWinFallback(bpy.types.Operator):
    """Force an adaptive fallback to true local root for broken Windows paths"""
    bl_idname = "krutart.fix_paths_win_fallback"
    bl_label = "Force Local Fallback"

    def execute(self, context):
        count = run_windows_adaptive_fallback(context)
        self.report({"INFO"}, f"Adaptive Fallback restored {count} broken paths.")
        return {"FINISHED"}


class KRUTART_OT_Diagnose(bpy.types.Operator):
    """Print path analysis to Console"""
    bl_idname = "krutart.diagnose_paths"
    bl_label = "Diagnose to Console"

    def execute(self, context):
        print("-" * 30)
        print("KRUTART BRIDGE DIAGNOSTIC")
        print("-" * 30)
        
        is_win = sys.platform.startswith("win")
        
        if is_win:
            win_drive = get_win_config(context)
            print(f"Mode: Windows")
            print(f"Canonical Target Drive: {win_drive}")
        else:
            root = get_mac_root(context)
            print(f"Detected Mac Root: {root}")

        for item, prop in iter_external_data():
            raw = getattr(item, prop)
            
            if is_win:
                calc = sanitize_windows_absolute(raw, context)
            else:
                calc = to_mac_absolute(raw, context, force=True)
                
            print(f"Item: {item.name}")
            print(f"  Raw: {raw}")
            print(f"  Calc: {calc}")
            if calc:
                exists = Path(calc).exists()
                print(f"  Exists on Disk: {exists}")

        self.report(
            {"INFO"},
            "Diagnostic printed to System Console (Window > Toggle System Console)",
        )
        return {"FINISHED"}


class KrutartPathPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__.partition(".")[0]
    win_drive_char: bpy.props.StringProperty(name="Win Drive", default="S")
    mac_root_path: bpy.props.StringProperty(
        name="Mac Root Override", subtype="DIR_PATH"
    )
    auto_manage: bpy.props.BoolProperty(name="Auto-Bridge", default=True)

    def draw(self, context):
        layout = self.layout
        
        row = layout.row()
        row.prop(self, "auto_manage")
        
        box = layout.box()
        box.label(text="Path Configuration", icon='SETTINGS')
        col = box.column(align=True)
        col.prop(self, "win_drive_char")
        col.prop(self, "mac_root_path")

        # --- Bridge Status & Operations (Moved from N-Panel) ---
        layout.separator()
        
        status_box = layout.box()
        status_box.label(text="Bridge Status", icon='WORLD')
        
        col = status_box.column(align=True)
        is_win = sys.platform.startswith("win")
        
        if is_win:
            win_drive = get_win_config(context)
            col.label(text="Mode: Windows", icon="FILE_FOLDER")
            col.label(text=f"Canonical Drive: {win_drive}")
        else:
            root = get_mac_root(context)
            if root:
                col.label(text="Active Root:", icon="CHECKMARK")
                col.label(text=root.name)
            else:
                col.alert = True
                col.label(text="Root NOT Found!", icon="ERROR")

        layout.separator()
        
        op_box = layout.box()
        op_box.label(text="Operations", icon='TOOL_SETTINGS')
        col = op_box.column(align=True)
        
        if is_win:
            col.operator("krutart.fix_paths_win", icon="FILE_REFRESH")
            col.operator("krutart.fix_paths_win_fallback", icon="RECOVER_LAST")
        else:
            col.operator("krutart.fix_paths_mac", icon="FILE_REFRESH")
            
        col.operator("krutart.diagnose_paths", icon="CONSOLE")


class KRUTART_PT_Panel(bpy.types.Panel):
    bl_label = "Krutart Bridge"
    bl_idname = "KRUTART_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Krutart"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        is_win = sys.platform.startswith("win")
        
        if is_win:
            win_drive = get_win_config(context)
            col.label(text="Mode: Windows", icon="FILE_FOLDER")
            col.label(text=f"Canonical Drive: {win_drive}")
        else:
            root = get_mac_root(context)
            if root:
                col.label(text="Active Root:", icon="CHECKMARK")
                col.label(text=root.name)
            else:
                col.alert = True
                col.label(text="Root NOT Found!", icon="ERROR")

        layout.separator()
        
        if is_win:
            layout.operator("krutart.fix_paths_win", icon="FILE_REFRESH")
            layout.operator("krutart.fix_paths_win_fallback", icon="RECOVER_LAST")
        else:
            layout.operator("krutart.fix_paths_mac", icon="FILE_REFRESH")
            
        layout.operator("krutart.diagnose_paths", icon="CONSOLE")


classes = (
    KrutartPathPreferences,
    KRUTART_OT_FixPathsMac,
    KRUTART_OT_FixPathsWin,
    KRUTART_OT_FixPathsWinFallback,
    KRUTART_OT_Diagnose,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if on_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(on_save_pre)
    if on_save_post not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(on_save_post)
    if on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(on_save_pre)
    if on_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(on_save_post)
    if on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)


if __name__ == "__main__":
    register()