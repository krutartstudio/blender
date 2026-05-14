bl_info = {
    "name": "Krutart bRender + Deadline + Sheets",
    "author": "iori, Krutart, Gemini",
    "version": (5, 0, 5),
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > bRender",
    "description": "Prepares render files, submits to Deadline, and logs to Google Sheets.",
    "warning": "CYCLES/10ms limit/Output: Hybrid (MP4 Prod / ProRes Preprod).",
    "doc_url": "",
    "category": "Sequencer",
}

import bpy
import re
import os
import logging
import sys
import subprocess
import tempfile
import json
import urllib.request
import threading
import time
from bpy.app.handlers import persistent

# --- DEBUG HANDLER FOR WORKERS ---
@persistent
def debug_path_on_load(dummy):
    """Prints the render filepath to stdout on load. Visible in Deadline logs."""
    try:
        scene = bpy.context.scene
        if scene:
            print(f"[bRender] DEBUG_WORKER_LOAD: Scene '{scene.name}' render path: {scene.render.filepath}")
    except:
        pass

# --- SETUP LOGGER ---
log = logging.getLogger("bRender")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[bRender] %(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
log.setLevel(logging.INFO)

def attach_project_logger(target_directory):
    """
    Dynamically attaches a FileHandler to route logs to the specific render directory.
    Removes any previously attached FileHandlers to prevent cross-logging in batches.
    """
    for handler in log.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            log.removeHandler(handler)

    if not target_directory:
        return

    os.makedirs(target_directory, exist_ok=True)
    log_file_path = os.path.join(target_directory, "brender_deadline_submit.log")
    
    file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    log.addHandler(file_handler)
    
    log.info(f"=== bRender Log Initialized in: {target_directory} ===")

# --- GLOBAL VARS FOR HANDLERS ---
_last_scene_name = ""

# --- PREFERENCES HELPER ---
def get_prefs(context):
    """
    Helper to get addon preferences. 
    Handles both installed addon case and text editor execution case.
    """
    try:
        return context.preferences.addons[__name__].preferences
    except:
        # Fallback if running from text editor and __name__ doesn't match registered addon name
        for addon_name, addon in context.preferences.addons.items():
            if addon.preferences and hasattr(addon.preferences, "project_code"):
                return addon.preferences
        return None

# --- USER IDENTITY HELPER ---
def get_current_user(context):
    """
    Fetch user identity from Configurator Addon's CACHED_IDENTITY_MAP first, 
    then fallback to OS Hostname.
    """
    import socket
    import re
    import sys
    
    user_name = "unknown"
    hostname = socket.gethostname().lower()

    # 1. Check for manual override in Configurator preferences
    try:
        cfg_prefs = context.preferences.addons.get('krutart-configurator').preferences
        if cfg_prefs and cfg_prefs.user_name_override.strip():
            user_name = cfg_prefs.user_name_override.strip()
    except Exception:
        pass

    # 2. Tap into the Configurator's CACHED_IDENTITY_MAP dynamically
    if user_name == "unknown":
        for mod_name, mod in sys.modules.items():
            if hasattr(mod, "CACHED_IDENTITY_MAP") and isinstance(mod.CACHED_IDENTITY_MAP, dict):
                if hostname in mod.CACHED_IDENTITY_MAP:
                    user_name = mod.CACHED_IDENTITY_MAP[hostname]
                    break

    # 3. Fallback to standard OS logic
    if user_name == "unknown":
        user_name = hostname
        
    return re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)

# --- GOOGLE SHEETS HELPER (ROBUST) ---
def _send_payload_thread(urls, payload):
    """
    Worker function to send data to Google Sheets.
    Uses standard library urllib for zero-dependency compatibility.
    """
    import json
    import urllib.request
    import urllib.error
    import time

    data = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Blender-bRender-Client'
    }

    for url in urls:
        max_retries = 2
        base_delay = 1 
        
        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method='POST')
                log.info(f"Uploading to Sheets: {url} (Attempt {attempt}/{max_retries})...")
                
                with urllib.request.urlopen(req, timeout=10) as response:
                    result = response.read().decode('utf-8')
                    log.info(f"Google Sheet Response: {result}")
                    break # Success! Move to next URL.
                    
            except Exception as e:
                log.error(f"Error during Sheets upload to {url}: {e}")
            
            if attempt < max_retries:
                time.sleep(base_delay)

def upload_shot_data(context, shot_name, filename, version_str):
    """
    Prepares data (Filename, Version, User) and starts the upload thread for both projects.
    """
    prefs = get_prefs(context)
    if not prefs:
        return

    url_preprod = prefs.google_webapp_url.strip() if prefs.google_webapp_url else ""
    url_prod = "https://script.google.com/macros/s/AKfycbwnRq0OGESnMN3BB41q5ldfL71q2FLBiXPcHbdkDR5hO-NeBqrTZoxPCWgKukjeleSE9Q/exec"
    
    urls = [u for u in [url_preprod, url_prod] if u and u.startswith("http")]
    
    if not urls:
        log.warning("No valid Google WebApp URLs set. Skipping upload.")
        return

    user_name = get_current_user(context)
    filename_no_ext = os.path.splitext(filename)[0]

    payload = {
        "filename": filename_no_ext,
        "version": version_str,
        "user": user_name
    }

    t = threading.Thread(target=_send_payload_thread, args=(urls, payload))
    t.start()


# --- UTILITY FUNCTIONS ---
def set_active_scene_safe(context, target_scene):
    """Safely sets the active scene whether in UI mode or headless (Deadline) mode."""
    if getattr(context, "window", None):
        context.window.scene = target_scene
    else:
        context.scene = target_scene
        
def get_active_scene_safe(context):
    """Safely gets the active scene."""
    if getattr(context, "window", None):
        return context.window.scene
    return context.scene


# --- CORE LOGIC ---
def get_os_bridge(context=None):
    """Safely retrieves the krutart-os_bridge module if available."""
    if 'krutart-os_bridge' in sys.modules:
        return sys.modules['krutart-os_bridge']
    for mod_name, mod in sys.modules.items():
        if hasattr(mod, "bl_info") and isinstance(mod.bl_info, dict):
            if mod.bl_info.get("name") == "Krutart OS Bridge":
                return mod
    return None

def _is_production(context):
    """Detects if we are currently operating on a PRODUCTION file vs a PREPRODUCTION file."""
    if not bpy.data.is_saved:
        return False
    filepath = bpy.data.filepath.lower()
    return "3212-production" in filepath

def get_production_scene_dir_b_render(context, sc, sh):
    """
    Uses os_bridge to find the absolute Krutart root, then scans 
    3212-PRODUCTION directly for the full SC folder (e.g. SC17-DARKPOINT)
    and returns to the specific SH folder.
    """
    os_bridge = get_os_bridge(context)
    if not os_bridge:
        log.warning("[bRender] get_production_scene_dir: os_bridge not found.")
        return None

    mac_root = os_bridge.get_mac_root(context)
    if not mac_root:
        log.warning("[bRender] get_production_scene_dir: mac_root could not be resolved.")
        return None
        
    shared_drives = mac_root.parent
    production_root = shared_drives / "3212-PRODUCTION"
    
    if not production_root.exists():
        log.warning(f"[bRender] get_production_scene_dir: PRODUCTION root missing at {production_root}")
        return None
        
    sc_upper = sc.upper()
    sh_upper = sh.upper()
    search_prefix = f"{sc_upper}-"
    
    sc_dir_name = None
    for d in production_root.iterdir():
        if d.is_dir() and d.name.upper().startswith(search_prefix):
            sc_dir_name = d.name
            break
            
    if not sc_dir_name:
        log.warning(f"[bRender] get_production_scene_dir: Could not find SC folder starting with {search_prefix} in {production_root}")
        return None
        
    sh_target = f"{sc_upper}-{sh_upper}"
    sh_dir = production_root / sc_dir_name / sh_target
    
    return str(sh_dir)

def _get_composite_production_version(context):
    """
    Parses the current production file to harvest the current version and the 
    stamped 'source_work_version' from the opposite department's root collection.
    Returns formatted string like 'ani_v001-art_v003'.
    """
    if not bpy.data.is_saved:
        return "vUNKNOWN"
        
    filepath = bpy.data.filepath.lower()
    filename = os.path.basename(filepath)
    
    v_match = re.search(r'-(v\d{3,})', filename)
    curr_v = v_match.group(1) if v_match else "vUNKNOWN"
    
    is_ani = "-ani-" in filename or "ani-work" in filename
    is_art = "-art-" in filename or "art-work" in filename
    
    ani_v = "vUNKNOWN"
    art_v = "vUNKNOWN"
    
    if is_art:
        art_v = curr_v
        ani_col = bpy.data.collections.get("+ANI+")
        if ani_col and "source_work_version" in ani_col:
            ani_v = ani_col["source_work_version"]
        else:
            log.warning("[bRender] Missing +ANI+ collection or 'source_work_version' property. Using fallback.")
    elif is_ani:
        ani_v = curr_v
        art_col = bpy.data.collections.get("+ART+")
        if art_col and "source_work_version" in art_col:
            art_v = art_col["source_work_version"]
        else:
            log.warning("[bRender] Missing +ART+ collection or 'source_work_version' property. Using fallback.")
    else:
        return curr_v
        
    return f"ani_{ani_v}-art_{art_v}"

def _find_film_scene_name_on_disk(base_path, scene_number_str):
    """
    Scans the OUTPUT_BASE directory for a folder matching SC{number}-NAME.
    Returns the 'NAME' part (Film Scene Name) if found, otherwise None.
    """
    if not os.path.exists(base_path):
        return None

    search_prefix = scene_number_str.upper()

    try:
        for d in os.listdir(base_path):
            if not os.path.isdir(os.path.join(base_path, d)):
                continue

            d_upper = d.upper()
            if d_upper.startswith(f"{search_prefix}-"):
                parts = d.split("-", 1)
                if len(parts) > 1:
                    return parts[1]

    except Exception as e:
        log.error(f"Error scanning directory for film scene name: {e}")

    return None

