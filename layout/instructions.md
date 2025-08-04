###hello, please create a blender collection tree traversal algorithm for my advanced_copy addon: 



 ###i have hierarchy logger -  


 Hierarchy for selected object: **+OSMA_CRASH+.001** 


 Scene Collection/ 

  ├── +LOC-MOON_D+/ 

  │   ├── MOON_D-TERRAIN/ 

  │   │   ├── moon_d_terrain 

  │   │   ├── moon_d_holes 

  │   │   └── moon_d_cary 

  │   ├── MOON_D-MODEL/ 

  │   │   ├── MOON_D-HILL/ 

  │   │   ├── MOON_D-CRATER/ 

  │   │   │   └── moon_crater_a_LOD1.004 

  │   │   ├── MOON_D-ROCK/ 

  │   │   │   └── +ROCK_G_LOD1+ 

  │   │   ├── MOON_D_HILL/ 

  │   │   │   └── moon_hill_e-LOD1.001 

  │   │   └── +MOON_FLAG_PROXY+ 

  │   └── MOON_D-VFX/ 

  ├── +ENV-APOLLO_CRASH+/ 

  │   ├── APOLLO_CRASH-MODEL/ 

  │   │   └── **+OSMA_CRASH+.001** 

  │   └── APOLLO_CRASH-VFX/ 

  └── +SC17-APOLLO_CRASH+/ 

      ├── +SC17-APOLLO_CRASH-ART+/ 

      │   ├── SC17-APOLLO_CRASH-MODEL/ 

      │   │   ├── +APOLLO_BASE-SC17-APOLLO_CRASH+ 

      │   │   └── +MOON_FLAG+ 

      │   ├── SC17-APOLLO_CRASH-PRELIGHT/ 

      │   │   ├── SC17-APOLLO_CRASH-PLGT-VOLUME/ 

      │   │   │   └── lgt-volume-apollo_crash.001 

      │   │   ├── SC17-APOLLO_CRASH-PLGT-LIGHTLINK/ 

      │   │   │   ├── 01-ALL-SC17-APOLLO_CRASH/ 

      │   │   │   │   └── lgt-01-all-key-sun_soft-apollo_crash.002 

      │   │   │   ├── 02-ACTOR-SC17-APOLLO_CRASH/ 

      │   │   │   ├── 03-ENV-SC17-APOLLO_CRASH/ 

      │   │   │   │   └── lgt-03-enviro-key-sun_hard-apollo_crash.001 

      │   │   │   ├── 04-TERRAIN-SC17-APOLLO_CRASH/ 

      │   │   │   ├── 05-ALL_BUT_VOLUME-SC17-APOLLO_CRASH/ 

      │   │   │   └── 06-VOLUME_ONLY-SC17-APOLLO_CRASH/ 

      │   │   ├── SC17-APOLLO_CRASH-PLGT-BLOCKER/ 

      │   │   │   └── lgt-blocker-apollo_crash.002 

      │   │   └── SC17-APOLLO_CRASH-PLGT-SKYDOME/ 

      │   └── SC17-APOLLO_CRASH-ART-SHOT/ 

      │       ├── SC17-SH160-ART/ 

      │       │   └── MODEL-SC17-SH160/ 

      │       ├── SC17-SH170-ART/ 

      │       │   └── MODEL-SC17-SH170/ 

      │       ├── SC17-SH180-ART/ 

      │       │   └── MODEL-SC17-SH180/ 

      │       │       └── +APOLLO_BASE-SC17-SH180+.002 

      │       └── SC17-SH190-ART/ 

      │           └── MODEL-SC17-SH190/ 

      ├── +SC17-APOLLO_CRASH-VFX+/ 

      │   └── SC17-APOLLO_CRASH-VFX-SHOT/ 

      │       ├── SC17-SH160-VFX/ 

      │       ├── SC17-SH170-VFX/ 

      │       ├── SC17-SH180-VFX/ 

      │       └── SC17-SH190-VFX/ 

      └── +SC17-APOLLO_CRASH-ANI+/ 

          ├── SC17-APOLLO_CRASH-ACTOR/ 

          │   ├── +OSMA+.002/ 

          │   │   ├── OSMA_MESH.002/ 

          │   │   │   ├── OSMA_KRYTY.002/ 

          │   │   │   │   └── ruka_kryt_A.R.002 

          │   │   │   ├── OSMA_ANTENA.002/ 

          │   │   │   │   ├── antena_bool.002/ 

          │   │   │   │   │   └── ant_maska_tycka.002 

          │   │   │   │   └── ant_misa.002 

          │   │   │   ├── telo_predek.002 

          │   │   │   └── zap.kryt.L.005 

          │   │   └── OSMA_RIG.002/ 

          │   │       └── +osma_rig.002 

          │   └── +HAMSTER_SPACE+.001/ 

          │       ├── hamster_helpers.001/ 

          │       └── hamster_ctrl.005 

          ├── SC17-APOLLO_CRASH-PROP/ 

          │   ├── +ROVER+.002 

          │   └── +APOLLO_DOOR-APOLLO_CRASH-SC17-PROP+ 

          └── SC17-APOLLO_CRASH-ANI-SHOT/ 

              ├── SC17-SH160-ANI/ 

              │   └── +CAM-SC17-SH160+/ 

              │       ├── +CAM-SC17_SH160+/ 

              │       │   ├── cam_boneshapes/ 

              │       │   │   ├── bs_cam_body 

              │       │   │   └── bs_square 

              │       │   ├── cam_mesh.004/ 

              │       │   │   ├── CAM-SC17-SH160-FLAT 

              │       │   │   └── CAM-SC17-SH160-FULLDOME 

              │       │   └── cam_rig.004/ 

              │       │       ├── cam_ref.004/ 

              │       │       │   └── ref_cam_TRG_base.004 

              │       │       └── +cam_rig.001 

              │       └── CAM-CURVE-SC17-SH160/ 

              │           └── BézierCurve.001 

              ├── SC17-SH170-ANI/ 

              │   └── +CAM-SC17-SH170+/ 

              │       ├── cam_boneshapes.001/ 

              │       │   └── bs_square.001 

              │       ├── cam_mesh.005/ 

              │       │   ├── CAM-SC17-SH170-FLAT 

              │       │   └── CAM-SC17-SH170-FULLDOME 

              │       └── cam_rig.005/ 

              │           ├── cam_ref.005/ 

              │           │   └── ref_cam_TRG_base.005 

              │           └── +cam_rig.002 

              ├── SC17-SH180-ANI/ 

              │   └── +CAM-SC17-SH180+/ 

              │       ├── cam_boneshapes.002/ 

              │       │   └── bs_square.002 

              │       ├── cam_mesh.006/ 

              │       │   ├── CAM-SC17-SH180-FLAT 

              │       │   └── CAM-SC17-SH180-FULLDOME 

              │       └── cam_rig.006/ 

              │           ├── cam_ref.006/ 

              │           │   └── ref_cam_TRG_base.006 

              │           └── +cam_rig.003 

              └── SC17-SH190-ANI/ 

                  └── +CAM-SC17-SH190+/ 

                      ├── cam_boneshapes.003/ 

                      │   └── bs_square.003 

                      ├── cam_mesh.007/ 

                      │   ├── CAM-SC17-SH190-FLAT 

                      │   └── CAM-SC17-SH190-FULLDOME 

                      └── cam_rig.007/ 

                          ├── cam_ref.007/ 

                          │   └── ref_cam_TRG_base.007 

                          └── +cam_rig.004 


 -------------------------------------------------- 


 ### please create a traverse functionality for COPY_TO_CURRENT_SHOT +OSMA_CRASH+.001 into SC##-APOLLO_CRASH-ART-SHOT/SC##-SH###-ART/MODEL-SC##-SH###/ 


 ###I need to add this functionality to automatically copy the object into the correct MODEL-SC##-SH### collection based on which shot the playhead is currently on. retain the toggle visibility on/off for the duplicate and the original from the advanced_copy.py addon. 


 ###leave just three options in the right click menu: 

 copy to current shot(functionality explained a paragraph above this) 

 copy to current scene(list button)

     > move copies to all scenes(just placeholder for now)

     > copy to current scene (move the +osma_crash+.001 to SC##-LOCATION_NAME-MODEL [in this case SC17-APOLLO_CRASH-MODEL]) 

 copy to current enviro - please just implement placeholder now 

## cameras and markers are named CAM-SC##-SH###-FLAT/CAM-SC##-SH###-FULLDOME


 ###advanced_copy.py: 


 bl_info = { 

     "name": "Advanced Copy", 

     "author": "iori, krutart, gemini", 

     "version": (1, 0), 

     "blender": (4, 0, 0), 

     "location": "View3D > Object Context Menu", 

     "description": "Advanced copy/move operations based on timeline shots and collection structure.", 

     "warning": "", 

     "doc_url": "", 

     "category": "Object", 

 } 


 import bpy 

 from bpy.props import EnumProperty 


 # --- Helper Functions --- 


 def get_timeline_shots(): 

     """ 

     Identifies shots based on camera markers in the timeline. 

     Returns a list of tuples, where each tuple contains (shot_name, start_frame, end_frame). 

     """ 

     shots = [] 

     scene = bpy.context.scene 

     markers = sorted([m for m in scene.timeline_markers if m.camera], key=lambda m: m.frame) 


     if not markers: 

         # If no camera markers, consider the whole timeline as one shot 

         shots.append(("Default Shot", scene.frame_start, scene.frame_end)) 

         return shots 


     for i, marker in enumerate(markers): 

         start_frame = marker.frame 

         end_frame = scene.frame_end 

         if i + 1 < len(markers): 

             end_frame = markers[i+1].frame - 1 

         shots.append((marker.name, start_frame, end_frame)) 


     return shots 


 def get_current_shot(): 

     """ 

     Determines the current shot based on the playhead position. 

     Returns a tuple (shot_name, start_frame, end_frame) or None if not in a shot. 

     """ 

     current_frame = bpy.context.scene.frame_current 

     shots = get_timeline_shots() 

     for shot_name, start, end in shots: 

         if start <= current_frame <= end: 

             return (shot_name, start, end) 

     return None 


 def toggle_object_visibility(obj, frame_range, hide_in_viewport=True, hide_in_render=True): 

     """ 

     Toggles the visibility of an object for a specific frame range. 

     """ 

     start_frame, end_frame = frame_range 


     # --- Viewport Visibility (hide_viewport) --- 

     # Set initial state 

     obj.hide_viewport = not hide_in_viewport 

     obj.keyframe_insert(data_path="hide_viewport", frame=start_frame - 1) 


     # Change state for the shot duration 

     obj.hide_viewport = hide_in_viewport 

     obj.keyframe_insert(data_path="hide_viewport", frame=start_frame) 


     # Revert state after the shot 

     obj.hide_viewport = not hide_in_viewport 

     obj.keyframe_insert(data_path="hide_viewport", frame=end_frame + 1) 



     # --- Render Visibility (hide_render) --- 

     # Set initial state 

     obj.hide_render = not hide_in_render 

     obj.keyframe_insert(data_path="hide_render", frame=start_frame - 1) 


     # Change state for the shot duration 

     obj.hide_render = hide_in_render 

     obj.keyframe_insert(data_path="hide_render", frame=start_frame) 


     # Revert state after the shot 

     obj.hide_render = not hide_in_render 

     obj.keyframe_insert(data_path="hide_render", frame=end_frame + 1) 



 def get_collections_by_prefix(prefix): 

     """ 

     Returns a list of all collections starting with a given prefix. 

     """ 

     return [c for c in bpy.data.collections if c.name.startswith(prefix)] 


 # --- Operators --- 


 class ADVCOPY_OT_copy_to_current_shot(bpy.types.Operator): 

     """Create a copy visible only in the current shot and hide the original""" 

     bl_idname = "object.advcopy_copy_to_current_shot" 

     bl_label = "Copy to Current Shot" 

     bl_options = {'REGISTER', 'UNDO'} 


     @classmethod 

     def poll(cls, context): 

         return context.active_object is not None and get_current_shot() is not None 


     def execute(self, context): 

         shot_info = get_current_shot() 

         if not shot_info: 

             self.report({'WARNING'}, "Not currently in a defined shot.") 

             return {'CANCELLED'} 


         _, start_frame, end_frame = shot_info 

         original_obj = context.active_object 


         # Duplicate the object 

         bpy.ops.object.duplicate() 

         new_obj = context.active_object 

         new_obj.name = f"{original_obj.name}_shot_copy" 


         # Toggle visibility 

         toggle_object_visibility(original_obj, (start_frame, end_frame), hide_in_viewport=True, hide_in_render=True) 

         toggle_object_visibility(new_obj, (start_frame, end_frame), hide_in_viewport=False, hide_in_render=False) 


         self.report({'INFO'}, f"Copied '{original_obj.name}' to current shot.") 

         return {'FINISHED'} 


 class ADVCOPY_OT_move_to_all_scenes(bpy.types.Operator): 

     """Move the original object to all scenes and remove from the original enviro folder""" 

     bl_idname = "object.advcopy_move_to_all_scenes" 

     bl_label = "Move Original to All Scenes" 

     bl_options = {'REGISTER', 'UNDO'} 


     @classmethod 

     def poll(cls, context): 

         return context.active_object is not None 


     def execute(self, context): 

         original_obj = context.active_object 

         original_collections = list(original_obj.users_collection) 


         # Link to all scenes 

         for scene in bpy.data.scenes: 

             if original_obj.name not in scene.collection.objects: 

                  scene.collection.objects.link(original_obj) 


         # Unlink from original collections that are not scene collections 

         for coll in original_collections: 

             if not coll.name.startswith("+SC"): 

                 coll.objects.unlink(original_obj) 


         self.report({'INFO'}, f"Moved '{original_obj.name}' to all scenes.") 

         return {'FINISHED'} 



 class ADVCOPY_OT_copy_to_one_scene(bpy.types.Operator): 

     """Copy object to a specific scene and manage visibility""" 

     bl_idname = "object.advcopy_copy_to_one_scene" 

     bl_label = "Copy to a Specific Scene" 

     bl_options = {'REGISTER', 'UNDO'} 


     def scene_items(self, context): 

         return [(s.name, s.name, "") for s in bpy.data.scenes if s != context.scene] 


     target_scene: EnumProperty(items=scene_items, name="Target Scene") 


     @classmethod 

     def poll(cls, context): 

         return context.active_object is not None and len(bpy.data.scenes) > 1 


     def execute(self, context): 

         original_obj = context.active_object 

         target_scene = bpy.data.scenes.get(self.target_scene) 


         if not target_scene: 

             self.report({'ERROR'}, "Target scene not found.") 

             return {'CANCELLED'} 


         # Duplicate object 

         bpy.ops.object.duplicate() 

         new_obj = context.active_object 

         new_obj.name = f"{original_obj.name}_{target_scene.name}_copy" 


         # Link copy to target scene 

         target_scene.collection.objects.link(new_obj) 

         context.scene.collection.objects.unlink(new_obj) # Unlink from current scene 


         # This is a simplified visibility toggle. A more robust implementation 

         # might use drivers or more complex animation data handling. 

         # For now, we just set the state. 

         original_obj.hide_render = False 

         original_obj.hide_viewport = False 

         new_obj.hide_render = True 

         new_obj.hide_viewport = True 


         self.report({'INFO'}, f"Copied '{original_obj.name}' to scene '{target_scene.name}'. Manual visibility setup may be needed.") 

         return {'FINISHED'} 


     def invoke(self, context, event): 

         context.window_manager.invoke_props_dialog(self) 

         return {'RUNNING_MODAL'} 



 class ADVCOPY_OT_move_to_enviro(bpy.types.Operator): 

     """Move the object to a specified Environment collection""" 

     bl_idname = "object.advcopy_move_to_enviro" 

     bl_label = "Move to Environment" 

     bl_options = {'REGISTER', 'UNDO'} 


     def enviro_items(self, context): 

         return [(c.name, c.name, "") for c in get_collections_by_prefix("+ENV-")] 


     target_enviro: EnumProperty(items=enviro_items, name="Environment Collection") 


     @classmethod 

     def poll(cls, context): 

         return context.active_object is not None and get_collections_by_prefix("+ENV-") 


     def execute(self, context): 

         obj = context.active_object 

         target_coll = bpy.data.collections.get(self.target_enviro) 


         if not target_coll: 

             self.report({'ERROR'}, "Target environment collection not found.") 

             return {'CANCELLED'} 


         # Unlink from all other collections 

         for coll in obj.users_collection: 

             coll.objects.unlink(obj) 


         # Link to target collection 

         target_coll.objects.link(obj) 


         self.report({'INFO'}, f"Moved '{obj.name}' to '{target_coll.name}'.") 

         return {'FINISHED'} 


     def invoke(self, context, event): 

         context.window_manager.invoke_props_dialog(self) 

         return {'RUNNING_MODAL'} 



 # --- Menus --- 


 class ADVCOPY_MT_copy_to_scene_menu(bpy.types.Menu): 

     bl_label = "Copy to Current Scene" 

     bl_idname = "OBJECT_MT_advcopy_copy_to_scene" 


     def draw(self, context): 

         layout = self.layout 

         layout.operator(ADVCOPY_OT_move_to_all_scenes.bl_idname) 

         layout.operator(ADVCOPY_OT_copy_to_one_scene.bl_idname) 



 def draw_main_menu(self, context): 

     layout = self.layout 

     layout.separator() 

     layout.operator(ADVCOPY_OT_copy_to_current_shot.bl_idname) 

     layout.menu(ADVCOPY_MT_copy_to_scene_menu.bl_idname) 

     layout.operator(ADVCOPY_OT_move_to_enviro.bl_idname) 



 # --- Registration --- 


 classes = [ 

     ADVCOPY_OT_copy_to_current_shot, 

     ADVCOPY_OT_move_to_all_scenes, 

     ADVCOPY_OT_copy_to_one_scene, 

     ADVCOPY_OT_move_to_enviro, 

     ADVCOPY_MT_copy_to_scene_menu, 

 ] 


 def register(): 

     for cls in classes: 

         bpy.utils.register_class(cls) 

     bpy.types.VIEW3D_MT_object_context_menu.append(draw_main_menu) 


 def unregister(): 

     bpy.types.VIEW3D_MT_object_context_menu.remove(draw_main_menu) 

     for cls in reversed(classes): 

         bpy.utils.unregister_class(cls) 



 if __name__ == "__main__": 

     register() 
