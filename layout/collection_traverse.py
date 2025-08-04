bl_info = {
    "name": "Object Hierarchy Tree",
    "author": "Krutart, iori, Gemini",
    "version": (1, 2),
    "blender": (4, 1, 0),
    "location": "System Console",
    "description": "Displays the full scene hierarchy in the console as a tree, highlighting the selected object.",
    "warning": "",
    "doc_url": "",
    "category": "Development",
}

import bpy
from bpy.app.handlers import persistent

def print_tree_recursive(collection, active_obj, prefix=""):
    """
    Recursively prints the collection and object hierarchy in a tree format,
    highlighting the active object.

    Args:
        collection (bpy.types.Collection): The collection to start printing from.
        active_obj (bpy.types.Object): The currently selected object to highlight.
        prefix (str): The string used for indentation and tree branch lines.
    """
    # Get all child items to correctly determine the last item for tree connectors
    child_collections = list(collection.children)
    objects_in_collection = list(collection.objects)
    total_items = len(child_collections) + len(objects_in_collection)
    item_count = 0

    # --- 1. Print Child Collections ---
    for child_col in child_collections:
        item_count += 1
        is_last = item_count == total_items
        connector = "└── " if is_last else "├── "
        
        # Print the collection name, adding a '/' to distinguish it
        print(f"{prefix}{connector}{child_col.name}/")

        # Calculate the prefix for the next level of recursion
        child_prefix = prefix + ("    " if is_last else "│   ")
        print_tree_recursive(child_col, active_obj, child_prefix)

    # --- 2. Print Objects in this Collection ---
    for obj in objects_in_collection:
        item_count += 1
        is_last = item_count == total_items
        connector = "└── " if is_last else "├── "
        
        # Check if the current object is the active one and format it
        if obj == active_obj:
            print(f"{prefix}{connector}**{obj.name}**")
        else:
            print(f"{prefix}{connector}{obj.name}")


@persistent
def selection_change_handler(scene):
    """
    This function is called every time the selection changes.
    It clears the console and logs the full scene hierarchy, highlighting
    the active object.
    """
    active_obj = bpy.context.active_object
    if not active_obj:
        return

    # A small function to clear the system console (works on Windows, Linux, macOS)
    # This makes the output clean for each selection change.
    #bpy.ops.wm.console_clear()

    print("-" * 50)
    print(f"Hierarchy for selected object: **{active_obj.name}**")
    
    # The root of the hierarchy tree is the scene's main collection
    root_collection = bpy.context.scene.collection
    print(f"{root_collection.name}/")

    # Start the recursive printing from the root collection
    print_tree_recursive(root_collection, active_obj, prefix=" ")
    print("-" * 50)


def register():
    """
    Registers the addon and adds the handler.
    """
    # Use depsgraph_update_post which is a reliable handler for selection changes.
    if selection_change_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(selection_change_handler)
    print("Object Hierarchy Tree Registered.")


def unregister():
    """
    Unregisters the addon and removes the handler.
    """
    if selection_change_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(selection_change_handler)
    print("Object Hierarchy Tree Unregistered.")


# This allows the script to be run directly from Blender's Text Editor
if __name__ == "__main__":
    # Unregister the previous version of the handler if it exists
    try:
        unregister()
    except (RuntimeError, Exception):
        # Fails if the script was never registered, which is fine.
        pass
    
    register()
