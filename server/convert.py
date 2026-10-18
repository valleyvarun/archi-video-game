"""Turn an uploaded 3D file into USD the viewer can load.

Runs in the converter environment (server/.venv-convert):
    python convert.py <uploaded file> <output folder>          # USD for the Omniverse viewer
    python convert.py <uploaded file> <output folder> --glb    # GLB for the web viewer

Picks a converter by file type (see formats.py), then writes `placed.usda`: a small
layer that references the converted model and turns it upright (Z up) and into metres.
Models far from the origin are centred on the grid. Prints one JSON line, either
    {"ok": true, "usd": "<path to placed.usda>", "bounds": [minx, miny, minz, maxx, maxy, maxz], "converter": "..."}
or
    {"ok": false, "error": "<message for the user>"}
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from pxr import Gf, Usd, UsdGeom, UsdShade

from formats import ARCHIVE, BLENDER, CAD, USD

SERVER_DIR = Path(__file__).resolve().parent
CONVERT_TIMEOUT = 15 * 60  # seconds


class ConversionError(Exception):
    pass


def find_blender() -> str:
    if os.environ.get("BLENDER"):
        return os.environ["BLENDER"]
    for name in ("blender.exe", "blender"):
        for candidate in sorted((SERVER_DIR / "tools").glob(f"blender-*/{name}")):
            return str(candidate)
    raise ConversionError("Blender is not installed on the server (run the setup script).")


def run(command: list[str], description: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=CONVERT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ConversionError(f"{description} took longer than {CONVERT_TIMEOUT // 60} minutes.")
    if result.returncode != 0:
        lines = [line for line in (result.stderr + result.stdout).splitlines() if line.strip()]
        raise ConversionError(f"{description} could not read this file. {lines[-1] if lines else ''}".strip())


def unpack_zip(archive: Path, folder: Path) -> Path:
    """Extract a .zip and return the model inside it (USD preferred)."""
    target = folder / "unzipped"
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            if not (target / member).resolve().is_relative_to(target.resolve()):
                raise ConversionError("The .zip contains unsafe file paths.")
        zf.extractall(target)
    models = [p for p in target.rglob("*") if p.suffix.lower() in USD | BLENDER | CAD]
    if not models:
        raise ConversionError("The .zip does not contain a supported 3D file.")
    models.sort(key=lambda p: (p.suffix.lower() not in USD, len(p.parts)))
    return models[0]


def convert(source: Path, folder: Path) -> tuple[Path, str]:
    """Return (USD file, converter name)."""
    extension = source.suffix.lower()
    if extension in ARCHIVE:
        source = unpack_zip(source, folder)
        extension = source.suffix.lower()
    if extension in USD:
        return source, "none (already USD)"

    output = folder / "converted.usdc"
    if extension in BLENDER:
        command = [find_blender(), "-b", "--factory-startup", "--disable-autoexec"]
        if extension == ".blend":
            command.append(str(source))
        command += ["--python", str(SERVER_DIR / "blender_export.py"), "--", str(source), str(output)]
        run(command, "Blender")
        name = "Blender"
    elif extension in CAD:
        run([sys.executable, "-m", "usd_convert_cad", "-i", str(source), "-o", str(output),
             "--material-type", "preview-surface"], "The CAD converter")
        name = "NVIDIA CAD Converter"
    else:
        raise ConversionError(f"{extension} files are not supported.")

    if not output.exists():
        raise ConversionError(f"{name} did not produce a USD file.")
    return output, name


def place(content: Path, folder: Path) -> tuple[Path, list[float]]:
    """Write placed.usda (the model upright and in metres) and return it with the model's bounds."""
    stage = Usd.Stage.Open(str(content))
    if stage is None:
        raise ConversionError("The USD file could not be opened.")

    default = stage.GetDefaultPrim()
    roots = [default] if default else [p for p in stage.GetPseudoRoot().GetChildren() if p.IsDefined() and not p.IsAbstract()]
    bound = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(stage.GetPseudoRoot())
    box = bound.ComputeAlignedRange()
    if not roots or box.IsEmpty():
        raise ConversionError("No visible 3D geometry was found in this file.")

    # Scale to metres, then turn Y-up content so it stands on the XY grid (row-vector convention).
    matrix = Gf.Matrix4d().SetScale(UsdGeom.GetStageMetersPerUnit(stage))
    if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y:
        matrix = matrix * Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), 90))
    placed = Gf.Range3d()
    for i in range(8):
        placed.UnionWith(matrix.Transform(box.GetCorner(i)))

    # Keep the model where its author put it, unless it sits far from the origin (for
    # example survey coordinates): then centre it on the grid with its base at z = 0.
    center, size = placed.GetMidpoint(), placed.GetSize()
    if max(abs(center[0]), abs(center[1]), abs(center[2])) > 2 * max(size[0], size[1], size[2]):
        shift = Gf.Vec3d(-center[0], -center[1], -placed.GetMin()[2])
        matrix = matrix * Gf.Matrix4d().SetTranslate(shift)
        placed = Gf.Range3d(placed.GetMin() + shift, placed.GetMax() + shift)
    bounds = [*placed.GetMin(), *placed.GetMax()]

    relative = os.path.relpath(content, folder).replace("\\", "/")
    children = "".join(
        f'\n    def "Part{i}" (\n        prepend references = @./{relative}@<{root.GetPath()}>\n    )\n    {{\n    }}\n'
        for i, root in enumerate(roots)
    )
    rows = ", ".join("(" + ", ".join(f"{matrix[r][c]:.12g}" for c in range(4)) + ")" for r in range(4))
    layer = folder / "placed.usda"
    layer.write_text(f"""#usda 1.0
(
    defaultPrim = "Model"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "Model"
{{
    matrix4d xformOp:transform = ({rows})
    uniform token[] xformOpOrder = ["xformOp:transform"]
{children}}}
""", encoding="utf-8")
    return layer, bounds


