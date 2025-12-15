bl_info = {
    "name": "Krutart Proxy-Master Switcher",
    "author": "iori, krutart, gemini",
    "version": (3, 0, 0), # Merged Patcher/Switcher, added Debug, fixed reload bug
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Asset Lister | Outliner > Context Menu",
    "description": "Unified addon to list, switch (reactively), and patch (proactively) proxy/master assets.",
    "warning": "The 'Scan & Fix' tool will modify linked .blend files. Use with caution.",
    "doc_url": "",
    "category": "Object",
}

import bpy
import subprocess
import sys
import os
import logging
import ast  # To safely evaluate the string output from the subprocess
import tempfile

# --- Setup (v3.0.0) ---
# Set up a unified logger for the entire addon.
# The log level is controlled by the 'debug_mode' property in the UI.
log = logging.getLogger("krutart_switcher")


# --- NEW (v3.0.0): Unified Background Scripts ---

# SCRIPT 1: The Scanner
# Gathers all asset and library data from a .blend file.
SCANNER_SCRIPT = """
import bpy
import sys
import os

# This script is designed to be run from a subprocess by the main addon.
# It gathers data about a .blend file and prints it as a dictionary string.

log_messages = []

try:
    output_data = {
        'collections': [],
        'assets': [],
        'linked_libraries': []
    }

    # 1. Get all top-level collection names
    output_data['collections'] = [c.name for c in bpy.data.collections]
    log_messages.append(f"Found {len(output_data['collections'])} collections.")

    # 2. Get all linked library file paths for recursive search
    for lib in bpy.data.libraries:
        if lib.filepath:
            output_data['linked_libraries'].append(lib.filepath)

    log_messages.append(f"Found {len(output_data['linked_libraries'])} linked libraries.")


    # 3. Define all data-block types to search for assets
    data_blocks_to_scan = [
        ("Collection", bpy.data.collections)
    ]

    # 4. Find all potential proxy/master assets
    token_pairs = [
        ('-P-', '-M-'), ('-p-', '-m-'), # Embedded tokens first
        ('-P', '-M'), ('-p', '-m')      # Suffix tokens last
    ]

    found_assets = [] # This will hold dicts

    for block_name, block_data in data_blocks_to_scan:
        if not block_data: continue
        for item in block_data:
            if not hasattr(item, "name"): continue

            name = item.name
            asset_data = None

            # Get the asset's *own* source path
            # If item.library is None, it's local (empty string)
            # If it's linked, this stores the path to *its* source file.
            item_source_path = item.library.filepath if item.library else ""

            for proxy_token, master_token in token_pairs:

                # Check if it's a PROXY
                if proxy_token in name:
                    partner_name = ""
                    base_name = ""
                    suffix = proxy_token
                    if name.endswith(proxy_token):
                        base_name = name[:-len(proxy_token)]
                        partner_name = base_name + master_token
                    else:
                        partner_name = name.replace(proxy_token, master_token, 1)

                    asset_data = {
                        "name": name, "category": block_name, "base_name": base_name,
                        "suffix": suffix, "is_proxy": True,
                        "partner_name": partner_name,
                        "theoretical_partner": partner_name,
                        "source_filepath": item_source_path # Add source path
                    }
                    break

                # Check if it's a MASTER
                elif master_token in name:
                    partner_name = ""
                    base_name = ""
                    suffix = master_token
                    if name.endswith(master_token):
                        base_name = name[:-len(master_token)]
                        partner_name = base_name + proxy_token
                    else:
                        partner_name = name.replace(master_token, proxy_token, 1)

                    asset_data = {
                        "name": name, "category": block_name, "base_name": base_name,
                        "suffix": suffix, "is_proxy": False,
                        "partner_name": partner_name,
                        "theoretical_partner": partner_name,
                        "source_filepath": item_source_path # Add source path
                    }
                    break

            if asset_data:
                found_assets.append(asset_data)

    # 5. Process found assets to validate pairs
    processed_assets = []
    
    # Note: v2.5.1 optimization to remove partner check was kept.
    # We now check for partners in the main addon, not in the script.
    for asset in found_assets:
        processed_assets.append(asset)

    output_data['assets'] = processed_assets
    log_messages.append(f"Found {len(processed_assets)} assets, processed into {len(processed_assets)} with partner data.")

finally:
    # 6. Print the final dictionary as a string to stdout.
    print("---KRUTART_SCANNER_START---")
    print(repr({'logs': log_messages, 'data': output_data}))
    print("---KRUTART_SCANNER_END---")
    sys.stdout.flush()
"""


# SCRIPT 2: The Patcher
# Links a missing asset into a file and saves it.
PATCHER_SCRIPT = """
import bpy
import sys
import os

# This script is run to link a missing asset into an intermediate file.
# It expects 3 arguments:
# 1. intermediate_file_path (this file, for saving)
# 2. source_file_path (the file to link from)
# 3. asset_to_link (the collection name to link)

print("--- [Patcher Script] Started ---")

try:
    args = sys.argv[sys.argv.index("--") + 1:]

    if len(args) != 3:
        raise Exception(f"Expected 3 arguments, got {len(args)}")

    intermediate_file_path = args[0]
    source_file_path = args[1]
    asset_to_link = args[2]

    print(f"--- [Patcher Script] File:     {intermediate_file_path}")
    print(f"--- [Patcher Script] Linking:  '{asset_to_link}'")
    print(f"--- [Patcher Script] From:     '{source_file_path}'")

    # --- 1. Link the missing asset ---
    is_relative = source_file_path.startswith('//')

    print(f"--- [Patcher Script] Linking '{asset_to_link}' (Relative: {is_relative})")

    # Use 'relative=is_relative' for Blender 4.x API
    with bpy.data.libraries.load(source_file_path, link=True, relative=is_relative) as (data_from, data_to):
        if asset_to_link in data_from.collections:
            data_to.collections = [asset_to_link]
            print(f"--- [Patcher Script] Successfully linked '{asset_to_link}'.")
        else:
            raise Exception(f"'{asset_to_link}' not found in source file {source_file_path}")

    # --- 2. Save the intermediate file ---
    print(f"--- [Patcher Script] Saving intermediate file: {intermediate_file_path}")
    bpy.ops.wm.save_as_mainfile(filepath=intermediate_file_path)
    print("--- [Patcher Script] Save complete.")

    # --- 3. Print success marker ---
    print("---KRUTART_PATCH_SUCCESS---")

except Exception as e:
    print(f"--- [Patcher Script] ERROR: {e}")
    # Print failure marker
    print("---KRUTART_PATCH_FAILURE---")

finally:
    bpy.ops.wm.quit_blender()
"""


