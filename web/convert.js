// Converts a 3D file to GLB (binary glTF 2.0, the standard 3D format for the web).
//
// Most formats are converted right here in the browser, so the file never leaves the
// visitor's computer: glTF, OBJ, FBX, Collada, STL, PLY, 3MF and USD with three.js's own
// loaders, Rhino .3dm with rhino3dm, IFC with web-ifc, and STEP/IGES with OpenCascade
// (occt-import-js). Formats no browser library can read (Revit, DWG, SolidWorks, Blender,
// ...) are sent to the TwinDrop server, which converts them and sends a GLB back.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { unzipSync } from 'three/addons/libs/fflate.module.js';
import { extensionOf } from '../formats.js';

// WebAssembly libraries, downloaded only when a file needs them.
const RHINO3DM = 'https://cdn.jsdelivr.net/npm/rhino3dm@8.32.1/';
const WEB_IFC = 'https://cdn.jsdelivr.net/npm/web-ifc@0.0.78/';
const OCCT = 'https://cdn.jsdelivr.net/npm/occt-import-js@0.0.23/dist/';

// Readers for formats the browser converts itself. Each returns { object, up, metres }:
// `up` is the file's up axis ('y' like glTF, or 'z' like most CAD) and `metres` its unit size.
const READERS = {
  '.glb': readGltf, '.gltf': readGltf,
  '.obj': readObj, '.fbx': readFbx, '.dae': readCollada,
  '.stl': readStl, '.ply': readPly, '.3mf': read3mf,
  '.usd': readUsd, '.usda': readUsd, '.usdc': readUsd, '.usdz': readUsd,
  '.3dm': readRhino, '.ifc': readIfc,
  '.stp': readStep, '.step': readStep, '.igs': readStep, '.iges': readStep,
};

const SERVER_TIPS = {
  '.rvt': 'In Revit, use File → Export → IFC and drop the .ifc file here: IFC opens right in your browser.',
  '.rfa': 'In Revit, use File → Export → IFC and drop the .ifc file here: IFC opens right in your browser.',
  '.blend': 'In Blender, use File → Export → glTF 2.0 (.glb): it opens right in your browser.',
  '.3dm': 'This Rhino file has no render meshes. In Rhino, shade a viewport and use Save (not Save Small), or export STEP.',
};
const DEFAULT_TIP = 'Export the model as STEP, IFC, glTF or OBJ: those open right in your browser.';

export const convertsInBrowser = (name) => extensionOf(name) in READERS || extensionOf(name) === '.zip';

/**
 * Convert `file` to GLB. Returns { glb: ArrayBuffer, where: 'browser' | 'server', note? }.
 * `onStatus(text)` receives progress messages.
 */
export async function toGLB(file, { servers, onStatus }) {
  const extension = extensionOf(file.name);
  let local = null;
  if (convertsInBrowser(file.name)) {
    try {
      onStatus(`Converting ${file.name} to GLB in your browser…`);
      local = await readInBrowser(file, extension);
    } catch (error) {
      console.warn('Browser conversion failed, trying the server:', error);
    }
  }
  if (local && local.missing === 0 && hasGeometry(local.root)) {
    return { glb: await exportGlb(local.root), where: 'browser' };
  }
  try {
    return { glb: await convertOnServer(file, extension, servers, onStatus), where: 'server' };
  } catch (error) {
    // The server is offline, but the browser read part of the model: show that part.
    if (local && hasGeometry(local.root)) {
      return { glb: await exportGlb(local.root), where: 'browser', note: `${local.missing} objects without render meshes are not shown` };
    }
    throw error;
  }
}

// -----------------------------------------------------------------------------------------
// In the browser

// Returns { root, missing } (missing: objects the browser couldn't turn into geometry), or null.
async function readInBrowser(file, extension) {
  const buffer = await file.arrayBuffer();
  if (extension === '.zip') return readZip(buffer);
  const manager = createManager();
  const result = await READERS[extension](buffer, { manager, extension });
  await manager.idle();  // textures finish loading
  return { root: toGltfSpace(result), missing: result.missing ?? 0 };
}

