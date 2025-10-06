bl_info = {
    "name": "bRender",
    "author": "iori, Krutart, Gemini",
    "version": (2, 6, 1),
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > bRender",
    "description": "Creates dedicated, prepared render files for single shots or batches with live progress tracking.",
    "warning": "This addon will save, create, and open files. Batch processing runs in the background.",
    "doc_url": "",
    "category": "Sequencer",
}

import bpy
import re
import os
import logging
import subprocess
import sys
import time
import threading
import queue
import shutil

# --- SETUP LOGGER ---
# Main logger for the addon's interactive part.
log = logging.getLogger("bRender")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[bRender] %(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
log.setLevel(logging.INFO)

# --- THREAD-SAFE QUEUE FOR SUBPROCESS LOGGING ---
# This queue will hold output from the background process, read by a separate thread.
log_queue = queue.Queue()

def enqueue_output(out, q):
    """
    This function runs in a separate thread. It reads each line from the 
    subprocess's output stream and puts it into the thread-safe queue.
    """
    try:
        for line in iter(out.readline, b''):
            q.put(line.decode('utf-8', errors='replace'))
        out.close()
    except Exception as e:
        # Log exceptions that might occur if the stream closes unexpectedly
        print(f"[bRender enqueue_output Thread Error]: {e}")


# --- CORE LOGIC ---

def _prepare_shot_in_current_file(context, shot_marker):
    """
    Contains the logic to prepare the 'render' scene for a given shot.
    This function assumes it is being run inside the newly saved, shot-specific .blend file.
    It does NOT handle file saving or loading.
    
    Returns True on success, False on failure.
    """
    log.info(f"--- Starting preparation for shot: {shot_marker.name} ---")
    shot_name = shot_marker.name
    name_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_name, re.IGNORECASE)
    if not name_match:
        log.error(f"Marker format for '{shot_name}' is not recognized. Expected 'CAM-SC##-SH###'.")
        return False

    scene_number = name_match.group(1).upper()
    
    # --- 1. Get shot timing info ---
    # Filter for valid shot markers only to ensure correct boundary detection. This is more robust
    # than checking all markers, as it prevents non-shot markers from affecting timing.
    shot_markers = sorted(
        [m for m in context.scene.timeline_markers if re.match(r"CAM-SC\d+-SH\d+", m.name, re.IGNORECASE)],
        key=lambda m: m.frame
    )

    shot_start_frame = shot_marker.frame
    shot_end_frame = context.scene.frame_end + 1  # Default for the last shot

    # Find the next shot marker in the filtered list to determine the end frame.
    try:
        current_marker_index = shot_markers.index(shot_marker)
        if current_marker_index < len(shot_markers) - 1:
            next_marker = shot_markers[current_marker_index + 1]
            shot_end_frame = next_marker.frame
    except ValueError:
        # This case should not happen if the passed shot_marker is valid, but as a fallback,
        # revert to the old logic of checking all markers.
        log.warning(f"Could not find shot marker '{shot_marker.name}' in the filtered list. Falling back to old method.")
        all_markers = sorted([m for m in context.scene.timeline_markers], key=lambda m: m.frame)
        for m in all_markers:
            if m.frame > shot_start_frame:
                shot_end_frame = m.frame
                break

    shot_duration = shot_end_frame - shot_start_frame
    log.info(f"Shot timing found: Start={shot_start_frame}, End={shot_end_frame-1}, Duration={shot_duration} frames.")

    # --- 2. Find the source scene from the marker name ---
    source_scene_prefix = scene_number
    source_scene = next((s for s in bpy.data.scenes if s.name.upper().startswith(source_scene_prefix)), None)
    if not source_scene or not source_scene.sequence_editor:
        log.error(f"Source scene starting with '{source_scene_prefix}' and containing a VSE could not be found.")
        return False
    log.info(f"Found source scene: '{source_scene.name}'")

    # --- 3. Find the guide strips in the source scene's VSE ---
    vse_source = source_scene.sequence_editor
    guide_video_strip, guide_audio_strip = None, None
    for strip in vse_source.sequences_all:
        if strip.frame_start == shot_start_frame:
            if strip.type == 'MOVIE' and not guide_video_strip: 
                guide_video_strip = strip
                log.info(f"Found guide video strip: '{strip.name}'")
            if strip.type == 'SOUND' and not guide_audio_strip: 
                guide_audio_strip = strip
                log.info(f"Found guide audio strip: '{strip.name}'")
            if guide_video_strip and guide_audio_strip: break
    if not guide_video_strip: log.warning("No guide video strip found for this shot.")
    if not guide_audio_strip: log.warning("No guide audio strip found for this shot.")

    # --- 4. Create or prepare the 'render' scene ---
    log.info("Preparing 'render' scene...")
    render_scene = bpy.data.scenes.get("render") or bpy.data.scenes.new("render")
    if not render_scene.sequence_editor: render_scene.sequence_editor_create()
    vse_render = render_scene.sequence_editor
    log.info(f"Clearing {len(vse_render.sequences)} existing strips from 'render' scene.")
    for strip in list(vse_render.sequences):
        vse_render.sequences.remove(strip)

    # --- 5. Add new strips to the render scene ---
    log.info("Adding new strips to 'render' scene.")
    if guide_audio_strip:
        new_audio = vse_render.sequences.new_sound(
            name=f"{shot_name}-guide_audio",
            filepath=bpy.path.abspath(guide_audio_strip.sound.filepath),
            channel=1, frame_start=shot_start_frame)
        new_audio.frame_final_duration = shot_duration
        new_audio.volume = 0.8
        log.info("Added audio strip.")

    shot_scene_strip = vse_render.sequences.new_scene(
        name=shot_name, scene=source_scene,
        channel=2, frame_start=shot_start_frame)
    shot_scene_strip.frame_final_duration = shot_duration
    shot_scene_strip.scene_input = 'CAMERA'
    log.info("Added main shot scene strip.")

    if guide_video_strip:
        new_video = vse_render.sequences.new_movie(
            name=f"{shot_name}-guide_video",
            filepath=bpy.path.abspath(guide_video_strip.filepath),
            channel=3, frame_start=shot_start_frame)
        new_video.frame_final_duration = shot_duration
        new_video.blend_type = 'ALPHA_OVER'
        new_video.blend_alpha = 0.5
        new_video.crop.max_y, new_video.crop.max_x = 40, 878
        new_video.crop.min_x, new_video.crop.min_y = 878, 1007
        new_video.transform.offset_y = 500
        if hasattr(new_video, 'sound') and new_video.sound: new_video.sound.volume = 0
        log.info("Added video guide strip.")

    # --- 6. Find and set the FULLDOME camera ---
    log.info("Attempting to set FULLDOME camera.")
    fulldome_camera_name = f"{shot_name}-FULLDOME"
    fulldome_camera = bpy.data.objects.get(fulldome_camera_name)

    if fulldome_camera and fulldome_camera.type == 'CAMERA':
        render_scene.camera = fulldome_camera
        log.info(f"Successfully set active camera to '{fulldome_camera_name}'.")
    else:
        log.warning(f"Could not find FULLDOME camera named '{fulldome_camera_name}'. The scene's active camera will be used instead.")

    # --- 7. Finalize render scene settings ---
    log.info("Finalizing render scene settings.")
    render_scene.frame_start = shot_start_frame
    render_scene.frame_end = shot_end_frame - 1
    render_scene.render.resolution_x = source_scene.render.resolution_x
    render_scene.render.resolution_y = source_scene.render.resolution_y
    render_scene.render.film_transparent = True
    
    # This check prevents errors in background mode where there is no window.
    if context.window:
        context.window.scene = render_scene
    else:
        log.info("No window context found (background mode). Active scene not set, but 'render' scene is prepared.")

    log.info(f"--- Successfully prepared shot: {shot_marker.name} ---")
    return True

