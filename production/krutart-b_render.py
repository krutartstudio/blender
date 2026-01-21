bl_info = {
    "name": "Krutart bRender + Deadline + Sheets",
    "author": "iori, Krutart, Gemini",
    "version": (4, 3, 3),
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > bRender",
    "description": "Prepares render files, submits to Deadline, and logs to Google Sheets (Robust).",
    "warning": "CYCLES/10ms limit/Output: ProRes. Deadline requires deadlinecommand.",
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

# --- SETUP LOGGER ---
log = logging.getLogger("bRender")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[bRender] %(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
log.setLevel(logging.INFO)

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

# --- GOOGLE SHEETS HELPER (ROBUST) ---

def _send_payload_thread(url, payload):
    """
    Worker function to send data to Google Sheets.
    Uses standard library urllib for zero-dependency compatibility.
    """
    import json
    import urllib.request
    import urllib.error
    import time

    max_retries = 3
    base_delay = 2 

    data = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Blender-bRender-Client'
    }

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method='POST')
            log.info(f"Uploading to Sheets (Attempt {attempt}/{max_retries})...")
            
            # urllib.request.urlopen handles the Google Apps Script 302 redirect automatically
            with urllib.request.urlopen(req, timeout=10) as response:
                result = response.read().decode('utf-8')
                log.info(f"Google Sheet Response: {result}")
                return # Success! Exit function.
                
        except urllib.error.HTTPError as e:
            log.error(f"HTTP Error {e.code}: {e.reason}")
        except Exception as e:
            log.error(f"Unexpected error during Sheets upload: {e}")
        
        if attempt < max_retries:
            log.info(f"Retrying upload in {base_delay} seconds...")
            time.sleep(base_delay)
        else:
            log.error("Max retries reached. Upload to Google Sheets failed.")

def upload_shot_data(context, shot_name, filename, version_int):
    """
    Prepares data (Filename, Version, User) and starts the upload thread.
    """
    prefs = get_prefs(context)
    if not prefs or not prefs.google_webapp_url:
        log.warning("Google WebApp URL not set in preferences. Skipping upload.")
        return

    url = prefs.google_webapp_url.strip()
    if not url.startswith("http"):
        log.warning("Invalid Google WebApp URL.")
        return

    # --- 1. DETERMINE USER IDENTITY ---
    # Try to fetch from Configurator Addon first, then fallback to Hostname Map, then System User.
    user_name = "unknown"
    
    # Map copied from Configurator for robustness
    HOSTNAME_MAP = {
        "alfa": "lukas",
        "osma": "Ondra",
        "inzenyr": "Jachym",
        "kit": "Vara",
        "simca": "Jakub",
        "myska": "Lisa",
        "schmitt": "Boza",
        "zedix": "zedix",
    }

    try:
        # Attempt A: Get explicit override from Configurator preferences
        cfg_prefs = context.preferences.addons.get('krutart-configurator').preferences
        if cfg_prefs and cfg_prefs.user_name_override:
            user_name = cfg_prefs.user_name_override
    except Exception:
        pass

    # Attempt B: If still unknown, use Hostname Map
    if user_name == "unknown":
        try:
            import socket
            hostname = socket.gethostname().lower()
            user_name = HOSTNAME_MAP.get(hostname, hostname) # Defaults to hostname if not in map
        except:
            # Attempt C: System Login Name
            import getpass
            user_name = getpass.getuser()

    # --- 2. PREPARE PAYLOAD ---
    # Strip extension (e.g., .blend)
    filename_no_ext = os.path.splitext(filename)[0]

    payload = {
        "filename": filename_no_ext,
        "version": f"v{version_int:03d}",
        "user": user_name
    }

    # --- 3. SEND ---
    # Start non-blocking thread
    t = threading.Thread(target=_send_payload_thread, args=(url, payload))
    t.start()


# --- CORE LOGIC ---

