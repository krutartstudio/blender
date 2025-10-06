bl_info = {
    "name": "Krutart Auto-Publisher",
    "author": "iori, Krutart, Gemini",
    "version": (1, 2, 1),
    "blender": (4, 0, 0),
    "location": "Properties > Output Properties > Krutart Auto-Publisher",
    "description": "Adds a panel for incremental saving and making hero files with detailed logging.",
    "warning": "",
    "doc_url": "",
    "category": "Output",
}

import bpy
import os
import re
import logging

# --- Logger Setup ---
# Set up a dedicated logger for this addon to provide clear, detailed feedback.
# This is more robust than using simple print() statements.
logger = logging.getLogger("KrutartAutoPublisher")
logger.setLevel(logging.INFO) # Set the lowest level to capture all messages (INFO, WARNING, ERROR)

# Prevent adding multiple handlers on script reload, which would cause duplicate logs.
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Helper Functions ---

def get_current_filepath():
    """Returns the absolute path of the current Blender file."""
    return bpy.data.filepath

def parse_filename(filepath):
    """
    Parses the filename to extract project name, asset name, flags, and version.
    Handles filenames with comments after the version flag.
    Expected format: PROJECT_NAME-ASSET_NAME-flags-v001-optional_comment.blend
    """
    if not filepath:
        logger.warning("File has not been saved yet. Cannot parse filename.")
        return None, None, None, None

    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    
    # Find the version number flag, e.g., "-v001"
    version_match = re.search(r'-v(\d{3,})', name)
    
    if not version_match:
        logger.warning(f"Filename '{name}' does not contain a version flag like '-v###'.")
        return None, None, None, None

    # Extract version and the part of the name before it
    version_str = version_match.group(1)
    version_int = int(version_str)
    
    before_version_part = name[:version_match.start()]
    
    # Split the pre-version part to get project, asset, and flags
    parts = before_version_part.split('-')
    
    if len(parts) < 3:
        logger.warning(f"Filename '{name}' format is incorrect before version flag. Expected 'PROJECT-ASSET-flags'.")
        return None, None, None, None
        
    flags = parts[-1]
    asset_name = parts[-2]
    project_name = '-'.join(parts[:-2])
    
    logger.info(f"Parsed filename: project='{project_name}', asset='{asset_name}', flags='{flags}', version='{version_str}'")
    return project_name, asset_name, flags, version_int


# --- Operators ---

