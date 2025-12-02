bl_info = {
    "name": "Krutart Publisher",
    "author": "iori, Krutart, Gemini",
    "version": (1, 4, 9),  # Updated version for this fix
    "blender": (4, 0, 0),
    "location": "Properties > Output Properties > Krutart Publisher",
    "description": "Streamlines incremental saving and hero file creation with detailed logging. Requires comment for actions.",
    "warning": "",
    "doc_url": "",
    "category": "Output",
}

import bpy
import os
import re
import logging
import shutil

# --- Logger Setup ---
# Define logger at the module level
# Configuration will be handled in register() to ensure it works on reload
logger = logging.getLogger("KrutartAutoPublisher")
# ---

# --- Helper Functions ---

def get_current_filepath():
    """Returns the absolute path of the current Blender file."""
    return bpy.data.filepath

def parse_filename(filepath):
    """
    Parses the filename to extract project name, asset name, flags, and version.
    This function is case-insensitive and returns all parts in lowercase.
    Expected format: PROJECT_NAME-ASSET_NAME-flags-v001-optional_comment.blend
    OR
    Expected format: PROJECT_NAME-ASSET_NAME-v001-optional_comment.blend
    """
    if not filepath:
        logger.warning("File has not been saved yet. Cannot parse filename.")
        return None, None, None, None

    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    
    # Make parsing case-insensitive by converting to lowercase
    name_lower = name.lower()
    
    # Find the version number flag, e.g., "-v001"
    version_match = re.search(r'-v(\d{3,})', name_lower)
    
    if not version_match:
        logger.warning(f"Filename '{name}' does not contain a version flag like '-v###'.")
        return None, None, None, None

    # Extract version and the part of the name before it
    version_str = version_match.group(1)
    version_int = int(version_str)
    
    before_version_part = name_lower[:version_match.start()]
    
    # Split the pre-version part to get project, asset, and flags
    parts = before_version_part.split('-')
    
    # --- MODIFIED LOGIC (v1.4.1) ---
    # Check for at least 2 parts (PROJECT-ASSET)
    if len(parts) < 2:
        logger.warning(f"Filename '{name}' format is incorrect before version flag. Expected 'PROJECT-ASSET-flags' or 'PROJECT-ASSET'.")
        return None, None, None, None
    
    if len(parts) == 2:
        # Format: PROJECT-ASSET-v### (e.g., "3212-layout_moon_d-v067")
        project_name = parts[0]
        asset_name = parts[1]
        flags = "" # No flags provided
        logger.info("Parsed format: PROJECT-ASSET")
    else:
        # Format: PROJECT-ASSET-flags-v### (or PROJECT-MORE-ASSET-flags-v###)
        flags = parts[-1]
        asset_name = parts[-2]
        project_name = '-'.join(parts[:-2])
        logger.info("Parsed format: PROJECT-ASSET-flags")
    # --- END MODIFIED LOGIC ---
    
    logger.info(f"Parsed filename: project='{project_name}', asset='{asset_name}', flags='{flags}', version='{version_str}'")
    return project_name, asset_name, flags, version_int


# --- Operators ---