def _parse_name_components(context, shot_marker_name, source_scene_name):
    """Parses all required name components."""
    log.info("Parsing name components...")

    shot_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker_name, re.IGNORECASE)
    if not shot_match:
        log.error(f"Could not parse shot marker name: {shot_marker_name}")
        return None

    scene_number = shot_match.group(1).upper()
    shot_number = shot_match.group(2).upper()

    prefs = get_prefs(context)
    if not prefs:
        log.error("Could not access Addon Preferences.")
        return None

    base_path = bpy.path.abspath(prefs.output_base)
    film_scene_name = _find_film_scene_name_on_disk(base_path, scene_number)

    if film_scene_name:
        log.info(f"Found Film Scene Name on disk: {film_scene_name}")
        env_name = film_scene_name
    else:
        log.warning(f"Could not find folder for {scene_number} in {base_path}. Falling back to Blender Scene Name.")
        env_match = re.search(r"sc\d+[-_](.+)", source_scene_name, re.IGNORECASE)
        env_name = env_match.group(1) if env_match else "env"

    project_code = prefs.project_code
    
    if _is_production(context):
        render_phase = getattr(context.scene, "brender_render_phase", "blocking")
        task = f"{render_phase}_r"
    else:
        task = "layout_r"

    components = {
        "project_code": project_code,
        "scene_number": scene_number,
        "shot_number": shot_number,
        "env_name": env_name,
        "task": task,
        "shot_marker_name": shot_marker_name
    }
    log.info(f"Parsed components: {components}")
    return components

def _get_shot_timing(context, shot_marker):
    """Utility to get shot start, end, and duration."""
    shot_markers = sorted(
        [m for m in context.scene.timeline_markers if re.match(r"CAM-SC\d+-SH\d+", m.name, re.IGNORECASE)],
        key=lambda m: m.frame
    )

    shot_start_frame = shot_marker.frame
    shot_end_frame = context.scene.frame_end + 1 

    try:
        current_marker_index = shot_markers.index(shot_marker)
        if current_marker_index < len(shot_markers) - 1:
            next_marker = shot_markers[current_marker_index + 1]
            shot_end_frame = next_marker.frame
    except ValueError:
        log.warning(f"Could not find shot marker '{shot_marker.name}' in the sorted list.")
        all_markers = sorted([m for m in context.scene.timeline_markers], key=lambda m: m.frame)
        for m in all_markers:
            if m.frame > shot_start_frame:
                shot_end_frame = m.frame
                break

    shot_duration = shot_end_frame - shot_start_frame
    if shot_duration <= 0:
        log.error(f"Calculated shot duration is zero or negative for '{shot_marker.name}'. Check marker positions.")
        return None, None, None

    log.info(f"Shot timing found: Start={shot_start_frame}, End={shot_end_frame-1}, Duration={shot_duration} frames.")
    return shot_start_frame, shot_end_frame, shot_duration

def _get_scene_content_duration(source_scene):
    """Finds the intended duration of the scene's content."""
    if not source_scene:
        log.error("No source scene provided to get content duration.")
        return 0

    end_marker = source_scene.timeline_markers.get("END")
    scene_content_duration = 0
    if end_marker:
        scene_content_duration = end_marker.frame - 1
    else:
        scene_content_duration = source_scene.frame_end - source_scene.frame_start + 1
        log.warning(f"No 'END' marker found in '{source_scene.name}'. Defaulting to scene's full duration: {scene_content_duration} frames.")

    if scene_content_duration <= 0:
        log.error(f"Calculated scene content duration is zero or negative for '{source_scene.name}'.")
        return 0

    return scene_content_duration

def _perform_destructive_save(context, new_filepath, source_scene_name, output_format):
    """
    Helper function to safely strip out all unneeded scenes, purge orphans,
    save the file, and seamlessly restore the user's Blender session using Undo.
    """
    log.info("Performing destructive scene cleanup for batch save...")
    
    can_undo = True
    try:
        bpy.ops.ed.undo_push(message="Pre-Cleanup State")
    except Exception as e:
        log.warning(f"Undo push failed: {e}. Scene cleanup bypassed to protect master file.")
        can_undo = False
        
    if can_undo:
        scenes_to_keep = [source_scene_name]
        if output_format == 'VIDEO':
            scenes_to_keep.append("render")
            
        for scn in list(bpy.data.scenes):
            if scn.name not in scenes_to_keep:
                try:
                    bpy.data.scenes.remove(scn)
                except Exception as e:
                    log.warning(f"Could not remove scene '{scn.name}': {e}")
        
        _purge_orphans()
        
    bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=True)
    log.info(f"Saved optimized copy: {os.path.basename(new_filepath)}")
    
    if can_undo:
        try:
            bpy.ops.ed.undo()
            log.info("Master file state restored successfully.")
        except Exception as e:
            log.error(f"Failed to restore scenes after save: {e}")

def apply_brender_optimizations(target_scene, use_simplify):
    """Applies general background configurations to the scene for the farm."""
    render = target_scene.render
    
    render.image_settings.quality = 90
    render.use_overwrite = False
    render.use_sequencer = False
    render.compositor_device = 'CPU' 
    render.compositor_denoise_device = 'CPU'
    render.compositor_denoise_preview_quality = 'FAST'
    render.compositor_denoise_final_quality = 'HIGH'
    render.threads_mode = 'AUTO'
    
    if hasattr(target_scene, 'cycles'):
        target_scene.cycles.use_auto_tile = True
        target_scene.cycles.tile_size = 2048
        
    render.use_simplify = use_simplify

    render.use_stamp = True
    render.use_stamp_labels = False
    render.use_stamp_date = True
    render.use_stamp_time = True
    render.use_stamp_render_time = True
    render.use_stamp_frame = True
    render.use_stamp_frame_range = True
    render.use_stamp_hostname = True
    render.use_stamp_scene = True
    render.use_stamp_sequencer_strip = True

    render.use_stamp_memory = False
    render.use_stamp_camera = False
    render.use_stamp_lens = False
    render.use_stamp_marker = False
    render.use_stamp_filename = False
    render.use_stamp_note = False
    
    max_res = max(render.resolution_x, render.resolution_y)
    if max_res >= 7680:   
        render.stamp_font_size = 64
    elif max_res >= 5760: 
        render.stamp_font_size = 48
    elif max_res >= 3840: 
        render.stamp_font_size = 32
    elif max_res >= 1920: 
        render.stamp_font_size = 24
    else:                 
        render.stamp_font_size = 12


