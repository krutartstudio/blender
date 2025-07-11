
import os
import re
from uu import Error

from .filesystem import get_filename
from .types import ProjectParts


def get_project_parts(filepath: str) -> ProjectParts:
    """
    Examples:
    /xxx/06_PRODUCTION/SC030_PLAYGROUND/SH010/03_ANIMATION/sc030_playground_sh010_animation_v001.blend
    C:\\/xxx\\06_PRODUCTION\\SC030_PLAYGROUND\\SH010\\03_ANIMATION\\sc030_playground_sh010_animation_v001.blend
    ->
        scene: SC030_PLAYGROUND
        scene_number: 030
        shot: SH010
        shot_number: 010
        shot_id: sc030-playground-sh010
        stage: 03_ANIMATION
        stage_number: 03
        stage_name: ANIMATION
        environment_name: PLAYGROUND
        workfile: sc030_playground_sh010_animation_v001
        workfile_name: sc030_playground_sh010_animation
        workfile_version: 001

    /xxx/06_PRODUCTION/SC070_BRANCH/SH070/07_FINAL/ART/sc070_branch_sh070_finalart_v002.blend
    ->
        scene: SC070_BRANCH
        scene_number: 070
        shot: SH070
        shot_number: 070
        shot_id: sc070-branch-sh070
        stage: 07_FINAL
        stage_number: 07
        stage_name: FINAL
        environment_name: BRANCH
        workfile: sc070_branch_sh070_finalart_v002
        workfile_name: sc070_branch_sh070_finalart
        workfile_version: 002

        // TODO: for new system:
        ===> ksve-sc150-sh080-finalart-v001(-light)
        ===> stage budou "ANIM, ART, VFX, SETDRESS, FINAL,..."
        ===> stage vzdy jen jedna slozka
        ===> stage bez cisla
        ===> pouzivat pomlcky
        ===> v ceste je navic "WORKFILE"
        ===> u branch neni _PLAYGROUND
        ===> na konci file muze byt poznamka
        ===> /xxx/KSVE_PRODUCTION/SC070/SH070/WORKFILE/FINAL/ksve-sc150-sh080-finalart-v001-light.blend

        ===> pridat check jestli filename/path je ok
        ===> hezky error, https://blender.stackexchange.com/questions/109711/how-to-popup-simple-message-box-from-python-console

        ===> testovat na 4.2

        ===> dynamicke buttony.. buttons="2K,2048,2048|4K,2048,2048"
    """

    sep = os.sep

    regex = re.escape(
        sep) + "(SC[0-9]{3}_[a-zA-Z]+)"+re.escape(
        sep)+"(SH[0-9]{3})"+re.escape(
        sep)+"([0-9]{2}_[a-zA-Z]+)"+re.escape(
        sep)

    parts = re.search(
        regex, filepath)
    if not parts:
        raise Error('get_project_parts: no match found')

    stage = parts.group(3)
    stage_parts = stage.split('_')

    workfile = get_filename(filepath)
    workfile_parts = re.search(
        "^(.*)_v([0-9]{3}[a-zA-Z_-]*)$", workfile)

    if not workfile_parts:
        raise Error('get_project_parts: workfile_parts failed')

    return {
        'scene': parts.group(1),
        'scene_number': parts.group(1).split('_')[0].replace('SC', ''),
        'shot': parts.group(2),
        'shot_number': parts.group(2).replace('SH', ''),
        'shot_id': '-'.join(workfile_parts.group(1).split('_')[0:3]),
        'stage': stage,
        'stage_number': stage_parts[0],
        'stage_name': stage_parts[1],
        'environment_name': parts.group(1).split('_')[1],
        'workfile': workfile,
        'workfile_name': workfile_parts.group(1),
        'workfile_version': workfile_parts.group(2),
    }