# --- NEW (v3.0.0): Log Level Control ---

def update_log_level(self, context):
    """Updates the logger's level based on the debug_mode property."""
    if context.scene.asset_lister_properties.debug_mode:
        log.setLevel(logging.INFO)
        log.info("Debug logging enabled.")
    else:
        # Don't log here, as the message would be hidden by the new level
        log.setLevel(logging.WARNING)


# --- Helper Function ---
# This helper encapsulates the token-parsing logic
# so it can be re-used by the new Outliner operator.

def get_asset_switch_info(collection):
    """
    Checks a collection to see if it's a valid, switchable proxy/master asset.

    Returns:
        A dict with switch info (current_name, new_name, filepath, partner_type)
        or None if the collection is not a valid, switchable asset.
    """
    if not collection or not collection.library or not collection.library.filepath:
        # Not a linked collection
        return None

    # We define pairs. Order matters: check for '-P-' before '-P'.
    # This ensures 'asset-P-LOD1' isn't misread as 'asset-P'
    token_pairs = [
        ('-P-', '-M-'), ('-p-', '-m-'), # Embedded tokens first
        ('-P', '-M'), ('-p', '-m')      # Suffix tokens last
    ]

    name = collection.name

    for proxy_token, master_token in token_pairs:

        # Check if it's a PROXY
        if proxy_token in name:
            partner_name = ""
            if name.endswith(proxy_token):
                base_name = name[:-len(proxy_token)]
                partner_name = base_name + master_token
            else:
                # It's an embedded token
                partner_name = name.replace(proxy_token, master_token, 1) # Replace only first instance

            return {
                "current_name": name,
                "new_name": partner_name,
                "filepath": bpy.path.abspath(collection.library.filepath),
                "partner_type": "Master"
            }

        # Check if it's a MASTER
        elif master_token in name:
            partner_name = ""
            if name.endswith(master_token):
                base_name = name[:-len(master_token)]
                partner_name = base_name + proxy_token
            else:
                # It's an embedded token
                partner_name = name.replace(master_token, proxy_token, 1) # Replace only first instance

            return {
                "current_name": name,
                "new_name": partner_name,
                "filepath": bpy.path.abspath(collection.library.filepath),
                "partner_type": "Proxy"
            }

    # If no token was found
    return None


# --- PROPERTY GROUPS ---
# These define the data structures for our addon.

class ASSETLISTER_PG_asset_item(bpy.types.PropertyGroup):
    """Property group to hold a single asset's details."""
    name: bpy.props.StringProperty(name="Asset Name")
    category: bpy.props.StringProperty(name="Asset Category")
    base_name: bpy.props.StringProperty(name="Base Name")
    suffix: bpy.props.StringProperty(name="Suffix")
    is_proxy: bpy.props.BoolProperty(name="Is Proxy")
    partner_name: bpy.props.StringProperty(name="Partner Asset Name")
    theoretical_partner: bpy.props.StringProperty(name="Theoretical Partner")
    source_filepath: bpy.props.StringProperty(name="Source File Path")


class ASSETLISTER_PG_collection_item(bpy.types.PropertyGroup):
    """Property group to hold a single collection name."""
    name: bpy.props.StringProperty(name="Collection Name")


class ASSETLISTER_PG_file_group(bpy.types.PropertyGroup):
    """Groups all data related to a single source file."""
    filepath: bpy.props.StringProperty(name="File Path")
    is_internal: bpy.props.BoolProperty(name="Is Internal Data") # Kept for potential future use

    # UI Toggles
    show_linked_assets: bpy.props.BoolProperty(name="Show Linked Assets", default=True)
    show_remote_assets: bpy.props.BoolProperty(name="Show All Remote Assets", default=False)
    show_collections: bpy.props.BoolProperty(name="Show Collections", default=False)

    # Data Collections
    linked_assets: bpy.props.CollectionProperty(type=ASSETLISTER_PG_asset_item)
    remote_assets: bpy.props.CollectionProperty(type=ASSETLISTER_PG_asset_item)
    collections: bpy.props.CollectionProperty(type=ASSETLISTER_PG_collection_item)


class ASSETLISTER_PG_properties(bpy.types.PropertyGroup):
    """Root property group to hold all addon data."""
    file_groups: bpy.props.CollectionProperty(type=ASSETLISTER_PG_file_group)
    searched: bpy.props.BoolProperty(
        default=False,
        description="Flag to check if a search has been performed"
    )

    auto_rename_instances: bpy.props.BoolProperty(
        name="Auto-rename Instances",
        description="Automatically rename instance objects (Empties) to match the new asset name. Only renames if the object's name contains the old asset name.",
        default=True
    )

    # --- NEW (v3.0.0): Debug Toggle ---
    debug_mode: bpy.props.BoolProperty(
        name="Enable Debug Logging",
        description="Prints verbose INFO logs to the System Console for debugging",
        default=False,
        update=update_log_level # Runs the update function
    )


# --- NEW (v3.0.0): Unified Operator Base Class ---