def bake_material_inputs(usd: Path) -> Path:
    """Copy material-level values (colour, roughness, ...) straight onto their shaders.

    The CAD converter connects each UsdPreviewSurface input to an input on its Material.
    That is valid USD, but Blender's importer doesn't follow those connections and would
    turn every material white.
    """
    stage = Usd.Stage.Open(str(usd))
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        for shader_input in UsdShade.Shader(prim).GetInputs():
            sources = shader_input.GetConnectedSources()[0]
            if sources and sources[0].source.GetPrim().IsA(UsdShade.Material):
                value = sources[0].source.GetInput(sources[0].sourceName).Get()
                if value is not None:
                    shader_input.ClearSources()
                    shader_input.Set(value)
    baked = usd.with_name("for_blender.usdc")
    stage.GetRootLayer().Export(str(baked))
    return baked


def convert_glb(source: Path, folder: Path) -> tuple[Path, str]:
    """For the web viewer: return (GLB file, converter name). Blender writes the GLB."""
    extension = source.suffix.lower()
    if extension in ARCHIVE:
        source = unpack_zip(source, folder)
        extension = source.suffix.lower()
    name = "Blender"
    if extension in CAD:
        source, name = convert(source, folder)  # CAD -> USD first, then Blender -> GLB
        source = bake_material_inputs(source)
        name += " + Blender"
    elif extension not in USD | BLENDER:
        raise ConversionError(f"{extension} files are not supported.")

    output = folder / "model.glb"
    command = [find_blender(), "-b", "--factory-startup", "--disable-autoexec"]
    if extension == ".blend":
        command.append(str(source))
    command += ["--python", str(SERVER_DIR / "blender_export.py"), "--", str(source), str(output)]
    run(command, "Blender")
    if not output.exists():
        raise ConversionError("Blender did not produce a GLB file.")
    return output, name


def main() -> None:
    glb = "--glb" in sys.argv
    source, folder = (Path(a) for a in sys.argv[1:] if a != "--glb")
    try:
        if glb:
            output, converter = convert_glb(source, folder)
            print(json.dumps({"ok": True, "glb": str(output), "converter": converter}))
            return
        content, converter = convert(source, folder)
        layer, bounds = place(content, folder)
        print(json.dumps({"ok": True, "usd": str(layer), "bounds": bounds, "converter": converter}))
    except ConversionError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
    except Exception as error:  # unexpected failure: report it rather than crash silently
        print(json.dumps({"ok": False, "error": f"Conversion failed: {error}"}))


if __name__ == "__main__":
    main()