def _parse_name_components(context, shot_marker_name, source_scene_name):
    """
    Parses all required name components from the shot and scene.
    Returns a dictionary of components or None if parsing fails.
    """
    log.info("Parsing name components...")

    # 1. From shot marker
    shot_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker_name, re.IGNORECASE)
    if not shot_match:
        log.error(f"Could not parse shot marker name: {shot_marker_name}")
        return None

    scene_number = shot_match.group(1)
    shot_number = shot_match.group(2)

    # 2. From source scene
    env_match = re.search(r"sc\d+[-_](.+)", source_scene_name, re.IGNORECASE)
    env_name = env_match.group(1) if env_match else "env"

    # 3. From Global Preferences
    prefs = get_prefs(context)
    if not prefs:
        log.error("Could not access Addon Preferences.")
        return None
        
    project_code = prefs.project_code
    task = prefs.task

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
    """
    Utility to get shot start, end, and duration.
    """
    shot_markers = sorted(
        [m for m in context.scene.timeline_markers if re.match(r"CAM-SC\d+-SH\d+", m.name, re.IGNORECASE)],
        key=lambda m: m.frame
    )

    shot_start_frame = shot_marker.frame
    shot_end_frame = context.scene.frame_end + 1 # Default for the last shot

    try:
        current_marker_index = shot_markers.index(shot_marker)
        if current_marker_index < len(shot_markers) - 1:
            next_marker = shot_markers[current_marker_index + 1]
            shot_end_frame = next_marker.frame
    except ValueError:
        log.warning(f"Could not find shot marker '{shot_marker.name}' in the sorted list. This may happen if it's the last shot.")
        # Fallback for the very last shot to ensure it goes to the end of the scene
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