class ASSETLISTER_OT_base(bpy.types.Operator):
    """Base class with shared helper methods for background ops."""

    def run_background_patch(self, file_to_open, *args):
        """
        Runs the PATCHER_SCRIPT in a background Blender instance.
        'file_to_open' is the .blend file to open.
        '*args' are passed to the script.
        Returns True on success, False on failure.
        """
        script_file = None
        log.info(f"Running background PATCH script on: {file_to_open}")
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tf:
                script_file = tf.name
                tf.write(PATCHER_SCRIPT)

            # Use abspath for the file to open
            abs_file_path = bpy.path.abspath(file_to_open)
            if not os.path.exists(abs_file_path):
                log.error(f"File not found for background script: {abs_file_path}")
                return False

            command = [bpy.app.binary_path, "-b", abs_file_path, "--python", script_file, "--"]
            command.extend(args)

            log.info(f"Executing command: {' '.join(command)}")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=90)

            if "---KRUTART_PATCH_SUCCESS---" in stdout:
                log.info("Background patch script executed successfully.")
                log.info(f"--- STDOUT ---\n{stdout}\n--------------")
                return True
            else:
                log.error(f"Background patch script failed for '{file_to_open}'.")
                log.error(f"--- STDOUT ---\n{stdout}\n--------------")
                log.error(f"--- STDERR ---\n{stderr}\n--------------")
                return False

        except subprocess.TimeoutExpired:
            log.error(f"Timeout (90s) expired while running background patch script on: {file_to_open}")
            if 'process' in locals() and process.poll() is None:
                process.kill()
            return False
        except Exception as e:
            log.error(f"Error running background patch script: {e}", exc_info=True)
            return False
        finally:
            if script_file and os.path.exists(script_file):
                try:
                    os.remove(script_file)
                except OSError as e:
                    log.error(f"Error removing temp file {script_file}: {e}")

    def get_remote_file_data(self, filepath, script_content):
        """
        Runs the SCANNER_SCRIPT in the background and returns the parsed data.
        """
        script_file = None

        abs_filepath = bpy.path.abspath(filepath)
        log_filepath = filepath # Use original path for logging

        log.info(f"Preparing to run background SCAN script for: {log_filepath}")
        if not os.path.exists(abs_filepath):
            log.error(f"File not found, cannot run background script: {abs_filepath}")
            return None

        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tf:
                script_file = tf.name
                tf.write(script_content)

            command = [bpy.app.binary_path, "-b", abs_filepath, "--python", script_file]

            log.info(f"Executing command: {' '.join(command)}")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=90)

            if process.returncode != 0:
                log.error(f"Background script for '{os.path.basename(log_filepath)}' finished with code {process.returncode}.")
                log.error(f"--- STDERR ---\n{stderr}\n--------------")
                return None

            if stdout:
                try:
                    data_start_marker = "---KRUTART_SCANNER_START---"
                    data_end_marker = "---KRUTART_SCANNER_END---"

                    if data_start_marker not in stdout or data_end_marker not in stdout:
                        log.error(f"Could not find start/end markers in stdout from '{os.path.basename(log_filepath)}'.")
                        log.error(f"--- STDOUT ---\n{stdout}\n--------------")
                        return None

                    data_str = stdout.split(data_start_marker, 1)[1]
                    data_str = data_str.split(data_end_marker, 1)[0]

                    parsed_output = ast.literal_eval(data_str.strip())

                    if 'logs' in parsed_output and parsed_output['logs']:
                        log.info(f"Logs from background process for '{os.path.basename(log_filepath)}':")
                        for msg in parsed_output['logs']:
                            log.info(f"                     > {msg}")

                    return parsed_output
                except (ValueError, SyntaxError) as e:
                    log.error(f"Could not parse stdout from '{os.path.basename(log_filepath)}': {e}")
                    log.error(f"--- STDOUT ---\n{stdout}\n--------------")
                    return None
                except Exception as e:
                    log.error(f"Error extracting data block from stdout for '{os.path.basename(log_filepath)}': {e}")
                    log.error(f"--- STDOUT ---\n{stdout}\n--------------")
                    return None
            else:
                log.warning(f"Background script for '{os.path.basename(log_filepath)}' produced no stdout.")
                if stderr: log.warning(f"--- STDERR ---\n{stderr}\n--------------")

        except subprocess.TimeoutExpired:
            log.error(f"Timeout (90s) expired while processing remote file: {log_filepath}")
            if 'process' in locals() and process.poll() is None:
                process.kill()
        except Exception as e:
            log.error(f"Error in get_remote_file_data for {log_filepath}: {e}", exc_info=True)
        finally:
            if script_file and os.path.exists(script_file):
                try:
                    os.remove(script_file)
                except OSError as e:
                    log.error(f"Error removing temp file {script_file}: {e}")
        return None


# --- OPERATORS ---

class ASSETLISTER_OT_open_file(ASSETLISTER_OT_base):
    """Opens the selected .blend file in a new instance of Blender."""
    bl_idname = "asset_lister.open_file"
    bl_label = "Open Linked File in New Instance"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def execute(self, context):
        log.info(f"Attempting to open file in a new Blender instance: '{self.filepath}'")
        if not self.filepath or not os.path.exists(bpy.path.abspath(self.filepath)):
            self.report({'ERROR'}, "File path is invalid or file does not exist.")
            return {'CANCELLED'}

        normalized_path = os.path.normpath(bpy.path.abspath(self.filepath))
        try:
            command = [bpy.app.binary_path, normalized_path]
            log.info(f"Executing command: {' '.join(command)}")
            subprocess.Popen(command)
            self.report({'INFO'}, f"Launched new Blender instance for {os.path.basename(normalized_path)}.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to open file in new Blender instance: {e}")
            log.error(f"Subprocess error opening file: {e}", exc_info=True)
            return {'CANCELLED'}
        return {'FINISHED'}