// A .zip may hold a model plus the textures and other files it references.
async function readZip(buffer) {
  const entries = unzipSync(new Uint8Array(buffer));
  const files = {};
  for (const [path, bytes] of Object.entries(entries)) {
    if (!path.endsWith('/') && !path.startsWith('__MACOSX')) files[path.split('/').pop()] = bytes;
  }
  const preferred = ['.gltf', '.glb', '.usdz', '.usda', '.usd', '.usdc', '.obj', '.fbx', '.dae'];
  const rank = (name) => (preferred.includes(extensionOf(name)) ? preferred.indexOf(extensionOf(name)) : 99);
  const main = Object.keys(files).filter((name) => extensionOf(name) in READERS).sort((a, b) => rank(a) - rank(b))[0];
  if (!main) return null;  // nothing the browser can read: the server will try

  // Serve the other files in the zip to the loaders from memory, by file name.
  const urls = Object.fromEntries(Object.entries(files).map(([name, bytes]) => [name, URL.createObjectURL(new Blob([bytes]))]));
  const manager = createManager((url) => urls[decodeURIComponent(url.split(/[/\\]/).pop())] ?? url);
  try {
    const bytes = files[main];
    const result = await READERS[extensionOf(main)](bytes.slice().buffer, { manager, extension: extensionOf(main), files });
    await manager.idle();
    return { root: toGltfSpace(result), missing: result.missing ?? 0 };
  } finally {
    Object.values(urls).forEach(URL.revokeObjectURL);
  }
}

// A LoadingManager that can tell when every texture/resource it started has finished.
function createManager(urlModifier) {
  const manager = new THREE.LoadingManager();
  if (urlModifier) manager.setURLModifier(urlModifier);
  let pending = 0;
  const waiting = [];
  const start = manager.itemStart.bind(manager);
  const end = manager.itemEnd.bind(manager);
  manager.itemStart = (url) => { pending += 1; start(url); };
  manager.itemEnd = (url) => {
    end(url);
    pending -= 1;
    if (pending === 0) waiting.splice(0).forEach((resolve) => resolve());
  };
  manager.idle = () => new Promise((resolve) => setTimeout(() => (pending === 0 ? resolve() : waiting.push(resolve)), 0));
  return manager;
}

// Put the model in glTF's conventions (Y up, metres) and drop what glTF can't hold.
function toGltfSpace({ object, up = 'y', metres = 1 }) {
  const root = new THREE.Group();
  if (up === 'z') root.rotation.x = -Math.PI / 2;
  root.scale.setScalar(metres);
  root.add(object);
  const extras = [];
  object.traverse((child) => (child.isLight || child.isCamera || child.isSprite) && extras.push(child));
  extras.forEach((child) => child.removeFromParent());
  return root;
}

function hasGeometry(root) {
  let found = false;
  root.traverse((child) => {
    if ((child.isMesh || child.isLine || child.isPoints) && child.geometry?.attributes.position?.count > 0) found = true;
  });
  return found;
}

async function exportGlb(root) {
  // Textures whose image never loaded (missing file) would make the exporter fail.
  root.traverse((child) => {
    for (const material of [child.material].flat().filter(Boolean)) {
      for (const [key, value] of Object.entries(material)) {
        if (value?.isTexture && !value.image) material[key] = null;
      }
    }
  });
  return new GLTFExporter().parseAsync(root, { binary: true, onlyVisible: true });
}

const decode = (buffer) => new TextDecoder().decode(buffer);
const grey = () => new THREE.MeshStandardMaterial({ color: 0xb0b0b0, roughness: 0.6, side: THREE.DoubleSide });

// -- three.js loaders -----------------------------------------------------------------------

async function readGltf(buffer, { manager }) {
  const gltf = await new GLTFLoader(manager).parseAsync(buffer, '');
  return { object: gltf.scene, up: 'y' };
}

async function readObj(buffer, { manager, files }) {
  const { OBJLoader } = await import('three/addons/loaders/OBJLoader.js');
  const text = decode(buffer);
  const loader = new OBJLoader(manager);
  const library = text.match(/^mtllib\s+(.+)$/m)?.[1].trim().split(/[/\\]/).pop();
  if (library && files?.[library]) {
    const { MTLLoader } = await import('three/addons/loaders/MTLLoader.js');
    const materials = new MTLLoader(manager).parse(decode(files[library]), '');
    materials.preload();
    loader.setMaterials(materials);
  }
  return { object: loader.parse(text), up: 'y' };
}

async function readFbx(buffer, { manager }) {
  const { FBXLoader } = await import('three/addons/loaders/FBXLoader.js');
  return { object: new FBXLoader(manager).parse(buffer, ''), up: 'y' };
}

async function readCollada(buffer, { manager }) {
  const { ColladaLoader } = await import('three/addons/loaders/ColladaLoader.js');
  return { object: new ColladaLoader(manager).parse(decode(buffer), '').scene, up: 'y' };
}