def _prepare_shot_in_current_file(context, shot_marker):
    """
    Prepares the 'render' scene for a given shot marker.
    """
    log.info(f"--- Starting preparation for shot: {shot_marker.name} ---")

    original_active_scene = context.window.scene
    # --- NEW: Store original frame and move playhead to shot start ---
    original_frame = original_active_scene.frame_current
    log.info(f"Jumping to frame {shot_marker.frame} to sync collection data.")
    original_active_scene.frame_set(shot_marker.frame)
    context.view_layer.update()

    # Delete any pre-existing 'render' scene
    existing_render_scene = bpy.data.scenes.get("render")
    if existing_render_scene:
        log.warning("Found existing 'render' scene. Removing it.")
        try:
            bpy.data.scenes.remove(existing_render_scene)
        except Exception as e:
            log.error(f"Could not remove existing 'render' scene: {e}. Aborting.")
            # Restore state on failure
            original_active_scene.frame_set(original_frame)
            context.window.scene = original_active_scene
            return (False, None, None)

    # Create a full copy of the currently active scene
    log.info(f"Creating a empty of the active scene '{original_active_scene.name}'.")
    bpy.ops.scene.new(type='EMPTY')
    render_scene = context.window.scene 
    render_scene.name = "render"
    
    # --- Force 30 FPS (Standard) ---
    render_scene.render.fps = 30
    render_scene.render.fps_base = 1.0
    
    context.window.scene = original_active_scene

    shot_name = shot_marker.name

    # --- 1. Get shot timing info ---
    shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
    if shot_start_frame is None:
        original_active_scene.frame_set(original_frame)
        return (False, None, None)

    # --- 2. Find the source scene from the marker name ---
    source_scene = original_active_scene

    if not source_scene or not source_scene.sequence_editor:
        log.error(f"Source scene '{source_scene.name}' has no VSE. Aborting.")
        original_active_scene.frame_set(original_frame)
        return (False, None, None)

    # --- START SCENE STRIP FIX ---
    scene_content_duration = _get_scene_content_duration(source_scene)
    if scene_content_duration <= 0:
        original_active_scene.frame_set(original_frame)
        return (False, None, None)

    # --- 2.1. Bind FULLDOME cameras in the source scene & CAPTURE RESOLUTION ---
    captured_res_x = 1920
    captured_res_y = 1080
    captured_res_pct = 100

    try:
        context.window.scene = source_scene
        if hasattr(source_scene, 'shot_camera_toggle'):
            source_scene.shot_camera_toggle = 'FULLDOME'
            context.view_layer.update()
            
            captured_res_x = source_scene.render.resolution_x
            captured_res_y = source_scene.render.resolution_y
            captured_res_pct = source_scene.render.resolution_percentage
            log.info(f"Captured Source Resolution: {captured_res_x}x{captured_res_y} @ {captured_res_pct}%")
        else:
            log.error("Cannot find 'shot_camera_toggle' property.")
            raise Exception("shot_camera_toggle property not found")
    except Exception as e:
        log.error(f"Failed to bind FULLDOME cameras: {e}")
        context.window.scene = original_active_scene
        original_active_scene.frame_set(original_frame)
        return(False, None, None)
    finally:
        context.window.scene = original_active_scene
    
    if render_scene:
        render_scene.render.resolution_x = captured_res_x
        render_scene.render.resolution_y = captured_res_y
        render_scene.render.resolution_percentage = captured_res_pct

    # --- 3. Find the guide strips in the source scene's VSE ---
    vse_source = source_scene.sequence_editor
    guide_video_strip, guide_audio_strip = None, None
    shot_name_prefix = shot_marker.name

    scene_num_str, shot_num_str = "", ""
    name_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker.name, re.IGNORECASE)
    if name_match:
        scene_num_str = name_match.group(1).lower()
        shot_num_str = name_match.group(2).lower()

    candidates = sorted([s for s in vse_source.sequences_all if not s.mute], key=lambda s: s.channel, reverse=True)

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

        new_video.transform.scale_x = 3
        new_video.transform.scale_y = 3
        new_video.transform.offset_x = 410
        new_video.transform.offset_y = 1708

        new_video.crop.max_x = 860
        new_video.crop.max_y = 498
        
        mod = new_video.modifiers.new(name="GreenMask", type='COLOR_BALANCE')
        mod.color_balance.lift = [0, 1, 0]
        mod.color_balance.gamma = [0, 1, 0]
        mod.color_balance.gain = [0, 1, 0]

    fulldome_camera_name = f"{shot_name}-FULLDOME"
    fulldome_camera = bpy.data.objects.get(fulldome_camera_name)

    if fulldome_camera and fulldome_camera.type == 'CAMERA':
        render_scene.camera = fulldome_camera
    else:
        log.warning(f"Could not find FULLDOME camera named '{fulldome_camera_name}'.")

    log.info("Setting CYCLES: 1 sample, No Denoise, 10ms Time Limit.")
    render_scene.render.engine = 'CYCLES'
    
    if hasattr(render_scene, 'cycles'):
        render_scene.cycles.samples = 1
        render_scene.cycles.use_denoising = False
        render_scene.cycles.transparent_max_bounces = 1
        render_scene.cycles.time_limit = 0.01 
    else:
        render_scene.render.samples = 1

    render_scene.frame_start = shot_start_frame
    render_scene.frame_end = shot_end_frame - 1
    render_scene.render.film_transparent = True

    log.info("Setting Output Format to FFMPEG / QUICKTIME / PRORES.")
    render_scene.render.image_settings.file_format = 'FFMPEG'
    render_scene.render.ffmpeg.format = 'QUICKTIME'
    render_scene.render.ffmpeg.codec = 'PRORES' 

    name_components = _parse_name_components(context, shot_marker.name, source_scene.name)
    if not name_components:
        log.error("Failed to parse name components for render path.")
        original_active_scene.frame_set(original_frame)
        context.window.scene = original_active_scene
        return (False, None, None)

    new_save_path, new_blend_filename_no_ext, version_int = _get_new_brender_filepath_parts(context, name_components)

    if not new_save_path:
        log.error(f"Failed to generate a new file path for {shot_marker.name}.")
        original_active_scene.frame_set(original_frame)
        context.window.scene = original_active_scene
        return (False, None, None)

    name_components['new_save_path'] = new_save_path
    name_components['version_int'] = version_int 

    try:
        prefs = get_prefs(context)
        base_path = bpy.path.abspath(prefs.output_base)
        target_sc_prefix = name_components['scene_number'].upper() 
        
        found_scene_dir = None
        if os.path.exists(base_path):
            try:
                for d in os.listdir(base_path):
                    full_d = os.path.join(base_path, d)
                    if os.path.isdir(full_d):
                        if d.upper().startswith(target_sc_prefix):
                            found_scene_dir = d
                            break
            except Exception as scan_e:
                log.warning(f"Failed to scan output directory: {scan_e}")
        
        scene_dir_path = ""
        if found_scene_dir:
            scene_dir_path = os.path.join(base_path, found_scene_dir)
        else:
            env_upper = name_components['env_name'].upper()
            new_scene_dirname = f"{target_sc_prefix}-{env_upper}"
            scene_dir_path = os.path.join(base_path, new_scene_dirname)

        shot_dir_name = new_blend_filename_no_ext
        output_dir = os.path.join(scene_dir_path, shot_dir_name)
        
        final_filename_mov = new_blend_filename_no_ext.lower() + ".mov"
        render_filepath = os.path.join(output_dir, final_filename_mov)

        render_scene.render.filepath = render_filepath
        render_scene.render.use_file_extension = False 

        log.info(f"Set render output path to (Single File Mode): {render_filepath}")

    except Exception as e:
        log.error(f"Error setting render output path: {e}")

    try:
        shot_scene_strip.frame_start = 1
    except Exception as e:
        log.error(f"Could not move scene strip to frame 1: {e}")

    # --- Restore original state and set final scene ---
    original_active_scene.frame_set(original_frame)
    context.window.scene = render_scene

    log.info(f"--- Successfully prepared shot: {shot_marker.name} ---")
    return (True, source_scene, name_components)

