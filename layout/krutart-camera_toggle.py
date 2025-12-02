bl_info = {
    "name": "Shot Camera Switcher",
    "author": "iori, Krutart, Gemini",
    "version": (1, 1),
    "blender": (4, 5, 0),
    "location": "3D Viewport > Header (Top bar)",
    "description": "Toggle all shot markers between FLAT and FULLDOME cameras.",
    "warning": "",
    "doc_url": "",
    "category": "Scene",
}

import bpy
from bpy.app.handlers import persistent

def update_all_shot_cameras(self, context):
    """
    This is the main "Switcher" function.
    It iterates through all scenes and all markers,
    then re-binds the correct camera based on the new toggle state.
    """
    if not context.scene:
        return

    # Get the desired camera type from the toggle ('FLAT' or 'FULLDOME')
    camera_suffix = context.scene.shot_camera_toggle
    
    print(f"--- Shot Camera Switcher: Setting all markers to '{camera_suffix}' ---")

    # Iterate through ALL scenes in the .blend file
    for scene in bpy.data.scenes:
        
        # Iterate through all timeline markers in that scene
        for marker in scene.timeline_markers:
            
            # Check if the marker name matches the shot pattern
            if marker.name.startswith("CAM-SC"):
                shot_name = marker.name
                target_cam_name = f"{shot_name}-{camera_suffix}"
                
                # Find the camera object in Blender's master data
                target_cam_obj = bpy.data.objects.get(target_cam_name)
                
                if target_cam_obj and target_cam_obj.type == 'CAMERA':
                    # Successfully found the camera. Bind it to the marker.
                    marker.camera = target_cam_obj
                    print(f"  [{scene.name}] Bound marker '{shot_name}' to camera '{target_cam_name}'")
                else:
                    # Could not find the target camera.
                    # Unbind the camera from the marker to avoid errors.
                    marker.camera = None
                    print(f"  ! WARNING: [{scene.name}] Could not find camera '{target_cam_name}' for marker '{shot_name}'.")

    # After updating bindings, force the frame change handler to run
    # to update the active camera immediately.
    if bpy.context.scene:
        on_frame_change(bpy.context.scene)
    
    return None


@persistent
def on_frame_change(scene):
    """
    This handler runs every time the frame changes.
    It finds the "active" marker for the current frame
    and sets the scene's camera to match the one bound to that marker.
    """
    
    # We only care about the currently active scene
    if not scene == bpy.context.scene:
        return

    current_frame = scene.frame_current
    active_marker = None

    # Find the last marker at or before the current frame
    # We sort by frame number to ensure we get the correct one.
    sorted_markers = sorted(scene.timeline_markers, key=lambda m: m.frame)
    
    for marker in sorted_markers:
        if marker.frame <= current_frame:
            active_marker = marker
        else:
            # We've gone past the current frame, so we can stop.
            break
            
    if active_marker and active_marker.camera:
        # If we found an active marker and it has a camera bound to it,
        # set it as the scene's active camera.
        if scene.camera != active_marker.camera:
            scene.camera = active_marker.camera
            
    # If no marker is active (e.g., before the first shot),
    # the scene camera simply remains unchanged.


def draw_camera_toggle(self, context):
    """
    This function draws the FLAT / FULLDOME toggle
    in the 3D Viewport's header.
    """
    layout = self.layout
    scene = context.scene
    
    # Draw the EnumProperty.
    # 'expand=True' makes it show up as buttons (FLAT | FULLDOME)
    # 'text=""' removes the "Camera Type:" label to save space
    layout.prop(scene, "shot_camera_toggle", text="", expand=True)


@persistent
def on_file_loaded(dummy):
    """
    Runs after a .blend file is loaded.
    This is a safe place to run the initial camera sync.
    """
    print("--- Shot Camera Switcher: File loaded, running initial sync ---")
    # By this point, context is guaranteed to be valid.
    if bpy.context.scene:
        # We can't pass context directly, so we call the function
        # and it will read the context itself.
        update_all_shot_cameras(bpy.context.scene, bpy.context)


# --- REGISTRATION ---

def register():
    # 1. Create the main property on the Scene type
    # This property will store the state of the toggle.
    # The 'update' function is the most important part:
    # it triggers our 'update_all_shot_cameras' logic.
    bpy.types.Scene.shot_camera_toggle = bpy.props.EnumProperty(
        name="Shot Camera Type",
        description="Switch all shot markers to use FLAT or FULLDOME cameras",
        items=[
            ('FLAT', "Flat", "Use all FLAT cameras"),
            ('FULLDOME', "Fulldome", "Use all FULLDOME cameras")
        ],
        default='FLAT',
        update=update_all_shot_cameras
    )
    
    # 2. Add the UI draw function to the 3D Viewport Header
    bpy.types.VIEW3D_HT_header.append(draw_camera_toggle)
    
    # 3. Add the frame change handler
    if on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(on_frame_change)
        
    # 4. Add a handler to sync cameras when a file is loaded
    # This replaces the old method of running on registration,
    # which caused a context error.
    if on_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_file_loaded)


def unregister():
    # 1. Remove the frame change handler
    if on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(on_frame_change)
        
    # 2. Remove the UI draw function
    bpy.types.VIEW3D_HT_header.remove(draw_camera_toggle)
    
    # 3. Remove the file loaded handler
    if on_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_file_loaded)

    # 4. Delete the property from the Scene type
    del bpy.types.Scene.shot_camera_toggle


if __name__ == "__main__":
    register()

