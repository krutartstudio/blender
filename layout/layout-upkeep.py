"""
This script provides a Blender addon to automate the creation of a specific
collection hierarchy for organizing large projects. It helps maintain a consistent
structure for different types of scenes: Locations, Environments, and Shots.

--- NAMING CONVENTIONS ---

LOCATION =>
  - Scene Name: LOC-<loc_name>
  - Example: "LOC-MOON_D"

ENVIRO =>
  - Scene Name: ENV-<env_name>
  - Example: "ENV-APOLLO_HILL"

SCENE =>
  - Scene Name: SC<id>-<env_name>
  - Example: "SC17-APOLLO_CRASH"

SC<id> = SC##
SH<id> = SH###

--- COLLECTION STRUCTURE ---

+LOC-<loc_name>+ (Blue)
  LOC-<loc_name>-TERRAIN
  LOC-<loc_name>-MODEL
  LOC-<loc_name>-VFX

+ENV-<env_name>+ (Green)
  ENV-<env_name>-MODEL
  ENV-<env_name>-VFX

+SC<id>-<env_name>+ (Red)
  +SC<id>-<env_name>-ART+
    MODEL-SC<id>-<env_name>
    SHOT-SC<id>-<env_name>-ART
      MODEL-SC<id>-SH<id>

  +SC<id>-<env_name>-ANI+
    ACTOR-SC<id>-<env_name>
    PROP-SC<id>-<env_name>
    SHOT-SC<id>-<env_name>-ANI
      CAM-SC<id>-SH<id>

  +SC<id>-<env_name>-VFX+
    VFX-SC<id>-<env_name>
    SHOT-SC<id>-<env_name>-VFX
      VFX-SC<id>-SH<id>
"""

bl_info = {
    "name": "Custom Scene Layout Setup",
    "author": "IORI, Gemini, Krutart",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),  # Compatible starting with Blender 4.0
    "location": "Outliner > Context Menu (Right-click)",
    "description": "Initializes and color-codes collection structures based on scene name. Avoids creating duplicates.",
    "warning": "",
    "doc_url": "",
    "category": "Scene",
}

import bpy
import re

# --- Color Constants for Collection Tags ---
# Using a dictionary makes it easy to manage and change colors.
COLLECTION_COLORS = {
    "LOCATION": "COLOR_05",  # Blue
    "ENVIRO": "COLOR_04",  # Green
    "SCENE": "COLOR_01",  # Red
    "ART": "COLOR_02",  # Yellow
    "ANI": "COLOR_03",  # Orange
    "VFX": "COLOR_06",  # Pink/Magenta
}


def get_or_create_collection(name, parent_collection, color_tag=None):
    """
    Checks if a collection with the given name exists as a child of the parent.
    If it exists, returns the existing collection.
    If it doesn't exist, creates it, links it, and then returns it.
    Applies a color tag if one is provided, whether the collection is new or existing.

    Args:
        name (str): The name of the collection to get or create.
        parent_collection (bpy.types.Collection): The parent collection.
        color_tag (str, optional): The color tag to apply (e.g., 'COLOR_01').

    Returns:
        tuple[bpy.types.Collection, bool]: A tuple containing the collection
                                           and a boolean that is True if the
                                           collection was newly created.
    """
    # Check if the collection already exists as a child of the specified parent.
    if name in parent_collection.children:
        collection = parent_collection.children[name]
        created = False
    else:
        # If not, create a new collection in the main blend file data.
        collection = bpy.data.collections.new(name)
        # Link the new collection to the parent.
        parent_collection.children.link(collection)
        created = True

    # Set the color tag. This is done for both existing and new collections
    # to ensure the color scheme is always correctly applied.
    if color_tag:
        collection.color_tag = color_tag

    return collection, created


# --- Operators ---
class SCENE_OT_create_location_structure(bpy.types.Operator):
    """Operator to build the LOCATION collection structure."""

    bl_idname = "scene.create_location_structure"
    bl_label = "Setup LOCATION Collections"
    bl_description = "Creates the collection structure for a LOCATION scene (LOC-)"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        master_collection = scene.collection
        parent_col_name = f"+{base_name}+"

        # Get or create the main parent collection for the location.
        loc_parent_col, created = get_or_create_collection(
            parent_col_name,
            master_collection,
            color_tag=COLLECTION_COLORS["LOCATION"],
        )

        # If the main collection already existed, inform the user.
        if not created:
            self.report(
                {"INFO"},
                f"Base collection '{parent_col_name}' already exists. Verifying sub-collections.",
            )

        # Ensure the required sub-collections exist.
        get_or_create_collection(f"{base_name}-TERRAIN", loc_parent_col)
        get_or_create_collection(f"{base_name}-MODEL", loc_parent_col)
        get_or_create_collection(f"{base_name}-VFX", loc_parent_col)

        self.report({"INFO"}, f"Verified LOCATION structure for '{base_name}'.")
        return {"FINISHED"}