# --- UTILITY FUNCTIONS ---

def _get_new_brender_filepath_parts(context, name_components):
    """
    Calculates the directory, version, and final path for a new bRender file.
    UPDATED: Returns (new_filepath, filename_no_ext, version_int)
    """
    if not bpy.data.is_saved:
        log.error("Source file is not saved. Cannot determine output path.")
        return None, None, None

    if not name_components:
        return None, None, None

    try:
        base_dir = os.path.dirname(bpy.data.filepath)      
        parent_dir_path = os.path.dirname(base_dir)       
        grandparent_dir_name = os.path.basename(parent_dir_path)
        brender_dir_name = f"{grandparent_dir_name}-BRENDER" 
        brender_dir = os.path.join(parent_dir_path, brender_dir_name) 

        os.makedirs(brender_dir, exist_ok=True)
    except Exception as e:
        log.error(f"Error creating BRENDER directory: {e}")
        return None, None, None

    project_code = name_components['project_code']
    scene_number = name_components['scene_number']
    shot_number = name_components['shot_number']
    env_name = name_components['env_name']
    task = name_components['task']

    version = 1
    filename_prefix = f"{project_code}-{scene_number}-{env_name}-{shot_number}-{task}-v"

    try:
        prefix_lower = filename_prefix.lower()
        existing_files = [f for f in os.listdir(brender_dir) if f.lower().startswith(prefix_lower) and f.lower().endswith('.blend')]
        if existing_files:
            max_version = 0
            for f in existing_files:
                version_match = re.search(r"-v(\d+)\.blend$", f, re.IGNORECASE)
                if version_match:
                    max_version = max(max_version, int(version_match.group(1)))
            version = max_version + 1
    except Exception as e:
        log.error(f"Error checking for existing versions: {e}")

    filename_base_no_ext = f"{filename_prefix}{version:03d}"
    filename_base_no_ext_lower = filename_base_no_ext.lower()
    new_filename = f"{filename_base_no_ext_lower}.blend"

    new_filepath = os.path.join(brender_dir, new_filename)

    # Return version integer for use in Google Sheets logging
    return new_filepath, filename_base_no_ext_lower.upper(), version


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
    """
    Submits a specific blend file to Deadline.
    """
    if not os.path.exists(deadline_cmd):
        log.error(f"Deadline executable not found at: {deadline_cmd}")
        return False

    job_name = os.path.basename(filepath)
    
    prefs = get_prefs(context)
    batch_name = prefs.project_code if prefs else "bRender_Batch"
    
    major = bpy.app.version[0]
    minor = bpy.app.version[1]
    blender_version = f"{major}.{minor}"

    # --- SINGLE MACHINE LOGIC ---
    total_frames = (end_frame - start_frame) + 1
    chunk_size = total_frames + 5000 

    # --- RETRIEVE UI SETTINGS ---
    priority = context.scene.brender_deadline_priority
    pool = context.scene.brender_deadline_pool
    sec_pool = context.scene.brender_deadline_secondary_pool
    group = context.scene.brender_deadline_group

    # Job Info
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

    # Plugin Info
    plugin_info = [
        f"SceneFile={filepath}",
        f"Version={blender_version}",
        "Build=None",
        "Threads=0",
    ]

    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".job", encoding='utf-8') as j_file:
            j_file.write("\n".join(job_info))
            j_job_path = j_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".job", encoding='utf-8') as p_file:
            p_file.write("\n".join(plugin_info))
            p_plugin_path = p_file.name

        log.info(f"Submitting {job_name} to Deadline...")
        
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
            log.info(f"Deadline Submission Successful: {stdout.strip()}")
            return True
        else:
            log.error(f"Deadline Submission Failed: {stderr}")
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
    task: bpy.props.StringProperty(
        name="Task", default="layout_r")
    output_base: bpy.props.StringProperty(
        name="Render Output Base", 
        default="S:\\3212-EDIT\\SOURCE\\LAYOUT_RENDER\\", 
        subtype='DIR_PATH')
    
    # New Google Sheets Config
    google_webapp_url: bpy.props.StringProperty(
        name="Google WebApp URL",
        description="The Web App URL from your deployed Google Apps Script",
        default="https://script.google.com/macros/s/AKfycbxNBjD9rjBHgesVCxYpsH6J_m9qHt2ZL1n-ANGKxiuceOtF7pNV584ylJNOSTK55t5A/exec"
    )

    # Default Deadline Command Path
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
        box.prop(self, "task")
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

