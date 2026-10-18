"""Builds the viewer's root USD layer.

The root layer adds everything the viewer owns: the camera and its RenderProduct, a grid
on the XY plane, X/Y/Z axes, and a white sky light so scenes are visible without their
own lights. A visitor's uploaded model is added later as a reference at MODEL_PATH.
An optional base scene (--stage) is sublayered and never modified.
"""

CAMERA_PATH = "/Viewer/Camera"
PRODUCT_PATH = "/Render/Viewport"
BGRA_VAR = PRODUCT_PATH + "/BgraColor"
HELPERS_PATH = "/Viewer/Helpers"   # grid + axes; scaled to suit the model's size
MODEL_PATH = "/Upload"

GRID_HALF_SIZE = 10      # grid spans -10..10 (before scaling)
GRID_LINE_WIDTH = 0.015
GRID_HEIGHT = 0.002      # lift slightly above z=0 so it does not fight a ground plane
AXIS_LENGTH = 5.0
AXIS_THICKNESS = 0.015


def _floats(values) -> str:
    return ", ".join(f"({x:g}, {y:g}, {z:g})" for x, y, z in values)


def _grid_mesh() -> str:
    """Thin flat strips on the XY plane, one per grid line in each direction."""
    points, counts, indices = [], [], []
    w = GRID_LINE_WIDTH / 2
    s = GRID_HALF_SIZE
    z = GRID_HEIGHT
    for i in range(-s, s + 1):
        strips = (
            [(-s, i - w, z), (s, i - w, z), (s, i + w, z), (-s, i + w, z)],  # line along X
            [(i - w, -s, z), (i + w, -s, z), (i + w, s, z), (i - w, s, z)],  # line along Y
        )
        for quad in strips:
            start = len(points)
            points.extend(quad)
            counts.append(4)
            indices.extend(range(start, start + 4))
    return f"""
        def Mesh "Grid"
        {{
            uniform bool doubleSided = 1
            uniform token subdivisionScheme = "none"
            int[] faceVertexCounts = [{", ".join(map(str, counts))}]
            int[] faceVertexIndices = [{", ".join(map(str, indices))}]
            point3f[] points = [{_floats(points)}]
            color3f[] primvars:displayColor = [(0.3, 0.3, 0.3)]
            bool primvars:doNotCastShadows = 1
        }}"""


def _box_mesh(name: str, lo, hi, color) -> str:
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    points = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    faces = [0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 1, 2, 6, 5, 2, 3, 7, 6, 3, 0, 4, 7]
    r, g, b = color
    return f"""
        def Mesh "{name}"
        {{
            uniform token subdivisionScheme = "none"
            int[] faceVertexCounts = [4, 4, 4, 4, 4, 4]
            int[] faceVertexIndices = [{", ".join(map(str, faces))}]
            point3f[] points = [{_floats(points)}]
            color3f[] primvars:displayColor = [({r}, {g}, {b})]
            bool primvars:doNotCastShadows = 1
        }}"""


def _axes() -> str:
    t = AXIS_THICKNESS / 2
    n = AXIS_LENGTH
    return (
        _box_mesh("AxisX", (0, -t, -t), (n, t, t), (0.85, 0.1, 0.1))
        + _box_mesh("AxisY", (-t, 0, -t), (t, n, t), (0.1, 0.65, 0.15))
        + _box_mesh("AxisZ", (-t, -t, 0), (t, t, n), (0.1, 0.25, 0.9))
    )


def build_root_layer(scene: str | None, width: int, height: int, sky: bool = True) -> str:
    sublayers = f"\n    subLayers = [@{scene.replace(chr(92), '/')}@]" if scene else ""
    sky_light = """
    def DomeLight "Sky"
    {
        color3f inputs:color = (1, 1, 1)
        float inputs:intensity = 1200
    }""" if sky else ""
    horizontal_aperture = 20.955
    # BgraColor is LdrColor with red/blue swapped on the GPU by the SPG Swizzle node,
    # which is the BGRA8 layout ovstream expects.
    return f"""#usda 1.0
(
    upAxis = "Z"
    metersPerUnit = 1{sublayers}
)

def Scope "Viewer"
{{{sky_light}

    def Xform "Helpers"
    {{
        matrix4d xformOp:transform = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
        uniform token[] xformOpOrder = ["xformOp:transform"]
{_grid_mesh()}{_axes()}
    }}

    def Camera "Camera"
    {{
        float focalLength = 18.15
        float horizontalAperture = {horizontal_aperture}
        float verticalAperture = {horizontal_aperture * height / width:.4f}
        float2 clippingRange = (0.001, 100000)
        matrix4d xformOp:transform = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
        uniform token[] xformOpOrder = ["xformOp:transform"]
    }}
}}

def Scope "Render"
{{
    def RenderProduct "Viewport" (
        prepend apiSchemas = ["OmniRtxPostTonemapAPI_1"]
    )
    {{
        # No filmic tone curve: a white sky stays pure white and colours stay true.
        token omni:rtx:post:tonemap:operator = "none"
        uniform int2 resolution = ({width}, {height})
        uint[] deviceIds = [0]
        rel camera = <{CAMERA_PATH}>
        rel orderedVars = [<LdrColor>, <BgraColor>]

        def RenderVar "LdrColor"
        {{
            uniform string sourceName = "LdrColor"
            opaque omni:rtx:aov
        }}

        def RenderVar "BgraColor"
        {{
            uniform string sourceName = "BgraColor"
            opaque omni:rtx:aov.connect = <../Swizzle.outputs:Output>
        }}

        def Shader "Swizzle"
        {{
            uniform token info:implementationSource = "id"
            uniform token info:id = "spg:rtx.spg.stdlib/Swizzle"
            token inputs:swizzle = "zyxw"
            opaque inputs:Input.connect = <../LdrColor.omni:rtx:aov>
            opaque outputs:Output
        }}
    }}
}}
"""
