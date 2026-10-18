"""Runs inside Blender: import a 3D file (or open a .blend) and export it as USD or GLB.

    blender -b --factory-startup --disable-autoexec [model.blend] --python blender_export.py -- <input> <output.usdc|output.glb>
"""

import sys

import bpy


def import_usd(filepath):
    # Instances become real copies: CAD converters share repeated parts (doors, windows,
    # Rhino blocks) as USD instances, and the glTF export would otherwise drop some of them.
    bpy.ops.wm.usd_import(filepath=filepath, import_cameras=False, import_lights=False,
                          apply_unit_conversion_scale=True, support_scene_instancing=False)


IMPORTERS = {
    "fbx": bpy.ops.import_scene.fbx,
    "obj": bpy.ops.wm.obj_import,
    "gltf": bpy.ops.import_scene.gltf,
    "glb": bpy.ops.import_scene.gltf,
    "stl": bpy.ops.wm.stl_import,
    "ply": bpy.ops.wm.ply_import,
    "abc": bpy.ops.wm.alembic_import,
    "dae": bpy.ops.wm.collada_import,
    "usd": import_usd,
    "usda": import_usd,
    "usdc": import_usd,
    "usdz": import_usd,
}

source, target = sys.argv[sys.argv.index("--") + 1:]
extension = source.rsplit(".", 1)[-1].lower()

# A .blend is opened by Blender itself; everything else is imported into an empty scene.
if extension != "blend":
    bpy.ops.wm.read_factory_settings(use_empty=True)
    IMPORTERS[extension](filepath=source)

# Lights and cameras are left out: the viewers bring their own camera and lighting.
if target.lower().endswith(".glb"):
    bpy.ops.export_scene.gltf(
        filepath=target,
        export_format="GLB",
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        export_apply=True,
    )
else:
    bpy.ops.wm.usd_export(
        filepath=target,
        export_animation=False,
        export_lights=False,
        export_cameras=False,
        export_materials=True,
        generate_preview_surface=True,
        export_textures_mode="NEW",
    )
