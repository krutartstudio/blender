import os
import platform
import importlib.util

STRIP_DATA_FILENAME = 'strip_data.txt'

# Configurable through environment variables
DEADLINE_DISC = '//172.16.20.2/DEADLINE' if platform.system() == 'Windows' else '/Volumes/DEADLINE'
DEADLINE_FOLDER = os.path.join(DEADLINE_DISC, 'KSVE').replace('/', os.sep)

# Load environment overrides
if importlib.util.find_spec('dotenv'):
    import dotenv
    dotenv.load_dotenv()
    if os.getenv('KA_DEADLINE_FOLDER'):
        DEADLINE_FOLDER = os.getenv('KA_DEADLINE_FOLDER')
