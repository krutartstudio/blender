from typing import Literal

import bpy

from .types import ProjectParts


def flush_markers():
    # Get all the markers from the current scene
    markers = bpy.context.scene.timeline_markers

    # Remove all markers
    for marker in markers:
        markers.remove(marker)


def create_outer_marker(project_parts: ProjectParts, kind: Literal['START', 'END']):
    """
    Name:
        CAM_070_BRANCH_SH050_START
        CAM_070_BRANCH_SH050_END
    """
    name = f'CAM_{project_parts["scene_number"]}_{project_parts["environment_name"]}_{project_parts["shot"]}_{kind}'
    frame = bpy.context.scene.frame_start if kind == 'START' else bpy.context.scene.frame_end
    return bpy.context.scene.timeline_markers.new(
        name=name, frame=frame)


def create_inner_marker(kind: Literal['IN', 'OUT'], frame: int):
    """
    Name:
        IN
        OUT
    """
    bpy.context.scene.timeline_markers.new(
        name=kind, frame=frame)


def find_fulldome_camera():
    for obj in bpy.context.scene.objects:
        if obj.type == 'CAMERA' and 'FULLDOME' in obj.name:
            return obj