# --- UTILITY FUNCTIONS ---

def get_shot_info_from_frame(context):
    scene = context.scene
    current_frame = scene.frame_current
    markers = sorted([m for m in scene.timeline_markers], key=lambda m: m.frame)
    active_shot_marker = None
    for m in reversed(markers):
        if m.frame <= current_frame and m.name.startswith("CAM-SC"):
            active_shot_marker = m
            break
    if not active_shot_marker: return None
    end_frame = scene.frame_end + 1
    for m in markers:
        if m.frame > active_shot_marker.frame:
            end_frame = m.frame
            break
    return {"shot_marker": active_shot_marker, "end_frame": end_frame, "duration": end_frame - active_shot_marker.frame}

def get_all_shots(context):
    scene = context.scene
    shot_markers = [m for m in scene.timeline_markers if re.match(r"CAM-SC\d+-SH\d+", m.name, re.IGNORECASE)]
    return sorted(shot_markers, key=lambda m: m.frame)

# --- DATA STRUCTURE FOR SHOT LIST ---

class BRENDER_ShotListItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    is_selected: bpy.props.BoolProperty(name="", description="Include this shot in the batch preparation", default=True)
    frame: bpy.props.IntProperty()

# --- OPERATORS ---

class BRENDER_OT_prepare_active_shot(bpy.types.Operator):
    bl_idname = "brender.prepare_active_shot"
    bl_label = "Prepare Active Shot"
    bl_description = "Creates and prepares a dedicated render file, then re-opens the original file"
    bl_options = {"REGISTER"}

    def execute(self, context):
        is_background_run = '--background-run' in sys.argv
        
        # --- BACKGROUND LOGIC ---
        if is_background_run:
            log.info("BACKGROUND: Executing 'Prepare Active Shot' in background mode.")
            
            target_shot_name = context.scene.get("brender_target_shot_name")
            if not target_shot_name:
                log.error("BACKGROUND: No target shot name found in custom property. Aborting.")
                sys.exit(1)
            
            shot_marker = context.scene.timeline_markers.get(target_shot_name)
            if not shot_marker:
                log.error(f"BACKGROUND: Could not find marker named '{target_shot_name}'. Aborting.")
                sys.exit(1)
            
            log.info(f"BACKGROUND: Found target shot marker '{shot_marker.name}' by name.")
            success = _prepare_shot_in_current_file(context, shot_marker)
            
            if success:
                log.info(f"BACKGROUND: Shot '{shot_marker.name}' prepared successfully. Saving file.")
                bpy.ops.wm.save_mainfile()
            else:
                log.error(f"BACKGROUND: Preparation failed for shot '{shot_marker.name}'.")
                sys.exit(1)
            
            log.info("BACKGROUND: Run finished.")
            return {'FINISHED'}

        # --- INTERACTIVE LOGIC ---
        log.info("INTERACTIVE: Executing 'Prepare Active Shot'.")
        original_filepath = bpy.data.filepath
        if not original_filepath:
            self.report({"ERROR"}, "Please save the main file before preparing a render.")
            log.error("Operation cancelled: Main file is not saved.")
            return {"CANCELLED"}

        log.info("Saving main file before proceeding.")
        bpy.ops.wm.save_mainfile()

        shot_info = get_shot_info_from_frame(context)
        if not shot_info:
            self.report({"ERROR"}, "No active shot marker found at the current frame.")
            log.error("Operation cancelled: No active shot marker found.")
            return {"CANCELLED"}

        shot_marker = shot_info["shot_marker"]
        shot_marker_name = shot_marker.name
        log.info(f"Found active shot: {shot_marker_name}")

        name_match = re.match(r"CAM-(SC\d+)-(SH\d+)", shot_marker_name, re.IGNORECASE)
        if not name_match:
            self.report({"ERROR"}, f"Marker '{shot_marker_name}' format is incorrect.")
            log.error(f"Marker format error for '{shot_marker_name}'.")
            return {"CANCELLED"}
        
        scene_number, shot_number = name_match.group(1).upper(), name_match.group(2).upper()
        base_render_path = r"S:\3212-PREPRODUCTION_TEST\LAYOUT\LAYOUT_MOON_D\LAYOUT_MOON_D-RENDER\RENDER_FILE"
        new_filename = f"{scene_number}-{shot_number}.blend"
        new_filepath = os.path.join(base_render_path, new_filename)
        os.makedirs(base_render_path, exist_ok=True)

        log.info(f"Saving new file as: {new_filepath}")
        bpy.ops.wm.save_as_mainfile(filepath=new_filepath)

        try:
            marker_in_new_file = bpy.context.scene.timeline_markers.get(shot_marker_name)
            if marker_in_new_file:
                success = _prepare_shot_in_current_file(bpy.context, marker_in_new_file)
                if success:
                    log.info("Shot preparation successful. Saving.")
                else:
                    log.error("Shot preparation failed. The new file may be incorrect.")
                bpy.ops.wm.save_mainfile()
            else:
                log.error(f"Could not find marker '{shot_marker_name}' in new file. Aborting preparation.")
        
        finally:
            log.info(f"Returning to original file: {original_filepath}")
            bpy.ops.wm.open_mainfile(filepath=original_filepath)
            
        return {'FINISHED'}


