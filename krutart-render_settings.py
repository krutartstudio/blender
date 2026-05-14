# -------------------------------------------------------------------------------------------------
# Krutart Render Settings Addon
# Robust Google Sheets Integration for Blender 4.2+
# NO DEPENDENCIES - Uses Public CSV Export
# -------------------------------------------------------------------------------------------------

bl_info = {
    "name": "Krutart Render Settings",
    "author": "iori, Krutart, gemini",
    "version": (2, 7, 5),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > bRender",
    "description": "Fetch render settings from Public Google Sheets. Includes timeline utilities.",
    "warning": "",
    "doc_url": "",
    "category": "Render",
}

import bpy
import os
import sys
import threading
import queue
import csv
import urllib.request
import urllib.error
import io
from bpy.props import StringProperty, EnumProperty
from bpy.types import Operator, Panel, AddonPreferences

# -------------------------------------------------------------------------------------------------
# GLOBAL CONSTANTS
# -------------------------------------------------------------------------------------------------
DEFAULT_SPREADSHEET_ID = '1v_D4aEYObApIydC43SUlOSNtQAO0MqrxYSDKJwyNX3o'
DEFAULT_SHEET_NAME = 'render_settings' 

COL_DEFAULT_NAME = 'default_name'
COL_API_NAME = 'api_name'
COL_GENERAL = 'general_value'
COL_ANI = 'ani_value'         
COL_ART = 'art_value'         

execution_queue = queue.Queue()

# -------------------------------------------------------------------------------------------------
# UTILITIES
# -------------------------------------------------------------------------------------------------
def parse_project_name(filepath):
    if not filepath:
        return 'Untitled'
    path_parts = filepath.replace('\\', '/').split('/')
    filename = path_parts[-1]
    name_parts = filename.split('-')
    if len(name_parts) >= 1:
        return name_parts[0]
    return 'Unknown'

def get_rna_property_type(obj, attr_name):
    try:
        if hasattr(obj, "bl_rna"):
            prop = obj.bl_rna.properties.get(attr_name)
            if prop:
                return prop.type
    except Exception:
        pass
    return None

def robust_cast(value_str, target_obj, attr_name):
    if value_str is None:
        return None
    val_str = str(value_str).strip()
    if val_str in ['-', '']:
        return None

    rna_type = get_rna_property_type(target_obj, attr_name)
    
    if rna_type:
        try:
            if rna_type == 'BOOLEAN':
                return val_str.lower() in ('true', '1', 'yes', 'on', 'enable')
            elif rna_type == 'INT':
                return int(float(val_str))
            elif rna_type == 'FLOAT':
                return float(val_str)
            elif rna_type == 'ENUM':
                return str(val_str)
            elif rna_type == 'STRING':
                return str(val_str)
        except ValueError:
            print(f"[Krutart] Warning: Could not cast '{val_str}' to {rna_type} for {attr_name}. Attempting fallback.")

    val_lower = val_str.lower()
    if val_lower in ('true', 'yes', 'on'): return True
    if val_lower in ('false', 'no', 'off'): return False
    
    try:
        f_val = float(val_str)
        if f_val.is_integer():
            return int(f_val)
        return f_val
    except ValueError:
        pass 
        
    return str(val_str)