class ASSETLISTER_OT_switch_asset_version(ASSETLISTER_OT_base):
    """Swaps all instances of a linked collection for its proxy/master partner."""
    bl_idname = "asset_lister.switch_version"
    bl_label = "Switch Asset Version"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(name="File Path")
    current_name: bpy.props.StringProperty(name="Current Asset Name")
    new_name: bpy.props.StringProperty(name="New Asset Name")
    source_filepath: bpy.props.StringProperty(name="Source File Path")

    @classmethod
    def description(cls, context, properties):
        return f"Switch all instances of '{properties.current_name}' to '{properties.new_name}'"

    def link_and_find_collection(self, lib_path, coll_name):
        """
        Attempts to link 'coll_name' from 'lib_path'.
        Returns the collection datablock or None.
        """
        try:
            with bpy.data.libraries.load(lib_path, link=True) as (data_from, data_to):
                if coll_name in data_from.collections:
                    data_to.collections = [coll_name]
                else:
                    # Asset not found in the library
                    raise FileNotFoundError(f"'{coll_name}' not found in {lib_path}")

            # Find the newly linked collection
            for coll in bpy.data.collections:
                if (coll.name == coll_name and
                    coll.library and
                    coll.library.filepath == lib_path):
                    return coll

            raise Exception("Failed to find collection after linking.")

        except Exception as e:
            log.warning(f"Failed to link '{coll_name}' from '{lib_path}': {e}")
            return None

    def execute(self, context):
        log.info(f"Attempting to switch '{self.current_name}' to '{self.new_name}' from file '{self.filepath}'")

        props = context.scene.asset_lister_properties
        auto_rename = props.auto_rename_instances
        log.info(f"Auto-rename setting is: {auto_rename}")

        # --- 1. Find the old collection PRECISELY ---
        old_collection = None
        norm_filepath = bpy.path.abspath(self.filepath)

        for coll in bpy.data.collections:
            if (coll.name == self.current_name and
                coll.library and
                bpy.path.abspath(coll.library.filepath) == norm_filepath):
                old_collection = coll
                break

        if not old_collection:
            self.report({'ERROR'}, f"Could not find linked collection: {self.current_name} from {norm_filepath}")
            log.error(f"Precise search failed for: {self.current_name} from {norm_filepath}")
            return {'CANCELLED'}

        if not old_collection.library:
            self.report({'ERROR'}, f"{self.current_name} is not a linked collection.")
            return {'CANCELLED'}

        original_lib_path = old_collection.library.filepath
        log.info(f"Precisely found old collection: '{old_collection.name}'")
        log.info(f"  > Using original library path for linking: '{original_lib_path}'")

        # --- 2. Find or link the new collection ---
        new_collection = None
        for coll in bpy.data.collections:
            if (coll.name == self.new_name and
                coll.library and
                coll.library.filepath == original_lib_path):
                new_collection = coll
                break

        if not new_collection:
            log.info(f"'{self.new_name}' not in memory. Attempting to link from {original_lib_path}")
            new_collection = self.link_and_find_collection(original_lib_path, self.new_name)

            if not new_collection:
                log.warning(f"Initial link failed. Checking for intermediate file...")

                is_intermediate = (self.source_filepath and
                                   bpy.path.abspath(self.source_filepath) != bpy.path.abspath(original_lib_path))

                log.info(f"  > Asset Source: {self.source_filepath}")
                log.info(f"  > Link Source:  {original_lib_path}")
                log.info(f"  > Is Intermediate: {is_intermediate}")

                if is_intermediate:
                    log.info(f"This is an intermediate link. Attempting to patch '{original_lib_path}'...")
                    
                    # Use absolute path for saving in the patch script
                    abs_intermediate_path = bpy.path.abspath(original_lib_path)

                    link_success = self.run_background_patch(
                        original_lib_path,         # File to open
                        abs_intermediate_path,     # Arg 0: intermediate_file_path (for saving)
                        self.source_filepath,      # Arg 1: source_file_path (where the asset lives)
                        self.new_name              # Arg 2: asset_to_link
                    )

                    # --- BUG FIX (v3.0.0): Logic to handle lib.reload() ---
                    if link_success:
                        log.info("Background patch successful. Retrying link into current file...")
                        lib = old_collection.library
                        log.info(f"Reloading library: {lib.filepath}")
                        lib.reload()

                        # --- FIX: Search memory *first* after reload ---
                        # The asset is now in memory, we just need to find it.
                        for coll in bpy.data.collections:
                            if (coll.name == self.new_name and
                                coll.library and
                                coll.library.filepath == original_lib_path):
                                new_collection = coll
                                log.info(f"Found collection '{coll.name}' in memory after library reload.")
                                break
                        
                        if not new_collection:
                            log.warning("Collection not found in memory after reload. Attempting one final link operation.")
                            # As a last resort, try the link_and_find_collection function again.
                            new_collection = self.link_and_find_collection(original_lib_path, self.new_name)
                        # --- END FIX ---
                    
                    else:
                        self.report({'ERROR'}, f"Failed to patch intermediate file '{original_lib_path}'. See console.")
                        return {'CANCELLED'}
                    # --- END BUG FIX ---

        if not new_collection:
            # If it's still not found after all attempts, fail.
            self.report({'ERROR'}, f"Failed to link '{self.new_name}' from {original_lib_path}")
            log.error(f"Could not find or link new collection '{self.new_name}'")
            return {'CANCELLED'}

        log.info(f"Successfully found/linked old: '{old_collection.name}' and new: '{new_collection.name}'")

        # --- 3. Swap all instances ---
        objects_to_process = []

        active_obj = None
        if hasattr(context, "id") and isinstance(context.id, bpy.types.Object):
            active_obj = context.id
        elif context.active_object:
            active_obj = context.active_object

        is_outliner_call = False
        if active_obj and active_obj.instance_type == 'COLLECTION' and active_obj.instance_collection == old_collection:
            is_outliner_call = True # Assume it's an Outliner call

        if is_outliner_call:
            log.info("Processing selection from Outliner context.")
            def get_children_recursive(obj):
                children = [obj]
                for child in obj.children:
                    children.extend(get_children_recursive(child))
                return children

            objects_to_process = get_children_recursive(active_obj)
            log.info(f"Found {len(objects_to_process)} objects in selected hierarchy.")

        else:
            log.info("Processing all objects in scene (Panel call).")
            objects_to_process = bpy.data.objects

        swapped_count = 0
        renamed_count = 0
        rename_failures = []

        for obj in objects_to_process:
            if obj.instance_type == 'COLLECTION' and obj.instance_collection == old_collection:

                if obj.parent:
                    log.info(f"Swapping instance '{obj.name}' (Parent: '{obj.parent.name}')")
                else:
                    log.info(f"Swapping instance '{obj.name}' (Parent: None)")

                obj.instance_collection = new_collection

                if auto_rename:
                    if not obj.library:
                        if self.current_name in obj.name:
                            original_name = obj.name
                            try:
                                obj.name = obj.name.replace(self.current_name, self.new_name, 1)
                                log.info(f"Renamed local object '{original_name}' to '{obj.name}'")
                                renamed_count += 1
                            except Exception as e:
                                log.warning(f"Could not rename '{original_name}': {e}")
                                rename_failures.append(original_name)
                                try:
                                    obj.color_tag = 'COLOR_01' # Set tag to red
                                    log.warning(f"Tagged '{original_name}' (now '{obj.name}') with RED color tag due to rename failure.")
                                except Exception as tag_e:
                                    log.error(f"Failed to set color tag for '{obj.name}': {tag_e}")
                        else:
                            log.info(f"Skipping rename for '{obj.name}': name does not contain '{self.current_name}'.")
                    else:
                        log.info(f"Skipping rename for '{obj.name}': object is linked (read-only).")

                swapped_count += 1

        log.info(f"Swapped {swapped_count} instances. Renamed {renamed_count} objects.")

        report_message = f"Swapped {swapped_count} instances to '{self.new_name}' (Renamed {renamed_count})"
        if rename_failures:
            report_message += f". {len(rename_failures)} rename(s) failed (see console)."
            self.report({'WARNING'}, report_message)
        else:
            self.report({'INFO'}, report_message)

        # --- 4. Redraw ---
        for area in context.screen.areas:
            area.tag_redraw()

        return {'FINISHED'}


