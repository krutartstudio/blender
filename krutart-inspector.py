"""
================================================================================
BLENDER PIPELINE FILE INSPECTOR UTILITY
================================================================================
Target Environment: Blender 4.5 LTS (Compatible with headless execution)
Description: 
    Inspects and logs a .blend file's structure, collection/object hierarchy,
    render settings, VSE timeline, and checks the absolute path integrity 
    of linked libraries, image textures, and simulation/alembic cache files.

--------------------------------------------------------------------------------
⚙️ COMMAND LINE USAGE (CI/CD & RUNNER INTEGRATION)
--------------------------------------------------------------------------------
Execute this script headlessly from your terminal, python subprocess, or CI:

    1. DIRECT TERMINAL DUMP (Stdout stream capture):
       blender -b <path_to_file.blend> --python krutart-inspector.py

    2. WRITE TO FILE:
       blender -b <path_to_file.blend> --python krutart-inspector.py -- <output_path.json>

Arguments:
    -b                       : Runs Blender in background (headless) mode.
    <path_to_file.blend>     : The target file to inspect.
    --python <script.py>     : Runs this script inside Blender's Python context.
    --                       : Blender argument separator. Everything AFTER this 
                               is ignored by Blender and read by this script.
    <output_path.json>       : Path where the report should be written. If omitted,
                               the script dumps the JSON straight to the terminal.

================================================================================
"""

import bpy
import json
import os
import sys

def get_collection_tree(collection):
    """
    Recursively builds the structural tree of collections and their mapped objects.
    
    Args:
        collection (bpy.types.Collection): Starting collection block.
        
    Returns:
        dict: A nested dictionary representing the Outliner-like hierarchy.
    """
    return {
        "name": collection.name,
        "objects": [obj.name for obj in collection.objects],
        "children": [get_collection_tree(child) for child in collection.children]
    }

def inspect_blend_file(output_json_path=None):
    """
    Extracts high-level scene data, structural configurations, and asset paths,
    verifying absolute filesystems on disk. Saves to JSON or outputs to stdout.
    
    Args:
        output_json_path (str, optional): Filepath to save the JSON. If None,
                                          dumps directly to the terminal stdout.
    """
    scene = bpy.context.scene
    
    # 1. Render, Timeline & Output Configurations
    render_data = {
        "engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "fps": scene.render.fps,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "output_path": scene.render.filepath
    }
    
    # 2. Outliner & Scene Collection Hierarchy
    hierarchy = get_collection_tree(scene.collection)
    
    # 3. Object & Anim Data-block Registry (Simplified Check for Pipeline Verification)
    object_registry = {}
    for obj in bpy.data.objects:
        has_anim = obj.animation_data is not None
        action_name = obj.animation_data.action.name if (has_anim and obj.animation_data.action) else None
        
        object_registry[obj.name] = {
            "type": obj.type,
            "has_animation": has_anim,
            "active_action": action_name
        }

    # 4. VSE / Sequencer Strips Map (Ensuring Guide Strips are correctly structured)
    vse_strips = []
    if scene.sequence_editor:
        strips = getattr(scene.sequence_editor, "sequences_all", None)
        if strips is None:
            strips = getattr(scene.sequence_editor, "sequences", [])
        for strip in strips:
            strip_info = {
                "name": strip.name,
                "type": strip.type,
                "channel": strip.channel,
                "frame_start": strip.frame_start,
                "frame_final_start": strip.frame_final_start,
                "frame_duration": strip.frame_duration,
                "source_path": None,
                "source_path_absolute": None
            }
            # Catch strip-level file paths
            path = None
            if hasattr(strip, "filepath"):
                path = strip.filepath
            elif strip.type == 'IMAGE' and hasattr(strip, "directory"):
                path = strip.directory
                
            if path:
                strip_info["source_path"] = path
                # Resolve relative path to absolute
                strip_info["source_path_absolute"] = bpy.path.abspath(path)
                
            vse_strips.append(strip_info)

    # 5. Asset Health & Dependency Tracking (Checks absolute filesystem health)
    external_assets = {
        "linked_libraries": [],
        "textures_and_images": [],
        "cache_files": []
    }
    
    for lib in bpy.data.libraries:
        abs_path = bpy.path.abspath(lib.filepath) if lib.filepath else ""
        external_assets["linked_libraries"].append({
            "name": lib.name,
            "path": lib.filepath,
            "absolute_path": abs_path,
            "exists": os.path.exists(abs_path) if abs_path else False
        })
        
    for img in bpy.data.images:
        if img.source in {'FILE', 'SEQUENCE'}:
            abs_path = bpy.path.abspath(img.filepath) if img.filepath else ""
            external_assets["textures_and_images"].append({
                "name": img.name,
                "path": img.filepath,
                "absolute_path": abs_path,
                "exists": os.path.exists(abs_path) if abs_path else False
            })
            
    for cache in bpy.data.cache_files:
        abs_path = bpy.path.abspath(cache.filepath) if cache.filepath else ""
        external_assets["cache_files"].append({
            "name": cache.name,
            "path": cache.filepath,
            "absolute_path": abs_path,
            "exists": os.path.exists(abs_path) if abs_path else False
        })

    # Assemble the Consolidated Inspection Record
    report = {
        "blend_file": bpy.data.filepath,
        "render_settings": render_data,
        "collection_hierarchy": hierarchy,
        "object_registry": object_registry,
        "vse_timeline": vse_strips,
        "external_assets": external_assets
    }
    
    # Handle Output Destination
    if output_json_path:
        # Create parent directories if they don't exist
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        # Write directly to file
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=4)
        print(f"\n[INSPECTOR SUCCESS] Log written to: {output_json_path}")
    else:
        # Dump straight to the terminal stdout for pipeline streams
        print("\n--- BEGIN BLEND INSPECTION JSON ---")
        print(json.dumps(report, indent=4))
        print("--- END BLEND INSPECTION JSON ---")

if __name__ == "__main__":
    # Parsing CLI separation argument '--' to look for an output destination file
    try:
        args = sys.argv[sys.argv.index("--") + 1:]
        output_path = args[0]
    except (ValueError, IndexError):
        # No file argument passed -> Defers to terminal stream mode
        output_path = None
        
    inspect_blend_file(output_path)