class BRENDER_OT_prepare_this_file(bpy.types.Operator):
    bl_idname = "brender.prepare_this_file"
    bl_label = "Prepare This File"
    bl_description = "Prepares this file for rendering based on its filename (e.g., SC01-SH010.blend)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        filepath = bpy.data.filepath
        if not filepath:
            return False
        filename = os.path.basename(filepath)
        return re.match(r"(SC\d+)-(SH\d+)\.blend", filename, re.IGNORECASE) is not None

    def execute(self, context):
        log.info("OPERATOR: Executing 'Prepare This File'.")
        filepath = bpy.data.filepath
        filename = os.path.basename(filepath)

        name_match = re.match(r"(SC\d+)-(SH\d+)\.blend", filename, re.IGNORECASE)
        if not name_match:
            self.report({"ERROR"}, "Filename does not match the 'SC##-SH###.blend' format.")
            return {"CANCELLED"}

        scene_number, shot_number = name_match.group(1).upper(), name_match.group(2).upper()
        target_shot_name = f"CAM-{scene_number}-{shot_number}"
        log.info(f"Identified target shot from filename: {target_shot_name}")

        shot_marker = context.scene.timeline_markers.get(target_shot_name)
        if not shot_marker:
            self.report({"ERROR"}, f"Could not find a timeline marker named '{target_shot_name}'.")
            log.error(f"Failed to find marker '{target_shot_name}' in the current file.")
            return {"CANCELLED"}
        
        log.info(f"Found marker '{shot_marker.name}'. Proceeding with preparation.")
        success = _prepare_shot_in_current_file(context, shot_marker)

        if success:
            log.info("File preparation was successful. Saving.")
            bpy.ops.wm.save_mainfile()
            self.report({"INFO"}, f"Successfully prepared file for shot {target_shot_name}.")
        else:
            log.error("File preparation failed.")
            self.report({"ERROR"}, "An error occurred during file preparation. Check the console.")

        return {'FINISHED'}