class ASSETLISTER_OT_outliner_switch(ASSETLISTER_OT_base):
    """
    Calls the main switch operator from the Outliner.
    This operator's execute() method calls the main logic.
    """
    bl_idname = "asset_lister.outliner_switch"
    bl_label = "Switch Asset Version (Context)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = None
        if hasattr(context, "id") and isinstance(context.id, bpy.types.Object):
            obj = context.id
        elif context.active_object:
            obj = context.active_object
        else:
            return {'CANCELLED'} # No object found

        if not obj or not obj.instance_collection:
            return {'CANCELLED'}

        switch_info = get_asset_switch_info(obj.instance_collection)

        if not switch_info:
            self.report({'ERROR'}, "Selected object is not a valid proxy/master asset.")
            return {'CANCELLED'}

        log.info(f"Outliner switch triggered for '{obj.name}'")

        # Find and pass source_filepath
        source_filepath = ""
        props = context.scene.asset_lister_properties
        found = False
        for group in props.file_groups:
            if bpy.path.abspath(group.filepath) == switch_info["filepath"]:
                for asset in group.linked_assets:
                    if asset.name == switch_info["current_name"]:
                        source_filepath = asset.source_filepath
                        found = True
                        break
            if found:
                break

        if not found:
            log.warning("Could not find matching asset in UI properties to get source_filepath. Switch may fail for intermediate assets.")

        bpy.ops.asset_lister.switch_version(
            filepath=switch_info["filepath"],
            current_name=switch_info["current_name"],
            new_name=switch_info["new_name"],
            source_filepath=source_filepath # Pass the new property
        )

        return {'FINISHED'}


