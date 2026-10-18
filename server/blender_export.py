"""Runs inside Blender: import a 3D file (or open a .blend) and export it as USD.

    blender -b --factory-startup --disable-autoexec [model.blend] --python blender_export.py -- <input> <output.usdc>
"""

import sys

import bpy

IMPORTERS = {
    "fbx": bpy.ops.import_scene.fbx,
    "obj": bpy.ops.wm.obj_import,
    "gltf": bpy.ops.import_scene.gltf,
    "glb": bpy.ops.import_scene.gltf,
    "stl": bpy.ops.wm.stl_import,
    "ply": bpy.ops.wm.ply_import,
    "abc": bpy.ops.wm.alembic_import,
    "dae": bpy.ops.wm.collada_import,
}

source, target = sys.argv[sys.argv.index("--") + 1:]
extension = source.rsplit(".", 1)[-1].lower()

# A .blend is opened by Blender itself; everything else is imported into an empty scene.
if extension != "blend":
    bpy.ops.wm.read_factory_settings(use_empty=True)
    IMPORTERS[extension](filepath=source)

# Lights and cameras are left out: the viewer brings its own camera and white sky light.
bpy.ops.wm.usd_export(
    filepath=target,
    export_animation=False,
    export_lights=False,
    export_cameras=False,
    export_materials=True,
    generate_preview_surface=True,
    export_textures_mode="NEW",
)
