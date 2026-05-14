bl_info = {
    "name": "Krutart Animation Controls",
    "author": "iori, krutart, gemini",
    "version": (1, 3, 2),
    "blender": (4, 5, 0),
    "location": "Dope Sheet > Sidebar > View / Graph Editor > Sidebar > View",
    "description": "Unified animation controls for Dope Sheet and Graph Editor with Krutart Pipeline integration.",
    "category": "Animation",
}

import bpy
import os
import re
import sys
import socket
from datetime import datetime
from bpy.types import Panel, Operator

# --- HELPER FUNCTIONS ---

def get_current_user():
    """Determines the current user from Krutart Configurator or fallback."""
    user_name = None
    configurator_mod = None
    
    if 'krutart-configurator' in sys.modules:
        configurator_mod = sys.modules['krutart-configurator']
    else:
        for mod_name, mod in sys.modules.items():
            if hasattr(mod, "bl_info") and isinstance(mod.bl_info, dict):
                if mod.bl_info.get("name") == "Krutart Configurator":
                    configurator_mod = mod
                    break

    if configurator_mod:
        try:
            addon_prefs_obj = bpy.context.preferences.addons.get(configurator_mod.__name__)
            if addon_prefs_obj:
                prefs = addon_prefs_obj.preferences
                hostname = socket.gethostname().lower()
                
                if prefs.user_name_override.strip():
                    user_name = prefs.user_name_override.strip()
                elif hasattr(configurator_mod, "CACHED_IDENTITY_MAP"):
                    cached_map = configurator_mod.CACHED_IDENTITY_MAP
                    user_name = cached_map.get(hostname, hostname)
                else:
                    user_name = hostname
        except Exception:
            pass

    if not user_name:
        text_block_name = "krutart-configurations.info"
        if text_block_name in bpy.data.texts:
            content = bpy.data.texts[text_block_name].as_string()
            match = re.search(r"last saved by:\s*(.*?)\s+-", content, re.IGNORECASE)
            if match:
                user_name = match.group(1).strip()
                
    if not user_name:
        user_name = socket.gethostname().lower()

    return re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)

def get_sc_sh_from_filename(filepath):
    """Extracts SC and SH identifiers from the current blend file."""
    if not filepath:
        return "scXX", "shXXX"
        
    filename = os.path.basename(filepath)
    match = re.search(r"(sc\d+)-(sh\d+)", filename, re.IGNORECASE)
    
    if match:
        return match.group(1).lower(), match.group(2).lower()
    return "scXX", "shXXX"

# --- OPERATORS ---

class KRUTART_OT_set_keyingset(Operator):
    """Set the active keying set safely"""
    bl_idname = "anim.krutart_set_keyingset"
    bl_label = "Set Keying Set"
    bl_options = {'UNDO'}

    ks_name: bpy.props.StringProperty()

    def execute(self, context):
        ks_all = context.scene.keying_sets_all
        if self.ks_name in ks_all:
            ks_all.active = ks_all[self.ks_name]
        else:
            self.report({'WARNING'}, f"Keying Set '{self.ks_name}' not found.")
        return {"FINISHED"}

class KRUTART_OT_anim_work_settings(Operator):
    """Fetch and apply ANIM work settings from Google Sheets"""
    bl_idname = "anim.krutart_anim_work"
    bl_label = "Fetch ANIM Work Settings"
    bl_options = {'UNDO'}

    def execute(self, context):
        if hasattr(bpy.ops.ka, "apply_config"):
            try:
                bpy.ops.ka.apply_config(config_type='ANI')
                self.report({'INFO'}, "Successfully applied ANIM configuration.")
            except Exception as e:
                self.report({'ERROR'}, f"Failed to apply ANIM config: {e}")
        else:
            self.report({'WARNING'}, "Render Settings addon not found or disabled.")
        return {"FINISHED"}

class KRUTART_OT_set_simplify(Operator):
    """Set viewport subdivision simplify level"""
    bl_idname = "anim.krutart_set_simplify"
    bl_label = "Set Simplify"
    bl_options = {'UNDO'}

    level: bpy.props.IntProperty(default=0)

    def execute(self, context):
        scene = context.scene
        scene.render.use_simplify = True
        scene.render.simplify_subdivision = self.level
        self.report({'INFO'}, f"Simplify enabled: Viewport Subdiv set to {self.level}")
        return {"FINISHED"}