def _prepare_shot_in_current_file(context, shot_marker):
    """Prepares the target scene for a given shot marker based on Output Format."""
    log.info(f"--- Starting preparation for shot: {shot_marker.name} ---")

    original_active_scene = get_active_scene_safe(context)
    original_frame = original_active_scene.frame_current
    original_active_scene.frame_set(shot_marker.frame)
    context.view_layer.update()

    output_format = context.scene.brender_output_format

    shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
    if shot_start_frame is None:
        original_active_scene.frame_set(original_frame)
        return (False, None, None)

    source_scene = original_active_scene

    if not source_scene:
        log.error(f"Source scene '{source_scene.name}' is invalid. Aborting.")
        original_active_scene.frame_set(original_frame)
        return (False, None, None)

    if output_format == 'VIDEO':
        if not source_scene.sequence_editor:
            log.error(f"Source scene '{source_scene.name}' has no VSE. Aborting Video prep.")
            original_active_scene.frame_set(original_frame)
            return (False, None, None)

        scene_content_duration = _get_scene_content_duration(source_scene)
        if scene_content_duration <= 0:
            original_active_scene.frame_set(original_frame)
            return (False, None, None)

    shot_name = shot_marker.name

    captured_res_x = source_scene.render.resolution_x
    captured_res_y = source_scene.render.resolution_y
    captured_res_pct = source_scene.render.resolution_percentage
    
    final_res_x = captured_res_x
    final_res_y = captured_res_y
    final_res_pct = captured_res_pct

    if captured_res_x != captured_res_y:
        log.info(f"Non-square aspect ratio detected ({captured_res_x}x{captured_res_y}). Forcing 2K (2048x2048) @ 100%.")
        final_res_x = 2048
        final_res_y = 2048
        final_res_pct = 100
    else:
        log.info(f"Square aspect ratio detected. Preserving intended resolution: {final_res_x}x{final_res_y} @ {final_res_pct}%.")

    try:
        if hasattr(source_scene, 'shot_camera_toggle'):
            source_scene.shot_camera_toggle = 'FULLDOME'
            context.view_layer.update()
        else:
            log.error("Cannot find 'shot_camera_toggle' property.")
            raise Exception("shot_camera_toggle property not found")
    except Exception as e:
        log.error(f"Failed to bind FULLDOME cameras: {e}")
        original_active_scene.frame_set(original_frame)
        return (False, None, None)

    source_scene.render.resolution_x = final_res_x
    source_scene.render.resolution_y = final_res_y
    source_scene.render.resolution_percentage = final_res_pct

    target_scene = None

    if output_format == 'VIDEO':
        existing_render_scene = bpy.data.scenes.get("render")
        if existing_render_scene:
            log.warning("Found existing 'render' scene. Removing it.")
            try:
                bpy.data.scenes.remove(existing_render_scene)
            except Exception as e:
                log.error(f"Could not remove existing 'render' scene: {e}. Aborting.")
                original_active_scene.frame_set(original_frame)
                return (False, None, None)

        log.info(f"Creating an empty copy of the active scene '{original_active_scene.name}'.")
        bpy.ops.scene.new(type='EMPTY')
        target_scene = get_active_scene_safe(context)
        target_scene.name = "render"
        target_scene.render.fps = 30
        target_scene.render.fps_base = 1.0
        
        target_scene.render.resolution_x = final_res_x
        target_scene.render.resolution_y = final_res_y
        target_scene.render.resolution_percentage = final_res_pct
        
        set_active_scene_safe(context, original_active_scene)
    else:
        target_scene = source_scene
        log.info(f"EXR Mode: Targeting source scene '{target_scene.name}' directly.")

    fulldome_camera_name = f"{shot_name}-FULLDOME"
    fulldome_camera = bpy.data.objects.get(fulldome_camera_name)

    if fulldome_camera and fulldome_camera.type == 'CAMERA':
        target_scene.camera = fulldome_camera
    else:
        log.warning(f"Could not find FULLDOME camera named '{fulldome_camera_name}'.")

    target_scene.frame_start = shot_start_frame
    target_scene.frame_end = shot_end_frame - 1

    # --- APPLY BASELINE OPTIMIZATIONS ---
    # Natively check the real use_simplify state and always apply these to farm submissions
    apply_brender_optimizations(target_scene, context.scene.render.use_simplify)

    if output_format == 'VIDEO':
        is_prod = _is_production(context)
        log.info(f"Applying {'MP4 (H.264)' if is_prod else 'ProRes (.mov)'} VSE and Render Overrides...")
        vse_source = source_scene.sequence_editor
        guide_video_strip, guide_audio_strip = None, None
        shot_name_prefix = shot_marker.name

        scene_num_str, shot_num_str = "", ""
        name_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker.name, re.IGNORECASE)
        if name_match:
            scene_num_str = name_match.group(1).lower()
            shot_num_str = name_match.group(2).lower()

        all_strips = getattr(vse_source, 'sequences_all', vse_source.sequences)
        candidates = sorted([s for s in all_strips if not s.mute], key=lambda s: s.channel, reverse=True)

        for strip in candidates:
            if strip.name.startswith(shot_name_prefix):
                if strip.type == 'MOVIE' and not guide_video_strip:
                    guide_video_strip = strip
                if strip.type == 'SOUND' and not guide_audio_strip:
                    guide_audio_strip = strip
            if guide_video_strip and guide_audio_strip:
                break

        if (not guide_video_strip or not guide_audio_strip) and scene_num_str and shot_num_str:
            for strip in candidates:
                strip_name_lower = strip.name.lower()
                if scene_num_str in strip_name_lower and shot_num_str in strip_name_lower:
                    if strip.type == 'MOVIE' and not guide_video_strip:
                        guide_video_strip = strip
                    if strip.type == 'SOUND' and not guide_audio_strip:
                        guide_audio_strip = strip
                if guide_video_strip and guide_audio_strip:
                    break

        if not guide_video_strip or not guide_audio_strip:
            for strip in candidates:
                if strip.frame_start == shot_start_frame:
                    if strip.type == 'MOVIE' and not guide_video_strip:
                        guide_video_strip = strip
                    if strip.type == 'SOUND' and not guide_audio_strip:
                        guide_audio_strip = strip
                if guide_video_strip and guide_audio_strip:
                    break

        if not target_scene.sequence_editor:
            target_scene.sequence_editor_create()

        vse_render = target_scene.sequence_editor
        for strip in list(vse_render.sequences):
            vse_render.sequences.remove(strip)

        if guide_audio_strip:
            new_audio = vse_render.sequences.new_sound(
                name=f"{shot_name}-guide_audio",
                filepath=bpy.path.abspath(guide_audio_strip.sound.filepath),
                channel=1, frame_start=shot_start_frame)
            new_audio.frame_final_duration = shot_duration
            new_audio.frame_offset_start = 0
            new_audio.volume = 0.8

        shot_scene_strip = vse_render.sequences.new_scene(
            name=shot_name, scene=source_scene,
            channel=2, frame_start=shot_start_frame)

        shot_scene_strip.frame_final_duration = scene_content_duration
        shot_scene_strip.scene_input = 'CAMERA'
        shot_scene_strip.animation_offset_start = 1 - source_scene.frame_start

        if guide_video_strip:
            new_video = vse_render.sequences.new_movie(
                name=f"{shot_name}-guide_video",
                filepath=bpy.path.abspath(guide_video_strip.filepath),
                channel=3, frame_start=shot_start_frame)
            new_video.frame_final_duration = shot_duration
            new_video.frame_offset_start = 0
            new_video.blend_type = 'ALPHA_OVER'
            new_video.blend_alpha = 1
            if hasattr(new_video, 'sound') and new_video.sound: new_video.sound.volume = 0

            baseline_res = 2048.0
            mult_x = max(final_res_x, 1) / baseline_res
            mult_y = max(final_res_y, 1) / baseline_res
            
            new_video.transform.scale_x = 3 * mult_x
            new_video.transform.scale_y = 3 * mult_x
            new_video.transform.offset_x = 410 * mult_x
            new_video.transform.offset_y = 1708 * mult_y

            new_video.crop.max_x = 860
            new_video.crop.max_y = 498
            
            mod = new_video.modifiers.new(name="GreenMask", type='COLOR_BALANCE')
            mod.color_balance.lift = [0, 1, 0]
            mod.color_balance.gamma = [0, 1, 0]
            mod.color_balance.gain = [0, 1, 0]

        log.info("Setting CYCLES: 1 sample, No Denoise, 10ms Time Limit.")
        target_scene.render.engine = 'CYCLES'
        if hasattr(target_scene, 'cycles'):
            target_scene.cycles.samples = 1
            target_scene.cycles.use_denoising = False
            target_scene.cycles.transparent_max_bounces = 1
            target_scene.cycles.time_limit = 0.01 
        else:
            target_scene.render.samples = 1

        target_scene.render.film_transparent = True
        target_scene.render.use_compositing = True
        target_scene.render.use_sequencer = True

        is_prod = _is_production(context)
        target_scene.render.image_settings.file_format = 'FFMPEG'
        
        if is_prod:
            log.info("Setting Output Format to FFMPEG / MPEG4 / H.264 (High Quality).")
            target_scene.render.ffmpeg.format = 'MPEG4'
            target_scene.render.ffmpeg.codec = 'H264'
            target_scene.render.ffmpeg.constant_rate_factor = 'HIGH'
            target_scene.render.ffmpeg.ffmpeg_preset = 'GOOD'
            target_scene.render.ffmpeg.gopsize = 12
            target_scene.render.ffmpeg.max_b_frames = 0
            
            target_scene.render.ffmpeg.audio_codec = 'AAC'
            target_scene.render.ffmpeg.audio_channels = 'STEREO'
            target_scene.render.ffmpeg.audio_mixrate = 48000
            target_scene.render.ffmpeg.audio_bitrate = 128
        else:
            log.info("Setting Output Format to FFMPEG / QUICKTIME / PRORES.")
            target_scene.render.ffmpeg.format = 'QUICKTIME'
            target_scene.render.ffmpeg.codec = 'PRORES'
            target_scene.render.ffmpeg.audio_codec = 'PCM'
        
        target_scene.render.ffmpeg.audio_volume = 1.0 
        
        try:
            shot_scene_strip.frame_start = 1
        except Exception as e:
            log.error(f"Could not move scene strip to frame 1: {e}")

    # elif output_format == 'EXR':
    #     log.info("Applying EXR-specific settings. User engine/samples retained.")
    #     target_scene.render.use_sequencer = False
    #     target_scene.render.image_settings.file_format = 'OPEN_EXR'
    #     target_scene.render.image_settings.color_mode = 'RGBA'
    #     target_scene.render.image_settings.color_depth = '16'
    #     target_scene.render.image_settings.exr_codec = 'DWAB'
    #     target_scene.render.image_settings.quality = 50

    name_components = _parse_name_components(context, shot_marker.name, source_scene.name)
    if not name_components:
        log.error("Failed to parse name components for render path.")
        original_active_scene.frame_set(original_frame)
        set_active_scene_safe(context, original_active_scene)
        return (False, None, None)

    new_save_path, new_blend_filename_no_ext, version_str_out = _get_new_brender_filepath_parts(context, name_components)

    if not new_save_path:
        log.error(f"Failed to generate a new file path for {shot_marker.name}.")
        original_active_scene.frame_set(original_frame)
        set_active_scene_safe(context, original_active_scene)
        return (False, None, None)

    name_components['new_save_path'] = new_save_path
    name_components['version_str'] = version_str_out 

    try:
        is_prod = _is_production(context)
        scene_num = name_components["scene_number"].upper()
        env_name = name_components["env_name"].upper()  
        shot_num = name_components["shot_number"].upper()  
        target_scene_folder = f"{scene_num}-{env_name}"
        
        if output_format == 'VIDEO':
            if is_prod:
                # Production: Render right next to the render blender file in the sc##-sh###-RENDER directory
                output_dir = os.path.dirname(new_save_path)
                os.makedirs(output_dir, exist_ok=True)
                
                final_filename = f"{new_blend_filename_no_ext}.mp4".lower()
                render_filepath = os.path.join(output_dir, final_filename)
                
                # Final Sanitization for Production: Ensure internal render path is canonical for the farm
                os_bridge = get_os_bridge(context)
                if os_bridge and sys.platform.startswith("win"):
                    render_filepath = os_bridge.sanitize_windows_absolute(render_filepath, context)
                
                target_scene.render.filepath = render_filepath
                target_scene.render.use_file_extension = False 
                log.info(f"[bRender] Set PRODUCTION render output path ({'MP4' if is_prod else 'ProRes'}) to: {render_filepath}")
                print(f"[bRender] DEBUG_PREPARE: Final Production Path: {render_filepath}")

            else:
                prefs = get_prefs(context)
                base_path = bpy.path.abspath(prefs.output_base)
                
                found_scene_folder = target_scene_folder
                if os.path.exists(base_path):
                    for d in os.listdir(base_path):
                        if d.upper() == target_scene_folder:
                            found_scene_folder = d
                            break
                
                scene_dir_path = os.path.join(base_path, found_scene_folder)
                output_dir = os.path.join(scene_dir_path, shot_num)
                os.makedirs(output_dir, exist_ok=True)
                
                ext = ".mp4" if is_prod else ".mov"
                final_filename = new_blend_filename_no_ext.lower() + ext
                render_filepath = os.path.join(output_dir, final_filename)
                
                # Final Sanitization for Preproduction: Ensure internal render path is canonical for the farm
                os_bridge = get_os_bridge(context)
                if os_bridge and sys.platform.startswith("win"):
                    render_filepath = os_bridge.sanitize_windows_absolute(render_filepath, context)
                
                target_scene.render.filepath = render_filepath
                target_scene.render.use_file_extension = False 
                log.info(f"[bRender] Set PREPRODUCTION render output path to: {render_filepath}")
                print(f"[bRender] DEBUG_PREPARE: Final Preproduction Path: {render_filepath}")

        # elif output_format == 'EXR':
        #     exr_root = r"R:\3212"
        #     ver_folder = f"{scene_num}-{shot_num}-{version_str_out}_R"
        #     
        #     exr_dir = os.path.join(exr_root, target_scene_folder, shot_num, ver_folder, "EXR")
        #     os.makedirs(exr_dir, exist_ok=True)
        #     
        #     exr_filename = f"{ver_folder}-######.exr"
        #     render_filepath = os.path.join(exr_dir, exr_filename)
        #     
        #     target_scene.render.filepath = render_filepath
        #     target_scene.render.use_file_extension = False 
        #     log.info(f"[bRender] Set EXR sequence output path to: {render_filepath}")

    except Exception as e:
        log.error(f"Error setting render output path: {e}")

    # DO NOT restore the original frame here. We want the file to be saved 
    # with the playhead exactly ON the shot frame so advanced_copy fixes the visibility
    # before we perform the destructive save. 
    # original_active_scene.frame_set(original_frame)
    
    set_active_scene_safe(context, target_scene)

    log.info(f"--- Successfully prepared shot: {shot_marker.name} ---")
    return (True, source_scene, name_components)