async function readStl(buffer) {
  const { STLLoader } = await import('three/addons/loaders/STLLoader.js');
  const geometry = new STLLoader().parse(buffer);
  const material = grey();
  material.vertexColors = Boolean(geometry.hasColors);
  return { object: new THREE.Mesh(geometry, material), up: 'z' };
}

async function readPly(buffer) {
  const { PLYLoader } = await import('three/addons/loaders/PLYLoader.js');
  const geometry = new PLYLoader().parse(buffer);
  const hasColors = Boolean(geometry.attributes.color);
  if (!geometry.index) {
    return { object: new THREE.Points(geometry, new THREE.PointsMaterial({ size: 0.01, vertexColors: hasColors })), up: 'y' };
  }
  if (!geometry.attributes.normal) geometry.computeVertexNormals();
  const material = grey();
  material.vertexColors = hasColors;
  return { object: new THREE.Mesh(geometry, material), up: 'y' };
}

async function read3mf(buffer, { manager }) {
  const { ThreeMFLoader } = await import('three/addons/loaders/3MFLoader.js');
  return { object: new ThreeMFLoader(manager).parse(buffer), up: 'z', metres: 0.001 };  // 3MF default unit: mm
}

async function readUsd(buffer, { manager }) {
  const { USDLoader } = await import('three/addons/loaders/USDLoader.js');
  // onLoad fires once the textures inside the USD have loaded too.
  const object = await new Promise((resolve, reject) => new USDLoader(manager).parse(buffer, '', resolve, reject));
  return { object, up: 'y' };  // the loader applies upAxis and units
}

// -- Rhino (.3dm) with rhino3dm -------------------------------------------------------------

const RHINO_UNITS = { 1: 1e-6, 2: 0.001, 3: 0.01, 4: 1, 5: 1000, 8: 0.0254, 9: 0.3048, 10: 1609.344 };

async function readRhino(buffer, { manager }) {
  const { Rhino3dmLoader } = await import('three/addons/loaders/3DMLoader.js');
  const loader = new Rhino3dmLoader(manager);
  loader.setLibraryPath(RHINO3DM);
  try {
    const object = await new Promise((resolve, reject) => loader.parse(buffer, resolve, reject));
    const unit = object.userData.settings?.modelUnitSystem;
    // Solids saved without render meshes ("Save Small") can't be meshed in the browser.
    const missing = (object.userData.warnings ?? []).filter((warning) => warning.type === 'missing mesh').length;
    return { object, up: 'z', metres: RHINO_UNITS[unit?.value ?? unit] ?? 1, missing };
  } finally {
    loader.dispose();
  }
}

// -- IFC with web-ifc -------------------------------------------------------------------------

async function readIfc(buffer) {
  const { IfcAPI } = await import(`${WEB_IFC}web-ifc-api.js`);
  const ifc = new IfcAPI();
  ifc.SetWasmPath(WEB_IFC, true);
  await ifc.Init(undefined, true);  // single-threaded: works on any web host
  const model = ifc.OpenModel(new Uint8Array(buffer));

  // Collect every element's geometry in world space, grouped by colour, then merge each
  // colour into one mesh: far fewer draw calls for big buildings.
  const byColor = new Map();
  ifc.StreamAllMeshes(model, (mesh) => {
    for (let i = 0; i < mesh.geometries.size(); i += 1) {
      const placed = mesh.geometries.get(i);
      const data = ifc.GetGeometry(model, placed.geometryExpressID);
      const vertices = ifc.GetVertexArray(data.GetVertexData(), data.GetVertexDataSize());
      const indices = ifc.GetIndexArray(data.GetIndexData(), data.GetIndexDataSize());
      const positions = new Float32Array(vertices.length / 2);
      const normals = new Float32Array(vertices.length / 2);
      for (let v = 0; v < vertices.length / 6; v += 1) {
        positions.set(vertices.subarray(v * 6, v * 6 + 3), v * 3);
        normals.set(vertices.subarray(v * 6 + 3, v * 6 + 6), v * 3);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
      geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indices), 1));
      geometry.applyMatrix4(new THREE.Matrix4().fromArray(placed.flatTransformation));
      data.delete();

      const { x, y, z, w } = placed.color;
      const key = [x, y, z, w].map((c) => c.toFixed(3)).join();
      if (!byColor.has(key)) byColor.set(key, { color: placed.color, geometries: [] });
      byColor.get(key).geometries.push(geometry);
    }
  });
  ifc.CloseModel(model);

  const group = new THREE.Group();
  for (const { color, geometries } of byColor.values()) {
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color().setRGB(color.x, color.y, color.z, THREE.SRGBColorSpace),
      opacity: color.w,
      transparent: color.w < 1,
      roughness: 0.7,
      side: THREE.DoubleSide,
    });
    group.add(new THREE.Mesh(mergeGeometries(geometries), material));
    geometries.forEach((geometry) => geometry.dispose());
  }
  return { object: group, up: 'y' };  // web-ifc returns Y-up geometry in metres
}