# -------------------------------------------------------------------------------------------------
# PUBLIC SHEET CSV CLIENT (No Dependencies)
# -------------------------------------------------------------------------------------------------
class GoogleCSVClient:
    def __init__(self, spreadsheet_id, sheet_name):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name

    def fetch_all_settings(self):
        if not self.spreadsheet_id:
            raise ValueError("Spreadsheet ID is missing in Preferences.")

        url = f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={self.sheet_name}"
        print(f"[Krutart] Fetching URL: {url}")

        try:
            response = urllib.request.urlopen(url)
            data = response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            if e.code == 400:
                raise ValueError(f"Sheet '{self.sheet_name}' not found or ID is wrong.")
            elif e.code == 404:
                raise ValueError("Spreadsheet URL not found.")
            else:
                raise ConnectionError(f"HTTP Error {e.code}: Check internet or Sheet permissions (Must be 'Anyone with link').")
        except Exception as e:
            raise ConnectionError(f"Connection Failed: {str(e)}")

        f = io.StringIO(data)
        reader = csv.DictReader(f)
        
        if not reader.fieldnames:
            raise ValueError("CSV is empty or could not parse headers.")
            
        reader.fieldnames = [h.strip() for h in reader.fieldnames]

        required = [COL_API_NAME, COL_GENERAL]
        for req in required:
            if req not in reader.fieldnames:
                raise ValueError(f"Missing required column: {req}. Found: {reader.fieldnames}")

        rows = []
        for row in reader:
            if row.get(COL_API_NAME):
                rows.append(row)

        return rows