# --- RESTORED OPERATOR (Fixes NameError) ---
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
        original_scene = context.window.scene
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
            context.window.scene = original_scene
            return {"CANCELLED"}

        _purge_orphans()
        new_filepath = name_components.get('new_save_path')
        version_int = name_components.get('version_int', 1)

        if new_filepath:
            log.info(f"Saving prepared file to: {new_filepath}")
            bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=True)
            
            # --- GOOGLE SHEETS UPLOAD ---
            upload_shot_data(
                context, 
                shot_name=shot_marker.name, 
                filename=os.path.basename(new_filepath),
                version_int=version_int
            )
            # ---------------------------

            self.report({'INFO'}, f"Saved: {os.path.basename(new_filepath)}")
        else:
            self.report({"ERROR"}, f"Could not generate a valid filename for '{shot_marker.name}'.")
            context.window.scene = original_scene
            return {"CANCELLED"}

        context.window.scene = original_scene
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

        bpy.ops.wm.save_mainfile()
        log.info("Master file saved successfully before batch processing.")

        original_scene = context.window.scene
        # --- NEW: Store original playhead position ---
        original_frame = original_scene.frame_current
        
        prefs = get_prefs(context)
        deadline_cmd = prefs.deadline_path if prefs else ""

        selected_shots = [s for s in context.scene.brender_shot_list if s.is_selected]
        if not selected_shots:
            self.report({"WARNING"}, "No shots selected from the list.")
            return {"CANCELLED"}

        log.info(f"Starting batch preparation for {len(selected_shots)} shots.")
        processed_count = 0
        submitted_count = 0

        for shot_item in selected_shots:
            log.info(f"--- Preparing batch item: {shot_item.name} ---")

            shot_marker = context.scene.timeline_markers.get(shot_item.name)
            if not shot_marker:
                log.error(f"Marker '{shot_item.name}' not found. Skipping.")
                continue

            # This internal function now handles the frame_set and restoration
            success, source_scene, name_components = _prepare_shot_in_current_file(context, shot_marker)

            if not success:
                log.error(f"Preparation failed for '{shot_item.name}'. Skipping save.")
                context.window.scene = original_scene
                continue

            _purge_orphans()

            new_filepath = name_components.get('new_save_path')
            version_int = name_components.get('version_int', 1)

            if new_filepath:
                log.info(f"Saving prepared shot to: {new_filepath}")
                bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=True)
                
                upload_shot_data(
                    context, 
                    shot_name=shot_item.name, 
                    filename=os.path.basename(new_filepath),
                    version_int=version_int
                )

                render_scene = context.window.scene 
                start_frame = render_scene.frame_start
                end_frame = render_scene.frame_end
                output_path = render_scene.render.filepath

                submit_success = _submit_to_deadline(context, new_filepath, start_frame, end_frame, output_path, deadline_cmd)
                if submit_success:
                    submitted_count += 1

                shot_item.is_selected = False
                processed_count += 1
            else:
                log.error(f"Could not generate filename for '{shot_item.name}'. Skipping save.")

            context.window.scene = original_scene

        # --- Restoration ---
        context.window.scene = original_scene
        original_scene.frame_set(original_frame)
        
        temp_render = bpy.data.scenes.get("render")
        if temp_render:
            try:
                bpy.data.scenes.remove(temp_render)
                log.info("Cleaned up temporary 'render' scene.")
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
    
    # Safety: check if context is valid
    if not bpy.context or not bpy.context.scene:
        return

    current_scene_name = bpy.context.scene.name

    # Only run refresh if scene name has changed (swapped scene) or forced
    # Note: 'dummy' is None for load_post, but has value for depsgraph
    is_load_post = dummy is None
    
    if is_load_post or current_scene_name != _last_scene_name:
        _last_scene_name = current_scene_name
        
        # Don't refresh during rendering or animation playback to avoid stutter
        if bpy.context.screen.is_animation_playing:
            return

        # Trigger refresh logic directly (avoiding operator call to prevent Undo stack push)
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
    bl_order = 0 

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Global Settings Link
        box = layout.box()
        box.label(text="Global Settings", icon="PREFERENCES")
        row = box.row()
        row.label(text="Use Add-on Preferences to set Project Code & Output.")
        row.operator("screen.userpref_show", text="Open Preferences", icon="SETTINGS")

        layout.separator()

        box = layout.box()
        row = box.row(align=True)
        row.label(text="Batch Shot Preparation", icon="FILE_TICK")
        row.operator(BRENDER_OT_refresh_shot_list.bl_idname, text="", icon="FILE_REFRESH")
        
        # Select All / Deselect All
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
            
            # --- COLLAPSIBLE FARM SETTINGS ---
            box_farm = box.box()
            row = box_farm.row()
            row.prop(scene, "brender_show_farm_settings", 
                icon="TRIA_DOWN" if scene.brender_show_farm_settings else "TRIA_RIGHT", 
                icon_only=True, emboss=False
            )
            row.label(text="Farm Settings (Deadline)", icon="NETWORK_DRIVE")
            
            if scene.brender_show_farm_settings:
                d_col = box_farm.column(align=True)
                d_col.prop(scene, "brender_deadline_pool")
                d_col.prop(scene, "brender_deadline_secondary_pool")
                d_col.prop(scene, "brender_deadline_group")
                d_col.prop(scene, "brender_deadline_priority")
            # ---------------------------------

            row = box.row()
            row.operator(BRENDER_OT_prepare_render_batch.bl_idname, icon="EXPORT", text="Send to render")

        else:
            box.label(text="No shots found. Refresh or check markers.", icon="INFO")


