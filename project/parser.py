import os
import re
from ..core.types import ProjectParts
from ..io import filesystem

def get_project_parts(filepath: str) -> ProjectParts:
    sep = os.sep
    regex = (
        re.escape(sep) + r"(SC\d{3}_[A-Za-z]+)" +
        re.escape(sep) + r"(SH\d{3})" +
        re.escape(sep) + r"(\d{2}_[A-Za-z]+)" +
        re.escape(sep)

    match = re.search(regex, filepath)
    if not match:
        raise ValueError("Invalid project path structure")

    scene, shot, stage = match.groups()
    workfile = filesystem.get_filename(filepath)
    workfile_match = re.match(r"^(.+?)_v(\d+)$", workfile)

    if not workfile_match:
        raise ValueError("Invalid workfile naming convention")

    workfile_name, workfile_version = workfile_match.groups()
    scene_number = scene[2:5]
    shot_number = shot[2:5]
    stage_number, stage_name = stage.split('_', 1)
    environment_name = scene.split('_', 1)[1]

    return {
        'scene': scene,
        'scene_number': scene_number,
        'shot': shot,
        'shot_number': shot_number,
        'shot_id': f"{scene.lower()}-{shot.lower()}",
        'stage': stage,
        'stage_number': stage_number,
        'stage_name': stage_name,
        'environment_name': environment_name,
        'workfile': workfile,
        'workfile_name': workfile_name,
        'workfile_version': workfile_version,
    }
