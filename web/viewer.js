// The web viewer: shows a GLB model with three.js, rendered by the visitor's own computer.
// Same look and controls as the Omniverse viewer: white background, grid on the XY plane,
// X/Y/Z axes, Z up, WASD/QE to move, drag to look, mouse wheel to move, Shift to go faster.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

const LOOK_GAIN = 0.004;    // radians per pixel of drag
const FAST_MULTIPLIER = 3;
const WHEEL_SECONDS = 0.2;  // one wheel notch moves as far as 0.2 s of walking

export class WebViewer {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xffffff);
    // Soft studio lighting from an environment map, so models are visible without lights.
    this.scene.environment = new THREE.PMREMGenerator(this.renderer).fromScene(new RoomEnvironment(), 0.04).texture;

    // Grid on the XY plane and X (red) / Y (green) / Z (blue) axes, scaled to suit the model.
    this.helpers = new THREE.Group();
    const grid = new THREE.GridHelper(20, 20, 0xbbbbbb, 0xdddddd);
    grid.rotation.x = Math.PI / 2;
    const axes = new THREE.AxesHelper(5);
    axes.material.depthTest = false;
    axes.renderOrder = 1;
    this.helpers.add(grid, axes);
    this.scene.add(this.helpers);

    this.camera = new THREE.PerspectiveCamera(60, 1, 0.01, 10000);
    this.model = null;
    this.keys = new Set();
    this.dragging = false;
    this.setHome(new THREE.Vector3(10, -10, 8), new THREE.Vector3(0, 0, 0), 5);

    this.listen();
    new ResizeObserver(() => this.resize()).observe(container);
  }

  // -- model ----------------------------------------------------------------------------

  async show(glb) {
    this.clear();
    const gltf = await new GLTFLoader().parseAsync(glb, '');
    const model = new THREE.Group();
    model.rotation.x = Math.PI / 2;  // glTF is Y-up; this viewer is Z-up like the CAD tools
    model.add(gltf.scene);
    model.updateMatrixWorld(true);

    // Keep the model where its author put it, unless it sits far from the origin
    // (for example survey coordinates): then centre it on the grid, base at z = 0.
    const box = new THREE.Box3().setFromObject(model);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    if (Math.max(Math.abs(center.x), Math.abs(center.y), Math.abs(center.z)) > 2 * Math.max(size.x, size.y, size.z)) {
      model.position.set(-center.x, -center.y, -box.min.z);
      model.updateMatrixWorld(true);
      box.setFromObject(model);
    }

    this.scene.add(model);
    this.model = model;
    this.frame(box);
  }

  clear() {
    if (!this.model) return;
    this.scene.remove(this.model);
    this.model.traverse((object) => {
      object.geometry?.dispose();
      for (const material of [object.material].flat().filter(Boolean)) {
        for (const value of Object.values(material)) value?.isTexture && value.dispose();
        material.dispose();
      }
    });
    this.model = null;
    this.helpers.scale.setScalar(1);
    this.setHome(new THREE.Vector3(10, -10, 8), new THREE.Vector3(0, 0, 0), 5);
  }

  // Look at a bounding box from the front-right, above, and scale grid, speed and clipping to it.
  frame(box) {
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 0.01);
    const direction = new THREE.Vector3(1, -1, 0.7).normalize();
    this.setHome(center.clone().addScaledVector(direction, radius * 2.4), center, Math.max(radius * 0.5, 0.05));

    const footprint = Math.max(size.x, size.y, 1e-3);
    this.helpers.scale.setScalar(10 ** Math.ceil(Math.log10(footprint / 20)));
    this.camera.near = Math.max(radius * 0.001, 0.0005);
    this.camera.far = radius * 200 + 100;
    this.camera.updateProjectionMatrix();
  }

  // -- camera ---------------------------------------------------------------------------

  setHome(eye, target, speed) {
    this.camera.position.copy(eye);
    const direction = target.clone().sub(eye).normalize();
    this.yaw = Math.atan2(direction.y, direction.x);
    this.pitch = Math.asin(direction.z);
    this.speed = speed;
  }

  update(dt) {
    const forward = new THREE.Vector3(Math.cos(this.yaw), Math.sin(this.yaw), 0);
    const right = new THREE.Vector3(Math.sin(this.yaw), -Math.cos(this.yaw), 0);
    const step = this.speed * dt * (this.keys.has('ShiftLeft') || this.keys.has('ShiftRight') ? FAST_MULTIPLIER : 1);
    const position = this.camera.position;
    if (this.keys.has('KeyW')) position.addScaledVector(forward, step);
    if (this.keys.has('KeyS')) position.addScaledVector(forward, -step);
    if (this.keys.has('KeyD')) position.addScaledVector(right, step);
    if (this.keys.has('KeyA')) position.addScaledVector(right, -step);
    if (this.keys.has('KeyE')) position.z += step;
    if (this.keys.has('KeyQ')) position.z -= step;

    const look = new THREE.Vector3(
      Math.cos(this.pitch) * Math.cos(this.yaw),
      Math.cos(this.pitch) * Math.sin(this.yaw),
      Math.sin(this.pitch),
    );
    this.camera.lookAt(position.clone().add(look));
  }

  listen() {
    const canvas = this.renderer.domElement;
    canvas.addEventListener('pointerdown', (event) => {
      this.dragging = true;
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointerup', () => { this.dragging = false; });
    canvas.addEventListener('pointermove', (event) => {
      if (!this.dragging) return;
      this.yaw -= event.movementX * LOOK_GAIN;
      this.pitch = Math.max(-1.55, Math.min(1.55, this.pitch - event.movementY * LOOK_GAIN));
    });
    canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      const look = new THREE.Vector3();
      this.camera.getWorldDirection(look);
      this.camera.position.addScaledVector(look, -Math.sign(event.deltaY) * this.speed * WHEEL_SECONDS);
    }, { passive: false });

    window.addEventListener('keydown', (event) => this.running && this.keys.add(event.code));
    window.addEventListener('keyup', (event) => this.keys.delete(event.code));
    window.addEventListener('blur', () => this.keys.clear());
  }

  // -- render loop ----------------------------------------------------------------------

  start() {
    this.running = true;
    this.resize();
    const timer = new THREE.Timer();
    this.renderer.setAnimationLoop((time) => {
      timer.update(time);
      this.update(Math.min(timer.getDelta(), 0.1));
      this.renderer.render(this.scene, this.camera);
    });
  }

  stop() {
    this.running = false;
    this.keys.clear();
    this.renderer.setAnimationLoop(null);
  }

  resize() {
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (!width || !height) return;
    this.renderer.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }
}