class ListAssetsOperator(ASSETLISTER_OT_base):
    """Scans for assets, separating linked from all available remote assets."""
    bl_idname = "wm.list_assets_operator"
    bl_label = "List Linked Assets"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.asset_lister_properties
        props.file_groups.clear()
        props.searched = True

        log.info("="*50)
        log.info("[Phase 1] Starting Asset Lister Scan...")

        grouped_data = {}

        log.info("[Step 1] Searching current file for linked assets...")

        tokens_to_find = ['-P-', '-M-', '-p-', '-m-', '-P', '-M', '-p', '-m']

        data_blocks = [
            ("Collection", bpy.data.collections),
        ]
        log.info("[Step 1] Scoping search to linked COLLECTIONS only.")

        for block_name, block_data in data_blocks:
            if not block_data: continue
            for item in block_data:
                if not hasattr(item, "name") or not item.library: continue

                found = False
                for token in tokens_to_find:
                    if token in item.name:
                        found = True
                        break

                if found:
                    filepath_abs = bpy.path.abspath(item.library.filepath)
                    filepath_orig = item.library.filepath

                    if filepath_abs not in grouped_data:
                        grouped_data[filepath_abs] = {
                            'linked_assets': [],
                            'remote_assets': [],
                            'collections': [],
                            'original_path': filepath_orig
                        }

                    if not any(d['name'] == item.name for d in grouped_data[filepath_abs]['linked_assets']):
                        log.info(f"  > [Step 1] Found linked asset: '{item.name}' ({block_name}) from '{filepath_orig}' (Abs: '{filepath_abs}')")
                        grouped_data[filepath_abs]['linked_assets'].append({
                            "name": item.name, "category": block_name
                        })

        log.info("[Step 1] Finished search for linked assets in current file.")

        log.info("[Step 2] Starting recursive scan of linked libraries...")

        initial_libs = [lib.filepath for lib in bpy.data.libraries if lib.filepath]
        queue = list(initial_libs)
        scanned_files_abs = set()

        log.info(f"[Step 2] Initial scan found {len(initial_libs)} direct libraries.")

        while queue:
            path_orig = queue.pop(0)
            path_abs = os.path.normpath(bpy.path.abspath(path_orig))

            if not path_abs or path_abs in scanned_files_abs:
                continue
            if not os.path.exists(path_abs):
                log.warning(f"Skipping non-existent file path: {path_orig} (Abs: {path_abs})")
                continue

            scanned_files_abs.add(path_abs)
            log.info(f"--- [Step 2] Processing: {path_orig} (Abs: {path_abs}) ---")

            # --- MODIFIED (v3.0.0): Use unified helper and pass SCANNER_SCRIPT ---
            remote_info = self.get_remote_file_data(path_orig, SCANNER_SCRIPT)
            # --- END MODIFIED ---

            if remote_info and 'data' in remote_info:
                remote_data = remote_info['data']
                log.info(f"  > [Step 2] Successfully parsed data for {path_orig}.")

                if path_abs not in grouped_data:
                    grouped_data[path_abs] = {
                        'linked_assets': [],
                        'remote_assets': [],
                        'collections': [],
                        'original_path': path_orig
                    }

                grouped_data[path_abs]['remote_assets'].extend(remote_data.get('assets', []))

                found_collections = remote_data.get('collections', [])
                if found_collections:
                    grouped_data[path_abs]['collections'].extend(found_collections)

                new_libraries = remote_data.get('linked_libraries', [])
                if new_libraries:
                    log.info(f"  > [Step 2] Found {len(new_libraries)} new libraries linked in {os.path.basename(path_orig)}.")
                    for lib_path_orig in new_libraries:
                        lib_path_abs = os.path.normpath(bpy.path.abspath(lib_path_orig))
                        if lib_path_abs not in scanned_files_abs and lib_path_orig not in queue:
                            queue.append(lib_path_orig)
            else:
                log.error(f"[Step 2] Failed to retrieve or parse data for {path_orig}. It will be skipped.")
        log.info("[Step 2] Finished recursive scan.")

        log.info("[Step 3] Scan complete. Populating UI...")
        if not grouped_data:
            log.info("No linked files or assets found.")

        global_remote_asset_lookup = {}

        for path_abs, data in grouped_data.items():
            unique_remote_names_cats = set()
            for asset_data in data['remote_assets']:
                name_cat_tuple = (asset_data.get("name"), asset_data.get("category"))

                if name_cat_tuple not in unique_remote_names_cats:
                    unique_remote_names_cats.add(name_cat_tuple)

                    existing_data = global_remote_asset_lookup.get(name_cat_tuple)
                    if not existing_data or (asset_data.get("partner_name") and not existing_data.get("partner_name")) or (asset_data.get("source_filepath") and not existing_data.get("source_filepath")):
                        global_remote_asset_lookup[name_cat_tuple] = asset_data

                elif name_cat_tuple in global_remote_asset_lookup:
                    existing_data = global_remote_asset_lookup[name_cat_tuple]
                    if (asset_data.get("partner_name") and not existing_data.get("partner_name")) or (asset_data.get("source_filepath") and not existing_data.get("source_filepath")):
                         global_remote_asset_lookup[name_cat_tuple] = asset_data

        log.info(f"[Step 3] Built global lookup with {len(global_remote_asset_lookup)} unique remote assets.")

        for path_abs, data in grouped_data.items():
            group = props.file_groups.add()

            group.filepath = data['original_path']
            group.is_internal = False

            log.info(f"[Step 3 UI] Populating 'Linked Assets' list for {os.path.basename(data['original_path'])}...")
            for asset_data in sorted(data['linked_assets'], key=lambda x: x['category']):
                asset_item = group.linked_assets.add()
                asset_item.name = asset_data["name"]
                asset_item.category = asset_data["category"]

                log.info(f"  > [Step 3 UI] Processing linked asset: '{asset_item.name}' (Category: {asset_item.category})")
                lookup_key = (asset_item.name, asset_item.category)
                log.info(f"  > [Step 3 UI]      - Lookup key: {lookup_key}")

                remote_data = global_remote_asset_lookup.get(lookup_key)

                if remote_data:
                    log.info(f"  > [Step 3 UI]      - Lookup result: FOUND")
                    log.info(f"  > [Step 3 UI]      - Partner name: '{remote_data.get('partner_name', '')}'")
                    log.info(f"  > [Step 3 UI]      - Source File: '{remote_data.get('source_filepath', '')}'")
                    asset_item.base_name = remote_data.get("base_name", "")
                    asset_item.suffix = remote_data.get("suffix", "")
                    asset_item.is_proxy = remote_data.get("is_proxy", False)
                    asset_item.partner_name = remote_data.get("partner_name", "")
                    asset_item.theoretical_partner = remote_data.get("theoretical_partner", "")
                    asset_item.source_filepath = remote_data.get("source_filepath", "")
                else:
                    log.info(f"  > [Step 3 UI]      - Lookup result: NOT FOUND in global lookup for key: {lookup_key}")
                    asset_item.base_name = ""
                    asset_item.suffix = ""
                    asset_item.is_proxy = False
                    asset_item.partner_name = ""
                    asset_item.theoretical_partner = ""
                    asset_item.source_filepath = ""

            log.info(f"[Step 3 UI] Populating 'All Remote Assets' list for {os.path.basename(data['original_path'])}...")
            processed_remote_assets = []
            unique_remote_names_cats_local = set()
            for asset_data in data['remote_assets']:
                name_cat_tuple = (asset_data.get("name"), asset_data.get("category"))
                if name_cat_tuple not in unique_remote_names_cats_local:
                    unique_remote_names_cats_local.add(name_cat_tuple)
                    processed_remote_assets.append(asset_data)

            for asset_data in sorted(processed_remote_assets, key=lambda x: x['category']):
                asset_item = group.remote_assets.add()
                asset_item.name = asset_data.get("name", "")
                asset_item.category = asset_data.get("category", "")
                asset_item.base_name = asset_data.get("base_name", "")
                asset_item.suffix = asset_data.get("suffix", "")
                asset_item.is_proxy = asset_data.get("is_proxy", False)
                asset_item.partner_name = asset_data.get("partner_name", "")
                asset_item.theoretical_partner = asset_data.get("theoretical_partner", "")
                asset_item.source_filepath = asset_data.get("source_filepath", "")

            log.info(f"[Step 3 UI] Populating 'All Remote Collections' list for {os.path.basename(data['original_path'])}...")
            for coll_name in sorted(list(set(data['collections']))):
                coll_item = group.collections.add()
                coll_item.name = coll_name

        log.info(f"Finished populating UI with data from {len(grouped_data)} file(s).")
        log.info("="*50)

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()
        return {'FINISHED'}


# --- NEW (v3.0.0): Merged operator from 'krutart_intermediate_asset_linker.py' ---

