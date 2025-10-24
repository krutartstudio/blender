import bpy
import subprocess
import sys
import os
import threading
import queue

# bl_info is required for Blender to recognize the script as an addon.
bl_info = {
    "name": "Subprocess Logger",
    "author": "Gemini",
    "version": (1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > My Subprocess Tab",
    "description": "Starts a subprocess and logs its output.",
    "warning": "",
    "doc_url": "",
    "category": "Development",
}

# This queue will hold the output from the subprocess.
# It's thread-safe, which is important because the subprocess output
# will be read in a separate thread.
output_queue = queue.Queue()

def enqueue_output(out, q):
    """
    This function runs in a separate thread and reads output from the subprocess.
    It places each line of output into the queue.
    """
    for line in iter(out.readline, b''):
        q.put(line.decode())
    out.close()

class WM_OT_RunSubprocess(bpy.types.Operator):
    """Tooltip for the operator"""
    bl_idname = "wm.run_subprocess"
    bl_label = "Run Subprocess and Log"

    _timer = None
    process = None
    thread = None

    def modal(self, context, event):
        """
        The modal operator checks the queue for new output from the subprocess
        at regular intervals without blocking Blender's main thread.
        """
        if event.type == 'TIMER':
            # Check if the subprocess has terminated.
            if self.process and self.process.poll() is not None:
                self.cancel(context)
                self.report({'INFO'}, "Subprocess finished.")
                return {'FINISHED'}

            # Process all messages in the queue.
            while not output_queue.empty():
                try:
                    line = output_queue.get_nowait()
                    print("Subprocess:", line, end='')
                except queue.Empty:
                    pass

        return {'PASS_THROUGH'}

    def execute(self, context):
        """
        This method is called when the button is pressed.
        It sets up and starts the subprocess and the modal timer.
        """
        # Get the path to the script to run as a subprocess.
        # It's placed in the same directory as the addon file.
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        script_path = os.path.join(addon_dir, "external_script.py")

        if not os.path.exists(script_path):
            self.report({'ERROR'}, f"External script not found at {script_path}")
            return {'CANCELLED'}

        # We use sys.executable to ensure the script runs with the same
        # Python interpreter that Blender is using.
        try:
            self.process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False # We handle decoding in the thread
            )
        except Exception as e:
            self.report({'ERROR'}, f"Failed to start subprocess: {e}")
            return {'CANCELLED'}

        # Start a separate thread to read the subprocess's output.
        # This prevents the main Blender thread from blocking while waiting for output.
        self.thread = threading.Thread(target=enqueue_output, args=(self.process.stdout, output_queue))
        self.thread.daemon = True  # Allows Blender to exit without waiting for the thread
        self.thread.start()

        # Add a timer to the window manager to periodically check for new output.
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)

        self.report({'INFO'}, "Subprocess started.")
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Clean up the timer when the operator is finished."""
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        # Ensure process is terminated if it's still running
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait()
        self.process = None
        self.thread = None

class VIEW3D_PT_SubprocessPanel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport"""
    bl_label = "Subprocess Logger"
    bl_idname = "VIEW3D_PT_subprocess_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'My Subprocess Tab'

    def draw(self, context):
        """Defines the panel's layout."""
        layout = self.layout
        row = layout.row()
        row.operator("wm.run_subprocess")

# A list of all classes that need to be registered with Blender.
classes = [
    WM_OT_RunSubprocess,
    VIEW3D_PT_SubprocessPanel,
]

def register():
    """This function is called when the addon is enabled."""
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    """This function is called when the addon is disabled."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

# This allows the script to be run directly in Blender's text editor
# to test the addon.
if __name__ == "__main__":
    register()