class KRUTART_OT_save_increment(bpy.types.Operator):
    """Saves the file with an incremented version number and opens the new file"""
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
            self.report({'ERROR'}, "Filename format incorrect. Expected 'PROJECT-ASSET-[flags]-v###.blend'")
            logger.error("Save Increment failed: Could not parse filename.")
            return {'CANCELLED'}

        # Increment version
        new_version = version + 1
        new_version_str = f"v{new_version:03d}"
        logger.info(f"Incrementing version from v{version:03d} to {new_version_str}")

        # Get comment
        comment = context.scene.krutart_comment.strip()
        
        # --- MODIFIED: Check for comment ---
        if not comment:
            self.report({'ERROR'}, "Comment is required to save increment.")
            logger.error("Save Increment failed: No comment provided.")
            return {'CANCELLED'}
        # --- END MODIFICATION ---
        
        # Construct new filename
        # Conditionally add flags part only if it exists
        if flags:
            base_name = f"{project}-{asset}-{flags}-{new_version_str}"
        else:
            base_name = f"{project}-{asset}-{new_version_str}"
        
        # Sanitize comment for filename
        sanitized_comment = re.sub(r'[^a-zA-Z0-9_-]', '_', comment)
        new_filename = f"{base_name}-{sanitized_comment}.blend"
        logger.info(f"Comment added: '{comment}', sanitized to '{sanitized_comment}'")

        # Ensure filename is lowercase
        new_filepath = os.path.join(directory, new_filename.lower())

        logger.info(f"Saving new incremented file to: {new_filepath}")
        
        try:
            # Save the file and make it the active file
            bpy.ops.wm.save_as_mainfile(filepath=new_filepath)
            self.report({'INFO'}, f"Saved and switched to: {os.path.basename(new_filepath)}")
            context.scene.krutart_comment = "" # Clear comment field after save
            logger.info(f"File saved and opened successfully: {new_filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save file: {e}")
            logger.error(f"An exception occurred during file save: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

class KRUTART_OT_make_hero(bpy.types.Operator):
    """Saves the current file, creates a 'hero' copy, then saves an incremented version of the work file."""
    bl_idname = "krutart.make_hero"
    bl_label = "Make hero"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        logger.info("-" * 50)
        logger.info("Starting 'Make hero' process...")

        # --- Preliminary Checks ---
        current_filepath = get_current_filepath()
        if not current_filepath:
            self.report({'ERROR'}, "Please save the file first.")
            logger.error("Make Hero failed: File has not been saved yet.")
            return {'CANCELLED'}

        project, asset, flags, version = parse_filename(current_filepath)
        if version is None:
            self.report({'ERROR'}, "Filename format incorrect. Expected 'PROJECT-ASSET-[flags]-v###.blend'")
            logger.error("Make Hero failed: Could not parse filename.")
            return {'CANCELLED'}

        # --- MODIFIED: Check for comment ---
        comment = context.scene.krutart_comment.strip()
        if not comment:
            self.report({'ERROR'}, "Comment is required to make hero.")
            logger.error("Make Hero failed: No comment provided.")
            return {'CANCELLED'}
        # --- END MODIFICATION ---

        # Define hero_filepath here to make it available for the final report
        hero_filepath = "[not saved]" # Initialize with a default/error string
        # This will be used by Step 3, so we define it early
        hero_asset_dir_path = "[not set]" 

        # --- Step 1: Normal save of current file ---
        try:
            logger.info(f"Step 1/4: Performing a normal save of the current file: {os.path.basename(current_filepath)}")
            bpy.ops.wm.save_mainfile()
            saved_work_filepath = get_current_filepath()
            logger.info(f"Step 1/4: Successfully saved current file to: {saved_work_filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save current file: {e}")
            logger.error(f"An exception occurred during initial save: {e}", exc_info=True)
            return {'CANCELLED'}

        # --- Step 2: Create Hero File (as a copy) ---
        try:
            logger.info(f"Step 2/4: Creating Hero file from: {os.path.basename(saved_work_filepath)}")
            work_dir = os.path.dirname(saved_work_filepath)
            
            # --- UPDATED LOGIC (v1.4.4) ---
            # Remove the '$' anchor to replace ALL occurrences of '-work'
            hero_asset_dir_path = re.sub(r'-work', '-HERO', work_dir, flags=re.IGNORECASE)

            if work_dir.lower() == hero_asset_dir_path.lower():
                # Updated error message to be more specific
                error_msg = "Could not find any '-work' directories in the path to convert to '-HERO'."
                self.report({'ERROR'}, error_msg)
                logger.error(f"{error_msg} Original path: {work_dir}")
                return {'CANCELLED'}
            # --- END UPDATED LOGIC ---

            logger.info(f"Transformed WORK path '{work_dir}' to HERO path '{hero_asset_dir_path}'")

            if not os.path.exists(hero_asset_dir_path):
                logger.info(f"Creating missing hero directory: {hero_asset_dir_path}")
                os.makedirs(hero_asset_dir_path, exist_ok=True)

            # Conditionally add flags to hero filename
            if flags:
                hero_filename = f"{project}-{asset}-{flags}-hero.blend"
            else:
                hero_filename = f"{project}-{asset}-hero.blend"
                
            hero_filepath = os.path.join(hero_asset_dir_path, hero_filename.lower())

            logger.info(f"Attempting to save Hero file copy to: {hero_filepath}")
            # Using copy=True saves the file without making it the active file in Blender
            bpy.ops.wm.save_as_mainfile(filepath=hero_filepath, copy=True)
            logger.info(f"Hero file successfully saved to {hero_filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create Hero file: {e}")
            logger.critical(f"An unexpected error in Hero creation logic: {e}", exc_info=True)
            return {'CANCELLED'}

        # --- Step 3: Failsafe copy of blender_assets.cats.txt (v1.4.9) ---
        try:
            logger.info("Step 3/4: Searching for 'blender_assets.cats.txt' in parent 'LIBRARY-WORK' folder...")
            
            # 1. Get current .blend directory
            current_blend_dir = os.path.dirname(saved_work_filepath)

            # 2. Find Source 'LIBRARY-WORK'
            source_library_dir = None
            temp_path = current_blend_dir
            
            # Limit search depth to 10 levels up to prevent infinite loops
            for _ in range(10): 
                # Check if the base folder name is 'library-work'
                if os.path.basename(temp_path).lower() == 'library-work':
                    source_library_dir = temp_path
                    logger.info(f"Found 'LIBRARY-WORK' directory at: {source_library_dir}")
                    break
                
                parent_path = os.path.dirname(temp_path)
                if parent_path == temp_path: # We've hit the root (e.g., S:\)
                    break
                temp_path = parent_path

            # 5. Failsafe Checks & Copy Logic
            if not source_library_dir:
                # This is a warning, not an error. The hero process can continue.
                logger.warning("Step 3/4: Could not find a parent 'LIBRARY-WORK' directory. Skipping .cats.txt copy.")
                self.report({'WARNING'}, "Could not find 'LIBRARY-WORK' folder. Skipping .cats.txt copy.")
            else:
                # 3. Define Source cats.txt Path
                source_cats_file = os.path.join(source_library_dir, "blender_assets.cats.txt")
                
                if not os.path.exists(source_cats_file):
                    # This is also a warning.
                    logger.warning(f"Step 3/4: Found '{source_library_dir}' but 'blender_assets.cats.txt' is missing. Skipping copy.")
                    self.report({'WARNING'}, "'blender_assets.cats.txt' not found in LIBRARY-WORK. Skipping copy.")
                else:
                    # 4. Define Destination cats.txt Path
                    # We build the path cleanly: '.../LIBRARY-WORK' -> '.../LIBRARY-HERO'
                    parent_of_library_work = os.path.dirname(source_library_dir)
                    dest_library_dir = os.path.join(parent_of_library_work, 'LIBRARY-HERO')
                    dest_cats_file = os.path.join(dest_library_dir, "blender_assets.cats.txt")
                    
                    logger.info(f"Source file: {source_cats_file}")
                    logger.info(f"Destination file: {dest_cats_file}")

                    # 5. Create Dest Dir & Copy
                    os.makedirs(dest_library_dir, exist_ok=True)
                    shutil.copy2(source_cats_file, dest_cats_file)
                    logger.info(f"Successfully copied 'blender_assets.cats.txt' to '{dest_library_dir}'.")
                    
        except Exception as e:
            # Report as an error, but do not cancel the 'Make Hero' process,
            # as the .cats.txt file is not critical.
            logger.error(f"Failed to copy 'blender_assets.cats.txt': {e}", exc_info=True)
            self.report({'ERROR'}, "Failed to copy 'blender_assets.cats.txt': See logs for details.")
            # This is not considered a critical failure, so the process continues.

        # --- Step 4: Run Save Incremental ---
        try:
            logger.info("Step 4/4: Performing final incremental save...")
            
            new_version = version + 1
            new_version_str = f"v{new_version:03d}"
            logger.info(f"Incrementing work file from v{version:03d} to {new_version_str}")
            
            # We already have the comment from the preliminary check
            
            # Conditionally add flags part only if it exists
            if flags:
                base_name = f"{project}-{asset}-{flags}-{new_version_str}"
            else:
                base_name = f"{project}-{asset}-{new_version_str}"
            
            sanitized_comment = re.sub(r'[^a-zA-Z0-9_-]', '_', comment)
            new_filename = f"{base_name}-{sanitized_comment}.blend"
            logger.info(f"Comment added: '{comment}', sanitized to '{sanitized_comment}'")

            work_dir = os.path.dirname(saved_work_filepath)
            new_incremental_filepath = os.path.join(work_dir, new_filename.lower())

            logger.info(f"Saving new incremented file to: {new_incremental_filepath}")
            
            # This save action opens the new file, fulfilling the last requirement.
            bpy.ops.wm.save_as_mainfile(filepath=new_incremental_filepath)
            
            context.scene.krutart_comment = ""
            logger.info(f"New incremental file saved and opened successfully: {new_incremental_filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save incremental file: {e}")
            logger.error(f"An exception occurred during final incremental save: {e}", exc_info=True)
            return {'CANCELLED'}

        # --- Final Report ---
        hero_basename = os.path.basename(hero_filepath)
        self.report({'INFO'}, f"Hero '{hero_basename}' created, and work file incremented to {new_version_str}")
        logger.info(f"Hero file saved to: {hero_filepath}") # Redundant log, but ensures it's logged at the end
        logger.info("'Make hero' process completed successfully.")
        logger.info("-" * 50)
        return {'FINISHED'}

# --- UI Panel ---

class KRUTART_PT_autopublisher_panel(bpy.types.Panel):
    """Creates a Panel in the Output Properties window"""
    bl_label = "KRUTART-AUTOPUBLISHER"
    bl_idname = "OUTPUT_PT_krutart_autopublisher"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_order = -1  # --- ADDED: This moves the panel to the top ---

    def draw(self, context):
        layout = self.layout
        scene = context.scene

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
            box.label(text="Expected: PROJECT-ASSET-[flags]-v###.blend")
            return
            
        # --- MODIFIED: Unified Publishing Box ---
        box = layout.box()
        box.label(text="Publishing Actions", icon='FILE_NEW')
        
        # Shared comment field at the top
        box.prop(scene, "krutart_comment", text="Comment")
        
        # --- NEW: Check if comment is empty ---
        comment = scene.krutart_comment.strip()
        is_comment_empty = not comment
        
        # --- NEW: Create a row for the buttons ---
        row = box.row()
        
        # --- NEW: Disable row if comment is empty ---
        if is_comment_empty:
            row.enabled = False
            
        # Add Save Increment button to the row
        row.operator(KRUTART_OT_save_increment.bl_idname)
        
        # Add Make Hero button to the row
        row.operator(KRUTART_OT_make_hero.bl_idname)
        # --- END MODIFIED SECTION ---

# --- Registration ---

classes = (
    KRUTART_OT_save_increment,
    KRUTART_OT_make_hero,
    KRUTART_PT_autopublisher_panel,
)

def register():
    # --- MODIFIED: Logger Setup ---
    # We configure the logger here to ensure it's set up every time
    # the addon is registered, which fixes issues with script reloading.
    global logger
    logger = logging.getLogger("KrutartAutoPublisher")
    logger.setLevel(logging.INFO)

    # Clear existing handlers to prevent duplicate logs on reload
    if logger.hasHandlers():
        logger.handlers.clear()

    # Add a fresh handler to print to the system console
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # --- End Logger Setup ---

    logger.info("Registering Krutart Publisher Addon v1.4.9") # Bumped version
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.krutart_comment = bpy.props.StringProperty(
        name="Comment",
        description="Optional comment for the incremental save filename",
        default="",
    )

def unregister():
    # --- MODIFIED: Removed 'global logger' declaration ---
    # The logger is defined at the module-level (global).
    # We only need to access it here, not assign it, so 'global'
    # is not needed and was causing the reload error.
    logger.info("Unregistering Krutart Publisher Addon")

    # --- ADDED: Logger Teardown ---
    # Get the logger and clear its handlers
    if 'logger' in globals() and logger and logger.hasHandlers():
        logger.handlers.clear()
    # --- End Logger Teardown ---

    for cls in reversed(classes):
        # Changed from register_class to unregister_class
        bpy.utils.unregister_class(cls) 
    del bpy.types.Scene.krutart_comment

if __name__ == "__main__":
    register()