class ASSETLINKER_OT_fix_intermediate_files(ASSETLISTER_OT_base):
    """
    Scans all linked .blend files recursively and attempts to fix any
    intermediate files that are missing a proxy/master asset pair.
    """
    bl_idname = "asset_linker.fix_intermediate_files"
    bl_label = "Scan & Fix Intermediate Assets"
    bl_options = {'REGISTER'}

    def execute(self, context):
        log.info("="*50)
        log.info("[Linker] Starting Proactive Asset Link Scan...")

        # --- Data Storage ---
        # { "abs_path": [list_of_asset_dicts] }
        file_asset_map = {}
        # { "asset_name": "true_source_abs_path" }
        asset_source_map = {}

        # --- 1. Scan Phase (Recursive) ---
        log.info("[Linker - Phase 1] Starting recursive scan of all linked libraries...")

        initial_libs = [lib.filepath for lib in bpy.data.libraries if lib.filepath]
        queue = list(initial_libs)
        scanned_files_abs = set()

        if not queue:
            log.info("[Linker - Phase 1] No linked libraries found in current file. Nothing to do.")
            self.report({'INFO'}, "No linked libraries found. Nothing to scan.")
            return {'FINISHED'}

        log.info(f"[Linker - Phase 1] Found {len(initial_libs)} direct libraries to scan.")

        while queue:
            path_orig = queue.pop(0)

            # Use os.path.normpath to clean up paths (e.g., //..\file -> file)
            path_abs = os.path.normpath(bpy.path.abspath(path_orig))

            if not path_abs or path_abs in scanned_files_abs:
                continue
            if not os.path.exists(path_abs):
                log.warning(f"[Linker - Phase 1] Skipping non-existent file path: {path_orig} (Abs: {path_abs})")
                continue

            scanned_files_abs.add(path_abs)
            log.info(f"--- [Linker - Phase 1] Processing: {path_orig} (Abs: {path_abs}) ---")

            # --- MODIFIED (v3.0.0): Use unified helper and pass SCANNER_SCRIPT ---
            remote_info = self.get_remote_file_data(path_orig, SCANNER_SCRIPT)
            # --- END MODIFIED ---

            if remote_info and 'data' in remote_info:
                remote_data = remote_info['data']
                log.info(f"  > [Linker - Phase 1] Successfully parsed data for {path_orig}.")

                # Store the assets found in this file
                assets_in_file = remote_data.get('assets', [])
                file_asset_map[path_abs] = assets_in_file

                # Update our global maps
                for asset in assets_in_file:
                    asset_name = asset['name']
                    asset_source = asset['source_filepath']

                    if asset_source:
                        # This asset is linked, so its source_filepath is its *true* source.
                        abs_source = os.path.normpath(bpy.path.abspath(asset_source))
                        
                        if asset_name not in asset_source_map or not asset_source_map[asset_name]:
                            asset_source_map[asset_name] = abs_source
                            log.info(f"  > [Linker - Phase 1] Mapped asset '{asset_name}' to true source '{abs_source}'")

                    elif asset_name not in asset_source_map:
                         # This asset is local to the file, so *this file* is its source.
                         asset_source_map[asset_name] = path_abs
                         log.info(f"  > [Linker - Phase 1] Mapped local asset '{asset_name}' to source '{path_abs}'")

                # Add new libraries to the queue
                new_libraries = remote_data.get('linked_libraries', [])
                if new_libraries:
                    log.info(f"  > [Linker - Phase 1] Found {len(new_libraries)} new libraries linked in {os.path.basename(path_orig)}.")
                    for lib_path_orig in new_libraries:
                        lib_path_abs = os.path.normpath(bpy.path.abspath(lib_path_orig))
                        if lib_path_abs not in scanned_files_abs and lib_path_orig not in queue:
                            queue.append(lib_path_orig)
            else:
                log.error(f"[Linker - Phase 1] Failed to retrieve or parse data for {path_orig}. It will be skipped.")

        log.info("[Linker - Phase 1] Recursive scan complete.")

        # --- 2. Analysis & Patch Phase ---
        log.info("[Linker - Phase 2] Analyzing links and patching missing pairs...")

        fixes_made = 0
        fixes_failed = 0

        for intermediate_filepath_abs, assets_in_file in file_asset_map.items():

            # Create a set of asset names for fast lookup
            asset_names_in_file = {a['name'] for a in assets_in_file}

            log.info(f"--- [Linker - Phase 2] Analyzing file: {intermediate_filepath_abs} ---")

            for asset_data in assets_in_file:
                partner_name = asset_data.get('theoretical_partner')

                if not partner_name:
                    continue # Not a proxy/master asset

                if partner_name in asset_names_in_file:
                    continue # Partner already exists, all good.

                # --- If we get here, the partner is MISSING ---

                log.warning(f"  > [Linker - Phase 2] MISSING LINK: '{intermediate_filepath_abs}' has '{asset_data['name']}' but is missing partner '{partner_name}'.")

                # Find the partner's true source file
                partner_source_file = asset_source_map.get(partner_name)

                if not partner_source_file:
                    log.error(f"  > [Linker - Phase 2] CANNOT FIX: True source for '{partner_name}' is unknown. Skipping patch.")
                    fixes_failed += 1
                    continue

                if not os.path.exists(bpy.path.abspath(partner_source_file)):
                    log.error(f"  > [Linker - Phase 2] CANNOT FIX: Source file '{partner_source_file}' for '{partner_name}' does not exist. Skipping patch.")
                    fixes_failed += 1
                    continue

                log.info(f"  > [Linker - Phase 2] ATTEMPTING FIX: Linking '{partner_name}' from '{partner_source_file}' into '{intermediate_filepath_abs}'...")

                # --- MODIFIED (v3.0.0): Use unified helper ---
                patch_success = self.run_background_patch(
                    intermediate_filepath_abs,  # File to open
                    intermediate_filepath_abs,  # Arg 0: intermediate_file_path (for saving)
                    partner_source_file,        # Arg 1: source_file_path
                    partner_name                # Arg 2: asset_to_link
                )
                # --- END MODIFIED ---

                if patch_success:
                    log.info(f"  > [Linker - Phase 2] FIX SUCCESSFUL: Patched '{intermediate_filepath_abs}' with '{partner_name}'.")
                    fixes_made += 1
                    # Add to set so we don't try to link it again this session
                    asset_names_in_file.add(partner_name)
                else:
                    log.error(f"  > [Linker - Phase 2] FIX FAILED: Could not patch '{intermediate_filepath_abs}' with '{partner_name}'. See logs above.")
                    fixes_failed += 1

        log.info("[Linker - Phase 2] Analysis and patching complete.")
        log.info("="*50)

        report_message = f"Scan complete. Patched {fixes_made} links."
        if fixes_failed > 0:
            report_message += f" {fixes_failed} patches failed (see console)."
            self.report({'WARNING'}, report_message)
        else:
            self.report({'INFO'}, report_message)

        return {'FINISHED'}


# --- UI PANEL ---

