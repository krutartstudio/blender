bl_info = {
    "name": "Krutart Proxy/Master Switcher",
    "author": "iori, Krutart, Gemini",
    "version": (0, 5, 9), # Fix: Hierarchy Parent Detection
    "blender": (4, 0, 0), 
    "location": "View3D > Sidebar (N-Panel) > Asset Switcher",
    "description": "Seamlessly swap between -p (proxy) and -m (master) asset versions preserving hierarchy.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import os
from bpy.props import (
    StringProperty,
    BoolProperty,
    PointerProperty,
    CollectionProperty,
)
from bpy.types import (
    PropertyGroup,
    Operator,
    Panel,
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
    
    name_lower = name.lower() 
    current_version_flag = None
    tandem_version_flag = None
    base_name = None
    tandem_version_name = None

    p_flag_found = False
    p_index = -1
    
    # Find the last '-p' or '-P'
    # We check for '-P' and '-p' separately
    p_indices = [name.rfind('-p'), name.rfind('-P')]
    p_dash_index = max(p_indices) # This is the index of the dash '-'

    if p_dash_index != -1:
        # Check what follows the flag. It must be either end-of-string or a separator.
        p_char_index = p_dash_index + 1 # This is the index of 'p' or 'P'
        
        # Check if it's at the end
        if p_char_index == len(name) - 1:
            p_flag_found = True
            p_index = p_char_index
        # Check if it's followed by a separator
        elif p_char_index + 1 < len(name) and name[p_char_index + 1] in {'-', '_', '.'}:
            p_flag_found = True
            p_index = p_char_index
            
    m_flag_found = False
    m_index = -1
    
    # Find the last '-m' or '-M'
    m_indices = [name.rfind('-m'), name.rfind('-M')]
    m_dash_index = max(m_indices)

    if m_dash_index != -1:
        # Check what follows the flag
        m_char_index = m_dash_index + 1 # This is the index of 'm' or 'M'
        
        # Check if it's at the end
        if m_char_index == len(name) - 1:
            m_flag_found = True
            m_index = m_char_index
        # Check if it's followed by a separator
        elif m_char_index + 1 < len(name) and name[m_char_index + 1] in {'-', '_', '.'}:
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
        
        original_flag_char = name[p_index] # This will be 'p' or 'P'
        tandem_flag_char = 'm'
        if original_flag_char.isupper():
            tandem_flag_char = 'M'
        
        base_name = name[:p_index]
        suffix = name[p_index + 1:] # Get text after 'p' or 'P'
        tandem_version_name = f"{base_name}{tandem_flag_char}{suffix}" # Use the correctly-cased char
        
    elif m_flag_found:
        current_version_flag = "-m"
        tandem_version_flag = "-p"

        original_flag_char = name[m_index] # This will be 'm' or 'M'
        tandem_flag_char = 'p'
        if original_flag_char.isupper():
            tandem_flag_char = 'P'

        base_name = name[:m_index]
        suffix = name[m_index + 1:] # Get text after 'm' or 'M'
        tandem_version_name = f"{base_name}{tandem_flag_char}{suffix}" # Use the correctly-cased char
        
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
             debug_log(f"Swap is OVERRIDE, but data_block '{data_block.name if data_block else 'None'}' or its override property is missing.")
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

    details = {
        "wrapper_object": wrapper_obj,
        "base_data_block": data_block,
        "base_name": base_name,
        "swap_property": swap_property,
        "source_filepath": os.path.realpath(bpy.path.abspath(source_filepath)),
        "is_local": is_local,
        "current_version_flag": current_version_flag,
        "tandem_version_flag": tandem_version_flag,
        "tandem_version_name": tandem_version_name,
        "asset_name_source": name # Store the name we matched on
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
    Analyzes a swappable object and returns a dictionary
    of its core properties. Tries Case 1, then Case 2.
    """
    if not obj:
        return None

    debug_log(f"--- Getting details for: {obj.name} (Combined) ---")

    # --- Try Case 1 First ---
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

class AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    debug_mode: BoolProperty(
        name="Enable Debug Logging",
        description="Prints verbose DEBUG logs to the System Console",
        default=False,
    )

    swappable_assets: CollectionProperty(
        name="Swappable Assets",
        description="List of discovered swappable wrapper objects in the scene",
        type=SwappableAsset,
    )

class MY_OT_refresh_assets(Operator):
    """
    Asset Discovery Operator.
    1. Scans MODEL- collections for *all* valid asset data-blocks.
    2. Scans the *entire scene* for objects that instance those assets.
    """
    bl_idname = "asset_switcher.refresh_assets"
    bl_label = "Refresh Asset List"
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

        model_collections = [
            c for c in bpy.data.collections if c.name.startswith("MODEL-")
        ]
        debug_log(f"Found {len(model_collections)} 'MODEL-' collections.")

        for coll in model_collections:
            # Scan OBJECTS (Empties) in the MODEL- collection
            debug_log(f"Scanning objects in: {coll.name}")
            for obj in coll.objects:
                base_data, _ = get_base_data_block(obj)
                if base_data:
                    debug_log(f"    Found asset datablock via object '{obj.name}': {base_data.name}")
                    valid_asset_datablocks.add(base_data)
                    
            # Scan CHILD COLLECTIONS in the MODEL- collection
            debug_log(f"Scanning child collections in: {coll.name}")
            for child_coll in coll.children:
                # The child collection *is* the asset data-block
                debug_log(f"    Found asset datablock via child collection: {child_coll.name}")
                valid_asset_datablocks.add(child_coll)

        if not valid_asset_datablocks:
            debug_log("No valid asset datablocks found in MODEL- collections.")
            self.report({'INFO'}, "No swappable assets found.")
            asset_list.clear()
            return {'FINISHED'}
            
        debug_log(f"Found {len(valid_asset_datablocks)} unique asset datablocks.")
        
        # Create a set of just the *names* for a more reliable string-based lookup
        valid_asset_names = {db.name for db in valid_asset_datablocks}
        debug_log(f"Valid asset names: {valid_asset_names}")

        # --- Step 2: Find all scene objects that *use* these assets ---
        found_wrapper_objects = [] # Use a list to preserve order
        processed_asset_names = set() # To prevent duplicates

        # --- PASS A: Find all Case 1 (Main Wrapper) objects ---
        debug_log("--- Running Pass A: Searching for Case 1 Wrappers ---")
        for obj in context.scene.objects:
            details = get_asset_details_case_1(obj, valid_asset_datablocks)
            
            if details:
                asset_name = details['asset_name_source']
                if asset_name in valid_asset_names:
                    debug_log(f"    +++ PASS A: Found Case 1 Wrapper '{obj.name}' for asset '{asset_name}'")
                    found_wrapper_objects.append(obj.name)
                    processed_asset_names.add(asset_name)
                else:
                    debug_log(f"    --- REJECTED (PASS A): '{obj.name}'. Asset '{asset_name}' not in valid list.")

        # --- PASS B: Find all Case 2 (Linked Content) objects ---
        debug_log("--- Running Pass B: Searching for Case 2 Contents ---")
        for obj in context.scene.objects:
            details = get_asset_details_case_2(obj, valid_asset_datablocks)
            
            if details:
                asset_name = details['asset_name_source']
                
                # Check if we already found a Case 1 wrapper for this asset
                if asset_name in processed_asset_names:
                    debug_log(f"    --- SKIPPED (PASS B): '{obj.name}'. Case 1 wrapper already found for asset '{asset_name}'.")
                    continue 
                    
                if asset_name in valid_asset_names:
                    debug_log(f"    +++ PASS B: Found Case 2 Content '{obj.name}' for asset '{asset_name}'")
                    found_wrapper_objects.append(obj.name)
                    processed_asset_names.add(asset_name) 
                else:
                    debug_log(f"    --- REJECTED (PASS B): '{obj.name}'. Asset '{asset_name}' not in valid list.")


        # --- Step 3: Populate the final list ---
        asset_list.clear()
        for obj_name in sorted(list(found_wrapper_objects)):
            new_asset_item = asset_list.add()
            new_asset_item.name = obj_name

        found_count = len(found_wrapper_objects)
        debug_log(f"=== Found {found_count} swappable assets ===")
        self.report({'INFO'}, f"Found {found_count} swappable assets.")
        return {'FINISHED'}


# --- Phase 3 & 4: Swap Logic ---
class MY_OT_swap_asset_version(Operator):
    """
    Swaps an asset to its tandem version.
    """
    bl_idname = "asset_switcher.swap_version"
    bl_label = "Swap Asset Version"
    bl_description = "Swap this asset to its tandem version"

    target_object_name: StringProperty(
        name="Target Object Name",
        description="The scene object to perform the swap on"
    )

    def execute(self, context):
        if not self.target_object_name:
            self.report({'ERROR'}, "No target object specified.")
            return {'CANCELLED'}

        obj = context.scene.objects.get(self.target_object_name)
        if not obj:
            self.report({'ERROR'}, f"Object '{self.target_object_name}' not found.")
            # Remove from list logic would go here
            return {'CANCELLED'}

        debug_log(f"--- SWAP REQUESTED FOR: {obj.name} ---")

        # Re-run refresh to get the valid asset datablocks (Context guarantee)
        prefs = get_addon_prefs(context)
        valid_asset_datablocks = set()
        model_collections = [c for c in bpy.data.collections if c.name.startswith("MODEL-")]
        for coll in model_collections:
            for c_obj in coll.objects:
                base_data, _ = get_base_data_block(c_obj)
                if base_data: valid_asset_datablocks.add(base_data)
            for child_coll in coll.children:
                valid_asset_datablocks.add(child_coll)


        # 1. Get asset details
        details = get_asset_details(obj, valid_asset_datablocks)
        if not details:
           self.report({'ERROR'}, f"Could not get asset details for '{obj.name}'.")
           return {'CANCELLED'}
        
        tandem_name = details['tandem_version_name']
        source_path = details['source_filepath']
        is_local = details['is_local']
        swap_property = details['swap_property'] 
        
        debug_log(f"Attempting to find tandem asset: '{tandem_name}'")

        # 2. Check if tandem asset already exists in bpy.data
        
        # Determine data type (collections, meshes, etc.)
        data_block_type = type(details['base_data_block'])
        data_target_lib = None
        data_list_name_str = "" 

        if swap_property == "LIBRARY_OVERRIDE":
            debug_log("Data target is Collection (for Library Override)")
            data_target_lib = bpy.data.collections
            data_list_name_str = "collections"

        elif swap_property == "instance_collection":
            debug_log("Data target is Collection (for Instance)")
            data_target_lib = bpy.data.collections
            data_list_name_str = "collections"
            
        elif swap_property == "data":
            debug_log(f"Data target is obj.data ({data_block_type})")
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
            # Handle the case where base_data_block is None
            if details['base_data_block'] is None:
                debug_log("Base data block is None. Guessing target library...")
                if obj.type == 'EMPTY':
                    data_target_lib = bpy.data.collections
                    data_list_name_str = "collections"
                    swap_property = "instance_collection" 
                    details['swap_property'] = "instance_collection"
                    debug_log("...Guessed 'collections' based on EMPTY wrapper.")
                else:
                    self.report({'ERROR'}, f"Cannot determine data type for swap: {obj.name}")
                    return {'CANCELLED'}
            else:
                self.report({'ERROR'}, f"Unsupported data block type/swap_property: {data_block_type} / {swap_property}")
                return {'CANCELLED'}

        tandem_data_block = data_target_lib.get(tandem_name)
        
        if tandem_data_block:
            debug_log("Tandem asset found in current bpy.data.")
        
        # 3. If not, try to link from source_filepath
        else:
            if is_local:
                self.report({'ERROR'}, f"Asset is local, but tandem '{tandem_name}' not found in file.")
                return {'CANCELLED'}
                
            debug_log(f"Tandem asset not loaded. Loading from: {source_path}")
            
            if not data_list_name_str:
                self.report({'ERROR'}, f"Swap logic not implemented for data type: {data_block_type}")
                return {'CANCELLED'}
            
            debug_log(f"Target data library string: '{data_list_name_str}'")
            
            try:
                with bpy.data.libraries.load(source_path, link=True) as (data_from, data_to):
                    if hasattr(data_from, data_list_name_str):
                        data_list = getattr(data_from, data_list_name_str)
                        if tandem_name in data_list:
                            setattr(data_to, data_list_name_str, [tandem_name])
                            debug_log(f"Successfully linked '{tandem_name}' from source.")
                        else:
                            debug_log(f"ERROR: Tandem asset '{tandem_name}' not found in file '{source_path}'")
                            self.report({'ERROR'}, f"Tandem asset '{tandem_name}' not found in {source_path}")
                            return {'CANCELLED'}
                    else:
                        debug_log(f"ERROR: Data library '{data_list_name_str}' not found in source file.")
                        self.report({'ERROR'}, f"Data type '{data_list_name_str}' not in {source_path}")
                        return {'CANCELLED'}
                        
            except Exception as e:
                self.report({'ERROR'}, f"Failed to load library: {e}")
                debug_log(f"ERROR: Failed to load library '{source_path}': {e}")
                return {'CANCELLED'}

            # Get the newly loaded data-block
            tandem_data_block = data_target_lib.get(tandem_name)
            if not tandem_data_block:
                self.report({'ERROR'}, "Failed to link tandem asset, even after load.")
                return {'CANCELLED'}
        
        # 6. Perform the swap
        try:
            wrapper_obj = details['wrapper_object']
            swap_prop = details['swap_property']
            
            # --- START: "Hierarchy Injection" Logic (v0.5.9 Fix: Smart Parent Detection) ---
            if swap_prop == "LIBRARY_OVERRIDE":
                debug_log("Performing 'Hierarchy Injection' (v0.5.9 Smart Parent Detection).")
                
                local_collection = details['base_data_block'] # The -p collection (current)
                tandem_collection = tandem_data_block       # The -m collection (new)

                if not local_collection:
                    raise Exception("Cannot swap: local override collection is missing.")
                if not tandem_collection:
                        raise Exception("Cannot swap: tandem override collection is missing.")

                # 1. Identify Parent Candidates (Filter for WRITABLE/LOCAL collections only)
                # Collections do not have .users_collection, so we must search, 
                # but we strictly filter for local (library=None) parents to avoid read-only errors.
                
                candidates = []
                for cand in bpy.data.collections:
                    # CRITICAL: Only look at local/override collections (writable)
                    # If cand.library is NOT None, it's a linked collection (Read-Only) -> Skip it.
                    if cand.library is None: 
                        if local_collection.name in cand.children:
                            candidates.append(cand)
                
                # Sort: Prioritize 'MODEL-' collections (True/False sorts False first, so we check 'not startswith')
                # This ensures standard 'MODEL-' collections are tried before random organizational collections.
                candidates.sort(key=lambda c: not c.name.startswith("MODEL-"))
                
                debug_log(f"Found {len(candidates)} writable parent candidates: {[c.name for c in candidates]}")

                target_parent = None
                swap_success = False

                # 2. Iterate and ATTEMPT link
                for cand in candidates:
                    debug_log(f"Attempting to link into writable candidate: '{cand.name}'...")
                    try:
                        # Check if already linked to avoid error/duplication
                        if tandem_collection.name not in cand.children:
                            cand.children.link(tandem_collection)
                            debug_log(f"  > SUCCESS: Linked to '{cand.name}'")
                        else:
                            debug_log(f"  > ALREADY EXISTS in '{cand.name}' (Skipping link step)")
                        
                        # If we reached here, the collection was writable and link worked.
                        target_parent = cand
                        
                        # 3. Unlink the OLD local asset
                        if local_collection.name in cand.children:
                             debug_log(f"  > Unlinking old asset from '{cand.name}'...")
                             cand.children.unlink(local_collection)
                        
                        swap_success = True
                        break # Stop at first success

                    except Exception as e:
                         debug_log(f"  > FAILED (Unexpected): {e}")
                         continue # Try next parent

                # Fallback
                if not swap_success:
                    debug_log("WARNING: All parent candidates failed. Defaulting to Scene Root.")
                    target_parent = context.scene.collection
                    if tandem_collection.name not in target_parent.children:
                        target_parent.children.link(tandem_collection)
                    
                self.report({'INFO'}, f"Swapped '{local_collection.name}' -> '{tandem_collection.name}'")
                
                # Schedule a refresh to update the UI
                def refresh_op():
                    try:
                        bpy.ops.asset_switcher.refresh_assets('EXEC_DEFAULT')
                    except Exception as e:
                        debug_log(f"Error during deferred refresh: {e}")
                    return None 
                
                bpy.app.timers.register(refresh_op, first_interval=0.01)

            else:
                # --- ORIGINAL SWAP (for obj.data or obj.instance_collection) ---
                debug_log(f"Setting {wrapper_obj.name}.{swap_prop} = {tandem_data_block.name}")
                setattr(wrapper_obj, swap_prop, tandem_data_block)
                wrapper_obj.update_tag()
                debug_log(f"--- SWAP SUCCESSFUL for: {obj.name} ---")
                self.report({'INFO'}, f"Swapped '{details['asset_name_source']}' to '{tandem_name}'")
            # --- END REVISED SWAP LOGIC ---

        except Exception as e:
            self.report({'ERROR'}, f"Failed to perform swap: {e}")
            debug_log(f"ERROR: Failed to perform swap: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


# --- Phase 5: UI/UX Implementation ---

class MY_PT_asset_switcher_panel(Panel):
    """
    The N-Panel UI for the addon.
    """
    bl_label = "Asset Switcher"
    bl_idname = "MY_PT_asset_switcher_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asset SwitchT' # Tab name in the N-Panel

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
            
            # Need to get valid datablocks for get_asset_details
            valid_asset_datablocks = set()
            model_collections = [c for c in bpy.data.collections if c.name.startswith("MODEL-")]
            for coll in model_collections:
                for c_obj in coll.objects:
                    base_data, _ = get_base_data_block(c_obj)
                    if base_data: valid_asset_datablocks.add(base_data)
                for child_coll in coll.children:
                    valid_asset_datablocks.add(child_coll)
            
            invalid_items = []

            for i, asset_item in enumerate(prefs.swappable_assets):
                obj = context.scene.objects.get(asset_item.name)
                if not obj:
                    invalid_items.append(i)
                    continue

                details = get_asset_details(obj, valid_asset_datablocks)
                
                row = box.row(align=True)
                
                if not details:
                    row.label(text=asset_item.name, icon='ERROR')
                    row.label(text="Invalid")
                    continue

                label_name = obj.name
                icon_type = 'LINKED'
                if details.get('swap_property') == "LIBRARY_OVERRIDE":
                    if details.get('base_data_block'):
                        label_name = details['base_data_block'].name
                        icon_type = 'LIBRARY_DATA_OVERRIDE'
                elif details.get('swap_property') == "instance_collection":
                    icon_type = 'OUTLINER_COLLECTION'
                elif details.get('swap_property') == 'data':
                    if obj.type == 'MESH':
                        icon_type = 'MESH_DATA'
                    elif obj.type == 'ARMATURE':
                        icon_type = 'ARMATURE_DATA'
                    elif obj.type == 'CURVE':
                        icon_type = 'CURVE_DATA'
                
                row.label(text=label_name, icon=icon_type)
                row.label(text=f"({details['current_version_flag'].upper()})")
                
                op = row.operator(
                    MY_OT_swap_asset_version.bl_idname,
                    text=f"Swap to {details['tandem_version_flag'].upper()}"
                )
                op.target_object_name = obj.name
            
            if invalid_items:
                for i in reversed(invalid_items):
                    prefs.swappable_assets.remove(i)


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

def unregister():
    debug_log("Unregistering Addon")
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()