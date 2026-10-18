"""File types the viewer accepts, grouped by the tool that turns them into USD."""

# Already USD: loaded as-is.
USD = {".usd", ".usda", ".usdc", ".usdz"}

# Converted by Blender (headless).
BLENDER = {".blend", ".fbx", ".obj", ".gltf", ".glb", ".stl", ".ply", ".abc", ".dae"}

# Converted by NVIDIA's CAD converter (usd-convert-cad, built on HOOPS Exchange).
CAD = {
    ".3dm",                                    # Rhino
    ".rvt", ".rfa",                            # Revit
    ".ifc",                                    # IFC / BIM
    ".dwg", ".dxf", ".dgn",                    # AutoCAD, MicroStation
    ".stp", ".step", ".igs", ".iges",          # STEP, IGES
    ".sat", ".x_t", ".x_b", ".jt", ".3mf",     # ACIS, Parasolid, JT, 3MF
    ".sldprt", ".sldasm",                      # SolidWorks
    ".ipt", ".iam",                            # Inventor
    ".catpart", ".catproduct", ".3dxml",       # CATIA
    ".prt", ".nwd",                            # NX / Creo, Navisworks
}

# A .zip holding a model plus its textures or other referenced files.
ARCHIVE = {".zip"}

SUPPORTED = USD | BLENDER | CAD | ARCHIVE