def _get_new_brender_filepath_parts(context, name_components):
    """
    Calculates the directory, version, and final path for a new bRender file.
    UPDATED: Returns (new_filepath, filename_no_ext, version_str)
    Handles both PREPRODUCTION and PRODUCTION workflows with composite tracking.
    """
    if not bpy.data.is_saved:
        log.error("Source file is not saved. Cannot determine output path.")
        return None, None, None

    if not name_components:
        return None, None, None

    project_code = name_components['project_code']
    scene_number = name_components['scene_number']
    shot_number = name_components['shot_number']
    env_name = name_components['env_name']
    task = name_components['task']

    is_prod = _is_production(context)
    version_str_out = "v001"

    try:
        if is_prod:
            sc_upper = scene_number.upper()
            sh_upper = shot_number.upper()
            
            master_sh_dir = get_production_scene_dir_b_render(context, sc_upper, sh_upper)
            if not master_sh_dir:
                log.error("[bRender] CRITICAL: Could not resolve absolute Production path.")
                return None, None, None

            brender_dir = os.path.join(master_sh_dir, f"{sc_upper}-{sh_upper}-RENDER")
            os.makedirs(brender_dir, exist_ok=True)
            
            composite_version = _get_composite_production_version(context)
            version_str_out = composite_version
            
            version_dir_name = f"{sc_upper}-{sh_upper}-{composite_version}_R"
            version_dir_path = os.path.join(brender_dir, version_dir_name)
            os.makedirs(version_dir_path, exist_ok=True)
            
            task_lower = task.lower()
            filename_base_no_ext = f"{project_code}-{sc_upper}-{sh_upper}-{composite_version}-{task_lower}"
            filename_base_no_ext_lower = filename_base_no_ext.lower()
            
            base_filepath = os.path.join(version_dir_path, f"{filename_base_no_ext_lower}.blend")
            
            final_filename_no_ext_lower = filename_base_no_ext_lower

            new_filename = f"{final_filename_no_ext_lower}.blend"
            new_filepath = os.path.join(version_dir_path, new_filename)
            
            filename_for_return = final_filename_no_ext_lower.upper()
            
        else:
            base_dir = os.path.dirname(bpy.data.filepath)       
            parent_dir_path = os.path.dirname(base_dir)        
            grandparent_dir_name = os.path.basename(parent_dir_path)
            brender_dir_name = f"{grandparent_dir_name}-BRENDER" 
            brender_dir = os.path.join(parent_dir_path, brender_dir_name) 
    
            os.makedirs(brender_dir, exist_ok=True)
            
            filename_prefix = f"{project_code}-{scene_number}-{env_name}-{shot_number}-{task}-v"
            prefix_lower = filename_prefix.lower()
            
            version = 1
            if os.path.exists(brender_dir):
                existing_files = [f for f in os.listdir(brender_dir) if f.lower().startswith(prefix_lower) and f.lower().endswith('.blend')]
                if existing_files:
                    max_version = 0
                    for f in existing_files:
                        version_match = re.search(r"-v(\d+)\.blend$", f, re.IGNORECASE)
                        if version_match:
                            max_version = max(max_version, int(version_match.group(1)))
                    version = max_version + 1
                    
            version_str_out = f"v{version:03d}"
            filename_base_no_ext = f"{filename_prefix}{version:03d}"
            filename_base_no_ext_lower = filename_base_no_ext.lower()
            new_filename = f"{filename_base_no_ext_lower}.blend"
            
            new_filepath = os.path.join(brender_dir, new_filename)
            filename_for_return = filename_base_no_ext_lower.upper()

    except Exception as e:
        log.error(f"Error creating BRENDER filepath: {e}")
        return None, None, None

    # Final Sanitization: Ensure the returned path respects the canonical S: drive on Windows
    # This ensures that even if the artist works on Z:, the file saved for the farm is on S:
    os_bridge = get_os_bridge(context)
    if os_bridge and sys.platform.startswith("win"):
        new_filepath = os_bridge.sanitize_windows_absolute(new_filepath, context)

    return new_filepath, filename_for_return, version_str_out

def get_new_brender_filepath(context, name_components):
    new_filepath, _, _ = _get_new_brender_filepath_parts(context, name_components)
    return new_filepath

def get_shot_info_from_frame(context):
    scene = context.scene
    current_frame = scene.frame_current

    shot_markers = sorted(
        [m for m in scene.timeline_markers if m.name.startswith("CAM-SC")],
        key=lambda m: m.frame
    )

    active_shot_marker = None
    for m in reversed(shot_markers):
        if m.frame <= current_frame:
            active_shot_marker = m
            break

    if not active_shot_marker: return None

    end_frame = scene.frame_end + 1
    for m in shot_markers:
        if m.frame > active_shot_marker.frame:
            end_frame = m.frame
            break

    return {"shot_marker": active_shot_marker, "end_frame": end_frame, "duration": end_frame - active_shot_marker.frame}

def get_all_shots(context):
    scene = context.scene
    shot_markers = [m for m in scene.timeline_markers if re.match(r"CAM-SC\d+-SH\d+", m.name, re.IGNORECASE)]
    return sorted(shot_markers, key=lambda m: m.frame)

def _purge_orphans():
    """Aggressively purges all orphaned data-blocks."""
    log.info("Purging orphaned data-blocks...")
    try:
        purged_count = bpy.data.orphans_purge(do_recursive=True)
        log.info(f"Purged {purged_count} orphaned data-blocks.")
        purged_count_2 = bpy.data.orphans_purge(do_recursive=True)
        if purged_count_2 > 0:
            log.info(f"Purged an additional {purged_count_2} nested data-blocks.")
    except Exception as e:
        log.error(f"Error during orphan purge: {e}.")

# --- DEADLINE SUBMISSION HELPER ---
def _submit_to_deadline(context, filepath, start_frame, end_frame, output_path, deadline_cmd):
    """Submits a specific blend file to Deadline and logs the payload."""
    
    # Path Sanitization: Ensure paths use the canonical drive letter (e.g., S:) on Windows
    if sys.platform.startswith("win"):
        os_bridge = get_os_bridge(context)
        if os_bridge:
            win_drive = os_bridge.get_win_config(context)
            if not filepath.upper().startswith(win_drive.upper()):
                filepath = os_bridge.sanitize_windows_absolute(filepath, context)
                log.info(f"[bRender] Sanitized SceneFile path for Deadline: {filepath}")
            if not output_path.upper().startswith(win_drive.upper()):
                output_path = os_bridge.sanitize_windows_absolute(output_path, context)
                log.info(f"[bRender] Sanitized Output path for Deadline: {output_path}")

    if not os.path.exists(deadline_cmd):
        log.error(f"Deadline executable not found at: {deadline_cmd}")
        return False

    job_name = os.path.basename(filepath)
    batch_name = job_name
    
    major, minor = bpy.app.version[0], bpy.app.version[1]
    blender_version = f"{major}.{minor}"

    total_frames = (end_frame - start_frame) + 1
    chunk_size = total_frames + 5000 

    priority = context.scene.brender_deadline_priority
    pool = context.scene.brender_deadline_pool
    sec_pool = context.scene.brender_deadline_secondary_pool
    group = context.scene.brender_deadline_group

    job_info = [
        f"Name={job_name}",
        f"BatchName={batch_name}",
        "Plugin=Blender",
        f"Frames={start_frame}-{end_frame}",
        f"ChunkSize={chunk_size}",
        f"Priority={priority}",
        f"Pool={pool}",
        f"SecondaryPool={sec_pool}",
        f"Group={group}",
        f"OutputDirectory0={os.path.dirname(output_path)}",
        f"OutputFilename0={os.path.basename(output_path)}", 
    ]

    plugin_info = [
        f"SceneFile={filepath}",
        f"Version={blender_version}",
        "Build=None",
        "Threads=0",
    ]

    log.info("--- Deadline Job Payload ---")
    for line in job_info: log.info(line)
    log.info("----------------------------")

    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".job", encoding='utf-8') as j_file:
            j_file.write("\n".join(job_info))
            j_job_path = j_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".job", encoding='utf-8') as p_file:
            p_file.write("\n".join(plugin_info))
            p_plugin_path = p_file.name

        log.info(f"Executing deadlinecommand for {job_name}...")
        
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        process = subprocess.Popen(
            [deadline_cmd, j_job_path, p_plugin_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo
        )
        stdout, stderr = process.communicate()

        try:
            os.remove(j_job_path)
            os.remove(p_plugin_path)
        except:
            pass

        if process.returncode == 0:
            log.info("Deadline Submission Successful:")
            log.info(stdout.strip())
            return True
        else:
            log.error("Deadline Submission Failed:")
            log.error(f"STDOUT:\n{stdout.strip()}")
            log.error(f"STDERR:\n{stderr.strip()}")
            return False

    except Exception as e:
        log.error(f"Exception during Deadline submission: {e}")
        return False

# --- DATA STRUCTURE FOR SHOT LIST ---
class BRENDER_ShotListItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty() 
    display_name: bpy.props.StringProperty() 
    is_selected: bpy.props.BoolProperty(name="", description="Include this shot in the batch preparation", default=True)
    frame: bpy.props.IntProperty()

class BRENDER_UL_shot_list(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "is_selected", text=item.display_name)
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text=item.display_name)

