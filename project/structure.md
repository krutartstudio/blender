LOCATION =>
LOC-<loc_name>
"LOC-MOON_D"

ENVIRO =>
ENV-<env_name>
"ENV-APOLLO_HILL"

SCENE =>
SC<id>-<env_name>
"SC17-APOLLO_CRASH"SCENE

SC<id> = SC##
SH<id> = SH###

### collection structure in file

+LOC-<loc_name>+
  LOC-<loc_name>-TERRAIN
  LOC-<loc_name>-MODEL
  LOC-<loc_name>-VFX

+ENV-<env_name>+
  ENV-<env_name>-MODEL
  ENV-<env_name>-VFX

+SC<id>-<env_name>+
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
