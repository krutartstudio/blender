import bpy
import os
import sys
from pathlib import Path
from bpy.app.handlers import persistent

bl_info = {
    "name": "Krutart OS Path Bridge",
    "author": "Krutart, iori, gemini",
    "version": (4, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > External Data > Krutart Bridge",
    "description": "Strict absolute path mapping. Auto-repairs /s/ bugs and enforces S:\\ structure.",
    "category": "System",
}

# ------------------------------------------------------------------------
#   Constants
# ------------------------------------------------------------------------

WIN_PATH_KEY = "krutart_win_source"
PROJECT_CONTAINER_NAME = "VELKE_PROJEKTY"

# ------------------------------------------------------------------------
#   Utilities
# ------------------------------------------------------------------------

def get_prefs(context):
    return context.preferences.addons[__package__].preferences

def get_mac_root(context):
    """Finds the local root for VELKE_PROJEKTY using auto-discovery or manual override."""
    prefs = get_prefs(context)
    if prefs.mac_root_path:
        p = Path(prefs.mac_root_path).expanduser().resolve()
        if p.exists(): return p

    home = Path.home()
    candidates = [
        home / PROJECT_CONTAINER_NAME,
        home / "Library/CloudStorage/GoogleDrive-handak.daniel@gmail.com/Shared drives",
        home / "Library/CloudStorage/GoogleDrive-jorik.chase@krutart.cz/Shared drives",
        Path("/Volumes/GoogleDrive/Shared drives"),
        Path(f"/Volumes/{PROJECT_CONTAINER_NAME}")
    ]
    for cand in candidates:
        if cand.exists(): return cand
    return None

def get_win_config(context):
    prefs = get_prefs(context)
    drive = prefs.win_drive_char.upper().replace(":", "").strip() + ":"
    return drive

def repair_path_string(path_str, win_drive):
    """Core logic for the Path Doctor to fix malformed strings."""
    if not path_str: return path_str
    
    orig = path_str
    
    # Fix 1: The legacy /s/ bug (Unix style mapping on Windows)
    if path_str.lower().startswith("/s/"):
        path_str = win_drive + "\\" + path_str[3:].replace("/", "\\")
    
    # Fix 2: Mixed slashes in Windows-style paths (e.g. S:/Folder/file.blend)
    if ":" in path_str and "/" in path_str:
        path_str = path_str.replace("/", "\\")
        
    return path_str

# ------------------------------------------------------------------------
#   Logic: Mac -> Windows (Saving)
# ------------------------------------------------------------------------

def to_win_absolute(item_path, context):
    """Calculates S:\ absolute path. Resolves relative paths automatically."""
    if not item_path:
        return None
        
    win_drive = get_win_config(context)
    mac_root = get_mac_root(context)

    # 1. Resolve Relative Paths (//) to Absolute Local Paths
    if item_path.startswith("//"):
        try:
            item_path = bpy.path.abspath(item_path)
        except Exception:
            return None

    # 2. Convert to Path Object for clean manipulation
    p = Path(item_path).expanduser().resolve()
    
    # 3. If it's already a valid Windows path, don't break it
    if str(p).upper().startswith(win_drive.upper() + "\\"):
        return str(p)

    # 4. Map Mac Root to Windows Drive
    if mac_root and str(mac_root).lower() in str(p).lower():
        try:
            # This preserves the 3212-PREPRODUCTION hierarchy exactly
            rel = p.relative_to(mac_root)
            return f"{win_drive}\\{str(rel).replace('/', '\\')}"
        except ValueError:
            pass
            
    return None

# ------------------------------------------------------------------------
#   Logic: Windows -> Mac (Loading)
# ------------------------------------------------------------------------

def to_mac_absolute(win_path_str, context):
    """Converts S:\ to local Mac path. Only swaps if file exists on Mac."""
    if not win_path_str: return None
    
    mac_root = get_mac_root(context)
    if not mac_root: return None
    
    win_drive = get_win_config(context)
    clean_win = win_path_str.replace("\\", "/")
    
    # Handle various possible drive prefixes
    prefixes = [win_drive + "/", f"/{win_drive[0].lower()}/", f"/{win_drive[0].upper()}/"]
    
    relative_part = None
    for pre in prefixes:
        if clean_win.lower().startswith(pre.lower()):
            relative_part = clean_win[len(pre):].lstrip("/")
            break
            
    if relative_part:
        target = mac_root / relative_part
        # Critical safety: Don't point to a non-existent Mac path
        if target.exists():
            return str(target)
            
    return None

# ------------------------------------------------------------------------
#   Core Iterators
# ------------------------------------------------------------------------

def iter_external_data():
    for lib in bpy.data.libraries: yield lib, "Library"
    for img in bpy.data.images: 
        if img.source in {'FILE', 'SEQUENCE'}: yield img, "Image"
    for cache in bpy.data.cache_files: yield cache, "Cache"
    for sound in bpy.data.sounds: yield sound, "Sound"
    for font in bpy.data.fonts: yield font, "Font"
    for scene in bpy.data.scenes:
        if scene.sequence_editor:
            for strip in scene.sequence_editor.sequences_all:
                if hasattr(strip, "filepath"): yield strip, "VSE"

# ------------------------------------------------------------------------
#   Execution Engines
# ------------------------------------------------------------------------

def run_path_doctor(context, silent=True):
    """Scans and repairs malformed Windows paths."""
    win_drive = get_win_config(context)
    fixed = 0
    for item, _ in iter_external_data():
        current = item.filepath
        repaired = repair_path_string(current, win_drive)
        if current != repaired:
            item.filepath = repaired
            fixed += 1
    if not silent:
        print(f"[Krutart] Path Doctor: Fixed {fixed} paths.")
    return fixed

def run_bridge_to_windows(context):
    """Enforces absolute S:\\ paths before saving."""
    if sys.platform.startswith("win"): return 0
    count = 0
    for item, _ in iter_external_data():
        calculated = to_win_absolute(item.filepath, context)
        # Use existing WIN_PATH_KEY as fallback if calculation fails
        cached = item.get(WIN_PATH_KEY)
        
        target = calculated or cached
        if target and item.filepath != target:
            item.filepath = target
            count += 1
    return count

def run_bridge_to_mac(context):
    """Maps server paths to local Mac storage and reloads libraries."""
    if sys.platform.startswith("win"): return 0
    count = 0
    for item, kind in iter_external_data():
        current = item.filepath
        target = to_mac_absolute(current, context)
        
        if target and current != target:
            # Store original Windows path for the return journey
            if not item.get(WIN_PATH_KEY):
                item[WIN_PATH_KEY] = current
                
            item.filepath = target
            
            if kind == "Library":
                try:
                    item.reload()
                except Exception as e:
                    print(f"[Krutart] Reload failed for {item.name}: {e}")
            count += 1
    return count

# ------------------------------------------------------------------------
#   Handlers
# ------------------------------------------------------------------------

@persistent
def on_save_pre(dummy):
    if sys.platform.startswith("win"): return
    prefs = get_prefs(bpy.context)
    if prefs.auto_manage:
        run_bridge_to_windows(bpy.context)

@persistent
def on_save_post(dummy):
    if sys.platform.startswith("win"): return
    prefs = get_prefs(bpy.context)
    if prefs.auto_manage:
        run_bridge_to_mac(bpy.context)

@persistent
def on_load_post(dummy):
    if sys.platform.startswith("win"): return
    
    def delayed_init():
        # 1. Run Doctor to clean up malformed paths
        run_path_doctor(bpy.context, silent=False)
        # 2. Map to local Mac paths
        run_bridge_to_mac(bpy.context)
        return None

    bpy.app.timers.register(delayed_init, first_interval=0.4)

# ------------------------------------------------------------------------
#   Operators & UI
# ------------------------------------------------------------------------

class OT_KrutartPathDoctor(bpy.types.Operator):
    bl_idname = "krutart.path_doctor"
    bl_label = "Path Doctor: Repair /s/ Bug"
    
    def execute(self, context):
        fixed = run_path_doctor(context, silent=False)
        self.report({'INFO'}, f"Path Doctor fixed {fixed} malformed paths.")
        return {'FINISHED'}

class OT_KrutartForceMac(bpy.types.Operator):
    bl_idname = "krutart.force_mac"
    bl_label = "Manual Map to Mac"
    
    def execute(self, context):
        c = run_bridge_to_mac(context)
        self.report({'INFO'}, f"Mapped {c} items to Mac.")
        return {'FINISHED'}

class KrutartPathPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    win_drive_char: bpy.props.StringProperty(name="Win Drive", default="S")
    mac_root_path: bpy.props.StringProperty(name="Mac Root Override", subtype='DIR_PATH')
    auto_manage: bpy.props.BoolProperty(name="Auto-Bridge (Save/Load)", default=True)

    def draw(self, context):
        layout = self.layout
        if sys.platform.startswith("win"):
            layout.label(text="Active on Windows (Monitoring Only)", icon='CHECKMARK')
            return
            
        root = get_mac_root(context)
        box = layout.box()
        if root:
            box.label(text=f"Mac Root: {root}", icon='CHECKMARK')
        else:
            box.alert = True
            box.label(text="Mac Root NOT FOUND", icon='ERROR')
            
        box.prop(self, "mac_root_path")
        layout.prop(self, "auto_manage")
        layout.separator()
        layout.operator(OT_KrutartPathDoctor.bl_idname, icon='HEALTH')

def menu_func(self, context):
    self.layout.separator()
    self.layout.operator(OT_KrutartForceMac.bl_idname, icon='LINKED')
    self.layout.operator(OT_KrutartPathDoctor.bl_idname, icon='HEALTH')

# ------------------------------------------------------------------------
#   Registration
# ------------------------------------------------------------------------

classes = (KrutartPathPreferences, OT_KrutartPathDoctor, OT_KrutartForceMac)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_external_data.append(menu_func)
    
    if on_save_pre not in bpy.app.handlers.save_pre: 
        bpy.app.handlers.save_pre.append(on_save_pre)
    if on_save_post not in bpy.app.handlers.save_post: 
        bpy.app.handlers.save_post.append(on_save_post)
    if on_load_post not in bpy.app.handlers.load_post: 
        bpy.app.handlers.load_post.append(on_load_post)

def unregister():
    for cls in classes: bpy.utils.unregister_class(cls)
    bpy.types.TOPBAR_MT_file_external_data.remove(menu_func)
    
    if on_save_pre in bpy.app.handlers.save_pre: 
        bpy.app.handlers.save_pre.remove(on_save_pre)
    if on_save_post in bpy.app.handlers.save_post: 
        bpy.app.handlers.save_post.remove(on_save_post)
    if on_load_post in bpy.app.handlers.load_post: 
        bpy.app.handlers.load_post.remove(on_load_post)

if __name__ == "__main__":
    register()