# --- PREFERENCES PANEL ---
class BRENDER_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    project_code: bpy.props.StringProperty(
        name="Project Code", default="3212")
    
    output_base: bpy.props.StringProperty(
        name="Render Output Base", 
        default="S:\\3212-EDIT\\SOURCE\\LAYOUT_RENDER\\", 
        subtype='DIR_PATH')
    
    google_webapp_url: bpy.props.StringProperty(
        name="Google WebApp URL",
        description="The Web App URL from your deployed Google Apps Script",
        default="https://script.google.com/macros/s/AKfycbxNBjD9rjBHgesVCxYpsH6J_m9qHt2ZL1n-ANGKxiuceOtF7pNV584ylJNOSTK55t5A/exec"
    )

    default_deadline_path = r"C:\Program Files\Thinkbox\Deadline10\bin\deadlinecommand.exe"
    if sys.platform == "darwin":
        default_deadline_path = "/Applications/Thinkbox/Deadline10/Resources/deadlinecommand"
    elif sys.platform == "linux":
        default_deadline_path = "/opt/Thinkbox/Deadline10/bin/deadlinecommand"

    deadline_path: bpy.props.StringProperty(
        name="Deadline Command", 
        default=default_deadline_path, 
        subtype='FILE_PATH',
        description="Path to deadlinecommand executable"
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Global bRender Settings", icon="PREFERENCES")
        box.prop(self, "project_code")
        box.prop(self, "output_base")
        
        box.separator()
        box.label(text="Google Sheets Logging", icon="URL")
        box.prop(self, "google_webapp_url")
        
        box.separator()
        box.label(text="Render Farm", icon="NETWORK_DRIVE")
        box.prop(self, "deadline_path")


# --- OPERATORS ---
class BRENDER_OT_select_all_shots(bpy.types.Operator):
    bl_idname = "brender.select_all_shots"
    bl_label = "Select All"
    bl_description = "Select or Deselect all shots"
    
    action: bpy.props.EnumProperty(
        items=[('SELECT', "Select", ""), ('DESELECT', "Deselect", "")],
        default='SELECT'
    )

    def execute(self, context):
        for shot in context.scene.brender_shot_list:
            shot.is_selected = (self.action == 'SELECT')
        return {'FINISHED'}

class BRENDER_OT_prepare_this_file(bpy.types.Operator):
    bl_idname = "brender.prepare_this_file"
    bl_label = "Prepare This File"
    bl_description = "Prepares this file for rendering based on its filename"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        filepath = bpy.data.filepath
        return bool(filepath)

    def execute(self, context):
        filepath = bpy.data.filepath
        filename = os.path.basename(filepath)

        name_match = re.match(r".*-(sc\d+)-.*-(sh\d+)-.*-v\d+\.blend", filename, re.IGNORECASE)
        if not name_match:
            name_match = re.match(r"(SC\d+)-(SH\d+)\.blend", filename, re.IGNORECASE)

        if not name_match:
            self.report({"ERROR"}, "Filename format not recognized.")
            return {"CANCELLED"}

        scene_number, shot_number = name_match.group(1).upper(), name_match.group(2).upper()
        target_shot_name = f"CAM-{scene_number}-{shot_number}"

        shot_marker = context.scene.timeline_markers.get(target_shot_name)
        if not shot_marker:
            self.report({"ERROR"}, f"Could not find a timeline marker named '{target_shot_name}'.")
            return {"CANCELLED"}

        success, _, _ = _prepare_shot_in_current_file(context, shot_marker)

        if success:
            _purge_orphans()
            bpy.ops.wm.save_mainfile()
            self.report({"INFO"}, f"Successfully prepared file for shot {target_shot_name}.")
        else:
            self.report({"ERROR"}, "An error occurred during file preparation. Check console.")

        return {'FINISHED'}

class BRENDER_OT_refresh_shot_list(bpy.types.Operator):
    bl_idname = "brender.refresh_shot_list"
    bl_label = "Refresh Shot List"

    def execute(self, context):
        shot_list = context.scene.brender_shot_list
        shot_list.clear()
        found_shots = get_all_shots(context)
        for marker in found_shots:
            item = shot_list.add()
            item.name = marker.name
            name_match = re.match(r"CAM-(SC\d+-SH\d+)", marker.name, re.IGNORECASE)
            if name_match:
                item.display_name = name_match.group(1)
            else:
                item.display_name = marker.name 
            item.frame = marker.frame

        context.scene.brender_active_shot_index = 0
        log.info(f"Found and listed {len(found_shots)} shots.")
        return {'FINISHED'}

class BRENDER_OT_prepare_active_shot(bpy.types.Operator):
    bl_idname = "brender.prepare_active_shot"
    bl_label = "Prepare Active Shot"
    bl_description = "Creates and saves a prepared render file for the shot under the playhead"
    bl_options = {"REGISTER"}

    def execute(self, context):
        original_scene = get_active_scene_safe(context)
        original_scene_name = original_scene.name  
        original_frame = original_scene.frame_current
        
        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the main project file first.")
            return {"CANCELLED"}

        shot_info = get_shot_info_from_frame(context)
        if not shot_info:
            self.report({"ERROR"}, "No active shot marker found at the current frame.")
            return {"CANCELLED"}

        shot_marker = shot_info["shot_marker"]
        log.info(f"Preparing active shot: {shot_marker.name}")

        success, source_scene, name_components = _prepare_shot_in_current_file(context, shot_marker)
        if not success:
            self.report({"ERROR"}, f"Failed to prepare render scene for {shot_marker.name}.")
            fresh_scene = bpy.data.scenes.get(original_scene_name)
            if fresh_scene: 
                set_active_scene_safe(context, fresh_scene)
                fresh_scene.frame_set(original_frame)
            return {"CANCELLED"}

        new_filepath = name_components.get('new_save_path')
        version_str_out = name_components.get('version_str', 'v001')

        if new_filepath:
            target_dir = os.path.dirname(new_filepath)
            attach_project_logger(target_dir)

            _perform_destructive_save(
                context, 
                new_filepath, 
                source_scene.name, 
                context.scene.brender_output_format
            )

            upload_shot_data(
                context, 
                shot_name=shot_marker.name, 
                filename=os.path.basename(new_filepath),
                version_str=version_str_out
            )

            self.report({'INFO'}, f"Saved clean optimized copy: {os.path.basename(new_filepath)}")
        else:
            self.report({"ERROR"}, f"Could not generate a valid filename for '{shot_marker.name}'.")
            fresh_scene = bpy.data.scenes.get(original_scene_name)
            if fresh_scene: 
                set_active_scene_safe(context, fresh_scene)
                fresh_scene.frame_set(original_frame)
            return {"CANCELLED"}

        fresh_scene = bpy.data.scenes.get(original_scene_name)
        if fresh_scene: 
            set_active_scene_safe(context, fresh_scene)
            fresh_scene.frame_set(original_frame)
        
        log.info("Preparation for active shot complete.")
        return {'FINISHED'}

class BRENDER_OT_prepare_render_batch(bpy.types.Operator):
    bl_idname = "brender.prepare_render_batch"
    bl_label = "Prepare Batch From Selection"
    bl_description = "Prepares render scenes for selected shots and submits to Deadline."

    def execute(self, context):
        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the main project file first.")
            return {"CANCELLED"}

        original_scene = get_active_scene_safe(context)
        original_scene_name = original_scene.name  
        original_frame = original_scene.frame_current
        
        prefs = get_prefs(context)
        deadline_cmd = prefs.deadline_path if prefs else ""

        # FIX: Store string names instead of live RNA objects
        selected_shot_names = [s.name for s in context.scene.brender_shot_list if s.is_selected]
        
        if not selected_shot_names:
            self.report({"WARNING"}, "No shots selected from the list.")
            return {"CANCELLED"}

        log.info(f"Starting batch preparation for {len(selected_shot_names)} shots.")
        processed_count = 0
        submitted_count = 0

        # Loop through the string names instead
        for shot_name in selected_shot_names:
            log.info(f"--- Preparing batch item: {shot_name} ---")

            # Must re-fetch scene each loop iteration as it gets invalidated by Undo
            current_fresh_scene = bpy.data.scenes.get(original_scene_name)
            if not current_fresh_scene:
                log.error("Original scene lost. Aborting batch.")
                break
                
            shot_marker = current_fresh_scene.timeline_markers.get(shot_name)
            if not shot_marker:
                log.error(f"Marker '{shot_name}' not found. Skipping.")
                continue

            success, source_scene, name_components = _prepare_shot_in_current_file(context, shot_marker)

            if not success:
                log.error(f"Preparation failed for '{shot_name}'. Skipping save.")
                fresh_scene = bpy.data.scenes.get(original_scene_name)
                if fresh_scene: set_active_scene_safe(context, fresh_scene)
                continue

            new_filepath = name_components.get('new_save_path')
            version_str_out = name_components.get('version_str', 'v001')

            if new_filepath:
                target_dir = os.path.dirname(new_filepath)
                attach_project_logger(target_dir)

                # Capture render settings BEFORE destructive save destroys the 'render' scene
                temp_render_scene = get_active_scene_safe(context)
                start_frame = temp_render_scene.frame_start
                end_frame = temp_render_scene.frame_end
                output_path = temp_render_scene.render.filepath

                _perform_destructive_save(
                    context, 
                    new_filepath, 
                    source_scene.name, 
                    context.scene.brender_output_format
                )
                
                upload_shot_data(
                    context, 
                    shot_name=shot_name, 
                    filename=os.path.basename(new_filepath),
                    version_str=version_str_out
                )

                submit_success = _submit_to_deadline(context, new_filepath, start_frame, end_frame, output_path, deadline_cmd)
                if submit_success:
                    submitted_count += 1

                processed_count += 1
            else:
                log.error(f"Could not generate filename for '{shot_name}'. Skipping save.")

            # FIX: Fetch fresh reference after Undo to safely uncheck the item in the UI
            fresh_scene = bpy.data.scenes.get(original_scene_name)
            if fresh_scene: 
                set_active_scene_safe(context, fresh_scene)
                
                # Safely find the UI list item in the new memory state and uncheck it
                fresh_shot_item = next((item for item in fresh_scene.brender_shot_list if item.name == shot_name), None)
                if fresh_shot_item:
                    fresh_shot_item.is_selected = False

        # --- Restoration ---
        fresh_scene = bpy.data.scenes.get(original_scene_name)
        if fresh_scene:
            set_active_scene_safe(context, fresh_scene)
            fresh_scene.frame_set(original_frame)
        
        temp_render = bpy.data.scenes.get("render")
        if temp_render:
            try:
                bpy.data.scenes.remove(temp_render)
                log.info("Cleaned up temporary 'render' scene left over in active file.")
            except Exception as e:
                log.error(f"Error cleaning up 'render' scene: {e}")

        msg = f"Batch complete. Saved {processed_count} files. Submitted {submitted_count} to Deadline."
        log.info(f"--- {msg} ---")
        self.report({'INFO'}, msg)

        return {'FINISHED'}

# --- HANDLERS (AUTO-REFRESH) ---
@persistent
def auto_refresh_shot_list(dummy):
    """
    Handler that refreshes the shot list when:
    1. A file is loaded (load_post)
    2. The scene changes (depsgraph_update_post check)
    """
    global _last_scene_name
    
    if not bpy.context or not bpy.context.scene:
        return

    current_scene_name = bpy.context.scene.name
    is_load_post = dummy is None
    
    if is_load_post or current_scene_name != _last_scene_name:
        _last_scene_name = current_scene_name
        
        if getattr(bpy.context.screen, 'is_animation_playing', False):
            return

        shot_list = bpy.context.scene.brender_shot_list
        shot_list.clear()
        found_shots = get_all_shots(bpy.context)
        for marker in found_shots:
            item = shot_list.add()
            item.name = marker.name
            name_match = re.match(r"CAM-(SC\d+-SH\d+)", marker.name, re.IGNORECASE)
            if name_match:
                item.display_name = name_match.group(1)
            else:
                item.display_name = marker.name 
            item.frame = marker.frame
        
        log.info(f"Auto-refreshed shot list for scene: {current_scene_name}")

# --- UI PANEL ---
class VIEW3D_PT_brender_panel(bpy.types.Panel):
    bl_label = "bRender"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "bRender"
    bl_order = 2 

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        box = layout.box()

        # --- NEW: Phase Toggle for Production ---
        if _is_production(context):
            row = box.row(align=True)
            row.prop(scene, "brender_render_phase", expand=True)
            box.separator()

        row = box.row(align=True)
        row.label(text="Batch Shot Preparation", icon="FILE_TICK")
        row.operator(BRENDER_OT_refresh_shot_list.bl_idname, text="", icon="FILE_REFRESH")
        
        layout.separator()
        
        row = box.row(align=True)
        row.alignment = 'RIGHT'
        op_sel = row.operator(BRENDER_OT_select_all_shots.bl_idname, text="", icon="CHECKBOX_HLT")
        op_sel.action = 'SELECT'
        op_desel = row.operator(BRENDER_OT_select_all_shots.bl_idname, text="", icon="CHECKBOX_DEHLT")
        op_desel.action = 'DESELECT'

        shot_list = context.scene.brender_shot_list
        if shot_list:
            display_height = min(len(shot_list), 10)
            display_height = max(3, display_height) 
            box.template_list(
                "BRENDER_UL_shot_list", "", 
                scene, "brender_shot_list", 
                scene, "brender_active_shot_index", 
                rows=display_height
            )
            
            box.separator()
            
            row = box.row()
            row.scale_y = 2.0  # This makes the button double the standard height
            row.operator(
                BRENDER_OT_prepare_render_batch.bl_idname, 
                icon="EXPORT", 
                text="Send to render"
            )

        else:
            box.label(text="No shots found. Refresh or check markers.", icon="INFO")

# --- DEBUG OPERATORS & PANEL ---
class BRENDER_OT_debug_test_upload(bpy.types.Operator):
    bl_idname = "brender.debug_test_upload"
    bl_label = "Test Google Sheets Connection"
    bl_description = "Sends a dummy payload to check Google Sheets connectivity"

    def execute(self, context):
        log.info("--- Testing Google Sheets Connection ---")
        upload_shot_data(
            context, 
            shot_name="TEST_SHOT", 
            filename="DEBUG_CONNECTION_TEST", 
            version_str="v999"
        )
        self.report({'INFO'}, "Test payload sent. Check Blender Console & Google Sheet.")
        return {'FINISHED'}

class BRENDER_OT_debug_set_shot(bpy.types.Operator):
    bl_idname = "brender.debug_set_shot"
    bl_label = "Set Checked Shot for Debugging"
    bl_description = "Sets the shot checked in the list as the target for debug operations"

    def execute(self, context):
        scene = context.scene
        shot_list = scene.brender_shot_list
        selected_shots = [item for item in shot_list if item.is_selected]

        if len(selected_shots) != 1:
            scene.brender_debug_shot_name = ""
            scene.brender_debug_status_message = f"ERROR: {len(selected_shots)} shots checked. Need exactly one."
            self.report({"WARNING"}, "Please check exactly one shot for debugging.")
            return {"CANCELLED"}

        shot_item = selected_shots[0]
        scene.brender_debug_shot_name = shot_item.name
        scene.brender_debug_status_message = f"Ready to debug shot: {shot_item.display_name}"
        return {'FINISHED'}

class BRENDER_OT_debug_step_1_create_scene(bpy.types.Operator):
    bl_idname = "brender.debug_step_1_create_scene"
    bl_label = "1. Create/Clean 'render' Scene"

    def execute(self, context):
        scene = context.scene
        if scene.brender_output_format == 'EXR':
            scene.brender_debug_status_message = "Skipped (Step 1): 'render' scene bypassed in EXR mode."
            return {'FINISHED'}

        existing = bpy.data.scenes.get("render")
        if existing: bpy.data.scenes.remove(existing)

        original_active_scene = get_active_scene_safe(context)
        bpy.ops.scene.new(type='EMPTY')
        render_scene = get_active_scene_safe(context)
        render_scene.name = "render"
        
        render_scene.render.fps = 30
        render_scene.render.fps_base = 1.0

        set_active_scene_safe(context, original_active_scene)
        scene.brender_debug_status_message = "OK (Step 1): 'render' scene created & FPS set to 30."
        return {'FINISHED'}

class BRENDER_OT_debug_step_2_find_data(bpy.types.Operator):
    bl_idname = "brender.debug_step_2_find_data"
    bl_label = "2. Find Scenes & Jump to Frame"

    def execute(self, context):
        scene = context.scene
        shot_name = scene.brender_debug_shot_name
        if not shot_name: return {"CANCELLED"}

        shot_marker = scene.timeline_markers.get(shot_name)
        
        scene.frame_set(shot_marker.frame)
        context.view_layer.update()

        shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
        source_scene = context.scene
        scene_content_duration = _get_scene_content_duration(source_scene)

        msg = f"OK (Step 2): Jumped to f{shot_marker.frame}. Content={scene_content_duration}f."
        scene.brender_debug_status_message = msg
        return {'FINISHED'}

class BRENDER_OT_debug_step_3_bind_cameras(bpy.types.Operator):
    bl_idname = "brender.debug_step_3_bind_cameras"
    bl_label = "3. Bind FULLDOME Cameras"

    def execute(self, context):
        scene = context.scene
        source_scene = context.scene

        captured_res_x = source_scene.render.resolution_x
        captured_res_y = source_scene.render.resolution_y
        captured_res_pct = source_scene.render.resolution_percentage
        
        if hasattr(source_scene, 'shot_camera_toggle'):
            source_scene.shot_camera_toggle = 'FULLDOME'
            context.view_layer.update()

            source_scene.render.resolution_x = captured_res_x
            source_scene.render.resolution_y = captured_res_y
            source_scene.render.resolution_percentage = captured_res_pct

            scene.brender_debug_status_message = f"OK (Step 3): Set FULLDOME & Restored Res ({captured_res_x}x{captured_res_y})."
        else:
            scene.brender_debug_status_message = "ERROR: 'shot_camera_toggle' property not found."
            return {"CANCELLED"}
        return {'FINISHED'}

class BRENDER_OT_debug_step_4_add_strips(bpy.types.Operator):
    bl_idname = "brender.debug_step_4_add_strips"
    bl_label = "4. Add VSE Strips"
    bl_description = "Finds guide strips and adds all strips to the 'render' scene's VSE"

    def execute(self, context):
        log.info("--- DEBUG STEP 4: Add VSE Strips ---")
        scene = context.scene

        if scene.brender_output_format == 'EXR':
            scene.brender_debug_status_message = "Skipped (Step 4): VSE strips not needed in EXR mode."
            return {'FINISHED'}

        shot_name = scene.brender_debug_shot_name
        if not shot_name:
            self.report({"ERROR"}, "No debug shot selected.")
            return {"CANCELLED"}

        render_scene = bpy.data.scenes.get("render")
        if not render_scene:
            msg = "ERROR: 'render' scene not found. Run Step 1."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        shot_marker = scene.timeline_markers.get(shot_name)
        source_scene = context.scene 
        shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
        scene_content_duration = _get_scene_content_duration(source_scene)

        if not all([shot_marker, source_scene, shot_start_frame is not None, scene_content_duration > 0]):
            msg = "ERROR: Missing data. Run Step 2."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        vse_source = source_scene.sequence_editor
        if not vse_source:
            msg = f"ERROR: Source scene '{source_scene.name}' has no VSE."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        guide_video_strip, guide_audio_strip = None, None
        shot_name_prefix = shot_marker.name 

        scene_num_str, shot_num_str = "", ""
        name_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker.name, re.IGNORECASE)
        if name_match:
            scene_num_str = name_match.group(1).lower() 
            shot_num_str = name_match.group(2).lower() 

        all_strips = getattr(vse_source, 'sequences_all', vse_source.sequences)
        candidates = sorted([s for s in all_strips if not s.mute], key=lambda s: s.channel, reverse=True)

        for strip in candidates:
            if strip.name.startswith(shot_name_prefix):
                if strip.type == 'MOVIE' and not guide_video_strip:
                    guide_video_strip = strip
                if strip.type == 'SOUND' and not guide_audio_strip:
                    guide_audio_strip = strip
            if guide_video_strip and guide_audio_strip:
                break 

        if (not guide_video_strip or not guide_audio_strip) and scene_num_str and shot_num_str:
            for strip in candidates:
                strip_name_lower = strip.name.lower()
                if scene_num_str in strip_name_lower and shot_num_str in strip_name_lower:
                    if strip.type == 'MOVIE' and not guide_video_strip:
                        guide_video_strip = strip
                    if strip.type == 'SOUND' and not guide_audio_strip:
                        guide_audio_strip = strip
                if guide_video_strip and guide_audio_strip:
                    break 

        if not guide_video_strip or not guide_audio_strip:
            for strip in candidates:
                if strip.frame_start == shot_start_frame:
                    if strip.type == 'MOVIE' and not guide_video_strip:
                        guide_video_strip = strip
                    if strip.type == 'SOUND' and not guide_audio_strip:
                        guide_audio_strip = strip
                if guide_video_strip and guide_audio_strip:
                    break 

        if not render_scene.sequence_editor:
            render_scene.sequence_editor_create()
        vse_render = render_scene.sequence_editor
        for strip in list(vse_render.sequences):
            vse_render.sequences.remove(strip)

        if guide_audio_strip:
            new_audio = vse_render.sequences.new_sound(
                name=f"{shot_name}-guide_audio",
                filepath=bpy.path.abspath(guide_audio_strip.sound.filepath),
                channel=1, frame_start=shot_start_frame)
            new_audio.frame_final_duration = shot_duration
            new_audio.frame_offset_start = 0
            new_audio.volume = 0.8
        
        shot_scene_strip = vse_render.sequences.new_scene(
            name=shot_name, scene=source_scene,
            channel=2, frame_start=shot_start_frame)
        shot_scene_strip.frame_final_duration = scene_content_duration
        shot_scene_strip.scene_input = 'CAMERA'
        shot_scene_strip.animation_offset_start = 1 - source_scene.frame_start

        if guide_video_strip:
            new_video = vse_render.sequences.new_movie(
                name=f"{shot_name}-guide_video",
                filepath=bpy.path.abspath(guide_video_strip.filepath),
                channel=3, frame_start=shot_start_frame)
            new_video.frame_final_duration = shot_duration
            new_video.frame_offset_start = 0
            new_video.blend_type = 'ALPHA_OVER'
            new_video.blend_alpha = 1
            if hasattr(new_video, 'sound') and new_video.sound: new_video.sound.volume = 0
        
            baseline_res = 2048.0
            captured_res_x = source_scene.render.resolution_x
            captured_res_y = source_scene.render.resolution_y
            
            mult_x = max(captured_res_x, 1) / baseline_res
            mult_y = max(captured_res_y, 1) / baseline_res
            
            new_video.transform.scale_x = 3 * mult_x
            new_video.transform.scale_y = 3 * mult_x
            new_video.transform.offset_x = 410 * mult_x
            new_video.transform.offset_y = 1708 * mult_y

            new_video.crop.max_x = 860
            new_video.crop.max_y = 498
            
            mod = new_video.modifiers.new(name="GreenMask", type='COLOR_BALANCE')
            mod.color_balance.lift = [0, 1, 0]
            mod.color_balance.gamma = [0, 1, 0]
            mod.color_balance.gain = [0, 1, 0]
            
        added_count = 1 
        missing_strips = []
        if guide_audio_strip: added_count += 1
        else: missing_strips.append("Audio")

        if guide_video_strip: added_count += 1
        else: missing_strips.append("Video")

        if not missing_strips:
            msg = f"OK (Step 4): Added all {added_count} strips."
        else:
            missing_str = " & ".join(missing_strips)
            msg = f"WARNING (Step 4): Added Scene strip, but MISSING guide {missing_str}."

        scene.brender_debug_status_message = msg
        return {'FINISHED'}