class KRUTART_OT_save_increment(bpy.types.Operator):
    """Saves the file with an incremented version number and opens it"""
    bl_idname = "krutart.save_increment"
    bl_label = "Save Increment"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        current_filepath = get_current_filepath()
        if not current_filepath:
            self.report({'ERROR'}, "Please save the file first.")
            logger.error("Save Increment failed: File has not been saved yet.")
            return {'CANCELLED'}

        directory = os.path.dirname(current_filepath)
        project, asset, flags, version = parse_filename(current_filepath)

        if version is None:
            self.report({'ERROR'}, "Filename format is incorrect. Expected 'PROJECT-ASSET-flags-v###.blend'")
            logger.error("Save Increment failed: Could not parse filename.")
            return {'CANCELLED'}

        # Increment version
        new_version = version + 1
        new_version_str = f"v{new_version:03d}"
        logger.info(f"Incrementing version from v{version:03d} to {new_version_str}")

        # Get comment
        comment = context.scene.krutart_comment.strip()
        
        # Construct new filename
        base_name = f"{project}-{asset}-{flags}-{new_version_str}"
        
        if comment:
            # Sanitize comment for filename
            sanitized_comment = re.sub(r'[^a-zA-Z0-9_-]', '_', comment)
            new_filename = f"{base_name}-{sanitized_comment}.blend"
            logger.info(f"Comment added: '{comment}', sanitized to '{sanitized_comment}'")
        else:
            new_filename = f"{base_name}.blend"
            logger.info("No comment provided.")

        new_filepath = os.path.join(directory, new_filename)

        logger.info(f"Saving new incremented file to: {new_filepath}")
        
        try:
            # Save the file and make it the active file (no 'copy=True')
            bpy.ops.wm.save_as_mainfile(filepath=new_filepath)
            self.report({'INFO'}, f"Saved and switched to: {new_filename}")
            context.scene.krutart_comment = "" # Clear comment field after save
            logger.info("File saved and opened successfully.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save file: {e}")
            logger.error(f"An exception occurred during file save: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

class KRUTART_OT_make_hero(bpy.types.Operator):
    """Saves a 'hero' version of the file in the corresponding LIBRARY-HERO directory"""
    bl_idname = "krutart.make_hero"
    bl_label = "Make Hero"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        current_filepath = get_current_filepath()
        if not current_filepath:
            self.report({'ERROR'}, "Please save the file first.")
            logger.error("Make Hero failed: File has not been saved yet.")
            return {'CANCELLED'}

        logger.info("-" * 50)
        logger.info(f"Starting 'Make Hero' process for: {current_filepath}")

        try:
            # 1. Go three directories up
            work_dir = os.path.dirname(current_filepath)
            asset_work_dir = os.path.basename(work_dir) # e.g., ASSET_NAME-WORK
            parent_dir_1 = os.path.dirname(work_dir) # e.g., ./MODEL-WORK
            parent_dir_2 = os.path.dirname(parent_dir_1) # e.g., ./LIBRARY-WORK
            
            logger.info(f"Current work directory: {work_dir}")
            logger.info(f"Navigated up two levels to: {parent_dir_1}")
            logger.info(f"Navigated up three levels to base: {parent_dir_2}")
            
            # Check if we are in a 'WORK' structure
            if not parent_dir_2.endswith('-WORK'):
                self.report({'ERROR'}, "Not in a recognized '...-WORK' directory structure.")
                logger.error(f"Path '{parent_dir_2}' does not end with '-WORK'. Aborting.")
                return {'CANCELLED'}
                
            # 2. Construct HERO path
            hero_base_path = parent_dir_2.replace('-WORK', '-HERO')
            asset_type_dir_name = os.path.basename(parent_dir_1) # e.g., MODEL-WORK
            hero_asset_type_path = os.path.join(hero_base_path, asset_type_dir_name.replace('-WORK', '-HERO'))
            
            project, asset, flags, version = parse_filename(current_filepath)
            if asset is None:
                self.report({'ERROR'}, "Filename format is incorrect. Cannot determine asset name.")
                logger.error("Make Hero failed: could not parse asset name from filename.")
                return {'CANCELLED'}
            
            # Use the name of the current directory (e.g., 'MOD-TANK-WORK') and replace -WORK with -HERO
            if not asset_work_dir.endswith('-WORK'):
                self.report({'ERROR'}, f"Parent directory '{asset_work_dir}' does not follow the '...-WORK' naming convention.")
                logger.error(f"Parent directory '{asset_work_dir}' does not end with '-WORK'. Aborting.")
                return {'CANCELLED'}
            hero_asset_dir_name = asset_work_dir.replace('-WORK', '-HERO')
            hero_asset_dir_path = os.path.join(hero_asset_type_path, hero_asset_dir_name)
            
            logger.info(f"Constructed Hero base path: {hero_base_path}")
            logger.info(f"Constructed Hero asset type path: {hero_asset_type_path}")
            logger.info(f"Constructed Final Hero asset directory: {hero_asset_dir_path}")

            # 3. Create hero directory if it doesn't exist
            if not os.path.exists(hero_asset_dir_path):
                logger.info(f"Hero asset directory does not exist. Creating: {hero_asset_dir_path}")
                try:
                    os.makedirs(hero_asset_dir_path)
                    logger.info("Directory created successfully.")
                except OSError as e:
                    self.report({'ERROR'}, f"Could not create directory: {e}")
                    logger.error(f"Failed to create directory '{hero_asset_dir_path}': {e}")
                    return {'CANCELLED'}
            else:
                logger.info("Hero asset directory already exists.")

            # 4. Construct hero filename and save
            hero_filename = f"{project}-{asset}-{flags}-HERO.blend"
            hero_filepath = os.path.join(hero_asset_dir_path, hero_filename)

            logger.info(f"Attempting to save Hero file to: {hero_filepath}")
            
            try:
                # Save a copy, so the current open file remains the work file
                bpy.ops.wm.save_as_mainfile(filepath=hero_filepath, copy=True)
                self.report({'INFO'}, f"Saved Hero file: {hero_filename}")
                logger.info(f"Hero file successfully saved at path: {hero_filepath}")
                logger.info("-" * 50)

            except Exception as e:
                self.report({'ERROR'}, f"Failed to save hero file: {e}")
                logger.error(f"An exception occurred during hero file save: {e}")
                return {'CANCELLED'}

        except Exception as e:
            self.report({'ERROR'}, f"An unexpected error occurred: {e}")
            logger.critical(f"An unexpected error in Make Hero logic: {e}", exc_info=True)
            return {'CANCELLED'}

        return {'FINISHED'}

# --- UI Panel ---

class KRUTART_PT_autopublisher_panel(bpy.types.Panel):
    """Creates a Panel in the Output Properties window"""
    bl_label = "KRUTART-AUTOPUBLISHER"
    bl_idname = "OUTPUT_PT_krutart_autopublisher"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Check if the file has been saved in the correct format
        is_valid_file = False
        if bpy.data.is_saved:
            _, _, _, version = parse_filename(get_current_filepath())
            if version is not None:
                is_valid_file = True
        
        if not bpy.data.is_saved:
            layout.label(text="Save file to enable addon.", icon='ERROR')
            return

        if not is_valid_file:
            box = layout.box()
            box.label(text="Filename format is incorrect!", icon='ERROR')
            box.label(text="Expected: PROJECT-ASSET-flags-v###.blend")
            return
            
        # --- Save Increment Section ---
        box = layout.box()
        box.label(text="Incremental Save", icon='FILE_NEW')
        box.prop(scene, "krutart_comment", text="Comment")
        box.operator(KRUTART_OT_save_increment.bl_idname)
        
        layout.separator()

        # --- Make Hero Section ---
        box = layout.box()
        box.label(text="Hero File", icon='OUTLINER_OB_ARMATURE') # Using a star-like icon
        box.operator(KRUTART_OT_make_hero.bl_idname)

# --- Registration ---

classes = (
    KRUTART_OT_save_increment,
    KRUTART_OT_make_hero,
    KRUTART_PT_autopublisher_panel,
)

def register():
    logger.info("Registering Krutart Auto-Publisher Addon")
    for cls in classes:
        bpy.utils.register_class(cls)
    # Add the comment property to the scene
    bpy.types.Scene.krutart_comment = bpy.props.StringProperty(
        name="Comment",
        description="Comment to append to the incremental save filename",
        default="",
    )

def unregister():
    logger.info("Unregistering Krutart Auto-Publisher Addon")
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    # Remove the comment property
    del bpy.types.Scene.krutart_comment

if __name__ == "__main__":
    register()

