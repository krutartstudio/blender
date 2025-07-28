import os
import re
from ..core import logging
from ..core import constants

log_fn_call = logging.log_fn_call

@log_fn_call
def get_filefolder(filepath):
    return os.path.dirname(filepath)

@log_fn_call
def get_filename(filepath):
    return os.path.basename(filepath).replace('.blend', '')

@log_fn_call
def create_folder_if_missing(folder_path):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

@log_fn_call
def check_latest_folder_version(main_folder: str, folder_name: str) -> int:
    version = 0
    existing_folders = os.listdir(main_folder)

    if os.path.isdir(os.path.join(main_folder, folder_name)):
        version = 1

    for folder in existing_folders:
        folder_path = os.path.join(main_folder, folder)
        if not os.path.isdir(folder_path):
            continue

        m = re.match(folder_name + r"_(\d{2})", folder)
        if m:
            version = max(version, int(m.group(1)))

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
    return os.path.exists(os.path.join(publish_dir, constants.STRIP_DATA_FILENAME))

@log_fn_call
def change_x_path_to_k(filepath):
    replace_regex = r'.*[\\/](?P<phase>[0-9]{2}_[A-Z]+)[\\/]'
    return re.sub(replace_regex, r'K:' + re.escape(os.sep) + r'\g<phase>' + re.escape(os.sep), filepath)