class BRENDER_OT_debug_step_5_set_scene_settings(bpy.types.Operator):
    bl_idname = "brender.debug_step_5_set_scene_settings"
    bl_label = "5. Set Settings (Optimizations, Output)"

    def execute(self, context):
        scene = context.scene
        source_scene = context.scene
        
        target_scene = source_scene if scene.brender_output_format == 'EXR' else bpy.data.scenes.get("render")
        if not target_scene: 
            return {"CANCELLED"}

        apply_brender_optimizations(target_scene, scene.render.use_simplify)
        
        # if scene.brender_output_format == 'EXR':
        #     target_scene.render.use_sequencer = False
        #     target_scene.render.image_settings.file_format = 'OPEN_EXR'
        #     target_scene.render.image_settings.color_mode = 'RGBA'
        #     target_scene.render.image_settings.color_depth = '16'
        #     target_scene.render.image_settings.exr_codec = 'DWAB'
        #     target_scene.render.image_settings.quality = 50
        #     scene.brender_debug_status_message = "OK (Step 5): EXR DWAB/16/50% set. User settings retained."
        #     return {'FINISHED'}

        target_scene.render.engine = 'CYCLES'
        if hasattr(target_scene, 'cycles'):
            target_scene.cycles.samples = 1
            target_scene.cycles.use_denoising = False
            target_scene.cycles.time_limit = 0.01

        target_scene.render.resolution_x = source_scene.render.resolution_x
        target_scene.render.resolution_y = source_scene.render.resolution_y
        target_scene.render.resolution_percentage = source_scene.render.resolution_percentage
        
        is_prod = _is_production(context)
        target_scene.render.use_sequencer = True
        target_scene.render.image_settings.file_format = 'FFMPEG'
        
        if is_prod:
            target_scene.render.ffmpeg.format = 'MPEG4'
            target_scene.render.ffmpeg.codec = 'H264'
            target_scene.render.ffmpeg.constant_rate_factor = 'HIGH'
            target_scene.render.ffmpeg.ffmpeg_preset = 'GOOD'
            target_scene.render.ffmpeg.gopsize = 12
            target_scene.render.ffmpeg.max_b_frames = 0
            scene.brender_debug_status_message = "OK (Step 5): Cycles/10ms/MP4 High Quality set."
        else:
            target_scene.render.ffmpeg.format = 'QUICKTIME'
            target_scene.render.ffmpeg.codec = 'PRORES'
            scene.brender_debug_status_message = "OK (Step 5): Cycles/10ms/ProRes set."
        return {'FINISHED'}

