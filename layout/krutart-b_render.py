bl_info = {
    "name": "Krutart bRender",
    "author": "iori, Krutart, Gemini",
    "version": (3, 5, 4), # Incremented version
    "blender": (4, 1, 0),
    "location": "3D View > Sidebar > bRender",
    "description": "Prepares render files for shots by creating a VSE timeline in a dedicated 'render' scene. Operates locally and saves copies.",
    "warning": "",
    "doc_url": "",
    "category": "Sequencer",
}

import bpy
import re
import os
import logging
import sys

# --- SETUP LOGGER ---
log = logging.getLogger("bRender")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[bRender] %(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
log.setLevel(logging.INFO)

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

    # 2. From source scene (Expected format: "sc##-env_name" or "SC##_env_name")
    env_match = re.search(r"sc\d+[-_](.+)", source_scene_name, re.IGNORECASE)
    env_name = env_match.group(1) if env_match else "env"
    
    # 3. From scene properties
    project_code = context.scene.brender_project_code
    task = context.scene.brender_task
    
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
    Returns (shot_start_frame, shot_end_frame, shot_duration) or (None, None, None)
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

# --- REMOVED _get_source_scene function ---
# The logic is now simplified to use the active scene when the operator is run.

def _get_scene_content_duration(source_scene):
    """Finds the intended duration of the scene's content."""
    if not source_scene:
        log.error("No source scene provided to get content duration.")
        return 0

    log.info(f"Determining content duration for '{source_scene.name}'...")
    end_marker = source_scene.timeline_markers.get("END")
    scene_content_duration = 0
    if end_marker:
        scene_content_duration = end_marker.frame - 1
        log.info(f"Found 'END' marker. Content duration set to: {scene_content_duration} frames.")
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
    This function creates/clears the render scene and populates its VSE
    with the correct sound, scene, and guide video strips.
    It operates entirely on the current file's data.

    Returns (True, source_scene_object, name_components_dict) on success,
    (False, None, None) on failure.
    
    The returned name_components_dict is augmented with 'new_save_path'
    if successful.
    """
    log.info(f"--- Starting preparation for shot: {shot_marker.name} ---")
    
    # --- TASK 1: Use a copy of the active scene, not a new one ---
    # Store the original scene to return to it
    original_active_scene = context.window.scene
    
    # Delete any pre-existing 'render' scene to avoid conflicts
    # This is what orphans the data from the *previous* batch loop
    existing_render_scene = bpy.data.scenes.get("render")
    if existing_render_scene:
        log.warning("Found existing 'render' scene. Removing it.")
        try:
            bpy.data.scenes.remove(existing_render_scene)
        except Exception as e:
            log.error(f"Could not remove existing 'render' scene: {e}. Aborting.")
            # Restore original scene before erroring
            context.window.scene = original_active_scene
            return (False, None, None)

    # Create a full copy of the currently active scene
    log.info(f"Creating a empty of the active scene '{original_active_scene.name}'.")
    bpy.ops.scene.new(type='EMPTY')
    render_scene = context.window.scene # The new scene is now active
    render_scene.name = "render"
    log.info(f"New scene 'render' created and settings copied.")
    
    # --- End Task 1 ---
    
    # Switch back to the original scene to find markers, etc.
    # We will switch *to* the render_scene at the very end.
    context.window.scene = original_active_scene
    
    shot_name = shot_marker.name

    # --- 1. Get shot timing info ---
    shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
    if shot_start_frame is None:
        return (False, None, None)

    # --- 2. Find the source scene from the marker name ---
    # --- MODIFIED: Use the original active scene as the source ---
    source_scene = original_active_scene
    log.info(f"Using active scene '{source_scene.name}' as the source.")
    # --- END MODIFICATION ---

    if not source_scene or not source_scene.sequence_editor:
        log.error(f"Source scene '{source_scene.name}' has no VSE. Aborting.")
        return (False, None, None)
    
    # --- START SCENE STRIP FIX (PLAN STEP 1) ---
    # Find the intended duration of the scene's content
    scene_content_duration = _get_scene_content_duration(source_scene)
    if scene_content_duration <= 0:
        return (False, None, None)
    # --- END SCENE STRIP FIX (PLAN STEP 1) ---

    # --- 2.1. NEW: Bind FULLDOME cameras in the source scene ---
    log.info(f"Binding FULLDOME cameras in '{source_scene.name}'...")
    try:
        context.window.scene = source_scene
        bpy.ops.scene.bind_cameras_to_markers(camera_type='FULLDOME')
        log.info("Successfully bound FULLDOME cameras.")
    except Exception as e:
        log.error(f"Failed to bind FULLDOME cameras: {e}")
        context.window.scene = original_active_scene # Ensure we switch back on error
        return(False, None, None)
    finally:
        # Crucially, restore the original scene context for the rest of the script
        context.window.scene = original_active_scene


    # --- 3. Find the guide strips in the source scene's VSE (NEW ROBUST 3-STEP LOGIC) ---
    vse_source = source_scene.sequence_editor
    guide_video_strip, guide_audio_strip = None, None
    shot_name_prefix = shot_marker.name # e.g., "CAM-SC17-SH130"
    
    # --- NEW: Get scene/shot numbers for substring search ---
    scene_num_str, shot_num_str = "", ""
    name_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker.name, re.IGNORECASE)
    if name_match:
        scene_num_str = name_match.group(1).lower() # "sc17"
        shot_num_str = name_match.group(2).lower() # "sh130"
    # --- END NEW ---

    log.info(f"Attempt 1: Finding strips starting with name: '{shot_name_prefix}'...")
    for strip in vse_source.sequences_all:
        # Check startswith *and* that we haven't found this type yet
        if strip.name.startswith(shot_name_prefix):
            if strip.type == 'MOVIE' and not guide_video_strip:
                guide_video_strip = strip
                log.info(f"  Found guide video (by prefix name): '{strip.name}'")
            if strip.type == 'SOUND' and not guide_audio_strip:
                guide_audio_strip = strip
                log.info(f"  Found guide audio (by prefix name): '{strip.name}'")
        if guide_video_strip and guide_audio_strip:
            break # Found both
            
    # --- NEW: Attempt 2: Find by substring ---
    if (not guide_video_strip or not guide_audio_strip) and scene_num_str and shot_num_str:
        log.warning(f"Attempt 1 failed. Attempt 2: Finding strips containing '{scene_num_str}' AND '{shot_num_str}'...")
        for strip in vse_source.sequences_all:
            strip_name_lower = strip.name.lower()
            # Check if name contains both scXX and shXXX
            if scene_num_str in strip_name_lower and shot_num_str in strip_name_lower:
                if strip.type == 'MOVIE' and not guide_video_strip:
                    guide_video_strip = strip
                    log.info(f"  Found guide video (by substring): '{strip.name}'")
                if strip.type == 'SOUND' and not guide_audio_strip:
                    guide_audio_strip = strip
                    log.info(f"  Found guide audio (by substring): '{strip.name}'")
            if guide_video_strip and guide_audio_strip:
                break # Found both
    # --- END NEW ATTEMPT 2 ---

    # Attempt 3: Fallback to frame-based search IF strips are missing
    if not guide_video_strip or not guide_audio_strip:
        log.warning(f"Attempt 1 & 2 (by name/substring) failed. Attempt 3 (Fallback): Falling back to frame search at frame {shot_start_frame}...")
        for strip in vse_source.sequences_all:
            # Only search for the one(s) we're missing
            if strip.frame_start == shot_start_frame:
                if strip.type == 'MOVIE' and not guide_video_strip:
                    guide_video_strip = strip
                    log.info(f"  Found guide video (by frame): '{strip.name}'")
                if strip.type == 'SOUND' and not guide_audio_strip:
                    guide_audio_strip = strip
                    log.info(f"  Found guide audio (by frame): '{strip.name}'")
            if guide_video_strip and guide_audio_strip:
                break # Found both

    # Final report
    if not guide_video_strip:
        log.warning(f"Could not find guide video strip for '{shot_name_prefix}' by name, substring, or frame.")
    if not guide_audio_strip:
        log.warning(f"Could not find guide audio strip for '{shot_name_prefix}' by name, substring, or frame.")
    # --- END NEW LOGIC ---

    # --- 4. Prepare the 'render' scene's VSE ---
    log.info("Preparing 'render' scene VSE...")
    if not render_scene.sequence_editor: 
        render_scene.sequence_editor_create()
        
    vse_render = render_scene.sequence_editor
    log.info(f"Clearing {len(vse_render.sequences)} existing strips from copied 'render' scene VSE.")
    for strip in list(vse_render.sequences):
        vse_render.sequences.remove(strip)

    # --- 5. Add new strips to the render scene ---
    log.info("Adding new strips to 'render' scene.")
    if guide_audio_strip:
        # This is correct, leave as is (per user request)
        new_audio = vse_render.sequences.new_sound(
            name=f"{shot_name}-guide_audio",
            filepath=bpy.path.abspath(guide_audio_strip.sound.filepath),
            channel=1, frame_start=shot_start_frame) # Start at the shot's real frame
        new_audio.frame_final_duration = shot_duration
        new_audio.frame_offset_start = 0 # No offset
        
        new_audio.volume = 0.8
        log.info(f"Added audio strip to channel 1. Start: {new_audio.frame_start}, Offset: {new_audio.frame_offset_start}")

    # --- START SCENE STRIP FIX (PLAN STEP 2) ---
    shot_scene_strip = vse_render.sequences.new_scene(
        name=shot_name, scene=source_scene,
        channel=2, frame_start=shot_start_frame) # Start at the shot's real frame
    
    # 1. Set duration based on "END" marker (or fallback)
    shot_scene_strip.frame_final_duration = scene_content_duration
    
    shot_scene_strip.scene_input = 'CAMERA'
    
    # 2. Set offset to force playback from frame 1 of the source scene
    shot_scene_strip.animation_offset_start = 1 - source_scene.frame_start
    
    log.info(f"Added main shot scene strip to channel 2. Start: {shot_scene_strip.frame_start}, Duration: {scene_content_duration}, Anim Offset: {shot_scene_strip.animation_offset_start} (to start at frame 1)")
    # --- END SCENE STRIP FIX (PLAN STEP 2) ---


    if guide_video_strip:
        # This is correct, leave as is (per user request)
        new_video = vse_render.sequences.new_movie(
            name=f"{shot_name}-guide_video",
            filepath=bpy.path.abspath(guide_video_strip.filepath),
            channel=3, frame_start=shot_start_frame) # Start at the shot's real frame
        new_video.frame_final_duration = shot_duration
        new_video.frame_offset_start = 0 # No offset
        
        new_video.blend_type = 'ALPHA_OVER'
        new_video.blend_alpha = 0.5
        if hasattr(new_video, 'sound') and new_video.sound: new_video.sound.volume = 0
        
        # --- NEW: Apply required transform and crop to the guide video ---
        log.info("Applying transform and crop to guide video.")
        new_video.transform.offset_x = -597
        new_video.transform.offset_y = 784
        new_video.crop.max_x = 611
        new_video.crop.min_y = 407
        
        log.info(f"Added video guide strip to channel 3. Start: {new_video.frame_start}, Offset: {new_video.frame_offset_start}")

    # --- 6. Find and set the FULLDOME camera ---
    log.info("Attempting to set FULLDOME camera.")
    fulldome_camera_name = f"{shot_name}-FULLDOME"
    fulldome_camera = bpy.data.objects.get(fulldome_camera_name)

    if fulldome_camera and fulldome_camera.type == 'CAMERA':
        render_scene.camera = fulldome_camera
        log.info(f"Successfully set active camera to '{fulldome_camera_name}'.")
    else:
        log.warning(f"Could not find FULLDOME camera named '{fulldome_camera_name}'. The scene's active camera will be used.")

    # --- 7. Finalize render scene settings ---
    log.info("Finalizing render scene settings.")
    # This is correct, leave as is
    render_scene.frame_start = shot_start_frame # Start render at the shot's real frame
    render_scene.frame_end = shot_end_frame - 1 # End render at the shot's real end frame
    
    log.info(f"Render scene range set: {render_scene.frame_start} to {render_scene.frame_end}")
    
    log.info("Setting resolution to 2048x2048 (FULLDOME).")
    render_scene.render.resolution_x = 2048
    render_scene.render.resolution_y = 2048
    
    render_scene.render.film_transparent = True
    
    # --- TASK 5 & 1 (MODIFIED): Parse names, determine file paths, set render output path ---
    log.info("Parsing names for render output path...")
    name_components = _parse_name_components(context, shot_marker.name, source_scene.name)
    if not name_components:
        log.error("Failed to parse name components for render path.")
        context.window.scene = original_active_scene # Restore
        return (False, None, None)

    # --- NEW (Step 2): Get the final blend file path *before* setting render path ---
    # This function now also calculates the blend file save path (Task 3)
    new_save_path, new_blend_filename_no_ext = _get_new_brender_filepath_parts(context, name_components)
    
    if not new_save_path:
        log.error(f"Failed to generate a new file path for {shot_marker.name}.")
        context.window.scene = original_active_scene # Restore
        return (False, None, None)

    # Store the calculated path for the operator to use
    name_components['new_save_path'] = new_save_path
    # --- End NEW (Step 2) ---
        
    try:
        # Get the base path from the UI
        base_path = bpy.path.abspath(context.scene.brender_output_base)
        
        # --- RENDER PATH FIX (PER USER REQUEST) ---
        
        # 1. Create directory name per plan: [Render Base] / [blend_filename_no_ext] /
        # e.g., S:\...\LAYOUT_RENDER\3212-sc17-apollo_crash-sh010-layout_r-v003
        output_dir = os.path.join(base_path, new_blend_filename_no_ext)
        
        # 2. Create the filename *prefix* from the same name
        # e.g., 3212-sc17-apollo_crash-sh010-layout_r-v003-
        # Blender will add "0001.exr" etc.
        filename_prefix_with_hyphen = new_blend_filename_no_ext + "-"
        
        # 3. Combine for final path, e.g.:
        # S:\...\LAYOUT_RENDER\3212-..-v003\3212-..-v003-
        render_filepath = os.path.join(output_dir, filename_prefix_with_hyphen)
        
        # --- END RENDER PATH FIX ---
        
        render_scene.render.filepath = render_filepath
        render_scene.render.use_file_extension = True # Ensure .exr (or other) is added
        
        log.info(f"Set render output path to: {render_filepath}")
        
    except Exception as e:
        log.error(f"Error setting render output path: {e}")
        # Don't fail the whole operation, just log the error
    
    # --- End Task 5 & 1 ---

    # --- MOVE ONLY THE SCENE STRIP (CHANNEL 2) TO FRAME 1 AT THE END ---
    try:
        shot_scene_strip.frame_start = 1
        log.info("Moved scene strip (channel 2) to frame 1 (post-setup).")
    except Exception as e:
        log.error(f"Could not move scene strip to frame 1: {e}")

    # Finally, set the fully prepared 'render' scene as the active one
    context.window.scene = render_scene


    log.info(f"--- Successfully prepared shot: {shot_marker.name} ---")
    # name_components now contains 'new_save_path'
    return (True, source_scene, name_components)

# --- UTILITY FUNCTIONS ---

def _get_new_brender_filepath_parts(context, name_components):
    """
    Calculates the directory, version, and final path for a new bRender file.
    Returns (full_filepath, filename_base_no_extension)
    Returns (None, None) on failure.
    """
    if not bpy.data.is_saved:
        log.error("Source file is not saved. Cannot determine output path.")
        return None, None
    
    if not name_components:
        log.error("Name components not provided. Cannot generate filepath.")
        return None, None

    # --- TASK 3 (MODIFIED): Directory uses ../GRANDPARENT_DIR_NAME-BRENDER ---
    try:
        base_dir = os.path.dirname(bpy.data.filepath)      # e.g., .../sc17/layout-moon_b
        parent_dir_path = os.path.dirname(base_dir)        # e.g., .../sc17
        
        # Get the name of the parent dir (e.g., 'sc17')
        grandparent_dir_name = os.path.basename(parent_dir_path) 
        
        brender_dir_name = f"{grandparent_dir_name}-BRENDER" # e.g., sc17-BRENDER
        # MODIFIED: Place it *inside* the parent dir (../)
        brender_dir = os.path.join(parent_dir_path, brender_dir_name) # e.g., .../sc17/sc17-BRENDER
        
        log.info(f"Target BRENDER directory: {brender_dir}")
        os.makedirs(brender_dir, exist_ok=True)
    except Exception as e:
        log.error(f"Error creating BRENDER directory: {e}")
        return None, None
    # --- End Task 3 ---

    # 2. Get info from components dict
    project_code = name_components['project_code']
    scene_number = name_components['scene_number']
    shot_number = name_components['shot_number']
    env_name = name_components['env_name']
    task = name_components['task']

    # 3. Handle versioning
    version = 1
    # Note: filename_prefix is different from the render output prefix
    filename_prefix = f"{project_code}-{scene_number}-{env_name}-{shot_number}-{task}-v"
    
    try:
        # We search for the lowercase prefix
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
        # Continue with version 1 as a fallback

    # 4. Construct final name and path
    # e.g., 3212-SC17-env-SH010-layout_r-v001
    filename_base_no_ext = f"{filename_prefix}{version:03d}"
    
    # --- TASK 4: Filenames are lowercase ---
    filename_base_no_ext_lower = filename_base_no_ext.lower()
    new_filename = f"{filename_base_no_ext_lower}.blend"
    # --- End Task 4 ---
    
    new_filepath = os.path.join(brender_dir, new_filename)
    
    # Return both the full path and the lowercase-base-name-for-the-folder
    return new_filepath, filename_base_no_ext_lower


def get_new_brender_filepath(context, name_components):
    """
    Constructs the full, versioned, absolute filepath for a new render file.
    Wrapper for _get_new_brender_filepath_parts.
    """
    new_filepath, _ = _get_new_brender_filepath_parts(context, name_components)
    if new_filepath:
        log.info(f"Generated new file path: {new_filepath}")
    return new_filepath

def get_shot_info_from_frame(context):
    """Gets information about the shot under the current playhead."""
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
    """Returns a sorted list of all valid shot markers in the scene."""
    scene = context.scene
    shot_markers = [m for m in scene.timeline_markers if re.match(r"CAM-SC\d+-SH\d+", m.name, re.IGNORECASE)]
    return sorted(shot_markers, key=lambda m: m.frame)

def _purge_orphans():
    """
    Aggressively purges all orphaned data-blocks from the file.
    Uses the modern bpy.data.orphans_purge() (Blender 4.1+).
    """
    log.info("Purging orphaned data-blocks...")
    try:
        # bpy.data.orphans_purge() is available in 4.1+
        # It's more robust and doesn't require context.
        # It returns the number of purged items.
        purged_count = bpy.data.orphans_purge(do_recursive=True)
        log.info(f"Purged {purged_count} orphaned data-blocks.")
        
        # Run it again to catch nested orphans just in case
        purged_count_2 = bpy.data.orphans_purge(do_recursive=True)
        if purged_count_2 > 0:
            log.info(f"Purged an additional {purged_count_2} nested data-blocks.")
            
    except Exception as e:
        log.error(f"Error during orphan purge: {e}.")

# --- DATA STRUCTURE FOR SHOT LIST ---
class BRENDER_ShotListItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty() # Full name, e.g., CAM-SC01-SH001
    display_name: bpy.props.StringProperty() # Short name for UI, e.g., SC01-SH001
    is_selected: bpy.props.BoolProperty(name="", description="Include this shot in the batch preparation", default=True)
    frame: bpy.props.IntProperty()

# --- NEW: UIList Class for Shot List ---
class BRENDER_UL_shot_list(bpy.types.UIList):
    """UIList class to draw the brender_shot_list."""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        # `data` is the scene
        # `item` is the BRENDER_ShotListItem
        # `active_data` is the scene (passed in template_list)
        # `active_propname` is "brender_active_shot_index" (passed in template_list)
        
        # We just need to draw the property, Blender handles the rest.
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            # Draw the 'is_selected' checkbox with the 'display_name' as its label
            layout.prop(item, "is_selected", text=item.display_name)
        
        elif self.layout_type in {'GRID'}:
            # Example for grid layout, though not used here
            layout.alignment = 'CENTER'
            layout.label(text=item.display_name)


# --- OPERATORS ---

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
        
        # Prepare the render scene in memory
        # name_components will now contain 'new_save_path' if successful
        success, source_scene, name_components = _prepare_shot_in_current_file(context, shot_marker)
        if not success:
            self.report({"ERROR"}, f"Failed to prepare render scene for {shot_marker.name}.")
            context.window.scene = original_scene # Restore original scene
            return {"CANCELLED"}
            
        # --- NEW: Purge any potential orphans before saving ---
        # (This is more for consistency, the batch operator is where it's critical)
        _purge_orphans()
        
        # --- MODIFIED (Step 2): The save path is now pre-calculated ---
        new_filepath = name_components.get('new_save_path')
        
        if new_filepath:
            log.info(f"Saving prepared file to: {new_filepath}")
            bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=True)
            self.report({'INFO'}, f"Saved: {os.path.basename(new_filepath)}")
        else:
            self.report({"ERROR"}, f"Could not generate a valid filename for '{shot_marker.name}'.")
            context.window.scene = original_scene
            return {"CANCELLED"}

        # Restore the original active scene
        context.window.scene = original_scene
        log.info("Preparation for active shot complete.")
        return {'FINISHED'}

class BRENDER_OT_prepare_this_file(bpy.types.Operator):
    bl_idname = "brender.prepare_this_file"
    bl_label = "Prepare This File"
    bl_description = "Prepares this file for rendering based on its filename (e.g., SC01-SH010.blend)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        filepath = bpy.data.filepath
        if not filepath: return False
        filename = os.path.basename(filepath)
        # This poll is now less relevant with the new naming scheme, but kept for legacy files.
        # It's better to just let the user click it.
        return True 

    def execute(self, context):
        filepath = bpy.data.filepath
        filename = os.path.basename(filepath)
        
        # Try to parse filename based on NEW format
        name_match = re.match(r".*-(sc\d+)-.*-(sh\d+)-.*-v\d+\.blend", filename, re.IGNORECASE)
        
        if not name_match:
            # Fallback to legacy format
            name_match = re.match(r"(SC\d+)-(SH\d+)\.blend", filename, re.IGNORECASE)
        
        if not name_match:
            self.report({"ERROR"}, "Filename format not recognized. Expected '...-SC##-...-SH###-...-v###.blend' or 'SC##-SH###.blend'.")
            return {"CANCELLED"}
            
        scene_number, shot_number = name_match.group(1).upper(), name_match.group(2).upper()
        target_shot_name = f"CAM-{scene_number}-{shot_number}"

        shot_marker = context.scene.timeline_markers.get(target_shot_name)
        if not shot_marker:
            self.report({"ERROR"}, f"Could not find a timeline marker named '{target_shot_name}'.")
            return {"CANCELLED"}
        
        # _prepare_shot_in_current_file will re-calculate paths, which is fine
        # It will set the render path and VSE correctly for this file.
        success, _, _ = _prepare_shot_in_current_file(context, shot_marker)

        if success:
            # --- NEW: Purge any orphans before saving ---
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
            # Generate the display name by removing the prefix
            name_match = re.match(r"CAM-(SC\d+-SH\d+)", marker.name, re.IGNORECASE)
            if name_match:
                item.display_name = name_match.group(1)
            else:
                item.display_name = marker.name # Fallback
            item.frame = marker.frame
        
        # --- UI FIX ---
        # When refreshing, reset the active index
        context.scene.brender_active_shot_index = 0
        
        log.info(f"Found and listed {len(found_shots)} shots.")
        return {'FINISHED'}


class BRENDER_OT_prepare_render_batch(bpy.types.Operator):
    bl_idname = "brender.prepare_render_batch"
    bl_label = "Prepare Batch From Selection"
    bl_description = "For each selected shot, prepares a render scene and saves a new .blend file"

    def execute(self, context):
        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the main project file first.")
            return {"CANCELLED"}
        
        original_scene = context.window.scene
        
        selected_shots = [s for s in context.scene.brender_shot_list if s.is_selected]
        if not selected_shots:
            self.report({"WARNING"}, "No shots selected from the list.")
            return {"CANCELLED"}
        
        log.info(f"Starting batch preparation for {len(selected_shots)} shots.")
        processed_count = 0
        
        # Main loop for processing each shot
        for shot_item in selected_shots:
            log.info(f"--- Preparing batch item: {shot_item.name} ---")
            
            shot_marker = context.scene.timeline_markers.get(shot_item.name)
            if not shot_marker:
                log.error(f"Marker '{shot_item.name}' not found. Skipping.")
                continue

            # Prepare the 'render' scene in memory for the current shot
            # This step DELETES the *previous* 'render' scene, orphaning its data.
            success, source_scene, name_components = _prepare_shot_in_current_file(context, shot_marker)
            
            if not success:
                log.error(f"Preparation failed for '{shot_item.name}'. Skipping save.")
                # Restore context even on failure before skipping to the next item
                context.window.scene = original_scene
                continue
                
            # --- NEW STEP: PURGE ORPHANED DATA ---
            # Purge the data orphaned by the deletion of the *previous*
            # 'render' scene inside _prepare_shot_in_current_file.
            # This cleans the file *before* we save the copy.
            _purge_orphans()
            # --- END NEW STEP ---
                
            # --- MODIFIED (Step 2): The save path is now pre-calculated ---
            new_filepath = name_components.get('new_save_path')
            
            if new_filepath:
                log.info(f"Saving prepared shot to: {new_filepath}")
                # Save a copy, which leaves the current main file active in Blender
                bpy.ops.wm.save_as_mainfile(filepath=new_filepath, copy=True)
                
                # Update UI: uncheck the processed item
                shot_item.is_selected = False
                processed_count += 1
            else:
                log.error(f"Could not generate filename for '{shot_item.name}'. Skipping save.")
            
            # --- FIX: Restore original scene CONTEXT inside the loop ---
            # This is crucial for the next iteration to find markers and UI lists.
            context.window.scene = original_scene

        # Final cleanup: Ensure original scene is active
        context.window.scene = original_scene
        
        # We no longer need to manually clear the render scene,
        # as it is forcefully removed and recreated at the start of _prepare_shot_in_current_file.
        
        log.info(f"--- Batch preparation complete. Successfully processed {processed_count}/{len(selected_shots)} shots. ---")
        self.report({'INFO'}, f"Batch complete. Saved {processed_count} shot files.")
        
        return {'FINISHED'}


# --- NEW: DEBUG OPERATORS ---

class BRENDER_OT_debug_set_shot(bpy.types.Operator):
    bl_idname = "brender.debug_set_shot"
    bl_label = "Set Checked Shot for Debugging"
    bl_description = "Sets the shot *checked* in the list (must be exactly one) as the target for debug operations"
    
    def execute(self, context):
        scene = context.scene
        shot_list = scene.brender_shot_list
        
        # Find all shots that are checked
        selected_shots = [item for item in shot_list if item.is_selected]
        
        if len(selected_shots) == 0:
            scene.brender_debug_shot_name = ""
            scene.brender_debug_status_message = "ERROR: No shot is checked in the list."
            self.report({"WARNING"}, "No shot selected for debugging. Please check exactly one shot.")
            return {"CANCELLED"}
            
        if len(selected_shots) > 1:
            scene.brender_debug_shot_name = ""
            scene.brender_debug_status_message = f"ERROR: {len(selected_shots)} shots checked. Need exactly one."
            self.report({"WARNING"}, "Too many shots selected. Please check exactly one shot for debugging.")
            return {"CANCELLED"}
            
        # Exactly one shot is selected
        shot_item = selected_shots[0] 
        scene.brender_debug_shot_name = shot_item.name
        scene.brender_debug_status_message = f"Ready to debug shot: {shot_item.display_name}"
        log.info(f"Debug shot set to: {shot_item.name}")
        return {'FINISHED'}

class BRENDER_OT_debug_step_1_create_scene(bpy.types.Operator):
    bl_idname = "brender.debug_step_1_create_scene"
    bl_label = "1. Create/Clean 'render' Scene"
    bl_description = "Deletes any existing 'render' scene and creates a new, empty one"
    
    def execute(self, context):
        log.info("--- DEBUG STEP 1: Create/Clean 'render' Scene ---")
        scene = context.scene
        
        existing_render_scene = bpy.data.scenes.get("render")
        if existing_render_scene:
            log.warning("Debug: Found existing 'render' scene. Removing it.")
            try:
                bpy.data.scenes.remove(existing_render_scene)
            except Exception as e:
                log.error(f"Debug: Could not remove 'render' scene: {e}.")
                self.report({"ERROR"}, f"Could not remove 'render' scene: {e}")
                scene.brender_debug_status_message = "ERROR: Could not remove 'render' scene."
                return {"CANCELLED"}

        log.info("Debug: Creating new 'EMPTY' scene.")
        original_active_scene = context.window.scene
        bpy.ops.scene.new(type='EMPTY')
        render_scene = context.window.scene 
        render_scene.name = "render"
        
        # Switch back to original scene
        context.window.scene = original_active_scene
        
        scene.brender_debug_status_message = "OK (Step 1): 'render' scene created."
        log.info("--- DEBUG STEP 1: Complete ---")
        return {'FINISHED'}

class BRENDER_OT_debug_step_2_find_data(bpy.types.Operator):
    bl_idname = "brender.debug_step_2_find_data"
    bl_label = "2. Find Scenes & Timing"
    bl_description = "Finds source scene, shot marker, and timing info"

    def execute(self, context):
        log.info("--- DEBUG STEP 2: Find Scenes & Timing ---")
        scene = context.scene
        shot_name = scene.brender_debug_shot_name
        if not shot_name:
            self.report({"ERROR"}, "No debug shot selected. Use 'Set Debug Shot'.")
            scene.brender_debug_status_message = "ERROR: No debug shot selected."
            return {"CANCELLED"}
            
        shot_marker = scene.timeline_markers.get(shot_name)
        if not shot_marker:
            msg = f"ERROR: Marker '{shot_name}' not found."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
        
        shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
        if shot_start_frame is None:
            msg = f"ERROR: Could not get timing for '{shot_name}'."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
            
        # --- MODIFIED: Use active scene, don't search ---
        source_scene = context.scene 
        log.info(f"Debug: Using active scene '{source_scene.name}' as source.")
        # --- END MODIFICATION ---

        if not source_scene:
            # This should technically be impossible if context.scene exists
            msg = f"ERROR: Source scene (context.scene) for '{shot_name}' not found."
            log.error(msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
            
        scene_content_duration = _get_scene_content_duration(source_scene)
        if scene_content_duration <= 0:
            msg = f"ERROR: Content duration for '{source_scene.name}' is 0."
            log.error(msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        msg = f"OK (Step 2): Src='{source_scene.name}', Time={shot_start_frame}-{shot_end_frame-1}, Content={scene_content_duration}f."
        log.info(msg)
        scene.brender_debug_status_message = msg
        log.info("--- DEBUG STEP 2: Complete ---")
        return {'FINISHED'}

class BRENDER_OT_debug_step_3_bind_cameras(bpy.types.Operator):
    bl_idname = "brender.debug_step_3_bind_cameras"
    bl_label = "3. Bind FULLDOME Cameras"
    bl_description = "Runs the 'Bind FULLDOME Cameras' operator in the source scene"

    def execute(self, context):
        log.info("--- DEBUG STEP 3: Bind FULLDOME Cameras ---")
        scene = context.scene
        shot_name = scene.brender_debug_shot_name
        if not shot_name:
            self.report({"ERROR"}, "No debug shot selected.")
            return {"CANCELLED"}
        
        # --- MODIFIED: Use active scene, don't search ---
        source_scene = context.scene
        # --- END MODIFICATION ---

        if not source_scene:
            msg = "ERROR: Source scene not found. Run Step 2."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        original_active_scene = context.window.scene
        try:
            context.window.scene = source_scene
            bpy.ops.scene.bind_cameras_to_markers(camera_type='FULLDOME')
            msg = f"OK (Step 3): Bound FULLDOME cameras in '{source_scene.name}'."
            log.info(msg)
            scene.brender_debug_status_message = msg
        except Exception as e:
            msg = f"ERROR: Failed to bind cameras: {e}"
            log.error(msg)
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
        finally:
            context.window.scene = original_active_scene
            
        log.info("--- DEBUG STEP 3: Complete ---")
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
        # --- MODIFIED: Use active scene, don't search ---
        source_scene = context.scene
        # --- END MODIFICATION ---
        shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
        scene_content_duration = _get_scene_content_duration(source_scene)
        
        if not all([shot_marker, source_scene, shot_start_frame is not None, scene_content_duration > 0]):
            msg = "ERROR: Missing data. Run Step 2."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
            
        # 3. Find guide strips (NEW ROBUST 3-STEP LOGIC)
        vse_source = source_scene.sequence_editor
        if not vse_source:
            msg = f"ERROR: Source scene '{source_scene.name}' has no VSE."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
            
        guide_video_strip, guide_audio_strip = None, None
        shot_name_prefix = shot_marker.name # e.g., "CAM-SC17-SH130"
        
        # --- NEW: Get scene/shot numbers for substring search ---
        scene_num_str, shot_num_str = "", ""
        name_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker.name, re.IGNORECASE)
        if name_match:
            scene_num_str = name_match.group(1).lower() # "sc17"
            shot_num_str = name_match.group(2).lower() # "sh130"
        # --- END NEW ---
        
        log.info(f"Debug Attempt 1: Finding strips starting with name: '{shot_name_prefix}'...")
        for strip in vse_source.sequences_all:
            if strip.name.startswith(shot_name_prefix):
                if strip.type == 'MOVIE' and not guide_video_strip:
                    guide_video_strip = strip
                    log.info(f"  Debug: Found guide video (by prefix name): '{strip.name}'")
                if strip.type == 'SOUND' and not guide_audio_strip:
                    guide_audio_strip = strip
                    log.info(f"  Debug: Found guide audio (by prefix name): '{strip.name}'")
            if guide_video_strip and guide_audio_strip:
                break # Found both

        # --- NEW: Attempt 2: Find by substring ---
        if (not guide_video_strip or not guide_audio_strip) and scene_num_str and shot_num_str:
            log.warning(f"Debug Attempt 1 failed. Attempt 2: Finding strips containing '{scene_num_str}' AND '{shot_num_str}'...")
            for strip in vse_source.sequences_all:
                strip_name_lower = strip.name.lower()
                # Check if name contains both scXX and shXXX
                if scene_num_str in strip_name_lower and shot_num_str in strip_name_lower:
                    if strip.type == 'MOVIE' and not guide_video_strip:
                        guide_video_strip = strip
                        log.info(f"  Debug: Found guide video (by substring): '{strip.name}'")
                    if strip.type == 'SOUND' and not guide_audio_strip:
                        guide_audio_strip = strip
                        log.info(f"  Debug: Found guide audio (by substring): '{strip.name}'")
                if guide_video_strip and guide_audio_strip:
                    break # Found both
        # --- END NEW ATTEMPT 2 ---

        # Attempt 3: Fallback to frame-based search IF strips are missing
        if not guide_video_strip or not guide_audio_strip:
            log.warning(f"Debug Attempt 1 & 2 (by name/substring) failed. Attempt 3 (Fallback): Falling back to frame search at frame {shot_start_frame}...")
            for strip in vse_source.sequences_all:
                # Only search for the one(s) we're missing
                if strip.frame_start == shot_start_frame:
                    if strip.type == 'MOVIE' and not guide_video_strip:
                        guide_video_strip = strip
                        log.info(f"  Debug: Found guide video (by frame): '{strip.name}'")
                    if strip.type == 'SOUND' and not guide_audio_strip:
                        guide_audio_strip = strip
                        log.info(f"  Debug: Found guide audio (by frame): '{strip.name}'")
                if guide_video_strip and guide_audio_strip:
                    break # Found both
        
        if not guide_video_strip:
            log.warning(f"Debug: Could not find guide video strip for '{shot_name_prefix}' by name, substring, or frame.")
        if not guide_audio_strip:
            log.warning(f"Debug: Could not find guide audio strip for '{shot_name_prefix}' by name, substring, or frame.")
        # --- END NEW LOGIC ---

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
            log.info("Debug: Added audio strip.")
        else:
            log.warning("Debug: No guide audio strip found.")

        shot_scene_strip = vse_render.sequences.new_scene(
            name=shot_name, scene=source_scene,
            channel=2, frame_start=shot_start_frame)
        shot_scene_strip.frame_final_duration = scene_content_duration
        shot_scene_strip.scene_input = 'CAMERA'
        shot_scene_strip.animation_offset_start = 1 - source_scene.frame_start
        log.info("Debug: Added scene strip.")
        
        if guide_video_strip:
            new_video = vse_render.sequences.new_movie(
                name=f"{shot_name}-guide_video",
                filepath=bpy.path.abspath(guide_video_strip.filepath),
                channel=3, frame_start=shot_start_frame)
            new_video.frame_final_duration = shot_duration
            new_video.frame_offset_start = 0
            new_video.blend_type = 'ALPHA_OVER'
            new_video.blend_alpha = 0.5
            if hasattr(new_video, 'sound') and new_video.sound: new_video.sound.volume = 0
            new_video.transform.offset_x = -597
            new_video.transform.offset_y = 784
            new_video.crop.max_x = 611
            new_video.crop.min_y = 407
            log.info("Debug: Added video strip with transforms.")
        else:
            log.warning("Debug: No guide video strip found.")

        # --- NEW: More accurate debug message ---
        added_count = 1 # We always add the scene strip
        missing_strips = []
        if guide_audio_strip:
            added_count += 1
        else:
            missing_strips.append("Audio")
            
        if guide_video_strip:
            added_count += 1
        else:
            missing_strips.append("Video")
            
        if not missing_strips:
            msg = f"OK (Step 4): Added all {added_count} strips (Scene, Audio, Video)."
            log.info(msg)
        else:
            missing_str = " & ".join(missing_strips)
            msg = f"WARNING (Step 4): Added Scene strip, but MISSING guide {missing_str}."
            log.warning(f"Debug: {msg}")
            
        scene.brender_debug_status_message = msg
        log.info("--- DEBUG STEP 4: Complete ---")
        return {'FINISHED'}

class BRENDER_OT_debug_step_5_set_scene_settings(bpy.types.Operator):
    bl_idname = "brender.debug_step_5_set_scene_settings"
    bl_label = "5. Set Camera & Scene Settings"
    bl_description = "Sets the FULLDOME camera, frame range, and resolution in 'render' scene"

    def execute(self, context):
        log.info("--- DEBUG STEP 5: Set Camera & Scene Settings ---")
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
        shot_start_frame, shot_end_frame, shot_duration = _get_shot_timing(context, shot_marker)
        if shot_start_frame is None:
            msg = "ERROR: Could not get timing. Run Step 2."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}

        # 6. Find and set camera
        fulldome_camera_name = f"{shot_name}-FULLDOME"
        fulldome_camera = bpy.data.objects.get(fulldome_camera_name)

        if fulldome_camera and fulldome_camera.type == 'CAMERA':
            render_scene.camera = fulldome_camera
            log.info(f"Debug: Set active camera to '{fulldome_camera_name}'.")
        else:
            log.warning(f"Debug: Could not find FULLDOME camera '{fulldome_camera_name}'.")

        # 7. Finalize settings
        render_scene.frame_start = shot_start_frame
        render_scene.frame_end = shot_end_frame - 1
        render_scene.render.resolution_x = 2048
        render_scene.render.resolution_y = 2048
        render_scene.render.film_transparent = True
        
        msg = f"OK (Step 5): Set Cam (found: {bool(fulldome_camera)}), Range: {render_scene.frame_start}-{render_scene.frame_end}, Res: 2048x2048."
        log.info(msg)
        scene.brender_debug_status_message = msg
        log.info("--- DEBUG STEP 5: Complete ---")
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
        # --- MODIFIED: Use active scene, don't search ---
        source_scene = context.scene
        # --- END MODIFICATION ---

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

        new_save_path, new_blend_filename_no_ext = _get_new_brender_filepath_parts(context, name_components)
        if not new_save_path:
            msg = "ERROR: Failed to generate new file path."
            log.error(msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
        
        try:
            base_path = bpy.path.abspath(context.scene.brender_output_base)
            output_dir = os.path.join(base_path, new_blend_filename_no_ext)
            filename_prefix_with_hyphen = new_blend_filename_no_ext + "-"
            render_filepath = os.path.join(output_dir, filename_prefix_with_hyphen)
            
            render_scene.render.filepath = render_filepath
            render_scene.render.use_file_extension = True
            
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
    bl_description = "Switches the window context to the 'render' scene"

    def execute(self, context):
        log.info("--- DEBUG STEP 8: Set 'render' Scene Active ---")
        scene = context.scene
        render_scene = bpy.data.scenes.get("render")
        if not render_scene:
            msg = "ERROR: 'render' scene not found. Run Step 1."
            self.report({"ERROR"}, msg)
            scene.brender_debug_status_message = msg
            return {"CANCELLED"}
            
        context.window.scene = render_scene
        msg = "OK (Step 8): 'render' scene is now active."
        log.info(msg)
        scene.brender_debug_status_message = msg # This message will be on the *old* scene
        
        # Set it on the new scene too so it's visible
        render_scene.brender_debug_status_message = msg 
        
        log.info("--- DEBUG STEP 8: Complete ---")
        return {'FINISHED'}


# --- UI PANEL ---

class VIEW3D_PT_brender_panel(bpy.types.Panel):
    bl_label = "bRender"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "bRender"
    bl_order = 0 # Main panel first

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        box = layout.box()
        box.label(text="Settings", icon="TOOL_SETTINGS")
        col = box.column(align=True)
        col.prop(scene, "brender_project_code")
        col.prop(scene, "brender_task")
        
        # --- TASK 5: Add UI for Render Output Base ---
        col.prop(scene, "brender_output_base")
        # --- End Task 5 ---

        # --- UI FIX: Hide 'Active Shot Rendering' panel ---
        # layout.separator()
        #
        # box = layout.box()
        # box.label(text="Active Shot Rendering", icon="SCENE_DATA")
        # shot_info = get_shot_info_from_frame(context)
        # if shot_info:
        #     col = box.column(align=True)
        #     col.label(text=f"Active Shot: {shot_info['shot_marker'].name}")
        #     col.label(text=f"Duration: {shot_info['duration']} frames")
        #     col.separator()
        #     col.operator(BRENDER_OT_prepare_active_shot.bl_idname, icon="RENDER_ANIMATION")
        # else:
        #     box.label(text="Move playhead over a shot marker.", icon="INFO")
        # --- END UI FIX ---
            
        # --- UI FIX: Hide 'Manual File Preparation' panel ---
        # layout.separator()
        #
        # box = layout.box()
        # box.label(text="Manual File Preparation", icon="FILE_BLEND")
        # col = box.column()
        # # --- FIX: Corrected typo from BRENDP to BRENDER ---
        # op = col.operator(BRENDER_OT_prepare_this_file.bl_idname, icon="PREFERENCES")
        # col.label(text="Use in a saved bRender file")
        # --- END UI FIX ---

        layout.separator()

        box = layout.box()
        row = box.row(align=True)
        row.label(text="Batch Shot Preparation", icon="FILE_TICK")
        row.operator(BRENDER_OT_refresh_shot_list.bl_idname, text="", icon="FILE_REFRESH")

        shot_list = context.scene.brender_shot_list
        if shot_list:
            
            # --- ROBUST UI FIX: Use template_list ---
            
            # Calculate height: max 10 lines, but shrink to fit if fewer.
            # Set a minimum of 3 rows and a maximum of 10.
            display_height = min(len(shot_list), 10)
            display_height = max(3, display_height) # Ensure at least 3 rows
            
            # Draw the template_list. This creates a self-contained,
            # properly scrolling list widget with a fixed height.
            box.template_list(
                "BRENDER_UL_shot_list",      # UIList class name
                "",                          # layout_id (unused)
                scene,                       # data: pointer to the scene
                "brender_shot_list",         # propname: name of the CollectionProperty
                scene,                       # active_data: pointer to scene
                "brender_active_shot_index", # active_propname: name of the IntProperty
                rows=display_height
            )
            
            # Draw the button *after* the list.
            # Because template_list respects its 'rows' height,
            # this button will always be drawn correctly after it.
            row = box.row()
            row.operator(BRENDER_OT_prepare_render_batch.bl_idname, icon="EXPORT", text="Send to render")
            
            # --- End ROBUST UI FIX ---
            
        else:
            box.label(text="Click Refresh to find shots.", icon="INFO")

# --- NEW: DEBUG PANEL ---

class VIEW3D_PT_brender_debug_panel(bpy.types.Panel):
    bl_label = "bRender Debug"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "bRender"
    bl_order = 1 # Debug panel second
    bl_options = {'DEFAULT_CLOSED'} # Start closed by default

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        box = layout.box()
        box.label(text="Single Shot Debugger", icon="CONSOLE")
        
        col = box.column()
        col.label(text="Select a shot from the list above, then:")
        col.operator(BRENDER_OT_debug_set_shot.bl_idname, icon="RESTRICT_SELECT_OFF")
        
        col.separator()
        
        # Display the selected shot and status
        row = col.row()
        row.label(text="Debugging:")
        row.label(text=scene.brender_debug_shot_name if scene.brender_debug_shot_name else "None")
        
        col.separator()
        
        # Display status message
        col.label(text="Status:")
        col.label(text=scene.brender_debug_status_message, icon="INFO")
        
        col.separator()
        
        # Draw the step-by-step buttons
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
        col.label(text="Final (Optional):")
        
        # Add purge/save buttons (these are just regular operators)
        row = col.row(align=True)
        row.operator("wm.save_as_mainfile", text="Save Copy...", icon="FILE_TICK")
        row.operator("wm.save_mainfile", text="Save This File", icon="SAVE_AS")


# --- REGISTRATION ---
classes = (
    BRENDER_ShotListItem,
    BRENDER_UL_shot_list, # Add the new UIList class
    BRENDER_OT_prepare_active_shot,
    BRENDER_OT_prepare_this_file,
    BRENDER_OT_refresh_shot_list,
    BRENDER_OT_prepare_render_batch,
    VIEW3D_PT_brender_panel,
    # --- NEW: Register Debug Classes ---
    BRENDER_OT_debug_set_shot,
    BRENDER_OT_debug_step_1_create_scene,
    BRENDER_OT_debug_step_2_find_data,
    BRENDER_OT_debug_step_3_bind_cameras,
    BRENDER_OT_debug_step_4_add_strips,
    BRENDER_OT_debug_step_5_set_scene_settings,
    BRENDER_OT_debug_step_6_set_render_path,
    BRENDER_OT_debug_step_7_move_strip,
    BRENDER_OT_debug_step_8_set_active,
    VIEW3D_PT_brender_debug_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    bpy.types.Scene.brender_shot_list = bpy.props.CollectionProperty(type=BRENDER_ShotListItem)
    
    # Add new IntProperty for the UIList's active index
    bpy.types.Scene.brender_active_shot_index = bpy.props.IntProperty(
        name="Active Shot Index",
        description="Index of the active item in the shot list"
    )
    
    bpy.types.Scene.brender_project_code = bpy.props.StringProperty(
        name="Project Code",
        description="Project code for filename (e.g., 3212)",
        default="3212"
    )
    bpy.types.Scene.brender_task = bpy.props.StringProperty(
        name="Task",
        description="Task name for filename (e.g., layout_r)",
        default="layout_r"
    )
    
    # --- TASK 5: Register new Scene property ---
    bpy.types.Scene.brender_output_base = bpy.props.StringProperty(
        name="Render Output Base",
        description="Base directory for render output files (e.g., S:\\3212-EDIT\\SOURCE\\LAYOUT_RENDER\\)",
        default="S:\\3212-EDIT\\SOURCE\\LAYOUT_RENDER\\",
        subtype='DIR_PATH'
    )
    # --- End Task 5 ---
    
    # --- NEW: Register Debug Properties ---
    bpy.types.Scene.brender_debug_shot_name = bpy.props.StringProperty(
        name="Debug Shot Name",
        description="The full marker name of the shot being debugged"
    )
    bpy.types.Scene.brender_debug_status_message = bpy.props.StringProperty(
        name="Debug Status",
        description="Status message for the debug panel"
    )
    # --- End NEW ---
    
    log.info("bRender addon registered successfully.")

def unregister():
    log.info("Unregistering bRender addon.")
    
    # --- NEW: Unregister Debug Properties ---
    del bpy.types.Scene.brender_debug_shot_name
    del bpy.types.Scene.brender_debug_status_message
    # --- End NEW ---
    
    # --- TASK 5: Unregister new Scene property ---
    del bpy.types.Scene.brender_output_base
    # --- End Task 5 ---
    
    # Remove the new IntProperty
    del bpy.types.Scene.brender_active_shot_index
    
    del bpy.types.Scene.brender_shot_list
    del bpy.types.Scene.brender_project_code
    del bpy.types.Scene.brender_task
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    log.info("bRender addon unregistered.")

if __name__ == "__main__":
    register()