# --- DEBUG OPERATORS & PANEL ---

class BRENDER_OT_debug_test_upload(bpy.types.Operator):
    bl_idname = "brender.debug_test_upload"
    bl_label = "Test Google Sheets Connection"
    bl_description = "Sends a dummy payload to check Google Sheets connectivity"

    def execute(self, context):
        log.info("--- Testing Google Sheets Connection ---")
        
        # Using dummy data (NO USER DATA)
        upload_shot_data(
            context, 
            shot_name="TEST_SHOT", 
            filename="DEBUG_CONNECTION_TEST", 
            version_int=999
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
        existing = bpy.data.scenes.get("render")
        if existing: bpy.data.scenes.remove(existing)

        original_active_scene = context.window.scene
        bpy.ops.scene.new(type='EMPTY')
        render_scene = context.window.scene
        render_scene.name = "render"
        
        # --- Force 30 FPS (Standard) ---
        render_scene.render.fps = 30
        render_scene.render.fps_base = 1.0

        context.window.scene = original_active_scene
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
        
        # --- NEW: Visual confirmation frame jump ---
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
        
        if hasattr(source_scene, 'shot_camera_toggle'):
            source_scene.shot_camera_toggle = 'FULLDOME'
            scene.brender_debug_status_message = f"OK (Step 3): Set 'shot_camera_toggle' to FULLDOME."
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
        source_scene = context.scene # Active scene
        shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
        scene_content_duration = _get_scene_content_duration(source_scene)

        if not all([shot_marker, source_scene, shot_start_frame is not None, scene_content_duration > 0]):
            msg = "ERROR: Missing data. Run Step 2."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        # 3. Find guide strips (ROBUST 3-STEP LOGIC)
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
            scene_num_str = name_match.group(1).lower() # "sc17"
            shot_num_str = name_match.group(2).lower() # "sh130"

        # --- LOGIC FIX: Filter candidates (Ignore Muted, Sort by Channel Desc) ---
        candidates = sorted([s for s in vse_source.sequences_all if not s.mute], key=lambda s: s.channel, reverse=True)

        log.info(f"Debug Attempt 1: Finding strips starting with name: '{shot_name_prefix}'...")
        for strip in candidates:
            if strip.name.startswith(shot_name_prefix):
                if strip.type == 'MOVIE' and not guide_video_strip:
                    guide_video_strip = strip
                if strip.type == 'SOUND' and not guide_audio_strip:
                    guide_audio_strip = strip
            if guide_video_strip and guide_audio_strip:
                break 

        if (not guide_video_strip or not guide_audio_strip) and scene_num_str and shot_num_str:
            log.warning(f"Debug Attempt 1 failed. Attempt 2: Finding strips containing '{scene_num_str}' AND '{shot_num_str}'...")
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
            log.warning(f"Debug Attempt 1 & 2 failed. Attempt 3 (Fallback): Falling back to frame search at frame {shot_start_frame}...")
            for strip in candidates:
                if strip.frame_start == shot_start_frame:
                    if strip.type == 'MOVIE' and not guide_video_strip:
                        guide_video_strip = strip
                    if strip.type == 'SOUND' and not guide_audio_strip:
                        guide_audio_strip = strip
                if guide_video_strip and guide_audio_strip:
                    break 

        if not guide_video_strip:
            log.warning(f"Debug: Could not find guide video strip for '{shot_name_prefix}' by name, substring, or frame.")
        if not guide_audio_strip:
            log.warning(f"Debug: Could not find guide audio strip for '{shot_name_prefix}' by name, substring, or frame.")

        # 4. Prepare 'render' VSE
        if not render_scene.sequence_editor:
            render_scene.sequence_editor_create()
        vse_render = render_scene.sequence_editor
        for strip in list(vse_render.sequences):
            vse_render.sequences.remove(strip)

        # 5. Add strips
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
       
            new_video.transform.scale_x = 3
            new_video.transform.scale_y = 3
            new_video.transform.offset_x = 410
            new_video.transform.offset_y = 1708
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
            msg = f"OK (Step 4): Added all {added_count} strips (Scene, Audio, Video w/ Green Mask)."
        else:
            missing_str = " & ".join(missing_strips)
            msg = f"WARNING (Step 4): Added Scene strip, but MISSING guide {missing_str}."

        scene.brender_debug_status_message = msg
        return {'FINISHED'}

class BRENDER_OT_debug_step_5_set_scene_settings(bpy.types.Operator):
    bl_idname = "brender.debug_step_5_set_scene_settings"
    bl_label = "5. Set Settings (Cycles, Time, ProRes)"

    def execute(self, context):
        scene = context.scene
        render_scene = bpy.data.scenes.get("render")
        if not render_scene: return {"CANCELLED"}
        
        render_scene.render.engine = 'CYCLES'
        if hasattr(render_scene, 'cycles'):
            render_scene.cycles.samples = 1
            render_scene.cycles.use_denoising = False
            render_scene.cycles.time_limit = 0.01
        
        render_scene.render.image_settings.file_format = 'FFMPEG'
        render_scene.render.ffmpeg.format = 'QUICKTIME'
        render_scene.render.ffmpeg.codec = 'PRORES'

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

        render_scene = bpy.data.scenes.get("render")
        if not render_scene:
            msg = "ERROR: 'render' scene not found. Run Step 1."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        shot_marker = scene.timeline_markers.get(shot_name)
        source_scene = context.scene 

        if not source_scene:
            msg = "ERROR: Source scene not found. Run Step 2."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        name_components = _parse_name_components(context, shot_marker.name, source_scene.name)
        if not name_components:
            msg = "ERROR: Failed to parse name components."
            log.error(msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        new_save_path, new_blend_filename_no_ext, _ = _get_new_brender_filepath_parts(context, name_components)
        if not new_save_path:
            msg = "ERROR: Failed to generate new file path."
            log.error(msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        try:
            # UPDATED: Use Global Prefs
            prefs = get_prefs(context)
            base_path = bpy.path.abspath(prefs.output_base)
            target_sc_prefix = name_components['scene_number'].upper() # "SC17"
            
            found_scene_dir = None
            if os.path.exists(base_path):
                for d in os.listdir(base_path):
                    full_d = os.path.join(base_path, d)
                    if os.path.isdir(full_d) and d.upper().startswith(target_sc_prefix):
                        found_scene_dir = d
                        break
            
            if found_scene_dir:
                scene_dir_path = os.path.join(base_path, found_scene_dir)
            else:
                env_upper = name_components['env_name'].upper()
                new_scene_folder = f"{target_sc_prefix}-{env_upper}"
                scene_dir_path = os.path.join(base_path, new_scene_folder)

            shot_dir_name = new_blend_filename_no_ext
            output_dir = os.path.join(scene_dir_path, shot_dir_name)

            final_filename_mov = new_blend_filename_no_ext.lower() + ".mov"
            render_filepath = os.path.join(output_dir, final_filename_mov)

            render_scene.render.filepath = render_filepath
            render_scene.render.use_file_extension = False 

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
        log.info("--- DEBUG STEP 7: Move Scene Strip to Frame 1 ---")
        scene = context.scene
        shot_name = scene.brender_debug_shot_name
        if not shot_name:
            self.report({"ERROR"}, "No debug shot selected.")
            return {"CANCELLED"}

        render_scene = bpy.data.scenes.get("render")
        if not render_scene or not render_scene.sequence_editor:
            msg = "ERROR: 'render' scene VSE not found. Run Step 4."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        shot_scene_strip = next((s for s in render_scene.sequence_editor.sequences if s.name == shot_name and s.type == 'SCENE'), None)

        if not shot_scene_strip:
            msg = f"ERROR: Scene strip '{shot_name}' not found in VSE."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        try:
            shot_scene_strip.frame_start = 1
            msg = "OK (Step 7): Moved scene strip to frame 1."
            log.info(msg)
            scene.brender_debug_status_message = msg
        except Exception as e:
            msg = f"ERROR: Could not move scene strip: {e}"
            log.error(msg)
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        log.info("--- DEBUG STEP 7: Complete ---")
        return {'FINISHED'}

class BRENDER_OT_debug_step_8_set_active(bpy.types.Operator):
    bl_idname = "brender.debug_step_8_set_active"
    bl_label = "8. Set 'render' Scene Active"

    def execute(self, context):
        render_scene = bpy.data.scenes.get("render")
        if render_scene:
            context.window.scene = render_scene
        return {'FINISHED'}

class VIEW3D_PT_brender_debug_panel(bpy.types.Panel):
    bl_label = "bRender Debug"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "bRender"
    bl_order = 1 
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

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.brender_shot_list = bpy.props.CollectionProperty(type=BRENDER_ShotListItem)
    bpy.types.Scene.brender_active_shot_index = bpy.props.IntProperty()

    # --- FARM CONFIG (SCENE LEVEL) ---
    bpy.types.Scene.brender_show_farm_settings = bpy.props.BoolProperty(
        name="Show Farm Settings", default=False)
    
    bpy.types.Scene.brender_deadline_pool = bpy.props.StringProperty(
        name="Pool", default="renderstations", description="Main Deadline Pool")
    
    bpy.types.Scene.brender_deadline_secondary_pool = bpy.props.StringProperty(
        name="Secondary Pool", default="workstations", description="Secondary Deadline Pool")

    bpy.types.Scene.brender_deadline_group = bpy.props.StringProperty(
        name="Group", default="krutart_renderfarm", description="Deadline Group")
    
    bpy.types.Scene.brender_deadline_priority = bpy.props.IntProperty(
        name="Priority", default=52, min=0, max=100, description="Job Priority")

    bpy.types.Scene.brender_debug_shot_name = bpy.props.StringProperty()
    bpy.types.Scene.brender_debug_status_message = bpy.props.StringProperty()
    
    # --- REGISTER HANDLERS ---
    bpy.app.handlers.load_post.append(auto_refresh_shot_list)
    bpy.app.handlers.depsgraph_update_post.append(auto_refresh_shot_list)
    
    log.info("bRender addon registered successfully.")

def unregister():
    # --- UNREGISTER HANDLERS ---
    if auto_refresh_shot_list in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(auto_refresh_shot_list)
    if auto_refresh_shot_list in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(auto_refresh_shot_list)

    del bpy.types.Scene.brender_show_farm_settings
    del bpy.types.Scene.brender_deadline_pool
    del bpy.types.Scene.brender_deadline_secondary_pool
    del bpy.types.Scene.brender_deadline_group
    del bpy.types.Scene.brender_deadline_priority

    del bpy.types.Scene.brender_debug_shot_name
    del bpy.types.Scene.brender_debug_status_message
    del bpy.types.Scene.brender_active_shot_index
    del bpy.types.Scene.brender_shot_list

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    log.info("bRender addon unregistered.")

if __name__ == "__main__":
    register()