# -------------------------------------------------------------------------------------------------
# PREFERENCES
# -------------------------------------------------------------------------------------------------
class KRUTART_AddonPreferences(AddonPreferences):
    bl_idname = __name__

    spreadsheet_id: StringProperty(
        name="Spreadsheet ID",
        default=DEFAULT_SPREADSHEET_ID,
        description="The long ID string in the Google Sheet URL"
    )
    
    sheet_name: StringProperty(
        name="Sheet Name",
        default=DEFAULT_SHEET_NAME,
        description="Name of the tab in Google Sheets"
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Google Configuration (Public Access)")
        box.label(text="Note: Sheet must be shared as 'Anyone with the link'", icon='INFO')
        box.prop(self, "spreadsheet_id")
        box.prop(self, "sheet_name")

# -------------------------------------------------------------------------------------------------
# LOGIC & ACTIONS
# -------------------------------------------------------------------------------------------------
def apply_settings_from_rows(scene, rows, context_key, report_func=None):
    applied_count = 0
    
    print("-" * 50)
    print(f"[Krutart] Applying Config: {context_key}")
    
    for row in rows:
        api_path = row.get(COL_API_NAME, "").strip()
        value_raw = row.get(context_key, "")
        
        if not api_path:
            continue
        
        original_path = api_path
        if api_path.startswith("bpy.context.scene."):
            api_path = api_path[18:]
        elif api_path.startswith("scene."):
            api_path = api_path[6:] 
            
        target_obj = scene
        path_parts = api_path.split('.')
        attr_name = path_parts[-1]
        
        valid_path = True
        try:
            for part in path_parts[:-1]:
                target_obj = getattr(target_obj, part)
        except AttributeError:
            msg = f"Path not found: '{original_path}'. Failed at '{part}'."
            print(f"[Krutart] ERROR | {msg}")
            if report_func:
                report_func({'WARNING'}, msg)
            valid_path = False

        if not valid_path:
            continue

        try:
            if not hasattr(target_obj, attr_name):
                msg = f"Property '{attr_name}' DOES NOT EXIST on {target_obj}."
                print(f"[Krutart] ERROR | {msg}")
                if report_func:
                    report_func({'WARNING'}, msg)
                continue

            final_value = robust_cast(value_raw, target_obj, attr_name)
            
            if final_value is not None:
                current_val = getattr(target_obj, attr_name)
                
                is_equal = False
                try:
                    is_equal = (current_val == final_value)
                except:
                    is_equal = False

                if not is_equal:
                    setattr(target_obj, attr_name, final_value)
                    applied_count += 1
                    
                    msg = f"Set {attr_name}: {final_value}"
                    print(f"[Krutart] CHANGE | {attr_name}: {current_val} -> {final_value}")
                    if report_func:
                        report_func({'INFO'}, msg)
                else:
                    pass
                    
        except Exception as e:
            msg = f"Exception setting {original_path}: {e}"
            print(f"[Krutart] {msg}")
            if report_func:
                report_func({'ERROR'}, msg)

    print(f"[Krutart] Finished. Updated {applied_count} settings.")
    print("-" * 50)
    return applied_count

# -------------------------------------------------------------------------------------------------
# RESOLUTION LOGIC
# -------------------------------------------------------------------------------------------------
RES_MAP = {
    'HD': (1920, 1080),
    '1K': (1024, 1024),
    '2K': (2048, 2048),
    '4K': (4096, 4096),
    '6K': (6144, 6144),
    '8K': (8192, 8192)
}

def apply_resolution_to_scene(target_scene, res_string):
    if res_string not in RES_MAP:
        return
        
    x, y = RES_MAP[res_string]
    target_scene.render.resolution_x = x
    target_scene.render.resolution_y = y
    target_scene.render.resolution_percentage = 100
    print(f"[Krutart] Applied {res_string} Resolution: {x}x{y}")

def get_brender_res(self):
    curr_x = self.render.resolution_x
    curr_y = self.render.resolution_y
    
    enum_items = ['1K', '2K', '4K', '6K', '8K']
    
    for i, key in enumerate(enum_items):
        w, h = RES_MAP[key]
        if curr_x == w and curr_y == h:
            return i
            
    return -1 

def set_brender_res(self, value):
    enum_items = ['1K', '2K', '4K', '6K', '8K']
    if 0 <= value < len(enum_items):
        apply_resolution_to_scene(self, enum_items[value])

# -------------------------------------------------------------------------------------------------
# OPERATORS
# -------------------------------------------------------------------------------------------------
class KA_OT_fetch_settings(Operator):
    """Fetch Settings from Public Google Sheets (Modal)"""
    bl_idname = "ka.fetch_settings"
    bl_label = "Fetch Settings"
    
    _timer = None
    
    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        
        def fetch_worker():
            try:
                client = GoogleCSVClient(
                    prefs.spreadsheet_id,
                    prefs.sheet_name
                )
                data = client.fetch_all_settings()
                execution_queue.put({"status": "SUCCESS", "data": data})
            except Exception as e:
                execution_queue.put({"status": "ERROR", "msg": str(e)})

        threading.Thread(target=fetch_worker, daemon=True).start()
        
        context.window_manager.ka_status = "Fetching..."
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            try:
                item = execution_queue.get_nowait()
                
                context.window_manager.event_timer_remove(self._timer)
                
                if item['status'] == 'SUCCESS':
                    context.scene['ka_last_rows'] = item['data']
                    msg = f"Fetched {len(item['data'])} settings."
                    context.window_manager.ka_status = msg
                    self.report({'INFO'}, f"Fetched {len(item['data'])} settings from Google.")
                elif item['status'] == 'ERROR':
                    context.window_manager.ka_status = f"Error: {item['msg']}"
                    self.report({'ERROR'}, item['msg'])
                    
                    def draw_error(self, context):
                        self.layout.label(text="Sheet Error:")
                        self.layout.label(text=item['msg'])
                    context.window_manager.popup_menu(draw_error, title="Fetch Error", icon='ERROR')
                
                return {'FINISHED'}
                
            except queue.Empty:
                pass
        
        return {'PASS_THROUGH'}

class KA_OT_apply_config(Operator):
    """Apply a Google Sheet Configuration (Default, Animation, Art)"""
    bl_idname = "ka.apply_config"
    bl_label = "Apply Config"
    bl_description = "Apply specific render configuration column from Sheets"
    
    config_type: EnumProperty(
        items=[
            ('GENERAL', "Default", "Use 'general_value' column"),
            ('ANI', "Animation", "Use 'ani_value' column"),
            ('ART', "Art", "Use 'art_value' column"),
        ]
    )
    
    def execute(self, context):
        rows = context.scene.get('ka_last_rows')
        if not rows:
            self.report({'ERROR'}, "Please fetch settings first!")
            return {'CANCELLED'}

        col_key = COL_GENERAL
        if self.config_type == 'ANI': col_key = COL_ANI
        elif self.config_type == 'ART': col_key = COL_ART
        
        count = apply_settings_from_rows(context.scene, rows, col_key, self.report)
        
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
                
        context.view_layer.update()
        
        self.report({'INFO'}, f"Applied {self.config_type} config. Changed {count} settings. Check Console for details.")
        return {'FINISHED'}

class KA_OT_set_simplify(Operator):
    """Set native viewport subdivision simplify level"""
    bl_idname = "ka.set_simplify"
    bl_label = "Set Simplify"
    bl_options = {'UNDO'}

    level: StringProperty()

    def execute(self, context):
        scene = context.scene
        if self.level == "OFF":
            scene.render.use_simplify = False
            self.report({'INFO'}, "Simplify Disabled")
        else:
            scene.render.use_simplify = True
            scene.render.simplify_subdivision = int(self.level)
            self.report({'INFO'}, f"Simplify Enabled: Viewport Subdiv set to {self.level}")
        return {"FINISHED"}

# -------------------------------------------------------------------------------------------------
# PANEL
# -------------------------------------------------------------------------------------------------
class KA_PT_render_settings(Panel):
    bl_label = "Render Settings" 
    bl_idname = "KA_PT_render_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "bRender"
    bl_order = 1 

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        
        # project = parse_project_name(bpy.data.filepath)
        
        # box = layout.box()
        # box.label(text=f"Project: {project}", icon='PROPERTIES')
        
        status = getattr(wm, "ka_status", "Ready")
        row = layout.row()
        row.label(text=f"Status: {status}")
        
        layout.separator()
        layout.operator("ka.fetch_settings", text="Fetch Google Settings", icon='URL')
        
        layout.separator()
        layout.label(text="Apply Configuration:")
        
        row = layout.row(align=True)
        row.operator("ka.apply_config", text="Default").config_type = 'GENERAL'
        row.operator("ka.apply_config", text="Animation").config_type = 'ANI'
        row.operator("ka.apply_config", text="Art").config_type = 'ART'
        
        layout.separator()
        
        # --- RESOLUTION ---
        layout.separator()
        layout.label(text="Resolution:") 
        
        row = layout.row(align=True)
        for res_key in RES_MAP.keys():
            if res_key != 'HD': 
                row.prop_enum(context.scene, "brender_resolution", value=res_key)
                
        # --- SIMPLIFY ---
        layout.separator()
        layout.label(text="Simplify:")
        row = layout.row(align=True)
        row.operator("ka.set_simplify", text="Off").level = "OFF"
        row.operator("ka.set_simplify", text="0").level = "0"
        row.operator("ka.set_simplify", text="1").level = "1"
        row.operator("ka.set_simplify", text="2").level = "2"
        
        # --- HIDDEN HANDLES UI ---
        # layout.separator()
        # box2 = layout.box()
        # box2.label(text="Handles", icon='TIME')
        # box2.operator("ka.mark_handles", text="Mark Handles (In/Out)", icon='MARKER')

# -------------------------------------------------------------------------------------------------
# REGISTRATION
# -------------------------------------------------------------------------------------------------
classes = (
    KRUTART_AddonPreferences,
    KA_OT_fetch_settings,
    KA_OT_apply_config,
    KA_OT_set_simplify,
    # KA_OT_mark_handles,  # <-- Commented out
    KA_PT_render_settings,
)

def register():
    bpy.types.WindowManager.ka_status = StringProperty(name="Status", default="Ready")
    
    bpy.types.Scene.brender_resolution = EnumProperty(
        items=[
            ('1K', "1K", "Set resolution to 1024x1024"),
            ('2K', "2K", "Set resolution to 2048x2048"),
            ('4K', "4K", "Set resolution to 4096x4096"),
            ('6K', "6K", "Set resolution to 6144x6144"),
            ('8K', "8K", "Set resolution to 8192x8192")
        ],
        name="Resolution",
        description="Render Resolution",
        get=get_brender_res,
        set=set_brender_res
    )

    bpy.types.Scene.krutart_apply_res = staticmethod(apply_resolution_to_scene)

    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.ka_status
    del bpy.types.Scene.brender_resolution
    del bpy.types.Scene.krutart_apply_res

if __name__ == "__main__":
    register()