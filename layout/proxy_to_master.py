bl_info = {
    "name": "Linked Collection Switcher (Recursive)",
    "author": "IORI, Krutart, Gemini",
    "version": (2, 0, 0),
    "blender": (4, 1, 0),
    "location": "3D Viewport > UI Panel (N) > Tool > Collection Switcher",
    "description": "Recursively switches linked collection instances between versions (e.g., Proxy '-P' and Master '-M') across multiple nesting levels. Renames the top-level instance and its containing collection.",
    "warning": "",
    "doc_url": "",
    "category": "Object",
}

import bpy

def switch_instance_recursively(obj, from_suffix, to_suffix, self, processed_objects):
    """
    Recursively finds and switches a collection instance and all valid nested instances.

    This function traverses down a hierarchy of linked collections. For each valid
    instance it finds, it performs the switch and then calls itself on the objects
    within the newly linked collection.

    Args:
        obj (bpy.types.Object): The object instance to process.
        from_suffix (str): The suffix of the current version (e.g., "-P").
        to_suffix (str): The suffix of the target version (e.g., "-M").
        self (bpy.types.Operator): The operator instance for reporting errors/info.
        processed_objects (set): A set of object names that have already been
                                 processed to prevent infinite loops.

    Returns:
        tuple: A tuple containing:
            - bool: True if the top-level object was switched, False otherwise.
            - int: The total count of switched instances in this chain (including nested).
    """
    # --- 1. BASE CASE & SAFETY CHECKS ---
    # Avoid infinite loops or processing invalid objects
    if not obj or obj in processed_objects:
        return False, 0

    processed_objects.add(obj)

    # Check if the object is a collection instance that can be switched
    if not (obj.instance_type == "COLLECTION" and obj.instance_collection and obj.instance_collection.library):
        return False, 0

    current_collection = obj.instance_collection
    if not current_collection.name.endswith(from_suffix):
        return False, 0

    # --- 2. FIND AND LINK TARGET COLLECTION ---
    target_name = current_collection.name[:-len(from_suffix)] + to_suffix
    target_collection = bpy.data.collections.get(target_name)

    # If the target collection is not in the file, link it from the source library
    if not target_collection:
        library_path = current_collection.library.filepath
        try:
            with bpy.data.libraries.load(library_path, link=True) as (data_from, data_to):
                if target_name in data_from.collections:
                    data_to.collections = [target_name]
                    self.report({"INFO"}, f"Successfully linked '{target_name}' from source library.")
                else:
                    self.report({"WARNING"}, f"'{target_name}' not found in the source file: {library_path}")
                    return False, 0
            target_collection = bpy.data.collections.get(target_name)
            if not target_collection:
                raise IOError(f"Failed to get collection '{target_name}' after linking.")
        except Exception as e:
            self.report({"ERROR"}, f"An error occurred while trying to link '{target_name}': {e}")
            return False, 0

    # --- 3. PERFORM THE SWITCH FOR THE CURRENT OBJECT ---
    # Sanity check: ensure both collections come from the same library file
    if target_collection.library != current_collection.library:
        self.report({"WARNING"}, f"Found '{target_name}', but it belongs to a different library. Skipping.")
        return False, 0

    # To make the change persistent, the object MUST be a library override.
    if obj.override_library is None:
        try:
            obj.make_override_library()
        except Exception as e:
            self.report({"WARNING"}, f"Could not create override for '{obj.name}'. Changes may not save. Error: {e}")
            # Continue anyway, as the switch might work for the current session.

    # Store metadata and perform the switch
    if "lc_switcher_original_collection" not in obj:
        obj["lc_switcher_original_collection"] = current_collection.name
    obj.instance_collection = target_collection

    switched_here = True
    total_switched_in_chain = 1

    # --- 4. RECURSE INTO THE NEW COLLECTION'S CONTENTS ---
    # After switching, check the objects inside the new collection for more instances to switch.
    for child_obj in target_collection.all_objects:
        _, child_switched_count = switch_instance_recursively(child_obj, from_suffix, to_suffix, self, processed_objects)
        total_switched_in_chain += child_switched_count

    return switched_here, total_switched_in_chain