class KRUTART_OT_draft_setup(Operator):
    """Configures resolution, paths, and settings for a playblast draft"""
    bl_idname = "anim.krutart_draft_setup"
    bl_label = "Setup ANIM Draft"
    bl_options = {'UNDO'}

    def execute(self, context):
        scene = context.scene
        filepath = bpy.data.filepath
        
        if not filepath:
            self.report({'ERROR'}, "Please save the file first.")
            return {"CANCELLED"}

        comment = scene.krutart_draft_comment.strip()
        sanitized_comment = re.sub(r'[^a-zA-Z0-9_-]', '_', comment)

        # 1. Extraction
        user = get_current_user()
        sc, sh = get_sc_sh_from_filename(filepath)
        date_str = datetime.now().strftime("%y_%m_%d")  # New Date Format: YY_MM_DD
        
        # 2. Directory Management
        base_draft_dir = r"S:\3212-PRODUCTION\DRAFT"
        target_dir = os.path.join(base_draft_dir, date_str)
        
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            self.report({'ERROR'}, f"Could not create directory {target_dir}: {e}")
            return {"CANCELLED"}
        
        # 3. Construct Filename (No Versioning)
        if sanitized_comment:
            filename = f"{sc}-{sh}-draft-{user}-{sanitized_comment}.mp4".lower()
        else:
            filename = f"{sc}-{sh}-draft-{user}.mp4".lower()
            
        full_output_path = os.path.join(target_dir, filename)

        # 4. Apply Render Settings
        render = scene.render
        render.resolution_x = 1920
        render.resolution_y = 1080
        render.resolution_percentage = 100
        
        render.image_settings.file_format = 'FFMPEG'
        render.ffmpeg.format = 'MPEG4'
        render.ffmpeg.codec = 'H264'
        render.ffmpeg.constant_rate_factor = 'MEDIUM'
        render.ffmpeg.ffmpeg_preset = 'GOOD'
        render.ffmpeg.gopsize = 12
        render.ffmpeg.max_b_frames = 0

        render.ffmpeg.audio_codec = 'AAC'
        render.ffmpeg.audio_channels = 'STEREO'
        render.ffmpeg.audio_mixrate = 48000
        render.ffmpeg.audio_bitrate = 128
        
        render.filepath = full_output_path
        
        # 5. Finish (No Render)
        self.report({'INFO'}, f"Draft configured! Output set to: {filename}")
        return {"FINISHED"}

class KRUTART_OT_open_draft_dir(Operator):
    """Opens the destination folder for the current day's draft"""
    bl_idname = "anim.krutart_open_draft_dir"
    bl_label = "Open Output Directory"

    def execute(self, context):
        date_str = datetime.now().strftime("%y_%m_%d")
        base_draft_dir = r"S:\3212-PRODUCTION\DRAFT"
        target_dir = os.path.join(base_draft_dir, date_str)
        
        if os.path.exists(target_dir):
            bpy.ops.wm.path_open(filepath=target_dir)
            self.report({'INFO'}, f"Opened directory: {target_dir}")
        else:
            self.report({'WARNING'}, "Directory does not exist yet. Please run Setup first.")
            
        return {"FINISHED"}

# --- UI PANELS ---

