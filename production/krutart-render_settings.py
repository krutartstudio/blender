# -------------------------------------------------------------------------------------------------
# Krutart Render Settings Addon
# Robust Google Sheets Integration for Blender 4.2+
# NO DEPENDENCIES - Uses Public CSV Export
# -------------------------------------------------------------------------------------------------

bl_info = {
    "name": "Krutart Render Settings",
    "author": "iori, Krutart, gemini",
    "version": (2, 5, 2),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Render Settings",
    "description": "Fetch render settings from Public Google Sheets.",
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

# Default Configuration
DEFAULT_SPREADSHEET_ID = '1v_D4aEYObApIydC43SUlOSNtQAO0MqrxYSDKJwyNX3o'
DEFAULT_SHEET_NAME = 'render_settings' 

# Column Headers from your CSV
COL_DEFAULT_NAME = 'default_name'
COL_API_NAME = 'api_name'
COL_GENERAL = 'general_value' # Mapped to "Default" config
COL_ANI = 'ani_value'         # Mapped to "Animation" config
COL_ART = 'art_value'         # Mapped to "Art" config

# Thread communication
execution_queue = queue.Queue()

# -------------------------------------------------------------------------------------------------
# UTILITIES
# -------------------------------------------------------------------------------------------------

def parse_project_name(filepath):
    """
    Parses Project Name from filename using convention: Project-Scene-Shot-Version.blend
    Example: 3212-sc01-sh010-v001.blend -> Project: 3212
    """
    if not filepath:
        return 'Untitled'

    path_parts = filepath.replace('\\', '/').split('/')
    filename = path_parts[-1]
    
    # Try Filename Parsing (Project-...)
    name_parts = filename.split('-')
    if len(name_parts) >= 1:
        return name_parts[0]
    
    return 'Unknown'

def get_rna_property_type(obj, attr_name):
    """Inspects the RNA of a Blender object to find the expected type of a property."""
    try:
        # Check if bl_rna exists (bpy_structs)
        if hasattr(obj, "bl_rna"):
            prop = obj.bl_rna.properties.get(attr_name)
            if prop:
                return prop.type
    except Exception:
        pass
    return None

def robust_cast(value_str, target_obj, attr_name):
    """
    Casts string from Google Sheet to correct Blender type.
    Improved to handle '24.0' strings for IntProperties.
    """
    if value_str is None:
        return None
    
    # Handle explicit empty or dash
    val_str = str(value_str).strip()
    if val_str in ['-', '']:
        return None

    rna_type = get_rna_property_type(target_obj, attr_name)
    
    # --- 1. RNA Type Known ---
    if rna_type:
        try:
            if rna_type == 'BOOLEAN':
                return val_str.lower() in ('true', '1', 'yes', 'on', 'enable')
            
            elif rna_type == 'INT':
                # Handle "24.0" -> 24
                return int(float(val_str))
                
            elif rna_type == 'FLOAT':
                return float(val_str)
                
            elif rna_type == 'ENUM':
                return str(val_str)
                
            elif rna_type == 'STRING':
                return str(val_str)
        except ValueError:
            print(f"[Krutart] Warning: Could not cast '{val_str}' to {rna_type} for {attr_name}. Attempting fallback.")

    # --- 2. Fallback (RNA Unknown or Failed) ---
    val_lower = val_str.lower()
    if val_lower in ('true', 'yes', 'on'): return True
    if val_lower in ('false', 'no', 'off'): return False
    
    # Try Number (Handle 24.0 as 24)
    try:
        f_val = float(val_str)
        if f_val.is_integer():
            return int(f_val)
        return f_val
    except ValueError:
        pass # Not a number
        
    return str(val_str)

# -------------------------------------------------------------------------------------------------
# PUBLIC SHEET CSV CLIENT (No Dependencies)
# -------------------------------------------------------------------------------------------------

class GoogleCSVClient:
    def __init__(self, spreadsheet_id, sheet_name):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name

    def fetch_all_settings(self):
        """
        Fetches data using the Google Visualization API CSV endpoint.
        This allows fetching a specific sheet by name without Oauth.
        """
        if not self.spreadsheet_id:
            raise ValueError("Spreadsheet ID is missing in Preferences.")

        # Construct URL for CSV export of a specific sheet name
        # /gviz/tq?tqx=out:csv&sheet={name} is reliable for named sheets
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

        # Parse CSV Data
        f = io.StringIO(data)
        reader = csv.DictReader(f)
        
        # Clean keys (remove potential BOM or whitespace)
        if not reader.fieldnames:
            raise ValueError("CSV is empty or could not parse headers.")
            
        # Normalize headers (strip whitespace)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]

        # Validate critical columns
        required = [COL_API_NAME, COL_GENERAL]
        for req in required:
            if req not in reader.fieldnames:
                raise ValueError(f"Missing required column: {req}. Found: {reader.fieldnames}")

        rows = []
        for row in reader:
            # Filter rows that have an api_name
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
    """
    Iterates through rows and applies settings.
    context_key: 'general_value' (Default), 'ani_value' (Animation), or 'art_value' (Art)
    report_func: Optional self.report from operator to log to UI
    """
    applied_count = 0
    
    print("-" * 50)
    print(f"[Krutart] Applying Config: {context_key}")
    
    for row in rows:
        api_path = row.get(COL_API_NAME, "").strip()
        value_raw = row.get(context_key, "")
        
        if not api_path:
            continue
        
        # --- FIX: Clean path prefixes so resolving works relative to 'scene' ---
        original_path = api_path
        if api_path.startswith("bpy.context.scene."):
            api_path = api_path[18:] # Remove 'bpy.context.scene.'
        elif api_path.startswith("scene."):
            api_path = api_path[6:]  # Remove 'scene.'
            
        # --- 1. Resolve Path (cycles.samples -> obj, attr) ---
        target_obj = scene
        path_parts = api_path.split('.')
        attr_name = path_parts[-1]
        
        valid_path = True
        try:
            # Navigate to the parent object of the property
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

        # --- 2. Check Existence, Cast and Set Value ---
        try:
            # Explicit check for property existence
            if not hasattr(target_obj, attr_name):
                msg = f"Property '{attr_name}' DOES NOT EXIST on {target_obj}."
                print(f"[Krutart] ERROR | {msg}")
                if report_func:
                    report_func({'WARNING'}, msg)
                continue

            final_value = robust_cast(value_raw, target_obj, attr_name)
            
            if final_value is not None:
                current_val = getattr(target_obj, attr_name)
                
                # Check for equality (handles mismatched types like 24 vs 24.0)
                is_equal = False
                try:
                    is_equal = (current_val == final_value)
                except:
                    is_equal = False

                if not is_equal:
                    setattr(target_obj, attr_name, final_value)
                    applied_count += 1
                    
                    # Log Change
                    msg = f"Set {attr_name}: {final_value}"
                    print(f"[Krutart] CHANGE | {attr_name}: {current_val} -> {final_value}")
                    if report_func:
                        report_func({'INFO'}, msg)
                else:
                    # Value is already set correctly, skip update to be efficient
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
# OPERATORS
# -------------------------------------------------------------------------------------------------

class KA_OT_fetch_settings(Operator):
    """Fetch Settings from Public Google Sheets (Modal)"""
    bl_idname = "ka.fetch_settings"
    bl_label = "Fetch Settings"
    
    _timer = None
    
    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        
        # 1. Start Thread
        def fetch_worker():
            try:
                # Uses standard library, no extra deps needed
                client = GoogleCSVClient(
                    prefs.spreadsheet_id,
                    prefs.sheet_name
                )
                data = client.fetch_all_settings()
                execution_queue.put({"status": "SUCCESS", "data": data})
            except Exception as e:
                execution_queue.put({"status": "ERROR", "msg": str(e)})

        threading.Thread(target=fetch_worker, daemon=True).start()
        
        # 2. Start Modal Timer
        context.window_manager.ka_status = "Fetching..."
        # 0.5 seconds check interval
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            try:
                # Check Queue (Non-blocking)
                item = execution_queue.get_nowait()
                
                # If we get here, thread is done -> Stop Timer
                context.window_manager.event_timer_remove(self._timer)
                
                if item['status'] == 'SUCCESS':
                    # Store raw list of dicts
                    context.scene['ka_last_rows'] = item['data']
                    msg = f"Fetched {len(item['data'])} settings."
                    context.window_manager.ka_status = msg
                    self.report({'INFO'}, f"Fetched {len(item['data'])} settings from Google.")
                elif item['status'] == 'ERROR':
                    context.window_manager.ka_status = f"Error: {item['msg']}"
                    self.report({'ERROR'}, item['msg'])
                    
                    # Popup for error
                    def draw_error(self, context):
                        self.layout.label(text="Sheet Error:")
                        self.layout.label(text=item['msg'])
                    context.window_manager.popup_menu(draw_error, title="Fetch Error", icon='ERROR')
                
                return {'FINISHED'}
                
            except queue.Empty:
                # Thread still running, keep waiting
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
        
        # Force UI update to show changes immediately
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
                
        # Force dependency graph update (crucial for render engine settings)
        context.view_layer.update()
        
        self.report({'INFO'}, f"Applied {self.config_type} config. Changed {count} settings. Check Console for details.")
        return {'FINISHED'}

# -------------------------------------------------------------------------------------------------
# PANEL
# -------------------------------------------------------------------------------------------------

class KA_PT_render_settings(Panel):
    bl_label = "Render Settings" # Generic, will update in draw
    bl_idname = "KA_PT_render_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Render Settings"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        
        # Parse Project Name (Universal)
        project = parse_project_name(bpy.data.filepath)
        
        # Dynamic Header
        box = layout.box()
        box.label(text=f"Project: {project}", icon='PROPERTIES')
        
        # Status
        status = getattr(wm, "ka_status", "Ready")
        row = layout.row()
        row.label(text=f"Status: {status}")
        
        # Fetch
        layout.separator()
        layout.operator("ka.fetch_settings", text="Fetch Google Settings", icon='URL')
        
        # Configurations (The 3 Columns)
        layout.separator()
        layout.label(text="Apply Configuration:")
        
        row = layout.row(align=True)
        row.operator("ka.apply_config", text="Default").config_type = 'GENERAL'
        row.operator("ka.apply_config", text="Animation").config_type = 'ANI'
        row.operator("ka.apply_config", text="Art").config_type = 'ART'

# -------------------------------------------------------------------------------------------------
# REGISTRATION
# -------------------------------------------------------------------------------------------------

classes = (
    KRUTART_AddonPreferences,
    KA_OT_fetch_settings,
    KA_OT_apply_config,
    KA_PT_render_settings,
)

def register():
    bpy.types.WindowManager.ka_status = StringProperty(name="Status", default="Ready")
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.ka_status

if __name__ == "__main__":
    register()