class AssetListerPanel(bpy.types.Panel):
    """Creates the UI Panel in the 3D Viewport."""
    bl_label = "Asset Suffix Lister"
    bl_idname = "VIEW3D_PT_asset_lister"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asset Lister'

    def draw_asset_list(self, layout, assets, is_linked_list=False, group_filepath=""):
        """Helper function to draw a categorized list of assets."""
        asset_box = layout.box()
        current_category = ""
        for asset in assets:
            if asset.category != current_category:
                current_category = asset.category
                asset_box.label(text=f"--- {current_category}s ---", icon='DOT')

            row = asset_box.row(align=True)

            display_text = f"     • {asset.name}"

            if is_linked_list:
                if asset.partner_name:
                    display_text += f"  <->  {asset.partner_name}"
                elif asset.theoretical_partner:
                    display_text += f"  (No Partner: {asset.theoretical_partner})"

            row.label(text=display_text)

            if (is_linked_list and
                asset.category == 'Collection' and
                asset.partner_name):

                if asset.is_proxy:
                    button_text = "Master"
                else:
                    button_text = "Proxy"

                op = row.operator(
                    ASSETLISTER_OT_switch_asset_version.bl_idname,
                    text=button_text
                )

                op.filepath = bpy.path.abspath(group_filepath)
                op.current_name = asset.name
                op.new_name = asset.partner_name
                op.source_filepath = asset.source_filepath
            else:
                row.label(text="")


    def draw(self, context):
        layout = self.layout
        props = context.scene.asset_lister_properties

        # --- MODIFIED (v3.0.0): Unified UI ---
        
        # 1. Reactive Lister Button
        layout.operator("wm.list_assets_operator", text="List Linked Assets")
        
        # 2. Settings Box
        settings_box = layout.box()
        settings_box.label(text="Settings:")
        settings_box.prop(props, "auto_rename_instances")
        settings_box.prop(props, "debug_mode")

        # 3. Proactive Patcher Box
        patch_box = layout.box()
        patch_box.label(text="Proactive Patcher", icon='ERROR')
        patch_box.label(text="This scans all linked files and", icon='DOT')
        patch_box.label(text="patches missing asset pairs.", icon='DOT')
        patch_box.label(text="This will MODIFY files on disk.", icon='DOT')
        patch_box.operator(
            ASSETLINKER_OT_fix_intermediate_files.bl_idname,
            text="Scan & Fix All Intermediate Files",
            icon='LINKED'
        )
        
        layout.separator()
        # --- END MODIFIED (v3.0.0) ---


        if not props.searched:
            layout.label(text="Press 'List Linked Assets' to scan scene.")
            return

        if not props.file_groups:
            layout.label(text="No linked assets or libraries found.")
            return

        for group in props.file_groups:
            file_box = layout.box()
            header_row = file_box.row()

            op = header_row.operator(
                ASSETLISTER_OT_open_file.bl_idname,
                text=os.path.basename(group.filepath),
                icon='LINKED'
            )
            op.filepath = group.filepath

            if group.linked_assets:
                row = file_box.row(align=True)
                icon = 'TRIA_DOWN' if group.show_linked_assets else 'TRIA_RIGHT'
                row.prop(group, "show_linked_assets", text="", icon=icon, emboss=False)
                row.label(text=f"Linked Assets ({len(group.linked_assets)})")
                if group.show_linked_assets:
                    self.draw_asset_list(
                        file_box,
                        group.linked_assets,
                        is_linked_list=True,
                        group_filepath=group.filepath
                    )

            if group.remote_assets:
                row = file_box.row(align=True)
                icon = 'TRIA_DOWN' if group.show_remote_assets else 'TRIA_RIGHT'
                row.prop(group, "show_remote_assets", text="", icon=icon, emboss=False)
                row.label(text=f"All Remote Assets ({len(group.remote_assets)})")
                if group.show_remote_assets:
                    self.draw_asset_list(file_box, group.remote_assets)

            if group.collections:
                row = file_box.row(align=True)
                icon = 'TRIA_DOWN' if group.show_collections else 'TRIA_RIGHT'
                row.prop(group, "show_collections", text="", icon=icon, emboss=False)
                row.label(text=f"All Remote Collections ({len(group.collections)})")
                if group.show_collections:
                    coll_box = file_box.box()
                    for coll in group.collections:
                        coll_box.label(text=f"  • {coll.name}")


# --- Outliner Menu Function ---

def ASSETLISTER_MT_outliner_menu(self, context):
    """
    Draws the context menu item in the Outliner.
    This function dynamically sets the text and enabled state.
    """
    layout = self.layout
    switch_info = None

    obj = None
    if hasattr(context, "id") and isinstance(context.id, bpy.types.Object):
        obj = context.id
    elif context.active_object:
        obj = context.active_object

    if (obj and
        obj.type == 'EMPTY' and
        obj.instance_type == 'COLLECTION' and
        obj.instance_collection):

        switch_info = get_asset_switch_info(obj.instance_collection)

    if switch_info:
        op_text = f"Switch to {switch_info['partner_type']}"
        op = layout.operator(ASSETLISTER_OT_outliner_switch.bl_idname, text=op_text)
        op.enabled = True
    else:
        op = layout.operator(ASSETLISTER_OT_outliner_switch.bl_idname, text="Switch Asset Version")
        op.enabled = False


# --- REGISTRATION ---

classes = (
    ASSETLISTER_PG_asset_item,
    ASSETLISTER_PG_collection_item,
    ASSETLISTER_PG_file_group,
    ASSETLISTER_PG_properties,
    ASSETLISTER_OT_open_file,
    ASSETLISTER_OT_switch_asset_version,
    ASSETLISTER_OT_outliner_switch,
    ListAssetsOperator,
    ASSETLINKER_OT_fix_intermediate_files, # <-- Merged operator
    AssetListerPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.asset_lister_properties = bpy.props.PointerProperty(
        type=ASSETLISTER_PG_properties
    )

    # --- NEW (v3.0.0): Set initial log level ---
    try:
        # Check if context and scene exist, fallback if not (e.g., headless)
        if bpy.context and bpy.context.scene:
            props = bpy.context.scene.asset_lister_properties
            if props.debug_mode:
                log.setLevel(logging.INFO)
            else:
                log.setLevel(logging.WARNING)
        else:
            log.setLevel(logging.WARNING) # Fallback
    except Exception as e:
        log.setLevel(logging.WARNING) # Fallback
        log.warning(f"Could not set initial log level: {e}")
    
    log.info("Krutart Proxy-Master Switcher Registered.")
    # --- END NEW ---

    # Add the menu to the Outliner
    bpy.types.OUTLINER_MT_context_menu.append(ASSETLISTER_MT_outliner_menu)

def unregister():
    log.info("Krutart Proxy-Master Switcher Unregistered.")
    
    # Remove the menu from the Outliner
    bpy.types.OUTLINER_MT_context_menu.remove(ASSETLISTER_MT_outliner_menu)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    if hasattr(bpy.types.Scene, "asset_lister_properties"):
        del bpy.types.Scene.asset_lister_properties

if __name__ == "__main__":
    register()


    