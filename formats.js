// File types both viewers accept (keep in sync with server/formats.py).
// Values are the names shown to visitors.
export const FORMATS = {
  '.usd': 'USD', '.usda': 'USD', '.usdc': 'USD', '.usdz': 'USD',
  '.blend': 'Blender', '.fbx': 'FBX', '.obj': 'OBJ', '.gltf': 'glTF', '.glb': 'glTF',
  '.stl': 'STL', '.ply': 'PLY', '.abc': 'Alembic', '.dae': 'Collada',
  '.3dm': 'Rhino', '.rvt': 'Revit', '.rfa': 'Revit', '.ifc': 'IFC',
  '.dwg': 'AutoCAD', '.dxf': 'AutoCAD', '.dgn': 'MicroStation',
  '.stp': 'STEP', '.step': 'STEP', '.igs': 'IGES', '.iges': 'IGES',
  '.sat': 'ACIS', '.x_t': 'Parasolid', '.x_b': 'Parasolid', '.jt': 'JT', '.3mf': '3MF',
  '.sldprt': 'SolidWorks', '.sldasm': 'SolidWorks', '.ipt': 'Inventor', '.iam': 'Inventor',
  '.catpart': 'CATIA', '.catproduct': 'CATIA', '.3dxml': 'CATIA',
  '.prt': 'CAD', '.nwd': 'Navisworks', '.zip': 'zipped',
};

export const extensionOf = (name) => (name.match(/\.[^./]+$/) || [''])[0].toLowerCase();
