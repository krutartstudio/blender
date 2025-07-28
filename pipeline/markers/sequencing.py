import bpy
from ...core.types import ProjectParts, MarkerType

def flush_markers():
    for marker in list(bpy.context.scene.timeline_markers):
        bpy.context.scene.timeline_markers.remove(marker)

def create_outer_marker(project_parts: ProjectParts, kind: MarkerType):
    name = f'CAM_{project_parts["scene_number"]}_{project_parts["environment_name"]}_{project_parts["shot"]}_{kind}'
    frame = bpy.context.scene.frame_start if kind == 'START' else bpy.context.scene.frame_end
    return bpy.context.scene.timeline_markers.new(name=name, frame=frame)

def create_inner_marker(kind: MarkerType, frame: int):
    return bpy.context.scene.timeline_markers.new(name=kind, frame=frame)

def find_fulldome_camera():
    return next((obj for obj in bpy.context.scene.objects
                if obj.type == 'CAMERA' and 'FULLDOME' in obj.name), None)