class BRENDER_OT_refresh_shot_list(bpy.types.Operator):
    bl_idname = "brender.refresh_shot_list"
    bl_label = "Refresh Shot List"

    def execute(self, context):
        log.info("OPERATOR: Executing 'Refresh Shot List'.")
        shot_list = context.scene.brender_shot_list
        shot_list.clear()
        found_shots = get_all_shots(context)
        for marker in found_shots:
            item = shot_list.add()
            item.name = marker.name
            item.frame = marker.frame
        log.info(f"Found and listed {len(found_shots)} shots.")
        return {'FINISHED'}


class BRENDER_OT_prepare_render_batch(bpy.types.Operator):
    """Processes selected shots in the background with live log tracking."""
    bl_idname = "brender.prepare_render_batch"
    bl_label = "Prepare Batch From Selection"
    bl_description = "For each selected shot, create a prepared render file in a background process"

    # Modal operator properties
    _timer = None
    _shot_queue = []
    _current_process = None
    _log_thread = None
    _start_time = 0.0
    _current_shot_name = None
    _processed_shots = 0
    _total_shots = 0
    
    # Constants
    TIMEOUT_SECONDS = 300.0 # 5 minutes

    # --- Python script template for the background process ---
    # This is defined once at the class level for efficiency.
    PY_COMMAND_TEMPLATE = """
import bpy
import sys
import logging
import re
import os

# --- Setup logging to print to stdout for the background process ---
bRender_log = logging.getLogger("bRender")
bRender_log.setLevel(logging.INFO)
bRender_log.handlers.clear() 
stdout_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('[BG %(levelname)s] %(message)s')
stdout_handler.setFormatter(formatter)
bRender_log.addHandler(stdout_handler)

bRender_log.info("--- Background Process Started for {shot_name} ---")

try:
    addon_module = '{addon_name}'
    bRender_log.info("Enabling addon: " + addon_module)
    bpy.ops.preferences.addon_enable(module=addon_module)
    bRender_log.info("Addon enabled successfully.")
    
    shot_marker_name = '{shot_name}'
    bRender_log.info("Target shot: " + shot_marker_name)

    name_match = re.match(r"CAM-(SC\\d+)-(SH\\d+)", shot_marker_name, re.IGNORECASE)
    if not name_match:
        bRender_log.error("Invalid marker format. Aborting.")
        sys.exit(1)
        
    scene_number, shot_number = name_match.group(1).upper(), name_match.group(2).upper()
    base_render_path = "S:\\\\3212-PREPRODUCTION_TEST\\\\LAYOUT\\\\LAYOUT_MOON_D\\\\LAYOUT_MOON_D-RENDER\\\\RENDER_FILE"
    new_filename = scene_number + "-" + shot_number + ".blend"
    new_filepath = os.path.join(base_render_path, new_filename)
    os.makedirs(base_render_path, exist_ok=True)
    bRender_log.info("Calculated new filepath: " + new_filepath)

    # This is the critical fix. By removing `copy=True`, this becomes a true "Save As" operation.
    # The script's context switches to the new file, preventing the original file from being modified.
    bpy.ops.wm.save_as_mainfile(filepath=new_filepath)
    bRender_log.info("Saved new file successfully. Context is now the new file.")

    sys.argv.append('--background-run')
    bpy.context.scene['brender_target_shot_name'] = shot_marker_name
    bpy.context.scene.frame_set({shot_frame})
    bRender_log.info("Frame set to {shot_frame} and target shot name stored.")

    bRender_log.info("Calling 'prepare_active_shot' operator...")
    bpy.ops.brender.prepare_active_shot()
    bRender_log.info("'prepare_active_shot' operator finished.")

except Exception as e:
    bRender_log.error("An exception occurred during background processing: " + str(e), exc_info=True)
    sys.exit(1) # Signal failure
finally:
    bRender_log.info("--- Background Process Finished ---")
    sys.stdout.flush() # Ensure all output is written before exit
"""

    def modal(self, context, event):
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            log.warning("User cancellation detected (ESC or Right-Click).")
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            # --- Drain the log queue and print any new messages ---
            while not log_queue.empty():
                try:
                    line = log_queue.get_nowait()
                    # Print line directly, as it comes from the background logger
                    print(line, end='')
                except queue.Empty:
                    # This can happen if the queue becomes empty between the check and the get
                    break
            
            # --- Check on the currently running process ---
            if self._current_process:
                # Check for timeout
                if (time.time() - self._start_time) > self.TIMEOUT_SECONDS:
                    log.error(f"TIMEOUT! Process for '{self._current_shot_name}' exceeded {self.TIMEOUT_SECONDS}s. Terminating.")
                    try:
                        self._current_process.kill()
                        log.info(f"Process for '{self._current_shot_name}' killed.")
                    except Exception as e:
                        log.error(f"Error while trying to kill process for '{self._current_shot_name}': {e}")
                    
                    self.finish_shot(context, self._current_shot_name, success=False, timed_out=True)
                    self._current_process = None
                
                # Check if process has finished
                elif self._current_process.poll() is not None:
                    return_code = self._current_process.returncode
                    elapsed_time = time.time() - self._start_time
                    log.info(f"Process for '{self._current_shot_name}' finished in {elapsed_time:.2f}s with return code: {return_code}.")
                    
                    self.finish_shot(context, self._current_shot_name, success=(return_code == 0))
                    self._current_process = None
            
            # --- If no process is running, start the next one ---
            if not self._current_process:
                if not self._shot_queue:
                    # Batch is complete
                    message = f"Batch complete. Processed {self._processed_shots}/{self._total_shots} shots."
                    log.info(f"BATCH COMPLETE. Processed {self._processed_shots}/{self._total_shots} shots.")
                    bpy.ops.brender.report_finished('INVOKE_DEFAULT', message=message)
                    self.cancel(context) # Final cleanup
                    return {'FINISHED'}
                
                # Pop next shot from the queue
                self._current_shot_name = self._shot_queue.pop(0)
                
                # Find the corresponding shot item from the list
                shot_item = next((s for s in context.scene.brender_shot_list if s.name == self._current_shot_name), None)

                if not shot_item:
                    log.warning(f"Could not find shot item '{self._current_shot_name}' in list. Skipping.")
                    return {'PASS_THROUGH'}

                log.info(f"BATCH ({self._processed_shots + 1}/{self._total_shots}): Starting background process for '{self._current_shot_name}' on frame {shot_item.frame}.")
                
                blender_executable = bpy.app.binary_path
                original_filepath = bpy.data.filepath
                
                py_command = self.PY_COMMAND_TEMPLATE.format(
                    addon_name=__name__,
                    shot_name=self._current_shot_name,
                    shot_frame=shot_item.frame
                )

                command = [blender_executable, "-b", original_filepath, "--python-expr", py_command]
                log.info(f"Executing command for '{self._current_shot_name}'")
                
                try:
                    self._current_process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=False # We handle decoding in the thread
                    )
                    
                    self._log_thread = threading.Thread(target=enqueue_output, args=(self._current_process.stdout, log_queue))
                    self._log_thread.daemon = True 
                    self._log_thread.start()

                    self._start_time = time.time()
                    log.info(f"Process for '{self._current_shot_name}' launched with PID: {self._current_process.pid}")
                except Exception as e:
                    log.error(f"FATAL: Failed to launch background process for '{self._current_shot_name}': {e}")
                    self.report({'ERROR'}, f"Failed to launch process for {self._current_shot_name}")
                    self.finish_shot(context, self._current_shot_name, success=False)
                    self._current_process = None

        return {'PASS_THROUGH'}

    def finish_shot(self, context, shot_name, success=True, timed_out=False):
        """Helper function to perform cleanup after a shot is processed."""
        status = "SUCCESS" if success else ("FAILED" if not timed_out else "TIMED OUT")
        log.info(f"Finishing shot '{shot_name}' with status: {status}.")

        if self._log_thread and self._log_thread.is_alive():
            log.info(f"Waiting for logging thread of '{shot_name}' to complete.")
            self._log_thread.join(timeout=1.0)
        self._log_thread = None

        while not log_queue.empty():
            try:
                print(log_queue.get_nowait(), end='')
            except queue.Empty:
                break

        shot_item = next((s for s in context.scene.brender_shot_list if s.name == shot_name), None)
        if shot_item:
            shot_item.is_selected = False
            log.info(f"Deselected '{shot_name}' from UI list.")
        
        self._processed_shots += 1
        self._current_shot_name = None
        
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def execute(self, context):
        log.info("OPERATOR: Executing 'Prepare Batch From Selection'.")
        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Please save the main project file first.")
            log.error("Batch cancelled: Main file is not saved.")
            return {"CANCELLED"}
            
        log.info("Saving main file before starting batch.")
        bpy.ops.wm.save_mainfile()
        
        selected_shots = [s.name for s in context.scene.brender_shot_list if s.is_selected]
        if not selected_shots:
            self.report({"WARNING"}, "No shots selected from the list.")
            log.warning("Batch cancelled: No shots were selected.")
            return {"CANCELLED"}

        self._shot_queue = selected_shots
        self._total_shots = len(self._shot_queue)
        self._processed_shots = 0
        log.info(f"Initializing batch preparation for {self._total_shots} shots: {self._shot_queue}")

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        log.info("Modal handler added. Batch is now running.")
        
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Cleanup function for the modal operator."""
        log.info("Running cleanup for modal operator.")
        if self._current_process:
            log.warning(f"Cancelling operation. Killing active process for '{self._current_shot_name}' (PID: {self._current_process.pid}).")
            try:
                self._current_process.kill()
                self._current_process = None
            except Exception as e:
                log.error(f"Error killing process during cancel: {e}")
        
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None
            log.info("Modal timer removed.")
        
        self._shot_queue.clear()
        self._current_shot_name = None
        self._log_thread = None
        log.info("Modal operator cancelled and internal state cleaned up.")
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        
class BRENDER_OT_report_finished(bpy.types.Operator):
    """An operator to show a popup message once the batch is done."""
    bl_idname = "brender.report_finished"
    bl_label = "bRender Batch Complete"
    message: bpy.props.StringProperty()

    def execute(self, context):
        self.report({'INFO'}, self.message)
        log.info(f"FINAL REPORT (UI): {self.message}")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.label(text=self.message)

# --- UI PANEL ---

class VIEW3D_PT_brender_panel(bpy.types.Panel):
    bl_label = "bRender"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "bRender"

    def draw(self, context):
        layout = self.layout
        
        box = layout.box()
        box.label(text="Active Shot Rendering", icon="SCENE_DATA")
        shot_info = get_shot_info_from_frame(context)
        if shot_info:
            col = box.column(align=True)
            col.label(text=f"Active Shot: {shot_info['shot_marker'].name}")
            col.label(text=f"Duration: {shot_info['duration']} frames")
            col.separator()
            col.operator(BRENDER_OT_prepare_active_shot.bl_idname, icon="RENDER_ANIMATION")
        else:
            box.label(text="Move playhead over a shot marker.", icon="INFO")
            
        layout.separator()

        box = layout.box()
        box.label(text="Manual File Preparation", icon="FILE_BLEND")
        col = box.column()
        col.operator(BRENDER_OT_prepare_this_file.bl_idname, icon="PREFERENCES")
        col.label(text="Use in a SC##-SH###.blend file")

        layout.separator()

        box = layout.box()
        row = box.row(align=True)
        row.label(text="Batch Shot Preparation", icon="FILE_TICK")
        row.operator(BRENDER_OT_refresh_shot_list.bl_idname, text="", icon="FILE_REFRESH")

        shot_list = context.scene.brender_shot_list
        if shot_list:
            scroll_box = box.box()
            for item in shot_list:
                scroll_box.prop(item, "is_selected", text=item.name)
            box.separator()
            
            is_running = False
            op_props = context.window_manager.operators
            if op_props:
                for op in op_props:
                    if op.bl_idname == BRENDER_OT_prepare_render_batch.bl_idname:
                        is_running = True
                        break

            row = box.row()
            row.enabled = not is_running
            op_text = "Processing Batch..." if is_running else "Prepare Batch"
            row.operator(BRENDER_OT_prepare_render_batch.bl_idname, icon="EXPORT", text=op_text)
        else:
            box.label(text="Click Refresh to find shots.", icon="INFO")

# --- REGISTRATION ---
classes = (
    BRENDER_ShotListItem,
    BRENDER_OT_prepare_active_shot,
    BRENDER_OT_prepare_this_file,
    BRENDER_OT_refresh_shot_list,
    BRENDER_OT_prepare_render_batch,
    BRENDER_OT_report_finished,
    VIEW3D_PT_brender_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.brender_shot_list = bpy.props.CollectionProperty(type=BRENDER_ShotListItem)
    log.info("bRender addon registered successfully.")

def unregister():
    log.info("Unregistering bRender addon.")
    del bpy.types.Scene.brender_shot_list
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    log.info("bRender addon unregistered.")

if __name__ == "__main__":
    register()

