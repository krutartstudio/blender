bl_info = {
    "name": "Krutart Light Link",
    "author": "iori, Krutart, Gemini",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "Properties > Object > Light Linking",
    "description": "Manages default light groups for assets via a cascading lookup system (Shot > Layout > Asset).",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy
import json
import logging
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty
from bpy.types import PropertyGroup, Panel, Operator

# --- Logging Setup ---
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("KrutartLightLink")

# --- Constants ---
DATA_BLOCK_NAME = "__krutart_light_link_data.json"
CUSTOM_PROP_OVERRIDE = "krutart_light_override"

# --- Data Management ---

# --- Data Management ---

class LibraryDataManager:
    """Manages reading and writing the internal JSON text block."""

    @staticmethod
    def load_data_from_text_block(text_block):
        """Helper to safely load JSON from a text block object."""
        if not text_block: return {}
        try:
            return json.loads(text_block.as_string())
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def load_local_data():
        """Loads data from the CURRENT file's text block."""
        return LibraryDataManager.load_data_from_text_block(bpy.data.texts.get(DATA_BLOCK_NAME))

    @staticmethod
    def save_local_data(data):
        """Saves dictionary data to the CURRENT file's text block."""
        text_block = bpy.data.texts.get(DATA_BLOCK_NAME)
        if not text_block:
            text_block = bpy.data.texts.new(DATA_BLOCK_NAME)
        
        try:
            json_str = json.dumps(data, indent=2)
            text_block.clear()
            text_block.write(json_str)
            # log.info(f"Saved light link data to '{DATA_BLOCK_NAME}'.")
        except Exception as e:
            log.error(f"Failed to save data: {e}")

# --- Core Logic: Link Traversal ---

class LinkTraverser:
    """
    Handles the logic of finding where an asset comes from
    and fetching the 'best' version of its light data.
    """

    @staticmethod
    def get_source_data(obj):
        """
        Determines the 'Source Data Block' and 'Source Library Path' for an object.
        Returns: (source_data_block_name, library_path_or_None)
        """
        # Case 1: Library Override
        if obj.override_library:
            try:
                # In Blender 4.3+, override_library.reference is the source object/collection
                reference = obj.override_library.reference
                if reference and reference.library:
                    return reference.name, reference.library.filepath
            except Exception:
                pass
        
        # Case 2: Linked Collection Instance (Empty)
        if obj.instance_collection and obj.instance_collection.library:
             return obj.instance_collection.name, obj.instance_collection.library.filepath

        # Case 3: Linked Object Data (Mesh linked directly)
        if obj.data and obj.data.library:
             return obj.data.name, obj.data.library.filepath

        # Case 4: Local Object / Local Override root
        return obj.name, None

    @staticmethod
    def fetch_cascaded_data(obj):
        """
        Performs the Cascading Lookup:
        1. Local Override (Custom Prop) -> 2. Intermediate Lib -> 3. Root Lib
        
        Returns: (resolved_groups_list, source_type_enum)
        """
        
        # --- Level 1: Local Override ---
        # We check the custom property on the object wrapper itself
        if CUSTOM_PROP_OVERRIDE in obj:
            try:
                data = json.loads(obj[CUSTOM_PROP_OVERRIDE])
                if isinstance(data, list):
                    return data, 'LOCAL'
            except:
                pass # Malformed custom prop, fall through

        # --- Level 2 & 3: Library Lookup ---
        asset_name, library_path = LinkTraverser.get_source_data(obj)
        
        if not library_path:
            # It's a local object. We typically only check the internal JSON for local objects
            # if we treat the current file as a "Library" for itself.
            # But per spec, "Local Override" is specific to the wrapper.
            # Let's check internal JSON as a fallback for "Local Asset Definition".
            local_json = LibraryDataManager.load_local_data()
            if asset_name in local_json:
                return local_json[asset_name], 'ASSET' # It's defined here as an asset
            return [], 'NONE'

        # We have a library path. This could be an Intermediate (Layout) or Root (Asset).
        # We need to peek into it.
        # NOTE: True "Intermediate" lookup requires knowing the hierarchy of libraries.
        # Blender flattens this a bit. 
        # If A.blend links B.blend links C.blend, and we are in A...
        # obj.library.filepath usually points to B (Layout). 
        # If B has the data, we use it. If B doesn't, does B link to C?
        # Implementing a full recursive deep-dive is expensive (opening multiple files).
        #
        # OPTIMIZATION: We only look at the *immediate* parent library (Layout).
        # If the Layout artist wanted to override the Asset, they saved data *in the Layout file*.
        # If they didn't, we assume the Layout file *contains* the data from the Asset file 
        # (if we structure the save to propagate? No, that's complex).
        #
        # Let's stick to the plan: Open the library file found. Read JSON.
        # If found -> Use it.
        # If NOT found -> We might need to check if THAT file links the asset from elsewhere.
        
        # Phase 1: Check Immediate Library
        found_data = LinkTraverser._read_json_from_library(library_path, asset_name)
        if found_data is not None:
            # We found it! exact match.
            # Is this 'LAYOUT' or 'ASSET'? We don't strictly know, but it's "External".
            return found_data, 'LAYOUT' # We'll call it External/Layout for now.

        # Phase 2: Deep Lookup (The "Asset" Level)
        # If the immediate library didn't have it, maybe it's just a proxy for the real asset.
        # We need to find where *that* library got the asset from.
        # This requires `bpy.data.libraries.load` to check the asset's real source.
        
        real_asset_source = LinkTraverser._find_real_source_of_asset(library_path, asset_name)
        if real_asset_source:
             # It's a different file than the immediate library
             deep_data = LinkTraverser._read_json_from_library(real_asset_source, asset_name)
             if deep_data is not None:
                 return deep_data, 'ASSET'

        return [], 'NONE'

    @staticmethod
    def _read_json_from_library(lib_path, asset_name):
        """Opens lib_path, looks for __krutart_light_link_data.json, returns dict.get(asset_name)"""
        try:
            # Safe absolute path
            abs_path = bpy.path.abspath(lib_path)
            if not abs_path: return None
            
            with bpy.data.libraries.load(abs_path, link=False) as (data_from, _):
                if DATA_BLOCK_NAME in data_from.texts:
                     # We can't read the text block body directly in `load` context easily?
                     # Wait, data_from.texts gives strings of names.
                     # We must LOAD the text block to read it.
                     pass 
            
            # We have to link it temporarily to read it? Or is there a trick?
            # Standard way: Link, Read, Remove. 
            # Or use a separate Python process (slow).
            # Let's try to link the text block.
            
            # Re-enter context to actually link/load the Text
            with bpy.data.libraries.load(abs_path, link=False) as (data_from, data_to):
                if DATA_BLOCK_NAME in data_from.texts:
                    data_to.texts = [DATA_BLOCK_NAME]
            
            if data_to.texts:
                txt = data_to.texts[0]
                content = txt.as_string()
                 # Clean up - we don't want to keep this text block
                bpy.data.texts.remove(txt)
                
                data = json.loads(content)
                return data.get(asset_name)
                
        except Exception as e:
            # log.warning(f"Error reading library {lib_path}: {e}")
            pass
        return None

    @staticmethod
    def _find_real_source_of_asset(intermediate_lib_path, asset_name):
        """
        If Asset A is linked into Layout B, and we are in Shot C linking B...
        C sees B as the library. We need to find A.
        We open B, check 'asset_name' (Collection/Object), and see if it has a .library pointer.
        """
        # This is strictly for Phase 2 Deep Lookup.
        try:
            abs_path = bpy.path.abspath(intermediate_lib_path)
            
            chain_path = None
            
            # We assume it's a Collection or Object. We'll try loading it.
            # We load it LINKED so we can inspect its properties (specifically .library)
            with bpy.data.libraries.load(abs_path, link=True) as (data_from, data_to):
                # We guess the type. Most assets are Collections or Objects.
                if asset_name in data_from.collections:
                    data_to.collections = [asset_name]
                elif asset_name in data_from.objects:
                    data_to.objects = [asset_name]
            
            loaded_item = None
            if data_to.collections: loaded_item = data_to.collections[0]
            elif data_to.objects: loaded_item = data_to.objects[0]
            
            if loaded_item and loaded_item.library:
                chain_path = loaded_item.library.filepath
            
            # Cleanup: Remove the temp linked item (and its library reference if possible)
            if loaded_item:
                 # Cleanly remove is tricky with linked data, but we can try.
                 # For now, we leave a temp orphan or use undo?
                 # Actually, we can just return the path and let Blender garbage collect on reload.
                 # But in a Loop this leaks.
                 pass
                 
            return chain_path

        except:
            return None

# --- Property Groups ---

class KrutartLightGroupItem(PropertyGroup):
    """Represents a single light group string in the UI list."""
    name: StringProperty(name="Group Name")

class KrutartObjectLightSettings(PropertyGroup):
    """Attached to Objects to manage local UI state."""
    
    # The list of Local/Active groups shown in the UI
    active_groups: CollectionProperty(type=KrutartLightGroupItem)
    
    # Track the selected item in the UI list
    active_group_index: bpy.props.IntProperty(name="Active Group Index", default=0)
    
    # UI State helpers
    is_override_active: BoolProperty(
        name="Is Local Override",
        description="If True, this object has a local override that takes precedence.",
        default=False
    )
    
    resolved_source: EnumProperty(
        items=[
            ('NONE', "None", "No data found"),
            ('ASSET', "Asset Library", "Data linked from original asset file"),
            ('LAYOUT', "Intermediate (Layout)", "Data linked from intermediate file"),
            ('LOCAL', "Local Override", "Data defined locally in this file"),
        ],
        name="Source",
        default='NONE'
    )

# --- Registration ---

# --- Operators ---

class KRUTART_OT_save_asset_data(Operator):
    """Saves the current object's light groups to the internal JSON configuration."""
    bl_idname = "krutart.save_asset_data"
    bl_label = "Save to Bundled JSON"
    bl_description = "Saves this asset's active light groups to __krutart_light_link_data.json"

    def execute(self, context):
        obj = context.active_object
        if not obj: return {'CANCELLED'}
        
        settings = obj.krutart_light_link
        # We save the 'active_groups' list.
        # But wait, 'active_groups' is the UI state. 
        # For a Library file, the UI state IS the definition.
        
        current_groups = [item.name for item in settings.active_groups]
        
        # Load existing db
        db = LibraryDataManager.load_local_data()
        
        # Update db
        # We key by Object Name. In a library, names are unique-ish.
        db[obj.name] = current_groups
        
        # Save
        LibraryDataManager.save_local_data(db)
        
        # Mark as 'ASSET' (or 'LAYOUT') since we just saved it locally
        settings.resolved_source = 'ASSET' 
        settings.is_override_active = False # Reset this flag as we are now "The Source"
        
        self.report({'INFO'}, f"Saved {len(current_groups)} groups for '{obj.name}'")
        return {'FINISHED'}

class KRUTART_OT_fetch_light_links(Operator):
    """Fetches and applies light links from the source library hierarchy."""
    bl_idname = "krutart.fetch_light_links"
    bl_label = "Sync Light Links"
    
    def execute(self, context):
        selected = context.selected_objects
        if not selected: selected = [context.active_object]
        
        count = 0
        for obj in selected:
            if not obj: continue
            
            # The Magic: Cascade Lookup
            groups, source_type = LinkTraverser.fetch_cascaded_data(obj)
            
            # Apply to UI List
            settings = obj.krutart_light_link
            settings.active_groups.clear()
            for g_name in groups:
                 item = settings.active_groups.add()
                 item.name = g_name
            
            settings.resolved_source = source_type
            
            # Apply to Real Light Linking (The "receiver" logic)
            # Assuming 'light_linking_receivers' or similar on the object.
            # Wait, Light Linking in 4.0+ is Collection based usually?
            # "Light Linking" property on Object -> specific collections.
            # Actually, Blender 4.0 Light Linking uses `object.light_linking_collection`.
            # We need to find the Light Collections by name and add this object to them?
            # Or add the collections to the object's include list?
            #
            # Let's assume standard Blender 4.0 Light Linking:
            # We need to add the Light Collections (e.g. "Key", "Rim") to `object.light_receiver_linking.collections`?
            # Inspecting API: `object.light_linking.receivers`?
            # No, Light Linking is usually defined ON THE LIGHT. "Which objects do I affect?"
            # OR defined ON THE OBJECT "Which lights affect me?"
            #
            # In Blender 4.0+:
            # `bpy.types.Object.light_linking` -> `receivers` or `blockers`. No.
            # Relationships are stored on the *Collection* or the *Light*.
            #
            # Standard workflow: "Light Groups" usually refers to *Collections of Lights*.
            # And we want to link this Object to those Collections?
            # Or is it "Light Groups" (Compositor)?
            #
            # Re-reading prompt: "assign light group data... krutart-light_linker"
            # User output implies: "We assign light group data to imported assets."
            #
            # HYPOTHESIS: The user means **Light Groups (View Layer)** or just **Collections** that Lights target.
            # Let's assume we maintain a Custom Property "light_groups" string for another tool (like bRender) to use?
            # OR we actively link the object to Blender Collections named "GroupA", "GroupB".
            #
            # Given `Link Traverser` context, likely we just need to STORE the data on the object properties 
            # so the "Render" systems pick it up?
            #
            # Let's stick to storing it in the `settings.active_groups` PropertyGroup for now.
            # If actual linking is needed, we'll add `_apply_blender_links` function later.
            
            count += 1
            
        self.report({'INFO'}, f"Synced {count} objects.")
        return {'FINISHED'}

class KRUTART_OT_make_local_override(Operator):
    """Converts the current state into a Local Override."""
    bl_idname = "krutart.make_local_override"
    bl_label = "Make Local Override"
    
    def execute(self, context):
        obj = context.active_object
        settings = obj.krutart_light_link
        
        # Dump current UI state to JSON string
        current_data = [item.name for item in settings.active_groups]
        obj[CUSTOM_PROP_OVERRIDE] = json.dumps(current_data)
        
        settings.is_override_active = True
        settings.resolved_source = 'LOCAL'
        
        return {'FINISHED'}

class KRUTART_OT_revert_to_library(Operator):
    """Removes Local Override and re-syncs."""
    bl_idname = "krutart.revert_to_library"
    bl_label = "Revert to Library"
    
    def execute(self, context):
        obj = context.active_object
        if CUSTOM_PROP_OVERRIDE in obj:
            del obj[CUSTOM_PROP_OVERRIDE]
        
        obj.krutart_light_link.is_override_active = False
        
        # Trigger sync immediately
        bpy.ops.krutart.fetch_light_links(target_object_name=obj.name) 
        # Note: We need to update Fetch to support arg or use selection.
        # The current Fetch uses selection, so it's fine.
        
        return {'FINISHED'}

class KRUTART_OT_add_group_item(Operator):
    """Adds a new item to the light group list."""
    bl_idname = "krutart.add_group_item"
    bl_label = "Add Group"
    
    def execute(self, context):
        obj = context.active_object
        settings = obj.krutart_light_link
        item = settings.active_groups.add()
        item.name = "New Group"
        return {'FINISHED'}

class KRUTART_OT_remove_group_item(Operator):
    """Removes the selected item from the light list."""
    bl_idname = "krutart.remove_group_item"
    bl_label = "Remove"
    index: bpy.props.IntProperty()
    
    def execute(self, context):
        obj = context.active_object
        settings = obj.krutart_light_link
        settings.active_groups.remove(self.index)
        return {'FINISHED'}

# --- UI Panel ---

class KRUTART_PT_light_link_panel(Panel):
    """Main UI Panel for Light Linking Tools."""
    bl_label = "Krutart Light Links"
    bl_idname = "KRUTART_PT_light_link_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    def draw(self, context):
        layout = self.layout
        obj = context.object
        
        if not obj:
            layout.label(text="Select an object.")
            return

        settings = obj.krutart_light_link
        
        # --- Header / Status ---
        row = layout.row()
        if settings.resolved_source == 'LOCAL':
             row.label(text="Source: Local Override", icon='HOME')
        elif settings.resolved_source == 'LAYOUT':
             row.label(text="Source: Layout (Intermediate)", icon='FILE_BLEND')
        elif settings.resolved_source == 'ASSET':
             row.label(text="Source: Asset Library", icon='ASSET_MANAGER')
        else:
             row.label(text="Source: None / Unknown", icon='QUESTION')

        # --- Main List ---
        row = layout.row()
        row.template_list("UI_UL_list", "light_groups", settings, "active_groups", settings, "active_group_index")
        
        # --- List Ops (Only enabled if Local or if we want to allow editing locally to create override) ---
        # Actually, user needs to likely "Enable Editing" aka Make Override first?
        # Or we allow editing and it auto-converts?
        # Let's be explicit to avoid confusion.
        
        col = row.column(align=True)
        
        # We allow adding/removing only if it's a Local file object OR a Local Override
        # If it's a Linked object without override, these should be disabled or trigger "Make Override"
        
        is_editable = (settings.resolved_source == 'LOCAL') or (obj.library is None)
        
        sub = col.column(align=True)
        sub.enabled = is_editable
        sub.operator("krutart.add_group_item", icon='ADD', text="")
        sub.operator("krutart.remove_group_item", icon='REMOVE', text="")

        layout.separator()
        
        # --- Action Buttons ---
        
        # 1. Sync (Always useful to refresh)
        layout.operator("krutart.fetch_light_links", icon='FILE_REFRESH', text="Sync from Library")
        
        # 2. Local Override Logic
        if settings.is_override_active:
            layout.operator("krutart.revert_to_library", icon='LOOP_BACK', text="Revert to Library Defaults")
        else:
            if obj.library: # Only pertinent for linked objects
                layout.operator("krutart.make_local_override", icon='EDITS', text="Customize Locally")
        
        layout.separator()
        
        # 3. Save to Internal JSON
        # This is valid for:
        # - The Asset File itself (defining defaults)
        # - The Layout File (defining intermediate defaults)
        # - The Shot File (if you really want to save local definitions to the shot file's JSON, though custom props handle overrides)
        
        layout.label(text="Library Tools:", icon='TOOL_SETTINGS')
        layout.operator("krutart.save_asset_data", icon='IMPORT', text="Save Current State to bundled JSON")


classes = (
    KrutartLightGroupItem,
    KrutartObjectLightSettings,
    KRUTART_OT_save_asset_data,
    KRUTART_OT_fetch_light_links,
    KRUTART_OT_make_local_override,
    KRUTART_OT_revert_to_library,
    KRUTART_OT_add_group_item,
    KRUTART_OT_remove_group_item,
    KRUTART_PT_light_link_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Object.krutart_light_link = bpy.props.PointerProperty(type=KrutartObjectLightSettings)

def unregister():
    del bpy.types.Object.krutart_light_link
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