class KRUTART_PT_timeline_base:
    """Base class for shared UI logic between Dope Sheet and Graph Editor"""
    bl_region_type = "UI"
    bl_category = "View"
    bl_label = "Custom controls"

    @classmethod
    def poll(cls, context):
        return context.area.type in {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        scene = context.scene
        screen = context.screen
        tool_settings = context.tool_settings
        
        # --- TRANSPORT CONTROLS ---
        row = layout.row(align=True)
        row.operator("screen.frame_jump", text="", icon="REW").end = False
        row.operator("screen.keyframe_jump", text="", icon="PREV_KEYFRAME").next = False
        row.operator("screen.marker_jump", text="", icon="TRIA_LEFT").next = False

        if not screen.is_animation_playing:
            if (scene.sync_mode == "AUDIO_SYNC" and 
                context.preferences.system.audio_device == "JACK"):
                row.scale_x = 2
                row.operator("screen.animation_play", text="", icon="PLAY")
                row.scale_x = 1
            else:
                row.operator("screen.animation_play", text="", icon="PLAY_REVERSE").reverse = True
                row.operator("screen.animation_play", text="", icon="PLAY")
        else:
            row.scale_x = 2
            row.operator("screen.animation_play", text="", icon="PAUSE")
            row.scale_x = 1

        row.operator("screen.marker_jump", text="", icon="TRIA_RIGHT").next = True
        row.operator("screen.keyframe_jump", text="", icon="NEXT_KEYFRAME").next = True
        row.operator("screen.frame_jump", text="", icon="FF").end = True
        row.separator()

        # --- PREVIEW RANGE & CURRENT FRAME ---
        row.prop(scene, "use_preview_range", text="", toggle=True)
        if scene.show_subframe:
            row.scale_x = 1.15
            row.prop(scene, "frame_float", text="")
        else:
            row.scale_x = 0.95
            row.prop(scene, "frame_current", text="")

        # --- START/END RANGE ---
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.scale_x = 0.8
        if not scene.use_preview_range:
            sub.prop(scene, "frame_start", text="")
            sub.prop(scene, "frame_end", text="")
        else:
            sub.prop(scene, "frame_preview_start", text="")
            sub.prop(scene, "frame_preview_end", text="")

        # --- FILTERING (Specific to Dope Sheet) ---
        if context.area.type == 'DOPESHEET_EDITOR':
            dopesheet = context.space_data.dopesheet
            if bpy.data.collections:
                col = layout.column(align=True)
                col.prop(dopesheet, "filter_collection", text="")

        # --- ACTIVE KEYING SET & AUTO-KEY ---
        col = layout.column(align=True)
        col.label(text="Active Keying Set")

        row = col.row(align=True)
        row.prop_search(scene.keying_sets_all, "active", scene, "keying_sets_all", text="")
        row.operator("anim.keyframe_insert", text="", icon="KEY_HLT")
        row.operator("anim.keyframe_delete", text="", icon="KEY_DEHLT")

        row.separator()
        row.prop(tool_settings, "use_keyframe_insert_auto", text="", toggle=True)

        row = col.row(align=True)
        row.prop(tool_settings, "use_keyframe_insert_keyingset", text="Only Active Keying Set", toggle=False)

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("anim.krutart_set_keyingset", text="Available").ks_name = "Available"
        row.operator("anim.krutart_set_keyingset", text="LocRot").ks_name = "Location & Rotation"
        row.operator("anim.krutart_set_keyingset", text="LocRotScale").ks_name = "Location, Rotation & Scale"

        # --- AUDIO CONTROLS ---
        row = layout.row(align=True)
        row.prop(scene, "use_audio_scrub", text="Scrubbing")
        row.prop(scene, "use_audio", text="Sound")

        flow = layout.grid_flow(row_major=True, columns=0, even_columns=True, even_rows=False, align=True)
        col = flow.column()
        col.prop(scene, "audio_volume")
        
        layout.separator()

        # ==========================================
        # --- KRUTART PIPELINE TOOLS ---
        # ==========================================
        box = layout.box()
        box.label(text="Krutart Pipeline Tools", icon='TOOL_SETTINGS')
        
        # 1. ANIM Work
        box.operator("anim.krutart_anim_work", text="Apply ANIM Render Config", icon='FILE_TICK')
        box.separator()
        
        # 2. Simplify Controls
        row = box.row(align=True)
        row.prop(scene.render, "use_simplify", text="", icon='MODIFIER')
        row.operator("anim.krutart_set_simplify", text="Subdiv: 0").level = 0
        row.operator("anim.krutart_set_simplify", text="1").level = 1
        row.operator("anim.krutart_set_simplify", text="2").level = 2
        box.separator()
        
        # 3. ANIM Draft (Playblast)
        box.prop(scene, "krutart_draft_comment", text="Comment")
        
        row = box.row(align=True)
        row.operator("anim.krutart_draft_setup", text="Setup ANIM Draft", icon='SETTINGS')
        row.operator("anim.krutart_open_draft_dir", text="", icon='FILE_FOLDER')
        
        # 4. Viewport Render
        playblast_op = box.operator("render.opengl", text="Render Playblast", icon='RENDER_ANIMATION')
        playblast_op.animation = True


class DOPESHEET_PT_krutart_controls(KRUTART_PT_timeline_base, Panel):
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_idname = "DOPESHEET_PT_krutart_controls"

class GRAPH_PT_krutart_controls(KRUTART_PT_timeline_base, Panel):
    bl_space_type = 'GRAPH_EDITOR'
    bl_idname = "GRAPH_PT_krutart_controls"

classes = (
    KRUTART_OT_set_keyingset,
    KRUTART_OT_anim_work_settings,
    KRUTART_OT_set_simplify,
    KRUTART_OT_draft_setup,
    KRUTART_OT_open_draft_dir, # <-- Appended new operator here
    DOPESHEET_PT_krutart_controls,
    GRAPH_PT_krutart_controls,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    bpy.types.Scene.krutart_draft_comment = bpy.props.StringProperty(
        name="Comment",
        description="Optional comment appended to the ANIM Draft playblast",
        default=""
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    del bpy.types.Scene.krutart_draft_comment

if __name__ == "__main__":
    register()