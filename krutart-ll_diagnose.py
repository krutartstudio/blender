"""
================================================================================
KRUTART LIGHT LINK - TRANSFER DIAGNOSTIC (read-only, no bl_info: never auto-installed)
================================================================================
Answers one question: if you ran "Append LGT File" with <source> into <target>,
which light group members would be assigned, and which would not - and why.

Nothing is modified: the target is opened read-only and never saved, the source
is read in the same way the add-on reads it.

--------------------------------------------------------------------------------
USAGE (headless, like krutart-inspector.py)
--------------------------------------------------------------------------------
    blender -b --factory-startup <target.blend> --python krutart-ll_diagnose.py -- <source.blend> [report.json]

--factory-startup keeps the other pipeline add-ons out of the log; the diagnostic loads
Light Link itself. Leave it out if you want the file opened exactly as artists see it.

Example:
    blender -b "S:\\...\\3212-sc11-sh060-art-v001-LightLink_test.blend" ^
        --python "S:\\3212-PREPRODUCTION\\SOFTWARE\\BLENDER\\ADDON\\krutart-ll_diagnose.py" ^
        -- "S:\\...\\3212-sc11-sh050-art-v005-LightLink_test.blend" "%USERPROFILE%\\ll_report.json"

The summary is printed to the console; pass a path to also get the full JSON.
================================================================================
"""

import bpy
import os
import sys
import json
import importlib.util

ADDON_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "krutart-light_link.py"),
    os.path.join(bpy.utils.user_resource('SCRIPTS'), "addons", "krutart-light_link.py"),
]


def load_addon():
    """Returns the Light Link add-on module, reusing it when Blender already has it loaded."""
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", "") or ""
        if path.endswith("krutart-light_link.py") and getattr(module, "bl_info", None):
            print("DIAG using the add-on already loaded in this Blender (v%s)"
                  % ".".join(str(n) for n in module.bl_info["version"]))
            return module
    for path in ADDON_CANDIDATES:
        if not os.path.exists(path):
            continue
        spec = importlib.util.spec_from_file_location("krutart_light_link_diag", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["krutart_light_link_diag"] = module
        spec.loader.exec_module(module)
        module.register()
        print("DIAG add-on loaded: %s (v%s)" % (path, ".".join(str(n) for n in module.bl_info["version"])))
        return module
    raise SystemExit("DIAG could not find krutart-light_link.py next to this script or in the add-ons folder.")


def kind_of(obj):
    if obj.override_library:
        return "override"
    return "linked" if obj.library else "local"


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("DIAG usage: blender -b <target.blend> --python krutart-ll_diagnose.py -- <source.blend> [report.json]")
    source_path, out_json = argv[0], (argv[1] if len(argv) > 1 else None)
    m = load_addon()

    target_name = bpy.path.basename(bpy.data.filepath) or "(unsaved)"
    print("DIAG target: %s | source: %s" % (target_name, os.path.basename(source_path)))

    # --- the target and the matching, using the add-on's own rules --------------------------
    kinds = {"override": 0, "linked": 0, "local": 0}
    for obj in bpy.context.scene.objects:
        kinds[kind_of(obj)] += 1
    print("DIAG target scene objects: %d (%d override, %d linked, %d local)"
          % (len(bpy.context.scene.objects), kinds["override"], kinds["linked"], kinds["local"]))
    index = m.build_target_index(bpy.context.scene, set(bpy.context.scene.objects))

    source, lights_blend, tempdir = m.extract_source_light_data(source_path)
    if not source:
        raise SystemExit("DIAG could not read the source file - see the messages above.")
    print("DIAG source entries: %d objects, %d nested collections" % (len(source["objects"]), len(source["collections"])))

    rows, used = [], set()
    for entry in source["objects"]:
        groups = sorted({g for g in set(entry["groups"]) | set(entry["props_groups"] or []) if m.is_light_group_name(g)})
        if not groups and not entry["customised"]:
            continue
        match, rule, reason = m.match_source_entry(entry, index, source, used)
        if match is not None:
            used.add(match.as_pointer())
        elif entry["name"] in index["any_name"]:
            others = index["any_name"][entry["name"]]
            gap = min((m.object_world_position(o) - entry["position"]).length for o in others)
            reason += " (the name exists in the target, nearest one is %.2f m away)" % gap
        rows.append({
            "source_object": entry["name"], "source_kind": entry["kind"], "groups": groups,
            "matched": match.name if match else None,
            "matched_kind": kind_of(match) if match else None,
            "writable": bool(match and not (match.library and not match.override_library)),
            "rule": rule, "reason": reason,
        })

    if tempdir:
        import shutil
        shutil.rmtree(tempdir, ignore_errors=True)

    matched = [r for r in rows if r["matched"]]
    missing = [r for r in rows if not r["matched"]]
    print("DIAG ---------------------------------------------------------------")
    print("DIAG would assign %d of %d light group members" % (len(matched), len(rows)))
    by_rule = {}
    for r in matched:
        by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
    print("DIAG matched by rule: %s" % (by_rule or "none"))
    print("DIAG membership-only (linked, cannot store settings): %d" % sum(1 for r in matched if not r["writable"]))
    per_group = {}
    for r in rows:
        for g in r["groups"]:
            hit, total = per_group.get(g, (0, 0))
            per_group[g] = (hit + (1 if r["matched"] else 0), total + 1)
    for g in sorted(per_group):
        hit, total = per_group[g]
        print("DIAG   %-24s %d/%d members would be assigned" % (g, hit, total))
    if missing:
        print("DIAG not matched (first 15):")
        for r in missing[:15]:
            print("DIAG   %-34s %-9s groups=%s -> %s" % (r["source_object"], r["source_kind"], ",".join(r["groups"]), r["reason"]))
    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump({"target": target_name, "source": os.path.basename(source_path), "rows": rows}, f, indent=2)
        print("DIAG full report written to %s" % out_json)


main()