class SCENE_OT_create_enviro_structure(bpy.types.Operator):
    """Operator to build the ENVIRONMENT collection structure."""

    bl_idname = "scene.create_enviro_structure"
    bl_label = "Setup ENVIRO Collections"
    bl_description = "Creates the collection structure for an ENVIRONMENT scene (ENV-)"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        master_collection = scene.collection
        parent_col_name = f"+{base_name}+"

        env_parent_col, created = get_or_create_collection(
            parent_col_name,
            master_collection,
            color_tag=COLLECTION_COLORS["ENVIRO"],
        )

        if not created:
            self.report(
                {"INFO"},
                f"Base collection '{parent_col_name}' already exists. Verifying sub-collections.",
            )

        get_or_create_collection(f"{base_name}-MODEL", env_parent_col)
        get_or_create_collection(f"{base_name}-VFX", env_parent_col)

        self.report({"INFO"}, f"Verified ENVIRO structure for '{base_name}'.")
        return {"FINISHED"}


class SCENE_OT_create_scene_structure(bpy.types.Operator):
    """Operator to build the SCENE collection structure and link the project's LOCATION."""

    bl_idname = "scene.create_scene_structure"
    bl_label = "Setup SCENE Collections"
    bl_description = "Creates the collection structure for a SCENE (SC##-) and links the root LOCATION"

    def execute(self, context):
        scene = context.scene
        base_name = scene.name
        master_collection = scene.collection

        match = re.match(r"^(SC\d+)-(.+)", base_name)
        if not match:
            self.report(
                {"ERROR"}, "Scene name format is incorrect. Expected 'SC##-<env_name>'."
            )
            return {"CANCELLED"}

        sc_id = match.group(1)
        sh_id = "SH001"  # Default Shot ID
        parent_col_name = f"+{base_name}+"

        sc_parent_col, created = get_or_create_collection(
            parent_col_name,
            master_collection,
            color_tag=COLLECTION_COLORS["SCENE"],
        )
        if not created:
            self.report(
                {"INFO"},
                f"Base collection '{parent_col_name}' already exists. Verifying sub-collections.",
            )

        # --- ART Sub-structure ---
        art_col, _ = get_or_create_collection(
            f"+{base_name}-ART+", sc_parent_col, color_tag=COLLECTION_COLORS["ART"]
        )
        get_or_create_collection(f"MODEL-{base_name}", art_col)
        shot_art_col, _ = get_or_create_collection(f"SHOT-{base_name}-ART", art_col)
        get_or_create_collection(f"MODEL-{sc_id}-{sh_id}", shot_art_col)

        # --- ANI Sub-structure ---
        ani_col, _ = get_or_create_collection(
            f"+{base_name}-ANI+", sc_parent_col, color_tag=COLLECTION_COLORS["ANI"]
        )
        get_or_create_collection(f"ACTOR-{base_name}", ani_col)
        get_or_create_collection(f"PROP-{base_name}", ani_col)
        shot_ani_col, _ = get_or_create_collection(f"SHOT-{base_name}-ANI", ani_col)
        get_or_create_collection(f"CAM-{sc_id}-{sh_id}", shot_ani_col)

        # --- VFX Sub-structure ---
        vfx_col, _ = get_or_create_collection(
            f"+{base_name}-VFX+", sc_parent_col, color_tag=COLLECTION_COLORS["VFX"]
        )
        get_or_create_collection(f"VFX-{base_name}", vfx_col)
        shot_vfx_col, _ = get_or_create_collection(f"SHOT-{base_name}-VFX", vfx_col)
        get_or_create_collection(f"VFX-{sc_id}-{sh_id}", shot_vfx_col)

        # --- Link Location Root Collection ---
        location_collection_to_link = None
        for collection in bpy.data.collections:
            if collection.name.startswith("+LOC-") and collection.name.endswith("+"):
                location_collection_to_link = collection
                break

        if location_collection_to_link:
            if location_collection_to_link.name not in master_collection.children:
                master_collection.children.link(location_collection_to_link)
                self.report(
                    {"INFO"}, f"Linked Location: '{location_collection_to_link.name}'."
                )
            else:
                self.report(
                    {"INFO"},
                    f"Location '{location_collection_to_link.name}' was already linked.",
                )
        else:
            self.report(
                {"WARNING"},
                "No root LOCATION collection ('+LOC-...') found in the file to link.",
            )

        self.report({"INFO"}, f"Verified SCENE structure for '{base_name}'.")
        return {"FINISHED"}


# --- UI Menu ---
class OUTLINER_MT_custom_structure_menu(bpy.types.Menu):
    bl_label = "Custom Scene Setup"
    bl_idname = "OUTLINER_MT_custom_structure_menu"

    def draw(self, context):
        layout = self.layout
        scene_name = context.scene.name

        if re.match(r"^LOC-", scene_name):
            layout.operator(SCENE_OT_create_location_structure.bl_idname)
        if re.match(r"^ENV-", scene_name):
            layout.operator(SCENE_OT_create_enviro_structure.bl_idname)
        if re.match(r"^SC\d+-", scene_name):
            layout.operator(SCENE_OT_create_scene_structure.bl_idname)


def draw_menu_in_outliner(self, context):
    scene_name = context.scene.name
    if re.match(r"^(LOC-|ENV-|SC\d+-)", scene_name):
        self.layout.separator()
        self.layout.menu(OUTLINER_MT_custom_structure_menu.bl_idname)


# --- Registration ---
classes = (
    SCENE_OT_create_location_structure,
    SCENE_OT_create_enviro_structure,
    SCENE_OT_create_scene_structure,
    OUTLINER_MT_custom_structure_menu,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.OUTLINER_MT_context_menu.append(draw_menu_in_outliner)


def unregister():
    bpy.types.OUTLINER_MT_context_menu.remove(draw_menu_in_outliner)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
