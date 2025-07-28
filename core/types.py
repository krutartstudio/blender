from typing import TypedDict, Literal

class ProjectParts(TypedDict):
    scene: str
    scene_number: str
    shot: str
    shot_number: str
    shot_id: str
    stage: str
    stage_number: str
    stage_name: str
    environment_name: str
    workfile: str
    workfile_name: str
    workfile_version: str

MarkerType = Literal['START', 'END', 'IN', 'OUT']
