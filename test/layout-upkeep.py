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