// -- STEP / IGES with OpenCascade (occt-import-js) -----------------------------------------

let occtReady = null;

function loadOcct() {
  occtReady ??= new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `${OCCT}occt-import-js.js`;
    script.onload = resolve;
    script.onerror = () => reject(new Error('Could not load the STEP/IGES reader.'));
    document.head.appendChild(script);
  }).then(() => window.occtimportjs({ locateFile: (name) => OCCT + name }));
  return occtReady;
}

async function readStep(buffer, { extension }) {
  const occt = await loadOcct();
  const options = { linearUnit: 'meter', linearDeflectionType: 'bounding_box_ratio', linearDeflection: 0.001, angularDeflection: 0.5 };
  const bytes = new Uint8Array(buffer);
  const result = extension.startsWith('.st') ? occt.ReadStepFile(bytes, options) : occt.ReadIgesFile(bytes, options);
  if (!result.success) throw new Error('OpenCascade could not read this file.');

  const group = new THREE.Group();
  const materials = new Map();
  const materialFor = (rgb) => {
    const key = rgb ? rgb.join() : 'default';
    if (!materials.has(key)) {
      const material = grey();
      if (rgb) material.color.setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace);
      materials.set(key, material);
    }
    return materials.get(key);
  };
  for (const mesh of result.meshes) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.attributes.position.array, 3));
    if (mesh.attributes.normal) geometry.setAttribute('normal', new THREE.Float32BufferAttribute(mesh.attributes.normal.array, 3));
    geometry.setIndex(mesh.index.array);
    // Faces can carry their own colours; otherwise the part colour (or grey) is used.
    const faceColors = mesh.brep_faces?.filter((face) => face.color) ?? [];
    let material = materialFor(mesh.color);
    if (faceColors.length) {
      material = [];
      for (const face of mesh.brep_faces) {
        geometry.addGroup(face.first * 3, (face.last - face.first + 1) * 3, material.length);
        material.push(materialFor(face.color ?? mesh.color));
      }
    }
    group.add(new THREE.Mesh(geometry, material));
  }
  return { object: group, up: 'z' };
}

// -----------------------------------------------------------------------------------------
// On the TwinDrop server (formats no browser library can read)

async function convertOnServer(file, extension, servers, onStatus) {
  for (const base of servers) {
    let job;
    try {
      job = await uploadForConversion(base, file, onStatus);
    } catch (error) {
      if (error.unreachable) continue;  // try the next server
      throw error;
    }
    for (;;) {
      onStatus(`Converting ${file.name} to GLB on the TwinDrop server…`);
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const status = await (await fetch(`${base}/api/glb?job=${job}`, { cache: 'no-store' })).json();
      if (status.state === 'error') throw new Error(status.message || 'The server could not convert this file.');
      if (status.state === 'expired') throw new Error('The conversion was interrupted. Please try again.');
      if (status.state === 'ready') break;
    }
    onStatus('Downloading the converted model…');
    return (await fetch(`${base}/api/glb/file?job=${job}`, { cache: 'no-store' })).arrayBuffer();
  }
  throw new Error(`${file.name} needs the TwinDrop conversion server, which is offline right now. ${SERVER_TIPS[extension] ?? DEFAULT_TIP}`);
}

// Upload with XMLHttpRequest because fetch() cannot report upload progress.
function uploadForConversion(base, file, onStatus) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${base}/api/glb?name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onStatus(`Uploading ${file.name} to the TwinDrop server… ${Math.round((event.loaded / event.total) * 100)}%`);
      }
    };
    xhr.onload = () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // Not JSON (for example a proxy error page).
      }
      if (xhr.status < 300 && body.job) resolve(body.job);
      else if (xhr.status === 404 || xhr.status >= 520) reject(Object.assign(new Error('offline'), { unreachable: true }));
      else reject(new Error(body.error || `Upload failed (error ${xhr.status}).`));
    };
    xhr.onerror = () => reject(Object.assign(new Error('offline'), { unreachable: true }));
    xhr.send(file);
  });
}
