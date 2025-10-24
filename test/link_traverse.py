bl_info = {
    "name": "Asset Suffix Lister",
    "author": "iori, krutart, Gemini",
    "version": (2, 4, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Asset Lister",
    "description": "Recursively lists linked assets, their file paths, and all available collections from source files.",
    "warning": "",
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

# --- Setup ---
# Set up a logger to print detailed messages to the Blender System Console.
# You can view this via Window > Toggle System Console.
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# --- PROPERTY GROUPS ---
# These define the data structures for our addon.

class ASSETLISTER_PG_asset_item(bpy.types.PropertyGroup):
    """Property group to hold a single asset's details (name, category, and source file path)."""
    name: bpy.props.StringProperty(name="Asset Name")
    category: bpy.props.StringProperty(name="Asset Category")
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


# --- OPERATORS ---

class ASSETLISTER_OT_open_file(bpy.types.Operator):
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
            # Construct the command to launch a new Blender process with the file.
            command = [bpy.app.binary_path, normalized_path]
            log.info(f"Executing command: {' '.join(command)}")
            
            # Use Popen to launch the new instance without blocking the current one.
            subprocess.Popen(command)
            self.report({'INFO'}, f"Launched new Blender instance for {os.path.basename(normalized_path)}.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to open file in new Blender instance: {e}")
            log.error(f"Subprocess error opening file: {e}", exc_info=True)
            return {'CANCELLED'}
        return {'FINISHED'}


class ASSETLISTER_OT_reveal_in_explorer(bpy.types.Operator):
    """Opens the directory of the given file path in the system's file explorer."""
    bl_idname = "asset_lister.reveal_in_explorer"
    bl_label = "Reveal in File Explorer"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def execute(self, context):
        if not self.filepath or not os.path.exists(self.filepath):
            self.report({'WARNING'}, "File path is invalid or file does not exist.")
            return {'CANCELLED'}
        
        folder_path = os.path.dirname(self.filepath)
        log.info(f"Opening folder in explorer: {folder_path}")
        
        # bpy.ops.wm.path_open is the simplest cross-platform way to open a path.
        bpy.ops.wm.path_open(filepath=folder_path)

        return {'FINISHED'}


class ListAssetsOperator(bpy.types.Operator):
    """Scans for assets and collections, separating linked from all available remote assets."""
    bl_idname = "wm.list_assets_operator"
    bl_label = "List Assets and Collections"

    background_script = """
import bpy
import sys
import os

# This script is designed to be run from a subprocess by the main addon.
# It gathers data about a .blend file and prints it as a dictionary string.

log_messages = []

try:
    suffixes_to_find = ['-p', '-m', '-P', '-M']
    output_data = {
        'assets': [],
        'linked_libraries': [] # For recursion
    }

    # 1. Get all linked library file paths for recursive search
    for lib in bpy.data.libraries:
        if lib.filepath:
            abs_path = bpy.path.abspath(lib.filepath)
            output_data['linked_libraries'].append(abs_path)
    log_messages.append(f"Found {len(output_data['linked_libraries'])} linked libraries.")


    # 2. Define all data-block types to search for assets
    data_blocks_to_scan = [
        ("Object", bpy.data.objects), ("Collection", bpy.data.collections),
        ("Mesh", bpy.data.meshes), ("Material", bpy.data.materials),
        ("Image", bpy.data.images), ("Texture", bpy.data.textures),
        ("Curve", bpy.data.curves), ("Light", bpy.data.lights),
        ("Camera", bpy.data.cameras), ("World", bpy.data.worlds),
    ]

    # 3. Find all assets in the file that match the suffixes
    found_assets = []
    for block_name, block_data in data_blocks_to_scan:
        if not block_data: continue
        for item in block_data:
            if not hasattr(item, "name"): continue
            for suffix in suffixes_to_find:
                if item.name.endswith(suffix):
                    # MODIFICATION: Also get the source file path if the item is linked.
                    # If it's local, the path is an empty string; the main addon will fill it in.
                    source_path = ""
                    if item.library and item.library.filepath:
                        source_path = bpy.path.abspath(item.library.filepath)

                    found_assets.append({
                        "name": item.name,
                        "category": block_name,
                        "source_filepath": source_path,
                    })
                    break # Avoid adding the same item multiple times

    output_data['assets'] = found_assets
    log_messages.append(f"Found {len(found_assets)} assets with matching suffixes.")

finally:
    # 4. Print the final dictionary as a string to stdout.
    print(repr({'logs': log_messages, 'data': output_data}))
    sys.stdout.flush()
"""

    def get_remote_file_data(self, filepath):
        """Runs Blender in the background to execute our script and returns the parsed data."""
        script_file = None
        log.info(f"Preparing to run background asset-finding script for: {filepath}")
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tf:
                script_file = tf.name
                tf.write(self.background_script)

            command = [bpy.app.binary_path, "-b", bpy.path.abspath(filepath), "--python", script_file]
            
            log.info(f"Executing command: {' '.join(command)}")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=90) # Increased timeout

            if process.returncode != 0:
                log.error(f"Background script for '{os.path.basename(filepath)}' finished with code {process.returncode}.")
                log.error(f"--- STDERR ---\n{stderr}\n--------------")
                return None
            
            if stdout:
                try:
                    parsed_output = ast.literal_eval(stdout.strip())
                    
                    if 'logs' in parsed_output and parsed_output['logs']:
                        log.info(f"Logs from background process for '{os.path.basename(filepath)}':")
                        for msg in parsed_output['logs']:
                            log.info(f"  > {msg}")

                    return parsed_output # Return the whole dict
                except (ValueError, SyntaxError) as e:
                    log.error(f"Could not parse stdout from '{os.path.basename(filepath)}': {e}")
                    log.error(f"--- STDOUT ---\n{stdout}\n--------------")
                    return None
            else:
                log.warning(f"Background script for '{os.path.basename(filepath)}' produced no stdout.")
                if stderr: log.warning(f"--- STDERR ---\n{stderr}\n--------------")
                
        except subprocess.TimeoutExpired:
            log.error(f"Timeout (90s) expired while processing remote file: {filepath}")
            if 'process' in locals() and process.poll() is None:
                process.kill()
        except Exception as e:
            log.error(f"Error in get_remote_file_data for {filepath}: {e}", exc_info=True)
        finally:
            if script_file and os.path.exists(script_file):
                try:
                    os.remove(script_file)
                except OSError as e:
                    log.error(f"Error removing temp file {script_file}: {e}")
        return None

    def execute(self, context):
        props = context.scene.asset_lister_properties
        props.file_groups.clear()
        props.searched = True

        log.info("="*50)
        log.info("Starting Asset Lister Scan...")

        # Master dictionary to hold all collected data, keyed by filepath
        grouped_data = {}

        # --- Step 1: Find assets linked directly into the current file ---
        suffixes_to_find = ['-p', '-m', '-P', '-M']
        data_blocks = [
            ("Object", bpy.data.objects), ("Collection", bpy.data.collections),
            ("Mesh", bpy.data.meshes), ("Material", bpy.data.materials),
            ("Image", bpy.data.images), ("Texture", bpy.data.textures),
            ("Curve", bpy.data.curves), ("Light", bpy.data.lights),
            ("Camera", bpy.data.cameras), ("World", bpy.data.worlds),
        ]
        for block_name, block_data in data_blocks:
            if not block_data: continue
            for item in block_data:
                if not hasattr(item, "name") or not item.library: continue
                for suffix in suffixes_to_find:
                    if item.name.endswith(suffix):
                        filepath = bpy.path.abspath(item.library.filepath)
                        if filepath not in grouped_data:
                            grouped_data[filepath] = {'linked_assets': [], 'remote_assets': [], 'collections': []}
                        
                        if not any(d['name'] == item.name for d in grouped_data[filepath]['linked_assets']):
                                grouped_data[filepath]['linked_assets'].append({
                                    "name": item.name,
                                    "category": block_name,
                                    "source_filepath": filepath # MODIFICATION: Store the asset's source path
                                })
                        break
        
        # --- Step 2: Recursive scan of all linked files ---
        initial_libs = [bpy.path.abspath(lib.filepath) for lib in bpy.data.libraries if lib.filepath]
        queue = list(initial_libs)
        scanned_files = set()

        log.info(f"Initial scan found {len(initial_libs)} direct libraries.")

        while queue:
            path = queue.pop(0)
            normalized_path = os.path.normpath(path)

            if not normalized_path or normalized_path in scanned_files:
                continue
            if not os.path.exists(normalized_path):
                log.warning(f"Skipping non-existent file path: {normalized_path}")
                continue

            scanned_files.add(normalized_path)
            log.info(f"--- Processing: {normalized_path} ---")

            # --- PART A: Scan for ASSETS and NESTED LIBS using background process ---
            remote_info = self.get_remote_file_data(normalized_path)

            if normalized_path not in grouped_data:
                grouped_data[normalized_path] = {'linked_assets': [], 'remote_assets': [], 'collections': []}

            if remote_info and 'data' in remote_info:
                remote_data = remote_info['data']
                log.info(f"Successfully parsed asset data for {normalized_path}.")
                
                # MODIFICATION: Process remote assets, filling in source path if empty.
                remote_assets_from_script = remote_data.get('assets', [])
                for asset_info in remote_assets_from_script:
                    # If the source path is empty, it's local to the file we just scanned
                    if not asset_info.get('source_filepath'):
                        asset_info['source_filepath'] = normalized_path
                grouped_data[normalized_path]['remote_assets'].extend(remote_assets_from_script)

                new_libraries = remote_data.get('linked_libraries', [])
                if new_libraries:
                    log.info(f"Found {len(new_libraries)} new libraries linked in {os.path.basename(normalized_path)}.")
                    for lib_path in new_libraries:
                        if lib_path not in scanned_files and lib_path not in queue:
                            queue.append(lib_path)
            else:
                log.error(f"Failed to retrieve asset data for {normalized_path}.")

            # --- PART B: Scan for COLLECTIONS using Collection Lister logic ---
            log.info(f"Listing collections directly from {os.path.basename(normalized_path)}...")
            try:
                with bpy.data.libraries.load(normalized_path, link=False) as (data_from, data_to):
                    collection_names = [coll_name for coll_name in data_from.collections]
                    if collection_names:
                        log.info(f"Found {len(collection_names)} collections: {', '.join(collection_names)}")
                        grouped_data[normalized_path]['collections'].extend(collection_names)
                    else:
                        log.info(f"No collections found in {os.path.basename(normalized_path)}.")
            except Exception as e:
                log.error(f"Could not list collections from '{normalized_path}'. Details: {e}", exc_info=True)


        # --- Step 3: Populate the UI Property Groups ---
        log.info("Scan complete. Populating UI...")
        if not grouped_data:
            log.info("No linked files or assets found.")
        
        for path, data in grouped_data.items():
            group = props.file_groups.add()
            group.filepath = path
            group.is_internal = False

            # MODIFICATION: Populate the new source_filepath property for each asset.
            for asset_data in sorted(data['linked_assets'], key=lambda x: x['category']):
                asset_item = group.linked_assets.add()
                asset_item.name = asset_data["name"]
                asset_item.category = asset_data["category"]
                asset_item.source_filepath = asset_data["source_filepath"]

            unique_remote_assets = {tuple(d.items()) for d in data['remote_assets']}
            for asset_tuple in sorted(unique_remote_assets, key=lambda x: dict(x)['category']):
                asset_data = dict(asset_tuple)
                asset_item = group.remote_assets.add()
                asset_item.name = asset_data["name"]
                asset_item.category = asset_data["category"]
                asset_item.source_filepath = asset_data["source_filepath"]
            
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


# --- UI PANEL ---

class AssetListerPanel(bpy.types.Panel):
    """Creates the UI Panel in the 3D Viewport."""
    bl_label = "Asset Suffix Lister"
    bl_idname = "VIEW3D_PT_asset_lister"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asset Lister'

    def draw_asset_list(self, layout, assets):
        """Helper function to draw a categorized list of assets with a reveal in explorer button."""
        asset_box = layout.box()
        current_category = ""
        for asset in assets:
            if asset.category != current_category:
                current_category = asset.category
                asset_box.label(text=f"--- {current_category}s ---", icon='DOT')
            
            # MODIFICATION: Use a split layout to add a folder icon button next to the name.
            split = asset_box.split(factor=0.95, align=True)
            split.label(text=f"  • {asset.name}")
            op = split.operator(ASSETLISTER_OT_reveal_in_explorer.bl_idname, text="", icon='FILE_FOLDER')
            op.filepath = asset.source_filepath

    def draw(self, context):
        layout = self.layout
        layout.operator("wm.list_assets_operator")

        props = context.scene.asset_lister_properties
        
        if not props.searched:
            layout.label(text="Press the button above to scan for assets.")
            return

        if not props.file_groups:
            layout.label(text="No linked assets or libraries found.")
            return

        for group in props.file_groups:
            file_box = layout.box()
            header_row = file_box.row(align=True)
            
            # MODIFICATION: Add an operator to open the file's containing folder.
            op_open = header_row.operator(
                ASSETLISTER_OT_open_file.bl_idname, 
                text=os.path.basename(group.filepath), 
                icon='LINKED'
            )
            op_open.filepath = group.filepath
            
            op_reveal = header_row.operator(ASSETLISTER_OT_reveal_in_explorer.bl_idname, text="", icon='FILE_FOLDER')
            op_reveal.filepath = group.filepath

            # --- 1. Linked Assets (in this file) ---
            if group.linked_assets:
                row = file_box.row(align=True)
                icon = 'TRIA_DOWN' if group.show_linked_assets else 'TRIA_RIGHT'
                row.prop(group, "show_linked_assets", text="", icon=icon, emboss=False)
                row.label(text=f"Linked Assets ({len(group.linked_assets)})")
                if group.show_linked_assets:
                    self.draw_asset_list(file_box, group.linked_assets)

            # --- 2. All Remote Assets (in source file) ---
            if group.remote_assets:
                row = file_box.row(align=True)
                icon = 'TRIA_DOWN' if group.show_remote_assets else 'TRIA_RIGHT'
                row.prop(group, "show_remote_assets", text="", icon=icon, emboss=False)
                row.label(text=f"All Remote Assets ({len(group.remote_assets)})")
                if group.show_remote_assets:
                    self.draw_asset_list(file_box, group.remote_assets)
            
            # --- 3. All Remote Collections ---
            if group.collections:
                row = file_box.row(align=True)
                icon = 'TRIA_DOWN' if group.show_collections else 'TRIA_RIGHT'
                row.prop(group, "show_collections", text="", icon=icon, emboss=False)
                row.label(text=f"All Remote Collections ({len(group.collections)})")
                if group.show_collections:
                    coll_box = file_box.box()
                    for coll in group.collections:
                        coll_box.label(text=f"  • {coll.name}", icon='OUTLINER_COLLECTION')


# --- REGISTRATION ---

classes = (
    ASSETLISTER_PG_asset_item,
    ASSETLISTER_PG_collection_item,
    ASSETLISTER_PG_file_group,
    ASSETLISTER_PG_properties,
    ASSETLISTER_OT_open_file,
    ASSETLISTER_OT_reveal_in_explorer, # MODIFICATION: Register the new operator
    ListAssetsOperator,
    AssetListerPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asset_lister_properties = bpy.props.PointerProperty(
        type=ASSETLISTER_PG_properties
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, "asset_lister_properties"):
        del bpy.types.Scene.asset_lister_properties

if __name__ == "__main__":
    register()