class BRENDER_OT_debug_step_6_set_render_path(bpy.types.Operator):
    bl_idname = "brender.debug_step_6_set_render_path"
    bl_label = "6. Set Render Output Path"
    bl_description = "Parses names and sets the final render.filepath"

    def execute(self, context):
        log.info("--- DEBUG STEP 6: Set Render Output Path ---")
        scene = context.scene
        shot_name = scene.brender_debug_shot_name
        if not shot_name:
            self.report({"ERROR"}, "No debug shot selected.")
            return {"CANCELLED"}

        output_format = scene.brender_output_format
        target_scene = bpy.data.scenes.get("render") if output_format == 'VIDEO' else context.scene
        
        if not target_scene:
            msg = "ERROR: Target scene not found. Run Step 1."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        shot_marker = scene.timeline_markers.get(shot_name)
        source_scene = context.scene 

        name_components = _parse_name_components(context, shot_marker.name, source_scene.name)
        if not name_components:
            msg = "ERROR: Failed to parse name components."
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        new_save_path, new_blend_filename_no_ext, version_str_out = _get_new_brender_filepath_parts(context, name_components)
        if not new_save_path:
            msg = "ERROR: Failed to generate new file path."
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        try:
            prefs = get_prefs(context)
            base_path = bpy.path.abspath(prefs.output_base)

            scene_num = name_components["scene_number"].upper()
            env_name = name_components["env_name"].upper() 
            shot_num = name_components["shot_number"].upper()

            target_scene_folder = f"{scene_num}-{env_name}"

            if output_format == 'VIDEO':
                found_scene_folder = target_scene_folder
                if os.path.exists(base_path):
                    for d in os.listdir(base_path):
                        if d.upper() == target_scene_folder:
                            found_scene_folder = d
                            break

                scene_dir_path = os.path.join(base_path, found_scene_folder)
                output_dir = os.path.join(scene_dir_path, shot_num)

                is_prod = _is_production(context)
                ext = ".mp4" if is_prod else ".mov"
                final_filename = new_blend_filename_no_ext.lower() + ext
                render_filepath = os.path.join(output_dir, final_filename)
                
            # elif output_format == 'EXR':
            #     exr_root = r"R:\3212"
            #     ver_folder = f"{scene_num}-{shot_num}-{version_str_out}_R"
            #     exr_dir = os.path.join(exr_root, target_scene_folder, shot_num, ver_folder, "EXR")
            #     os.makedirs(exr_dir, exist_ok=True)
            #     exr_filename = f"{ver_folder}-######.exr"
            #     render_filepath = os.path.join(exr_dir, exr_filename)

            target_scene.render.filepath = render_filepath
            target_scene.render.use_file_extension = False
            
            msg = f"OK (Step 6): Set render path to: {render_filepath}"
            log.info(msg)
            scene.brender_debug_status_message = msg
        except Exception as e:
            msg = f"ERROR: Setting render path: {e}"
            log.error(msg)
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        log.info("--- DEBUG STEP 6: Complete ---")
        return {'FINISHED'}

