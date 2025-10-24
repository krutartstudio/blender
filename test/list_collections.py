bl_info = {
    "name": "Collection Lister from Linked File",
    "author": "iori, krutart, Gemini",
    "version": (1, 2),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Collection Lister",
    "description": "Lists all collections from an external .blend file and displays them in the UI.",
    "warning": "",
    "doc_url": "",
    "category": "System",
}

import bpy
import os
import logging

# --- Setup a dedicated logger for this addon ---
# This ensures that the addon's messages are clearly identifiable.
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
logger = logging.getLogger(__name__)


# --- Property for storing a single collection name ---
# This is used by the CollectionProperty in the main properties class.
class CL_CollectionNameItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Collection Name")


# --- Properties ---
# This class holds the data for our addon.
class CL_Properties(bpy.types.PropertyGroup):
    linked_file_path: bpy.props.StringProperty(
        name="Blend File",
        description="Select the .blend file to inspect for collections",
        subtype='FILE_PATH'
    )
    # A collection to store the names of the found collections
    found_collections: bpy.props.CollectionProperty(
        type=CL_CollectionNameItem
    )
    # To track if a search has been performed to show the results area
    search_executed: bpy.props.BoolProperty(
        name="Search Executed",
        description="Whether the list operation has been run at least once",
        default=False
    )


# --- The Main Operator ---
# This class contains the logic that runs when the "List Collections" button is pressed.
class CL_OT_ListCollections(bpy.types.Operator):
    """Lists all collections from the selected .blend file"""
    bl_idname = "collection_lister.list_all"
    bl_label = "List Collections"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # The operator can only be run if a file path has been selected.
        props = context.scene.collection_lister_props
        return props.linked_file_path != ""

    def execute(self, context):
        """
        Executes the collection listing process.
        1. Validates the file path.
        2. Reads the file's contents using bpy.data.libraries.load().
        3. Populates the 'found_collections' property for the UI.
        4. Logs all collections found in the external file.
        """
        props = context.scene.collection_lister_props
        filepath = bpy.path.abspath(props.linked_file_path)

        # --- Clear previous results and set status ---
        props.found_collections.clear()
        props.search_executed = True
        
        logger.info(f"--- Starting Collection Lister Operator ---")
        logger.info(f"Attempting to read file: {filepath}")

        # --- Input Validation ---
        if not os.path.exists(filepath):
            error_message = f"File not found at path: {filepath}"
            logger.error(error_message)
            self.report({'ERROR'}, error_message)
            return {'CANCELLED'}

        if not filepath.lower().endswith(".blend"):
            error_message = f"Selected file is not a .blend file: {filepath}"
            logger.warning(error_message)
            self.report({'WARNING'}, error_message)
            # We can still try to read it, so we don't cancel here.

        # --- Core Logic ---
        try:
            with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
                # We load the names of the collections from the source file (data_from)
                data_to.collections = data_from.collections

            logger.info(f"Successfully read data from: {os.path.basename(filepath)}")

            # --- Reporting and Populating Results ---
            if not data_to.collections:
                info_message = "No collections were found in the selected file."
                logger.info(info_message)
                self.report({'INFO'}, info_message)
            else:
                num_collections = len(data_to.collections)
                success_message = f"Found {num_collections} collection(s):"
                logger.info(success_message)
                
                print("\n--- Collections in '{}' ---".format(os.path.basename(filepath)))
                # The loop iterates over Collection objects, not strings. We need to access the .name property.
                for collection in data_to.collections:
                    # Add the found collection name to our property for UI display
                    item = props.found_collections.add()
                    item.name = collection.name
                    
                    # Log and print to console as before
                    logger.info(f"  - Found: '{collection.name}'")
                    print(f"  - {collection.name}")
                print("---------------------------------\n")

                self.report({'INFO'}, f"Listed {num_collections} collection(s). See panel for results.")

        except Exception as e:
            error_message = f"An error occurred while reading the file. Is it a valid .blend file? Details: {e}"
            logger.critical(error_message, exc_info=True)
            self.report({'ERROR'}, error_message)
            return {'CANCELLED'}

        logger.info("--- Operator Finished ---")
        return {'FINISHED'}


# --- UI Panel ---
# This class defines how the addon looks in the Blender UI.
class CL_PT_MainPanel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport Sidebar"""
    bl_label = "Collection Lister"
    bl_idname = "CL_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Collection Lister' # The name of the tab in the sidebar

    def draw(self, context):
        layout = self.layout
        props = context.scene.collection_lister_props

        # --- File Selection UI ---
        box = layout.box()
        box.label(text="Select Linked File", icon='FILE_BLEND')
        box.prop(props, "linked_file_path", text="")

        # --- Operator Button ---
        row = layout.row()
        row.scale_y = 1.5
        row.operator(CL_OT_ListCollections.bl_idname, icon='VIEWZOOM')
        
        # --- Results Area ---
        # This section only appears after the operator has been run.
        if props.search_executed:
            results_box = layout.box()
            
            # If collections were found, list them.
            if props.found_collections:
                results_box.label(text="Found Collections:", icon='OUTLINER_COLLECTION')
                for item in props.found_collections:
                    results_box.label(text=f"• {item.name}")
            # Otherwise, show a "not found" message.
            else:
                results_box.label(text="No collections were found.", icon='INFO')


# --- Registration ---
classes = [
    CL_CollectionNameItem,
    CL_Properties,
    CL_OT_ListCollections,
    CL_PT_MainPanel,
]

def register():
    """Registers all addon classes with Blender."""
    logger.info("Registering Collection Lister Addon")
    for cls in classes:
        bpy.utils.register_class(cls)
    # Create an instance of the properties class on the scene object
    bpy.types.Scene.collection_lister_props = bpy.props.PointerProperty(type=CL_Properties)
    logger.info("Registration complete.")

def unregister():
    """Unregisters all addon classes and cleans up."""
    logger.info("Unregistering Collection Lister Addon")
    del bpy.types.Scene.collection_lister_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    logger.info("Unregistration complete.")


if __name__ == "__main__":
    register()

