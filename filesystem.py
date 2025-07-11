import os
import re

from .constants import STRIP_DATA_FILENAME
from .logging import log_fn_call


@log_fn_call
def get_filefolder(filepath):
    return os.path.dirname(filepath)


@log_fn_call
def get_filename(filepath):
    return os.path.basename(filepath).replace('.blend', '')


@log_fn_call
def create_folder_if_missing(folder_path):
    exists = os.path.exists(folder_path)
    if not exists:
        print('created folder', folder_path)
        os.makedirs(folder_path)


@log_fn_call
def check_latest_folder_version(main_folder: str, folder_name: str) -> int:
    """
        Finds highest version folder within another folder.
        E.g. in /xxx/F_01, /xxx/F_02, /xxx/F_03 returns 3
        E.g. in /xxx/ returns None
    """
    version = 0

    existing_folders = os.listdir(main_folder)

    if os.path.isdir(os.path.join(main_folder, folder_name)):
        version = 1

    for folder in existing_folders:
        if not os.path.isdir(os.path.join(main_folder, folder)):
            continue

        m = re.match(folder_name + r"_(\d{2})", folder)
        if not m:
            continue

        v = m.group(1)
        version = max(version, int(v))

    return version


@log_fn_call
def get_phase_dir(filepath: str, phase):
    directory = os.path.dirname(filepath)
    replace_regex = r"\1" + re.escape(os.sep) + phase
    new_dir = re.sub(r'(SH\d{3}).*', replace_regex, directory)
    create_folder_if_missing(new_dir)
    return new_dir


@log_fn_call
def strip_data_file_exists(filepath: str):
    publish_dir = get_phase_dir(filepath, '00_PUBLISH')
    return os.path.exists(publish_dir + os.sep + STRIP_DATA_FILENAME)


@log_fn_call
def change_x_path_to_k(filepath):
    replace_regex = r'.*[\\/](?P<phase>[0-9]{2}_[A-Z]+)[\\/]'
    return re.sub(replace_regex, r'K:' + re.escape(os.sep) + r'\g<phase>' + re.escape(os.sep), filepath)
