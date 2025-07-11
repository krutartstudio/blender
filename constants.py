import importlib.util
import os
import platform

DEADLINE_DISC = '//172.16.20.2/DEADLINE' if platform.system(
) == 'Windows' else '/Volumes/DEADLINE'
DEADLINE_FOLDER = DEADLINE_DISC + '/KSVE/'
DEADLINE_FOLDER = DEADLINE_FOLDER.replace('/', os.sep)

DEADLINE_DISC_LOCAL = 'D:' if platform.system(
) == 'Windows' else '/Volumes/DEADLINE'
DEADLINE_FOLDER_LOCAL = DEADLINE_DISC_LOCAL + '/KSVE/'

STRIP_DATA_FILENAME = 'strip_data.txt'

dotenv_exists = importlib.util.find_spec('dotenv')

if dotenv_exists:
    import dotenv
    dotenv.load_dotenv()
    KA_DEADLINE_FOLDER = os.getenv('KA_DEADLINE_FOLDER')
    if KA_DEADLINE_FOLDER:
        DEADLINE_FOLDER = KA_DEADLINE_FOLDER