class BRENDER_OT_debug_step_7_move_strip(bpy.types.Operator):
    bl_idname = "brender.debug_step_7_move_strip"
    bl_label = "7. Move Scene Strip to Frame 1"
    bl_description = "Moves the main scene strip (channel 2) to start at frame 1"
    
    def execute(self, context):
        scene = context.scene
        if scene.brender_output_format == 'EXR':
            scene.brender_debug_status_message = "Skipped (Step 7): No VSE strips to move in EXR mode."
            return {'FINISHED'}

        log.info("--- DEBUG STEP 7: Move Scene Strip to Frame 1 ---")
        shot_name = scene.brender_debug_shot_name
        if not shot_name:
            self.report({"ERROR"}, "No debug shot selected.")
            return {"CANCELLED"}

        render_scene = bpy.data.scenes.get("render")
        if not render_scene or not render_scene.sequence_editor:
            msg = "ERROR: 'render' scene VSE not found. Run Step 4."
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        shot_scene_strip = next((s for s in render_scene.sequence_editor.sequences if s.name == shot_name and s.type == 'SCENE'), None)

        if not shot_scene_strip:
            msg = f"ERROR: Scene strip '{shot_name}' not found in VSE."
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        try:
            shot_scene_strip.frame_start = 1
            msg = "OK (Step 7): Moved scene strip to frame 1."
            scene.brender_debug_status_message = msg
        except Exception as e:
            msg = f"ERROR: Could not move scene strip: {e}"
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        return {'FINISHED'}

class BRENDER_OT_debug_step_8_set_active(bpy.types.Operator):
    bl_idname = "brender.debug_step_8_set_active"
    bl_label = "8. Set 'render' Scene Active"

    def execute(self, context):
        if context.scene.brender_output_format == 'EXR':
            context.scene.brender_debug_status_message = "Skipped (Step 8): Targeting original scene in EXR mode."
            return {'FINISHED'}

        render_scene = bpy.data.scenes.get("render")
        if render_scene:
            set_active_scene_safe(context, render_scene)
        return {'FINISHED'}

class VIEW3D_PT_brender_debug_panel(bpy.types.Panel):
    bl_label = "bRender Debug"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "bRender"
    bl_order = 3 
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        box = layout.box()
        box.label(text="Single Shot Debugger", icon="CONSOLE")
        col = box.column()
        col.operator(BRENDER_OT_debug_set_shot.bl_idname, icon="RESTRICT_SELECT_OFF")
        col.separator()
        col.label(text=f"Debugging: {scene.brender_debug_shot_name or 'None'}")
        col.label(text=f"Status: {scene.brender_debug_status_message}", icon="INFO")
        col.separator()

        grid = col.grid_flow(columns=1, align=True)
        grid.operator(BRENDER_OT_debug_step_1_create_scene.bl_idname)
        grid.operator(BRENDER_OT_debug_step_2_find_data.bl_idname)
        grid.operator(BRENDER_OT_debug_step_3_bind_cameras.bl_idname)
        grid.operator(BRENDER_OT_debug_step_4_add_strips.bl_idname)
        grid.operator(BRENDER_OT_debug_step_5_set_scene_settings.bl_idname)
        grid.operator(BRENDER_OT_debug_step_6_set_render_path.bl_idname)
        grid.operator(BRENDER_OT_debug_step_7_move_strip.bl_idname)
        grid.operator(BRENDER_OT_debug_step_8_set_active.bl_idname)

        col.separator()
        col.label(text="Network / Sheets", icon="URL")
        col.operator(BRENDER_OT_debug_test_upload.bl_idname, text="Test Connection", icon="FILE_REFRESH")
        
        col.separator()
        row = col.row(align=True)
        row.operator("wm.save_as_mainfile", text="Save Copy...", icon="FILE_TICK")
        row.operator("wm.save_mainfile", text="Save This File", icon="FILE_TICK")

# --- REGISTRATION ---
classes = (
    BRENDER_ShotListItem,
    BRENDER_UL_shot_list,
    BRENDER_AddonPreferences, 
    BRENDER_OT_select_all_shots, 
    BRENDER_OT_prepare_active_shot,
    BRENDER_OT_prepare_this_file,
    BRENDER_OT_refresh_shot_list,
    BRENDER_OT_prepare_render_batch,
    VIEW3D_PT_brender_panel,
    BRENDER_OT_debug_set_shot,
    BRENDER_OT_debug_step_1_create_scene,
    BRENDER_OT_debug_step_2_find_data,
    BRENDER_OT_debug_step_3_bind_cameras,
    BRENDER_OT_debug_step_4_add_strips,
    BRENDER_OT_debug_step_5_set_scene_settings,
    BRENDER_OT_debug_step_6_set_render_path,
    BRENDER_OT_debug_step_7_move_strip,
    BRENDER_OT_debug_step_8_set_active,
    BRENDER_OT_debug_test_upload,
    VIEW3D_PT_brender_debug_panel,
)

# --- PHASE PROPERTY HELPERS ---
def get_render_phase_items(self, context):
    """
    Detects the work line (ANI or ART) from the file path and returns appropriate phases.
    Matches the logic used in the Publisher addon for consistency.
    """
    filepath = bpy.data.filepath.lower()
    
    if "-art-" in filepath or "art-work" in filepath:
        return [
            ('setdress', 'Setdress', 'Art: Setdress phase'),
            ('lighting', 'Lighting', 'Art: Lighting phase'),
        ]
    else:
        # Default to ANI
        return [
            ('blocking', 'Blocking', 'Ani: Animation Blocking phase'),
            ('fincam', 'FinCam', 'Ani: Final Camera phase'),
        ]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.brender_shot_list = bpy.props.CollectionProperty(type=BRENDER_ShotListItem)
    bpy.types.Scene.brender_active_shot_index = bpy.props.IntProperty()

    # --- RETAINED FARM CONFIG PROPERTIES (HIDDEN IN UI BUT NEEDED FOR BACKEND) ---
    bpy.types.Scene.brender_deadline_pool = bpy.props.StringProperty(
        name="Pool", default="renderstations", description="Main Deadline Pool")
    
    bpy.types.Scene.brender_deadline_secondary_pool = bpy.props.StringProperty(
        name="Secondary Pool", default="workstations", description="Secondary Deadline Pool")

    bpy.types.Scene.brender_deadline_group = bpy.props.StringProperty(
        name="Group", default="krutart_renderfarm", description="Deadline Group")
    
    bpy.types.Scene.brender_deadline_priority = bpy.props.IntProperty(
        name="Priority", default=52, min=0, max=100, description="Job Priority")

    bpy.types.Scene.brender_output_format = bpy.props.EnumProperty(
        items=[
            ('VIDEO', "Video (MP4/ProRes)", "Context-aware Video Render (MP4 for Prod, ProRes for Preprod)"),
            # ('EXR', "EXR Sequence", "EXR sequence render"),
        ],
        name="Output Format",
        description="Format for the rendered output",
        default='VIDEO'
    )

    bpy.types.Scene.brender_render_phase = bpy.props.EnumProperty(
        name="Phase",
        items=get_render_phase_items,
        description="Select the production phase for this render",
    )

    bpy.types.Scene.brender_debug_shot_name = bpy.props.StringProperty()
    bpy.types.Scene.brender_debug_status_message = bpy.props.StringProperty()
    
    bpy.app.handlers.load_post.append(auto_refresh_shot_list)
    bpy.app.handlers.depsgraph_update_post.append(auto_refresh_shot_list)
    
    if debug_path_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(debug_path_on_load)
        
    log.info("bRender addon registered successfully.")

def unregister():
    if auto_refresh_shot_list in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(auto_refresh_shot_list)
    if auto_refresh_shot_list in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(auto_refresh_shot_list)

    if debug_path_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(debug_path_on_load)

    del bpy.types.Scene.brender_deadline_pool
    del bpy.types.Scene.brender_deadline_secondary_pool
    del bpy.types.Scene.brender_deadline_group
    del bpy.types.Scene.brender_deadline_priority
    del bpy.types.Scene.brender_output_format
    del bpy.types.Scene.brender_render_phase

    del bpy.types.Scene.brender_debug_shot_name
    del bpy.types.Scene.brender_debug_status_message
    del bpy.types.Scene.brender_active_shot_index
    del bpy.types.Scene.brender_shot_list

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    log.info("bRender addon unregistered.")

if __name__ == "__main__":
    register()