def switch_collection_main(self, context, from_suffix, to_suffix, version_name):
    """
    Main operator function to initiate the recursive switching process.
    """
    selected_objects = context.selected_objects

    if not selected_objects:
        self.report({"WARNING"}, "No objects selected. Please select one or more collection instances.")
        return {"CANCELLED"}

    total_switched = 0
    not_found_count = 0
    processed_objects = set()  # Use one set for the entire operation

    for obj in selected_objects:
        if obj in processed_objects:
            continue

        # Initiate the recursive switch for each top-level selected object
        switched_at_top_level, num_switched_in_chain = switch_instance_recursively(
            obj, from_suffix, to_suffix, self, processed_objects
        )
        
        total_switched += num_switched_in_chain

        # --- RENAME ONLY THE TOP-LEVEL OBJECT AND ITS WRAPPER ---
        # This is done only if the user-selected object itself was switched.
        # This avoids the read-only error on nested, non-overridden objects.
        if switched_at_top_level:
            # Rename the instance object itself
            if from_suffix in obj.name:
                try:
                    obj.name = obj.name.replace(from_suffix, to_suffix, 1)
                except AttributeError:
                    # This can still happen in complex cases, so we catch it gracefully.
                    self.report({'WARNING'}, f"Could not rename '{obj.name}'. It may be protected even after override.")
                except Exception as e:
                    self.report({'WARNING'}, f"An error occurred renaming '{obj.name}': {e}")

            # Rename any local "wrapper" collection containing the object
            for coll in obj.users_collection:
                if from_suffix in coll.name and not coll.library: # Ensure it's a local collection
                    coll.name = coll.name.replace(from_suffix, to_suffix, 1)

    # --- Final, comprehensive report to the user ---
    if total_switched > 0:
        self.report({"INFO"}, f"Switched {total_switched} collection(s) (including nested) to {version_name} version.")
    else:
        self.report({"INFO"}, f"No linked collections with suffix '{from_suffix}' found in selection.")

    return {"FINISHED"}


class OBJECT_OT_switch_collection_to_master(bpy.types.Operator):
    """Switches the selected linked collection instances from Proxy (-P) to Master (-M)"""

    bl_idname = "object.switch_collection_to_master"
    bl_label = "Switch to Master (-M)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return switch_collection_main(
            self, context, from_suffix="-P", to_suffix="-M", version_name="Master"
        )


class OBJECT_OT_switch_collection_to_proxy(bpy.types.Operator):
    """Switches the selected linked collection instances from Master (-M) to Proxy (-P)"""

    bl_idname = "object.switch_collection_to_proxy"
    bl_label = "Switch to Proxy (-P)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return switch_collection_main(
            self, context, from_suffix="-M", to_suffix="-P", version_name="Proxy"
        )


class VIEW3D_PT_collection_switcher(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport UI"""

    bl_label = "Collection Switcher"
    bl_idname = "VIEW3D_PT_collection_switcher"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tool"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Switch Linked Collection", icon="COLLECTION_COLOR_02")

        # --- Button to switch to Master
        row_master = box.row()
        row_master.scale_y = 1.5
        row_master.operator(
            OBJECT_OT_switch_collection_to_master.bl_idname, icon="HIDE_OFF"
        )

        # --- Button to switch to Proxy
        row_proxy = box.row()
        row_proxy.scale_y = 1.5
        row_proxy.operator(
            OBJECT_OT_switch_collection_to_proxy.bl_idname, icon="HIDE_ON"
        )


# --- List of classes to register
classes = (
    OBJECT_OT_switch_collection_to_master,
    OBJECT_OT_switch_collection_to_proxy,
    VIEW3D_PT_collection_switcher,
)


def register():
    """Registers the classes with Blender."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregisters the classes from Blender."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
