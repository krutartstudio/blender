bl_info = {
    "name": "Krutart Proxy/Master Switcher",
    "author": "iori, Krutart, Gemini",
    "version": (1, 6, 1), 
    "blender": (4, 0, 0), 
    "location": "View3D > Sidebar (N-Panel, Toggleable) & Outliner Context Menu",
    "description": "Seamlessly swap between -p (proxy) and -m (master) asset versions preserving hierarchy. Supports Batch Swapping.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import os
import re

# --- Constants ---
# Recognized prefixes for swappable asset datablocks or their enclosing Collections.
ASSET_PREFIXES = ("MODEL-", "MOD-", "MOD - ", "PROP-", "PRP-", "PRP - ")

# --- Utility Functions ---
from bpy.props import (
    StringProperty,
    BoolProperty,
    PointerProperty,
    CollectionProperty,
    EnumProperty,
)
from bpy.types import (
    PropertyGroup,
    Operator,
    Panel,
    Menu,
)

# --- Task 1.2: Debug System ---

def get_addon_prefs(context):
    """Helper function to get the addon preferences"""
    addon = context.preferences.addons.get(__name__)
    if addon:
        return addon.preferences
    return None

def debug_log(message):
    """
    Custom logging function that prints to the console only if
    debug_mode is True.
    """
    prefs = None
    try:
        # Use bpy.context if available
        if bpy.context:
            addon = bpy.context.preferences.addons.get(__name__)
            if addon:
                prefs = addon.preferences
    except Exception:
         # Fallback during registration
        try:
            prefs = bpy.context.preferences.addons[__name__].preferences
        except Exception:
            pass

    if (prefs and getattr(prefs, "debug_mode", False)) or "Registering" in message or "Unregistering" in message:
        print(f"Asset Switcher DEBUG: {message}")

# --- Phase 2: Asset Data-Block Analysis ---

def get_base_data_block(obj):
    """
    Helper function to "dig" into a wrapper object and find the
    actual data-block we might want to swap.

    Returns a tuple: (data_block, property_name_to_swap)
    e.g., (bpy.data.collections['ASSET-P'], 'instance_collection')
    """
    if not obj:
        return None, None

    # Case 1: Empty instancing a collection (most common)
    if obj.type == 'EMPTY' and obj.instance_collection:
        return obj.instance_collection, "instance_collection"

    # Case 2: Linked/Local Mesh, Curve, etc.
    if obj.type in {'MESH', 'CURVE', 'ARMATURE', 'LIGHT', 'CAMERA'} and obj.data:
        return obj.data, "data"
    
    # Case 3: Override Collections
    # This check is now handled by get_asset_details_case_2
    # But we still need this for Case 1 overrides (if that's a thing)
    if obj.is_instancer and obj.instance_type == 'COLLECTION' and obj.instance_collection:
        return obj.instance_collection, "instance_collection"

    debug_log(f"Could not find a swappable base data-block for object '{obj.name}'")
    return None, None

def get_asset_details_from_name(name, data_block, swap_property, wrapper_obj):
    """
    Helper function to parse a name and build the details dictionary.
    This avoids code duplication in get_asset_details.
    """
    
    # --- Isolate the duplication suffix (e.g., '.005') ---
    suffix_match = re.search(r'(\.\d{3,})$', wrapper_obj.name)
    name_suffix = suffix_match.group(1) if suffix_match else ""
    
    clean_match = re.search(r'(\.\d{3,})$', name)
    clean_name = name[:-len(clean_match.group(1))] if clean_match else name
    
    name_lower = clean_name.lower() 
    current_version_flag = None
    tandem_version_flag = None
    base_name = None
    tandem_version_name = None

    p_flag_found = False
    p_index = -1
    
    # Find the last '-p' or '-P'
    # We check for '-P' and '-p' separately
    p_indices = [clean_name.rfind('-p'), clean_name.rfind('-P')]
    p_dash_index = max(p_indices) # This is the index of the dash '-'

    if p_dash_index != -1:
        # Check what follows the flag. It must be either end-of-string or a separator.
        p_char_index = p_dash_index + 1 # This is the index of 'p' or 'P'
        
        # Check if it's at the end
        if p_char_index == len(clean_name) - 1:
            p_flag_found = True
            p_index = p_char_index
        # Check if it's followed by a separator
        elif p_char_index + 1 < len(clean_name) and clean_name[p_char_index + 1] in {'-', '_', '.'}:
            p_flag_found = True
            p_index = p_char_index
            
    m_flag_found = False
    m_index = -1
    
    # Find the last '-m' or '-M'
    m_indices = [clean_name.rfind('-m'), clean_name.rfind('-M')]
    m_dash_index = max(m_indices)

    if m_dash_index != -1:
        # Check what follows the flag
        m_char_index = m_dash_index + 1 # This is the index of 'm' or 'M'
        
        # Check if it's at the end
        if m_char_index == len(clean_name) - 1:
            m_flag_found = True
            m_index = m_char_index
        # Check if it's followed by a separator
        elif m_char_index + 1 < len(clean_name) and clean_name[m_char_index + 1] in {'-', '_', '.'}:
            m_flag_found = True
            m_index = m_char_index

    # Prioritize 'p' if both are somehow found (e.g. "asset-m-proxy-p")
    if p_flag_found and m_flag_found:
        if p_index > m_index:
            m_flag_found = False
        else:
            p_flag_found = False

    if p_flag_found:
        current_version_flag = "-p"
        tandem_version_flag = "-m"
        
        original_flag_char = clean_name[p_index] # This will be 'p' or 'P'
        tandem_flag_char = 'm'
        if original_flag_char.isupper():
            tandem_flag_char = 'M'
        
        base_name = clean_name[:p_index]
        suffix_part = clean_name[p_index + 1:] # Get text after 'p' or 'P'
        tandem_version_name = f"{base_name}{tandem_flag_char}{suffix_part}" # Use the correctly-cased char
        
    elif m_flag_found:
        current_version_flag = "-m"
        tandem_version_flag = "-p"

        original_flag_char = clean_name[m_index] # This will be 'm' or 'M'
        tandem_flag_char = 'p'
        if original_flag_char.isupper():
            tandem_flag_char = 'P'

        base_name = clean_name[:m_index]
        suffix_part = clean_name[m_index + 1:] # Get text after 'm' or 'M'
        tandem_version_name = f"{base_name}{tandem_flag_char}{suffix_part}" # Use the correctly-cased char
        
    else:
        # This name didn't have a flag.
        return None

    # --- Find Source Filepath ---
    source_filepath = None
    is_local = False
    
    if swap_property == "LIBRARY_OVERRIDE":
        debug_log(f"Override asset detected. Getting source from override main.")
        
        # Blender 4.0 / 4.1+ API compatibility
        override_prop = None
        if hasattr(data_block, "library_override"):
            override_prop = data_block.library_override # Blender 4.1+
        elif hasattr(data_block, "override_library"):
            override_prop = data_block.override_library # Blender 4.0
        
        if data_block and override_prop:
            # Get the *original* linked asset's library
            original_main = None
            # .reference is used in 4.0 and 4.2+
            if hasattr(override_prop, "reference"):
                original_main = override_prop.reference
            elif hasattr(override_prop, "main"):
                original_main = override_prop.main # Blender 4.1

            if original_main and original_main.library:
                source_filepath = original_main.library.filepath
                debug_log(f"Found linked override asset. Source: {source_filepath}")
            else:
                debug_log(f"Override collection '{data_block.name}' has no 'main'/'reference' or library.")
                is_local = True # Fallback
        else:
             is_local = True # Fallback
    
    if not source_filepath:
        # Original logic for non-override assets
        if data_block and data_block.library:
            source_filepath = data_block.library.filepath
            debug_log(f"Found linked asset. Source: {source_filepath}")
        else:
            is_local = True
            source_filepath = bpy.data.filepath # Current file
            if is_local:
                debug_log("Found local asset.")
            
    if not source_filepath:
            debug_log("Could not determine source filepath. Skipping.")
            return None

    # --- START PATH RESOLUTION (v1.3.0) ---
    def resolve_path(dirty_path):
        import sys
        from pathlib import Path
        
        if not dirty_path or sys.platform.startswith("win"):
            return dirty_path
            
        # We are on Mac/Linux. Try to translate standard Windows paths
        PROJECT_NAME = "3212-PREPRODUCTION"
        clean_str = dirty_path.replace("\\", "/")
        idx = clean_str.find(PROJECT_NAME)
        
        if idx != -1:
            relative_part = clean_str[idx:]
            
            # Find local Mac root
            mac_root = None
            if bpy.data.filepath:
                curr = Path(bpy.data.filepath).resolve()
                for p in [curr] + list(curr.parents):
                    if p.name == PROJECT_NAME:
                        mac_root = p
                        break
            
            if not mac_root:
                home = Path.home()
                candidates = [
                    home / "Library/CloudStorage/GoogleDrive-jorik.chase@krutart.cz/Shared drives" / PROJECT_NAME,
                    home / "Library/CloudStorage/GoogleDrive-handak.daniel@gmail.com/Shared drives" / PROJECT_NAME,
                    Path(f"/Volumes/GoogleDrive/Shared drives/{PROJECT_NAME}"),
                ]
                for cand in candidates:
                    if cand.exists():
                        mac_root = cand
                        break
                        
            if mac_root:
                target = mac_root.parent / relative_part
                if target.exists():
                    debug_log(f"Path Translator: Resolved '{dirty_path}' -> '{target}'")
                    return str(target)
                else:
                    debug_log(f"Path Translator: Target does not exist on disk: '{target}'")
                    
        return dirty_path
    # --- END PATH RESOLUTION ---

    # Resolve the path using the new translator
    final_source_filepath = resolve_path(os.path.realpath(bpy.path.abspath(source_filepath)))

    details = {
        "wrapper_object": wrapper_obj,
        "base_data_block": data_block,
        "base_name": base_name,
        "swap_property": swap_property,
        "source_filepath": final_source_filepath,
        "is_local": is_local,
        "current_version_flag": current_version_flag,
        "tandem_version_flag": tandem_version_flag,
        "tandem_version_name": tandem_version_name, # CLEAN target name for finding in external library
        "name_suffix": name_suffix,                 # Saved .xxx suffix to append post-swap
        "clean_base_name": clean_name,              # Clean base name for validation
        "asset_name_source": name # Store the original name we matched on
    }

    return details


def get_asset_details_case_1(obj, valid_asset_datablocks):
    """
    Performs ONLY a Case 1 check.
    Checks if the object's direct data-block (instance_collection or data)
    is a valid asset.
    """
    if not obj:
        return None

    debug_log(f"--- (Case 1) Checking: {obj.name} ---")
    base_data_block, swap_property = get_base_data_block(obj)

    if base_data_block:
        details = get_asset_details_from_name(
            base_data_block.name,
            base_data_block,
            swap_property,
            obj
        )
        if details:
            if base_data_block in valid_asset_datablocks:
                debug_log(f"    (Case 1) SUCCESS: Found valid asset wrapper.")
                return details
            else:
                debug_log(f"    (Case 1) FAILED: Data-block '{base_data_block.name}' is not a root asset.")
    else:
       debug_log(f"    (Case 1) FAILED: No base data block found.")
    
    return None

def get_asset_details_case_2(obj, valid_asset_datablocks):
    """
    Performs ONLY a Case 2 check.
    Checks if the object is a child of a valid asset collection.
    """
    if not obj:
        return None

    debug_log(f"--- (Case 2) Checking: {obj.name} ---")
    
    # This is the "Linked Contents" check
    for coll in obj.users_collection:
        if coll in valid_asset_datablocks:
            # This object is part of a valid asset collection.
            debug_log(f"    (Case 2) Found valid parent asset collection: {coll.name}")
            
            # We have found the asset. The asset IS the collection.
            # The swap property is special.
            details = get_asset_details_from_name(
                coll.name,            # Asset name comes from the collection
                coll,                 # The data-block *is* the collection
                "LIBRARY_OVERRIDE", # Special flag for swap operator
                obj                   # The wrapper is still the scene object
            )
            
            if details:
                # Store the *actual* collection to be swapped
                details['base_data_block'] = coll
                debug_log(f"    (Case 2) SUCCESS: Found valid asset content. Will swap override for collection '{coll.name}'")
                return details
    
    debug_log(f"    (Case 2) FAILED: No valid parent asset found.")
    return None


def get_asset_details(obj, valid_asset_datablocks):
    """
    Analyzes a swappable object or collection and returns a dictionary
    of its core properties. Tries Case 1, then Case 2.
    """
    if not obj:
        return None

    debug_log(f"--- Getting details for: {obj.name} (Combined) ---")
    
    # Handle the case where the wrapper is a Collection (common in Overrides)
    if isinstance(obj, bpy.types.Collection):
        debug_log(f"    (Combined) Input is a Collection: {obj.name}")
        if obj in valid_asset_datablocks:
             return get_asset_details_from_name(
                obj.name, 
                obj, 
                "LIBRARY_OVERRIDE", 
                obj # Collection acts as its own wrapper
            )

    # --- Try Case 1 First (for Objects) ---
    if isinstance(obj, bpy.types.Object):
        details = get_asset_details_case_1(obj, valid_asset_datablocks)
        if details:
            debug_log(f"    (Combined) Case 1 Succeeded for {obj.name}")
            return details
        
        # --- If Case 1 Failed, Try Case 2 ---
        debug_log(f"    (Combined) Case 1 Failed. Trying Case 2 for {obj.name}")
        details = get_asset_details_case_2(obj, valid_asset_datablocks)
        if details:
            debug_log(f"    (Combined) Case 2 Succeeded for {obj.name}")
            return details

    debug_log(f"    (Combined) All cases failed for {obj.name}.")
    return None


# --- Phase 1: Core Foundation & Asset Discovery ---
class SwappableAsset(PropertyGroup):
    """
    Stores the name of a *wrapper object* in the scene
    that has been identified as a swappable asset.
    """
    name: StringProperty(
        name="Object Name",
        description="The name of the wrapper object in the scene"
    )
    is_valid: BoolProperty(default=False)
    ui_label: StringProperty()
    ui_icon: StringProperty()
    current_version: StringProperty()
    tandem_version: StringProperty()
    swap_property: StringProperty()

class AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    debug_mode: BoolProperty(
        name="Enable Debug Logging",
        description="Prints verbose DEBUG logs to the System Console",
        default=False,
    )

    show_n_panel: BoolProperty(
        name="Show in N-Panel",
        description="Toggle the visibility of the Asset Switcher in the 3D View N-Panel",
        default=False,
    )

    swappable_assets: CollectionProperty(
        name="Swappable Assets",
        description="List of discovered swappable wrapper objects in the scene",
        type=SwappableAsset,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "show_n_panel")
        layout.prop(self, "debug_mode")

class MY_OT_refresh_assets(Operator):
    """
    Asset Discovery Operator.
    1. Scans MODEL- collections for *all* valid asset data-blocks.
    2. Scans the *entire scene* for objects that instance those assets.
    """
    bl_idname = "asset_switcher.refresh_assets"
    bl_label = "Refresh switcher p/m"
    bl_description = "Scan the scene for swappable proxy/master assets"

    def execute(self, context):
        debug_log(f"=== Refreshing Asset List (v{bl_info['version'][0]}.{bl_info['version'][1]}.{bl_info['version'][2]}) ===")
        prefs = get_addon_prefs(context)
        if not prefs:
            self.report({'ERROR'}, "Could not get addon preferences.")
            return {'CANCELLED'}
            
        asset_list = prefs.swappable_assets
        asset_list.clear()

        # --- Step 1: Find all valid 'asset data-blocks' ---
        valid_asset_datablocks = set()

        # Directly scan for collections that start with the prefix
        for coll in bpy.data.collections:
            if coll.name.startswith(ASSET_PREFIXES):
                debug_log(f"    Found asset datablock directly via collection name: {coll.name}")
                valid_asset_datablocks.add(coll)
                
        # Directly scan for objects that start with the prefix
        for obj in bpy.data.objects:
            if obj.name.startswith(ASSET_PREFIXES):
                base_data, _ = get_base_data_block(obj)
                if base_data and base_data not in valid_asset_datablocks:
                   debug_log(f"    Found asset datablock directly via object name '{obj.name}': {base_data.name}")
                   valid_asset_datablocks.add(base_data)

        if not valid_asset_datablocks:
            debug_log(f"No valid asset datablocks found matching prefixes {ASSET_PREFIXES}.")
            self.report({'INFO'}, "No swappable assets found.")
            asset_list.clear()
            return {'FINISHED'}
            
        debug_log(f"Found {len(valid_asset_datablocks)} unique asset datablocks.")
        
        # Create a set of just the *names* for a more reliable string-based lookup
        valid_asset_names = {db.name for db in valid_asset_datablocks}
        debug_log(f"Valid asset names: {valid_asset_names}")

        # --- Step 2: Find all scene objects that *use* these assets ---
        found_wrapper_objects = [] # Use a list to preserve order
        processed_wrapper_names = set() # Tracks exact wrapper names to prevent duplicate suffix merging

        # --- PASS A: Find all Case 1 (Main Wrapper) objects ---
        debug_log("--- Running Pass A: Searching for Case 1 Wrappers ---")
        for obj in context.scene.objects:
            details = get_asset_details_case_1(obj, valid_asset_datablocks)
            
            if details:
                asset_name = details['asset_name_source']
                clean_base = details.get('clean_base_name', asset_name)
                wrapper_name = details['wrapper_object'].name
                
                if wrapper_name in processed_wrapper_names:
                    continue

                if clean_base in valid_asset_names or asset_name in valid_asset_names:
                    debug_log(f"    +++ PASS A: Found Case 1 Wrapper '{wrapper_name}' for asset '{asset_name}'")
                    found_wrapper_objects.append(wrapper_name)
                    processed_wrapper_names.add(wrapper_name)
                else:
                    debug_log(f"    --- REJECTED (PASS A): '{wrapper_name}'. Asset '{asset_name}' not in valid list.")

        # --- PASS B: Find Collection Wrappers (Overrides) ---
        debug_log("--- Running Pass B: Searching for Collection Wrappers ---")
        # We can just iterate over the valid_asset_datablocks we already found!
        for coll in valid_asset_datablocks:
            if isinstance(coll, bpy.types.Collection):
                # An override collection should NEVER directly have a library flag.
                if getattr(coll, "library", None) is not None:
                    continue
                    
                details = get_asset_details(coll, valid_asset_datablocks)
                if details:
                    asset_name = details['asset_name_source']
                    clean_base = details.get('clean_base_name', asset_name)
                    wrapper_name = details['wrapper_object'].name
                    
                    if wrapper_name in processed_wrapper_names:
                        debug_log(f"    --- SKIPPED (PASS B): '{wrapper_name}'. Wrapper already found.")
                        continue 
                    
                    if clean_base in valid_asset_names or asset_name in valid_asset_names:
                        debug_log(f"    +++ PASS B: Found Collection Wrapper '{wrapper_name}' for asset '{asset_name}'")
                        found_wrapper_objects.append(wrapper_name)
                        processed_wrapper_names.add(wrapper_name)
                    else:
                        debug_log(f"    --- REJECTED (PASS B): '{wrapper_name}'. Asset '{asset_name}' not in valid list.")

        # --- PASS C: Find all Case 2 (Linked Content) objects ---
        debug_log("--- Running Pass C: Searching for Case 2 Contents ---")
        for obj in context.scene.objects:
            details = get_asset_details_case_2(obj, valid_asset_datablocks)
            
            if details:
                asset_name = details['asset_name_source']
                clean_base = details.get('clean_base_name', asset_name)
                wrapper_name = details['wrapper_object'].name
                
                # Check if we already found a Case 1 wrapper for this asset
                if wrapper_name in processed_wrapper_names:
                    debug_log(f"    --- SKIPPED (PASS C): '{wrapper_name}'. Wrapper already found.")
                    continue 
                    
                if clean_base in valid_asset_names or asset_name in valid_asset_names:
                    debug_log(f"    +++ PASS C: Found Case 2 Content '{wrapper_name}' for asset '{asset_name}'")
                    found_wrapper_objects.append(wrapper_name)
                    processed_wrapper_names.add(wrapper_name) 
                else:
                    debug_log(f"    --- REJECTED (PASS C): '{wrapper_name}'. Asset '{asset_name}' not in valid list.")


        # --- Step 3: Populate the final list AND CACHE Details ---
        asset_list.clear()
        # Deduplicate with set() before sorting to ensure objects matching multiple cases don't appear twice
        unique_found_objects = set(found_wrapper_objects)
        for obj_name in sorted(list(unique_found_objects)):
            new_asset_item = asset_list.add()
            new_asset_item.name = obj_name
            
            # Resolve the object/collection
            obj = context.scene.objects.get(obj_name)
            if not obj:
                obj = bpy.data.collections.get(obj_name)
                
            if obj:
                # Do the heavy lifting ONCE here during the refresh operation
                details = get_asset_details(obj, valid_asset_datablocks)
                if details:
                    new_asset_item.is_valid = True
                    new_asset_item.current_version = details['current_version_flag'].upper()
                    new_asset_item.tandem_version = details['tandem_version_flag'].upper()
                    new_asset_item.swap_property = details.get('swap_property', '')
                    
                    # Generate the nice label name
                    label_name = details['base_name']
                    # Attempt to strip the recognized prefix for cleaner UI
                    for prefix in ASSET_PREFIXES:
                        if label_name.startswith(prefix):
                            label_name = label_name[len(prefix):]
                            break
                    # Display the suffix to easily differentiate multiple instances
                    new_asset_item.ui_label = label_name + details.get('name_suffix', '')
                    
                    # Determine Icon
                    icon_type = 'OBJECT_DATAMODE' # Default
                    if details.get('swap_property') == 'LIBRARY_OVERRIDE':
                        icon_type = 'LIBRARY_DATA_OVERRIDE'
                    elif details.get('swap_property') == "instance_collection":
                        icon_type = 'OUTLINER_COLLECTION'
                    elif details.get('swap_property') == 'data':
                        if isinstance(obj, bpy.types.Object):
                            if obj.type == 'MESH':
                                icon_type = 'MESH_DATA'
                            elif obj.type == 'ARMATURE':
                                icon_type = 'ARMATURE_DATA'
                            elif obj.type == 'CURVE':
                                icon_type = 'CURVE_DATA'
                    new_asset_item.ui_icon = icon_type

        found_count = len(unique_found_objects) # Use the count of unique objects
        debug_log(f"=== Found {found_count} swappable assets ===")
        self.report({'INFO'}, f"Found {found_count} swappable assets.")
        return {'FINISHED'}


# --- Phase 3 & 4: Swap Logic ---
class MY_OT_swap_asset_version(Operator):
    """
    Swaps an asset to its tandem version. 
    Can handle single target (Panel) or multiple selected objects (Outliner).
    """
    bl_idname = "asset_switcher.swap_version"
    bl_label = "Swap Asset Version"
    bl_description = "Swap this asset to its tandem version"
    bl_options = {'REGISTER', 'UNDO'}

    target_object_name: StringProperty(
        name="Target Object Name",
        description="The scene object to perform the swap on"
    )
    
    use_selection: BoolProperty(
        name="Use Selection",
        description="If True, swaps all selected valid assets",
        default=False
    )
    
    force_state: EnumProperty(
        name="Force State",
        description="Force a specific target state, or toggle based on current state",
        items=[
            ('TOGGLE', "Toggle", "Swap to the tandem version"),
            ('PROXY', "Make Proxy", "Force state to Proxy (-p)"),
            ('MASTER', "Make Master", "Force state to Master (-m)"),
        ],
        default='TOGGLE'
    )

    def execute(self, context):
        
        # 1. Determine List of Objects to Swap
        objects_to_process = []
        
        if self.target_object_name:
            # Single object mode (via Panel)
            # Check objects first, then collections
            target = context.scene.objects.get(self.target_object_name)
            if not target:
                target = bpy.data.collections.get(self.target_object_name)
                
            if target:
                objects_to_process.append(target)
            else:
                self.report({'ERROR'}, f"Asset '{self.target_object_name}' not found in objects or collections.")
                return {'CANCELLED'}
        elif self.use_selection:
            # Batch mode (via Outliner Context Menu)
            potential_items = set()
            if context.selected_objects:
                potential_items.update(context.selected_objects)
            if hasattr(context, "selected_ids"):
                for id_data in context.selected_ids:
                    if isinstance(id_data, (bpy.types.Object, bpy.types.Collection)):
                        potential_items.add(id_data)
            
            # Fallback: context.collection (Outliner selection)
            if hasattr(context, "collection"):
                if context.collection:
                    potential_items.add(context.collection)
                        
            # Fallback to active
            if not potential_items and context.active_object:
                potential_items.add(context.active_object)
            
            objects_to_process = list(potential_items)
            
            if not objects_to_process:
                self.report({'WARNING'}, "No objects or collections selected.")
                return {'CANCELLED'}
        else:
             self.report({'ERROR'}, "No target specified.")
             return {'CANCELLED'}

        debug_log(f"--- SWAP REQUESTED FOR {len(objects_to_process)} OBJECTS ---")

        # 2. Build Valid Asset Database (Run ONCE for the whole batch)
        # This prevents O(N^2) complexity where we re-scan the scene for every single object in the batch
        prefs = get_addon_prefs(context)
        valid_asset_datablocks = set()
        
        # --- NEW LOGIC (v1.2.x+): Direct Prefix Detection ---
        for coll in bpy.data.collections:
            if coll.name.startswith(ASSET_PREFIXES):
                valid_asset_datablocks.add(coll)
                
        for obj_db in bpy.data.objects:
            if obj_db.name.startswith(ASSET_PREFIXES):
                base_data, _ = get_base_data_block(obj_db)
                if base_data:
                    valid_asset_datablocks.add(base_data)

        # 3. Processing Loop
        swap_count = 0
        error_count = 0
        
        # Deduplication Set: We must deduplicate by the specific WRAPPER (e.g. the Empty or the Override Collection).
        # Deduplicating by the base_data_block was causing multiple proxies of the same asset to be skipped.
        processed_wrapper_names = set()

        for obj in objects_to_process:
            # Safety check: Ensure the object hasn't been invalidated (e.g., deleted by a previous iteration)
            try:
                _ = obj.name
            except ReferenceError:
                continue
                
            # --- Get Details ---
            details = get_asset_details(obj, valid_asset_datablocks)
            if not details:
                debug_log(f"Skipping '{getattr(obj, 'name', 'Unknown')}': Not a valid swappable asset.")
                continue

            # --- State Filter (Prevents toggling back and forth when batch selecting) ---
            current_flag = details.get('current_version_flag', '').lower()
            if self.force_state == 'PROXY' and current_flag == '-p':
                debug_log(f"Skipping '{getattr(obj, 'name', 'Unknown')}': Already a Proxy.")
                continue
            if self.force_state == 'MASTER' and current_flag == '-m':
                debug_log(f"Skipping '{getattr(obj, 'name', 'Unknown')}': Already a Master.")
                continue

            # --- Deduplication Check ---
            # Use the exact wrapper object's name as the unique ID
            wrapper = details['wrapper_object']
            wrapper_name = getattr(wrapper, 'name', None)
            
            if not wrapper_name or wrapper_name in processed_wrapper_names:
                debug_log(f"Skipping '{wrapper_name}': Already swapped in this batch.")
                continue
            
            # Mark as processed immediately
            processed_wrapper_names.add(wrapper_name)

            # --- Perform Swap ---
            try:
                self.perform_swap(context, obj, details)
                swap_count += 1
            except Exception as e:
                import traceback
                traceback.print_exc()
                debug_log(f"ERROR swapping '{wrapper_name}': {e}")
                error_count += 1
        
        # 4. Final Cleanup
        if swap_count > 0:
            self.report({'INFO'}, f"Swapped {swap_count} assets.")
            
            # Deferred UI Refresh (Run ONCE at the end)
            def refresh_op():
                try:
                    bpy.ops.asset_switcher.refresh_assets('EXEC_DEFAULT')
                except Exception as e:
                    debug_log(f"Error during deferred refresh: {e}")
                return None 
            
            bpy.app.timers.register(refresh_op, first_interval=0.1)
            
        elif error_count > 0:
            self.report({'ERROR'}, "Failed to swap selected assets. Check console.")
        else:
            self.report({'WARNING'}, "No valid swappable assets found in selection (or they are already in the correct state).")

        return {'FINISHED'}

    def perform_swap(self, context, obj, details):
        """
        Encapsulated swap logic for a single object.
        """
        tandem_name = details['tandem_version_name'] # Clean base name WITHOUT .xxx
        name_suffix = details.get('name_suffix', '') # Suffix .xxx isolated earlier
        final_target_name = f"{tandem_name}{name_suffix}" # Full exact target name for Outliner
        
        source_path = details['source_filepath']
        is_local = details['is_local']
        swap_property = details['swap_property'] 
        
        debug_log(f"Swapping '{obj.name}' -> '{final_target_name}' (via source '{tandem_name}')")
        
        # Determine data type (collections, meshes, etc.)
        data_block_type = type(details['base_data_block'])
        data_target_lib = None
        data_list_name_str = "" 

        if swap_property == "LIBRARY_OVERRIDE":
            data_target_lib = bpy.data.collections
            data_list_name_str = "collections"

        elif swap_property == "instance_collection":
            data_target_lib = bpy.data.collections
            data_list_name_str = "collections"
            
        elif swap_property == "data":
            if data_block_type == bpy.types.Mesh:
                data_target_lib = bpy.data.meshes
                data_list_name_str = "meshes"
            elif data_block_type == bpy.types.Armature:
                data_target_lib = bpy.data.armatures
                data_list_name_str = "armatures"
            elif data_block_type == bpy.types.Curve:
                data_target_lib = bpy.data.curves
                data_list_name_str = "curves"
            elif data_block_type == bpy.types.Light:
                data_target_lib = bpy.data.lights
                data_list_name_str = "lights"
            elif data_block_type == bpy.types.Camera:
                data_target_lib = bpy.data.cameras
                data_list_name_str = "cameras"
        
        if not data_target_lib:
            # Handle the case where base_data_block is None (Empty guessing)
            if details['base_data_block'] is None:
                if obj.type == 'EMPTY':
                    data_target_lib = bpy.data.collections
                    data_list_name_str = "collections"
                    swap_property = "instance_collection" 
                    details['swap_property'] = "instance_collection"
                else:
                    raise Exception(f"Cannot determine data type for swap: {obj.name}")
            else:
                raise Exception(f"Unsupported data block type: {data_block_type}")

        # --- 2. Smart Fetcher: Prevents Overrides of Overrides by strictly finding Linked Data ---
        tandem_data_block = None
        
        if not is_local:
            # Priority 1: Find pure linked data block matching base name (ignores overrides)
            for block in data_target_lib:
                b_name_clean = re.sub(r'\.\d{3,}$', '', block.name)
                if b_name_clean == tandem_name and getattr(block, 'library', None) is not None:
                    tandem_data_block = block
                    break
        else:
            # For purely local assets, find the local original (ignoring overrides)
            for block in data_target_lib:
                b_name_clean = re.sub(r'\.\d{3,}$', '', block.name)
                is_override = getattr(block, 'library_override', getattr(block, 'override_library', None)) is not None
                if b_name_clean == tandem_name and getattr(block, 'library', None) is None and not is_override:
                    tandem_data_block = block
                    break

        if tandem_data_block:
            debug_log(f"Tandem asset found in current bpy.data: {tandem_data_block.name}")
        
        # 3. If not found, safely link from source_filepath
        else:
            if is_local:
                raise Exception(f"Asset is local, but pure tandem '{tandem_name}' not found in file.")
                
            debug_log(f"Tandem asset not loaded. Loading from: {source_path}")
            
            if not data_list_name_str:
                raise Exception(f"Swap logic not implemented for data type: {data_block_type}")
            
            names_before = set(data_target_lib.keys())
            
            with bpy.data.libraries.load(source_path, link=True) as (data_from, data_to):
                if hasattr(data_from, data_list_name_str):
                    data_list = getattr(data_from, data_list_name_str)
                    if tandem_name in data_list:
                        setattr(data_to, data_list_name_str, [tandem_name])
                    else:
                        raise Exception(f"Tandem asset '{tandem_name}' not found in {source_path}")
                else:
                    raise Exception(f"Data type '{data_list_name_str}' not in {source_path}")
                        
            # Identify the newly loaded block dynamically
            names_after = set(data_target_lib.keys())
            new_names = names_after - names_before
            
            for name in new_names:
                b_name_clean = re.sub(r'\.\d{3,}$', '', name)
                if b_name_clean == tandem_name:
                    tandem_data_block = data_target_lib.get(name)
                    break
                    
            if not tandem_data_block:
                # Absolute fallback if set diff failed
                for block in data_target_lib:
                    b_name_clean = re.sub(r'\.\d{3,}$', '', block.name)
                    if b_name_clean == tandem_name and getattr(block, 'library', None) is not None:
                        tandem_data_block = block
                        break

            if not tandem_data_block:
                raise Exception("Failed to link tandem asset, even after load.")
        
        # --- Helper Functions for Transform Replication ---
        def get_scene_parent_collection(wrapper_target, scene):
            """Strictly finds a parent collection that is actively linked in the Scene."""
            if isinstance(wrapper_target, bpy.types.Object):
                return wrapper_target.users_collection[0] if wrapper_target.users_collection else scene.collection
            
            def find_parent_recursive(parent_coll, tgt_coll):
                if tgt_coll.name in parent_coll.children: return parent_coll
                for child in parent_coll.children:
                    res = find_parent_recursive(child, tgt_coll)
                    if res: return res
                return None
                
            found = find_parent_recursive(scene.collection, wrapper_target)
            return found if found else scene.collection

        def copy_transforms(source_obj, target_obj):
            target_obj.location = source_obj.location
            target_obj.rotation_euler = source_obj.rotation_euler
            target_obj.rotation_quaternion = source_obj.rotation_quaternion
            target_obj.scale = source_obj.scale
            
        def find_root_objects(collection):
            # Find objects in the collection that have no parent, or their parent 
            # is outside this collection.
            roots = []
            if not collection: return roots
            coll_obj_names = {o.name for o in collection.all_objects}
            for obj in collection.all_objects:
                # Removed the type filter: cameras, lights, etc. are all perfectly valid roots
                if obj.parent is None or obj.parent.name not in coll_obj_names:
                    roots.append(obj)
            return roots
            
        def purge_override_hierarchy(collection):
            """Deep deletes an override collection and all its contents."""
            if not collection: return
            debug_log(f"Purging override hierarchy: {collection.name}")
            
            # 1. Remove all objects
            obs_to_remove = list(collection.all_objects)
            for obj in obs_to_remove:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception as e:
                    debug_log(f"Warning: Could not remove object {getattr(obj, 'name', 'Unknown')}: {e}")
                
            # 2. Remove all child collections
            cols_to_remove = list(collection.children)
            for child in cols_to_remove:
                try:
                    bpy.data.collections.remove(child, do_unlink=True)
                except Exception as e:
                    debug_log(f"Warning: Could not remove child collection {getattr(child, 'name', 'Unknown')}: {e}")
                
            # 3. Remove the parent collection itself
            try:
                # Force unlink from all parent collections first
                for p_col in bpy.data.collections:
                    if collection.name in p_col.children:
                        p_col.children.unlink(collection)
                if collection.name in context.scene.collection.children:
                    context.scene.collection.children.unlink(collection)
                    
                # The crucial step: If it's a proxy that lost its override status, removing it via do_unlink=True
                # might fail gracefully if it thinks it's strictly linked. We must ensure it's deleted.
                if collection in bpy.data.collections.values():
                    bpy.data.collections.remove(collection, do_unlink=True)
                    
            except Exception as e:
                debug_log(f"Warning: Could not remove parent collection {getattr(collection, 'name', 'Unknown')}: {e}")

        def strip_name(name):
            # Strip trailing .001 and standardize M/P flags for matching
            base = name.split('.')[0]
            base = base.replace('-M-', '-').replace('-P-', '-')
            base = base.replace('_M_P_', '_').replace('_M_M_', '_')
            return base

        # 6. Perform the swap
        wrapper_obj = details['wrapper_object']
        swap_prop = details['swap_property']
        
        # --- EDGE CASE IDENTIFICATION ---
        is_source_override = (swap_prop == "LIBRARY_OVERRIDE")
        is_source_empty = (swap_prop == "instance_collection")
        
        target_version = details.get('tandem_version_flag', '').lower()
        
        is_target_override = False
        is_target_empty = False
        
        if tandem_data_block:
            is_target_collection = isinstance(tandem_data_block, bpy.types.Collection)
            
            if is_target_collection:
                # Dynamically determine if the target should be an Override
                # by checking if it contains an overridable root ('emp' or 'arm')
                has_override_root = False
                for t_obj in tandem_data_block.all_objects:
                    base_name = t_obj.name.split('.')[0].lower()
                    if base_name.endswith("emp") or base_name.endswith("arm"):
                        has_override_root = True
                        break
                
                if has_override_root:
                    is_target_override = True
                else:
                    is_target_empty = True
            else:
                is_target_empty = True
        
        is_case_A = (is_source_empty and is_target_override) or (isinstance(wrapper_obj, bpy.types.Collection) and not is_source_override and is_target_override)
        is_case_B = (is_source_override and is_target_empty)
        is_case_C = (is_source_override and is_target_override)
        
        if is_case_A:
            # Case A: Non-Override Proxy -> Override Master
            debug_log("Running Case A Swap: Proxy -> Override")
            # 1. Extract
            proxy_wrapper = wrapper_obj
            new_master_coll = tandem_data_block
            
            # Find exact parent collection of proxy within the scene hierarchy
            target_parent = get_scene_parent_collection(proxy_wrapper, context.scene)
            
            # 2. Import & Override
            # new_master_coll MUST be linked to the scene/parent before override can be created
            if new_master_coll.name not in target_parent.children:
                target_parent.children.link(new_master_coll)
                
            override_coll = None
            try:
                override_coll = new_master_coll.override_hierarchy_create(context.scene, context.view_layer, do_fully_editable=True)
            except Exception as e:
                debug_log(f"Override hierarchy creation failed/rejected: {e}")
                
            has_old_override = getattr(override_coll, 'override_library', None) is not None
            has_new_override = getattr(override_coll, 'library_override', None) is not None
            
            # Robust verification
            is_valid_override = False
            if override_coll and (has_old_override or has_new_override):
                prop = getattr(override_coll, 'library_override', getattr(override_coll, 'override_library', None))
                if getattr(prop, "reference", getattr(prop, "main", None)) is not None:
                    is_valid_override = True
            
            # Clean up the base linked collection immediately so it doesn't leave a ghost linked duplicate
            try: target_parent.children.unlink(new_master_coll)
            except: pass
            if context.scene.collection != target_parent:
                try: context.scene.collection.children.unlink(new_master_coll)
                except: pass
            
            if override_coll and is_valid_override:
                # SUCCESS: True Override Collection
                if override_coll.name not in target_parent.children:
                    target_parent.children.link(override_coll)
                if override_coll.name in context.scene.collection.children and target_parent != context.scene.collection:
                    context.scene.collection.children.unlink(override_coll)
                
                # --- APPLY FULL SUFFIX NAME ---
                override_coll.name = final_target_name

                # 4. Paste
                root_objects = find_root_objects(override_coll)
                if isinstance(proxy_wrapper, bpy.types.Object):
                    # Simple paste from Empty
                    for root_obj in root_objects:
                        copy_transforms(proxy_wrapper, root_obj)
                else:
                    # Proxy is a Collection, find its roots and copy from them
                    proxy_roots = find_root_objects(proxy_wrapper)
                    if len(proxy_roots) == 1 and len(root_objects) == 1:
                        copy_transforms(proxy_roots[0], root_objects[0])
                    else:
                        for old_rot in proxy_roots:
                            stripped_old = strip_name(old_rot.name)
                            for new_rot in root_objects:
                                if strip_name(new_rot.name) == stripped_old:
                                    copy_transforms(old_rot, new_rot)
                                    break
            else:
                # FAILURE: Target rejected override status.
                # Fallback to Case B: Spawn an Empty Container Instance instead.
                debug_log("Master target rejected override status. Falling back to Case B Empty Instancing.")
                if override_coll and override_coll != new_master_coll:
                    try: bpy.data.collections.remove(override_coll, do_unlink=True)
                    except: pass
                
                # --- APPLY FULL SUFFIX NAME ON CREATION ---
                new_empty = bpy.data.objects.new(final_target_name, None)
                new_empty.instance_type = 'COLLECTION'
                new_empty.instance_collection = new_master_coll
                target_parent.objects.link(new_empty)
                
                if isinstance(proxy_wrapper, bpy.types.Object):
                    copy_transforms(proxy_wrapper, new_empty)
                else:
                    proxy_roots = find_root_objects(proxy_wrapper)
                    if proxy_roots:
                        copy_transforms(proxy_roots[0], new_empty)
                
            # 5. Remove
            instance_coll_to_remove = None
            if isinstance(proxy_wrapper, bpy.types.Object) and proxy_wrapper.type == 'EMPTY':
                instance_coll_to_remove = proxy_wrapper.instance_collection
                
            if isinstance(proxy_wrapper, bpy.types.Object):
                bpy.data.objects.remove(proxy_wrapper, do_unlink=True)
            else:
                purge_override_hierarchy(proxy_wrapper)
                # Ensure the root wrapper itself is gone from blender data
                if proxy_wrapper.name in bpy.data.collections:
                    try:
                        bpy.data.collections.remove(proxy_wrapper, do_unlink=True)
                    except Exception as e:
                        debug_log(f"Final override purge fallback failed: {e}")
            
            # Safely purge the underlying instanced collection ONLY if no other objects are using it
            if instance_coll_to_remove and instance_coll_to_remove.name in bpy.data.collections:
                if instance_coll_to_remove.users == 0:
                    debug_log(f"Purging shared instance collection '{instance_coll_to_remove.name}' (0 users remaining).")
                    purge_override_hierarchy(instance_coll_to_remove)
                else:
                    debug_log(f"Preserving shared instance collection '{instance_coll_to_remove.name}' ({instance_coll_to_remove.users} users remaining for batch swap).")
            
        elif is_case_B:
            # Case B: Override Master -> Non-Override Proxy
            debug_log("Running Case B Swap: Override -> Empty")
            old_master_coll = details['base_data_block']
            new_proxy_coll = tandem_data_block
            
            # Find parent collection of the old master override
            target_parent = get_scene_parent_collection(old_master_coll, context.scene)
            
            # 1. Extract
            root_objects = find_root_objects(old_master_coll)
            saved_loc, saved_rot_e, saved_rot_q, saved_scale = None, None, None, None
            if root_objects:
                saved_loc = root_objects[0].location.copy()
                saved_rot_e = root_objects[0].rotation_euler.copy()
                saved_rot_q = root_objects[0].rotation_quaternion.copy()
                saved_scale = root_objects[0].scale.copy()
            
            # 2. Import & Construct Empty
            # --- APPLY FULL SUFFIX NAME ON CREATION ---
            new_empty = bpy.data.objects.new(final_target_name, None)
            new_empty.instance_type = 'COLLECTION'
            new_empty.instance_collection = new_proxy_coll
            target_parent.objects.link(new_empty)
            
            # 4. Paste
            if saved_loc:
                new_empty.location = saved_loc
                new_empty.rotation_euler = saved_rot_e
                new_empty.rotation_quaternion = saved_rot_q
                new_empty.scale = saved_scale
                
            # 5. Thorough Remove
            purge_override_hierarchy(old_master_coll)
            
        elif is_case_C:
            # Case C: Override Master -> Override Master
            debug_log("Running Case C Swap: Override -> Override (with Empty Fallback)")
            old_master_coll = details['base_data_block']
            new_master_coll = tandem_data_block
            
            # Find parent collection
            target_parent = get_scene_parent_collection(old_master_coll, context.scene)
            
            # 1. Extract
            old_roots = find_root_objects(old_master_coll)
            
            # 2. Import & Override
            # new_master_coll MUST be linked to the scene/parent before override can be created
            if new_master_coll.name not in target_parent.children:
                target_parent.children.link(new_master_coll)
                
            override_coll = None
            try:
                override_coll = new_master_coll.override_hierarchy_create(context.scene, context.view_layer, do_fully_editable=True)
            except Exception as e:
                debug_log(f"Override hierarchy creation failed/rejected: {e}")
                
            # Verify it actually produced an override (Blender can silently return linked collections)
            has_old_override = getattr(override_coll, 'override_library', None) is not None
            has_new_override = getattr(override_coll, 'library_override', None) is not None
            
            # Robust verification: It must have a 'reference' or 'main' indicating a true override link
            is_valid_override = False
            if override_coll and (has_old_override or has_new_override):
                prop = getattr(override_coll, 'library_override', getattr(override_coll, 'override_library', None))
                if getattr(prop, "reference", getattr(prop, "main", None)) is not None:
                    is_valid_override = True
            
            # Clean up the base linked collection immediately so it doesn't leave a ghost linked duplicate
            try: target_parent.children.unlink(new_master_coll)
            except: pass
            if context.scene.collection != target_parent:
                try: context.scene.collection.children.unlink(new_master_coll)
                except: pass
            
            if override_coll and is_valid_override:
                # SUCCESS: True Override Collection
                if override_coll.name not in target_parent.children:
                    target_parent.children.link(override_coll)
                if override_coll.name in context.scene.collection.children and target_parent != context.scene.collection:
                    context.scene.collection.children.unlink(override_coll)
                
                # --- APPLY FULL SUFFIX NAME ---
                override_coll.name = final_target_name

                # 4. Paste
                new_roots = find_root_objects(override_coll)
                if len(old_roots) == 1 and len(new_roots) == 1:
                    copy_transforms(old_roots[0], new_roots[0])
                elif len(old_roots) == 1 and len(new_roots) > 1:
                    # Asymmetrical Root Fallback: Master has 1 armature, Proxy has multiple decoupled meshes
                    debug_log("Asymmetrical root mapping detected. Applying primary transform to all new roots.")
                    for new_rot in new_roots:
                        copy_transforms(old_roots[0], new_rot)
                else:
                    for old_rot in old_roots:
                        stripped_old = strip_name(old_rot.name)
                        for new_rot in new_roots:
                            if strip_name(new_rot.name) == stripped_old:
                                copy_transforms(old_rot, new_rot)
                                break
            else:
                # FAILURE: Target rejected override status.
                # Fallback to Case B: Spawn an Empty Container Instance instead.
                debug_log("Target rejected override status. Falling back to Case B Empty Instancing.")
                if override_coll and override_coll != new_master_coll:
                    try: bpy.data.collections.remove(override_coll, do_unlink=True)
                    except: pass
                
                # --- APPLY FULL SUFFIX NAME ON CREATION ---
                new_empty = bpy.data.objects.new(final_target_name, None)
                new_empty.instance_type = 'COLLECTION'
                new_empty.instance_collection = new_master_coll
                target_parent.objects.link(new_empty)
                
                if old_roots:
                    copy_transforms(old_roots[0], new_empty)
                        
            # 5. Thorough Remove
            purge_override_hierarchy(old_master_coll)

        else:
            # --- EXACTLY THE OLD LOGIC (Fallback for standard assets) ---
            if isinstance(wrapper_obj, bpy.types.Collection):
                # If we reached here with a Collection, it means it's an Override Swap
                # that somehow missed Case A/B/C. We cannot 'setattr' on a Collection to swap it.
                # It MUST be handled by the override logic. 
                raise Exception(f"Cannot perform simple property swap on Collection '{wrapper_obj.name}'. Edge-case interceptor bypassed incorrectly.")
            else:
                debug_log(f"Setting {wrapper_obj.name}.{swap_prop} = {tandem_data_block.name}")
                setattr(wrapper_obj, swap_prop, tandem_data_block)
                # --- APPLY FULL SUFFIX NAME ---
                wrapper_obj.name = final_target_name
                wrapper_obj.update_tag()


# --- Phase 5: UI/UX Implementation ---

class MY_PT_asset_switcher_panel(Panel):
    """
    The N-Panel UI for the addon.
    """
    bl_label = "Asset Switcher"
    bl_idname = "MY_PT_asset_switcher_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'proxy/master'

    @classmethod
    def poll(cls, context):
        prefs = get_addon_prefs(context)
        return prefs.show_n_panel if prefs else False

    def draw(self, context):
        layout = self.layout
        prefs = get_addon_prefs(context)
        if not prefs:
            layout.label(text="Error loading preferences.")
            return

        layout.prop(prefs, "debug_mode")
        layout.operator(MY_OT_refresh_assets.bl_idname, icon='FILE_REFRESH')

        box = layout.box()
        num_assets = len(prefs.swappable_assets)
        
        if num_assets == 0:
            box.label(text="No swappable assets found.")
        else:
            box.label(text=f"Found {num_assets} swappable assets:")
            
            for asset_item in prefs.swappable_assets:
                # Use cached validity check!
                if not asset_item.is_valid:
                     continue
                     
                row = box.row(align=True)

                # 1. Select Button
                icon = 'RESTRICT_SELECT_OFF'
                obj = context.scene.objects.get(asset_item.name)
                is_selected = False
                if obj and obj in context.selected_objects:
                    icon = 'RESTRICT_SELECT_ON'
                    is_selected = True
                
                # Collections need a slightly different select logic check
                if not obj:
                    coll = bpy.data.collections.get(asset_item.name)
                    # Checking collection selection state from python is tricky without context, 
                    # relying mainly on outliner context menu for Collections anyway.
                    
                select_op = row.operator("object.select_pattern", text="", icon=icon, depress=is_selected)
                select_op.pattern = asset_item.name
                select_op.case_sensitive = True
                select_op.extend = True
                
                # 2. Icon and Name
                # Read straight from the property cache! Zero calculation!
                row.label(text=asset_item.ui_label, icon=asset_item.ui_icon)
                row.label(text=f"(-{asset_item.current_version})")

                # 3. Swap Button
                # Check actual selection for context
                context_objects = set()
                if context.selected_objects: context_objects.update(context.selected_objects)
                
                # If this item is selected, button respects selection logic
                is_item_selected = (obj and obj in context_objects)
                
                op = row.operator(
                    MY_OT_swap_asset_version.bl_idname, 
                    text=f"Swap to -{asset_item.tandem_version}"
                )
                
                if is_item_selected:
                    op.use_selection = True
                    op.target_object_name = ""
                else:
                    op.use_selection = False
                    op.target_object_name = asset_item.name
                
                # Because the panel should still just strictly toggle whatever the tandem is,
                # we don't assign force_state here, relying on the 'TOGGLE' default.

# --- Outliner Context Menu Integration (Improved for Blender 4.5+) ---

def outliner_context_menu_func(self, context):
    """
    Appends 'Swap Asset' buttons to the Outliner context menu.
    Robustly handles multiple selected objects using selected_ids where available.
    """
    prefs = get_addon_prefs(context)
    if not prefs:
        return
        
    # Always draw Refresh Asset List in the Outliner Context Menu
    layout = self.layout
    layout.separator()
    layout.operator(MY_OT_refresh_assets.bl_idname, icon='FILE_REFRESH', text="Refresh Asset Switcher List")

    # --- Robust Context Gathering ---
    potential_objects = set()

    # 1. Standard Selection
    if context.selected_objects:
        potential_objects.update(context.selected_objects)

    # 2. Outliner Specific Selection (Handles "Blender File" mode, etc.)
    # context.selected_ids is reliable in Blender 4.0+
    if hasattr(context, "selected_ids"):
        for id_data in context.selected_ids:
            if isinstance(id_data, (bpy.types.Object, bpy.types.Collection)):
                potential_objects.add(id_data)
                
    # 3. Fallback: Context-based collection selection (for outliner)
    if hasattr(context, "collection"):
        if context.collection:
            potential_objects.add(context.collection)

    # 4. Fallback: Active Object
    if not potential_objects and context.active_object:
        potential_objects.add(context.active_object)

    if not potential_objects:
        return

    # --- Filtering against Cache ---
    cached_asset_names = {item.name for item in prefs.swappable_assets}
    
    found_assets = []

    for item in potential_objects:
        if item.name in cached_asset_names:
            found_assets.append(item)
            
    if not found_assets:
        return

    # --- Draw Operators ---
    op_proxy = layout.operator(
        MY_OT_swap_asset_version.bl_idname, 
        text="Make Proxy (-p)",
        icon='FILE_REFRESH'
    )
    op_proxy.use_selection = True
    op_proxy.target_object_name = ""
    op_proxy.force_state = 'PROXY'
    
    op_master = layout.operator(
        MY_OT_swap_asset_version.bl_idname, 
        text="Make Master (-m)",
        icon='FILE_REFRESH'
    )
    op_master.use_selection = True
    op_master.target_object_name = ""
    op_master.force_state = 'MASTER'


# --- Addon Registration ---

CLASSES = [
    SwappableAsset,
    AddonPreferences,
    MY_OT_refresh_assets,
    MY_OT_swap_asset_version,
    MY_PT_asset_switcher_panel,
]

def register():
    debug_log("Registering Addon")
    for cls in CLASSES:
        bpy.utils.register_class(cls)
        
    # Append to Outliner Context Menu
    # Registering to both OBJECT and COLLECTION menus ensures visibility
    # regardless of where the user right-clicks in the Outliner (Blender 4.5 Robustness)
    try:
        bpy.types.OUTLINER_MT_object.append(outliner_context_menu_func)
    except Exception as e:
        debug_log(f"Failed to append to Outliner Object menu: {e}")
        
    try:
        # Important for users right-clicking collection instances
        bpy.types.OUTLINER_MT_collection.append(outliner_context_menu_func)
    except Exception as e:
        debug_log(f"Failed to append to Outliner Collection menu: {e}")

def unregister():
    debug_log("Unregistering Addon")
    
    # Remove from Outliner Context Menus
    try:
        bpy.types.OUTLINER_MT_object.remove(outliner_context_menu_func)
    except Exception as e:
        debug_log(f"Failed to remove from Outliner Object menu: {e}")
        
    try:
        bpy.types.OUTLINER_MT_collection.remove(outliner_context_menu_func)
    except Exception as e:
        debug_log(f"Failed to remove from Outliner Collection menu: {e}")
        
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()