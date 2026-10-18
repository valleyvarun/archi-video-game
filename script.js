// Connects the viewport to an Omniverse GPU server: claim a free viewer slot, upload the
// visitor's 3D file (the server converts it to USD and loads it), then play the WebRTC
// stream. The 3D scene is rendered on the server; this page only shows video and
// forwards keyboard and mouse input.

const params = new URLSearchParams(window.location.search);
const servers = params.has('server') ? params.get('server').split(',') : window.TWINDROP_CONFIG.servers;

const video = document.getElementById('remote-video');
const overlay = document.getElementById('overlay');
const statusText = document.getElementById('status');
const startButton = document.getElementById('start');
const badge = document.getElementById('badge');
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const dropzoneTitle = document.getElementById('dropzone-title');
const dropzoneDetail = document.getElementById('dropzone-detail');
const removeButton = document.getElementById('file-remove');

const HIDDEN_DISCONNECT_MS = 60000; // free the slot if the tab stays hidden this long
const MAX_UPLOAD_MB = 100;
const DEFAULT_DETAIL = dropzoneDetail.textContent;

// Keep in sync with server/formats.py. Values are the names shown while converting.
const FORMATS = {
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
fileInput.accept = Object.keys(FORMATS).join(',');

let selectedFile = null;
let streamer = null;
let hiddenTimer = null;

// The client library uses crypto.randomUUID, which browsers only expose on https:// and localhost.
if (window.crypto && !window.crypto.randomUUID) {
  window.crypto.randomUUID = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const extensionOf = (name) => (name.match(/\.[^.]+$/) || [''])[0].toLowerCase();

// ---------------------------------------------------------------------------------------
// Choosing a file

function selectFile(file) {
  if (!file) return;
  if (!FORMATS[extensionOf(file.name)]) {
    dropzoneDetail.textContent = `${extensionOf(file.name) || 'That'} files aren't supported. ${DEFAULT_DETAIL}`;
    return;
  }
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    dropzoneDetail.textContent = `That file is larger than ${MAX_UPLOAD_MB} MB. Try a smaller or zipped file.`;
    return;
  }
  selectedFile = file;
  dropzone.classList.add('has-file');
  dropzoneTitle.textContent = file.name;
  dropzoneDetail.textContent = `${FORMATS[extensionOf(file.name)]} file · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
  removeButton.hidden = false;
}

function clearFile() {
  selectedFile = null;
  fileInput.value = '';
  dropzone.classList.remove('has-file');
  dropzoneTitle.textContent = 'Drop a 3D file here, or click to choose one';
  dropzoneDetail.textContent = DEFAULT_DETAIL;
  removeButton.hidden = true;
}

fileInput.addEventListener('change', () => selectFile(fileInput.files[0]));
removeButton.addEventListener('click', (event) => {
  event.preventDefault();
  clearFile();
});
dropzone.addEventListener('dragover', (event) => {
  event.preventDefault();
  dropzone.classList.add('dragover');
});
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', (event) => {
  event.preventDefault();
  dropzone.classList.remove('dragover');
  selectFile(event.dataTransfer.files[0]);
});

// ---------------------------------------------------------------------------------------
// Overlay states

function showOverlay(message, buttonLabel) {
  overlay.hidden = false;
  overlay.classList.remove('busy');
  badge.hidden = true;
  statusText.textContent = message;
  startButton.hidden = !buttonLabel;
  startButton.disabled = false;
  if (buttonLabel) startButton.textContent = buttonLabel;
}

function showProgress(message) {
  overlay.classList.add('busy');
  startButton.disabled = true;
  statusText.textContent = message;
}

// ---------------------------------------------------------------------------------------
// Talking to the GPU server

// Ask each server for a free slot, in random order to spread viewers across GPUs.
async function claimSlot() {
  let reason = 'offline';
  for (const base of [...servers].sort(() => Math.random() - 0.5)) {
    try {
      const response = await fetch(`${base}/api/claim`, { cache: 'no-store' });
      const body = await response.json();
      if (response.ok) return { base, ...body };
      if (body.error === 'full' || reason === 'offline') reason = body.error;
    } catch {
      // Server offline or unreachable; try the next one.
    }
  }
  return { error: reason };
}

function slotQuery(claim) {
  return `slot=${claim.slot}&token=${encodeURIComponent(claim.token)}`;
}

function releaseSlot(claim) {
  fetch(`${claim.base}/api/release?${slotQuery(claim)}`).catch(() => {});
}

// Upload with XMLHttpRequest because fetch() cannot report upload progress.
function uploadFile(claim, file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${claim.base}/api/upload?${slotQuery(claim)}&name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        showProgress(`Uploading ${file.name}… ${Math.round((event.loaded / event.total) * 100)}%`);
      }
    };
    xhr.onload = () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // Not JSON (for example a proxy error page).
      }
      if (xhr.status < 300) resolve();
      else reject(new Error(body.error || `Upload failed (error ${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error('Upload failed. Check your connection and try again.'));
    xhr.send(file);
  });
}

// Wait while the server converts the file to USD and loads it onto the GPU.
async function waitForModel(claim, file) {
  const kind = FORMATS[extensionOf(file.name)];
  for (;;) {
    const response = await fetch(`${claim.base}/api/job?${slotQuery(claim)}`, { cache: 'no-store' });
    const job = await response.json();
    if (job.state === 'ready') return;
    if (job.state === 'error') throw new Error(job.message || 'The model could not be prepared.');
    if (job.state === 'expired') throw new Error('The viewport reservation expired. Please try again.');
    showProgress(job.state === 'loading'
      ? 'Loading the model onto the GPU…'
      : kind === 'USD' ? 'Preparing your USD file…' : `Converting your ${kind} file to USD…`);
    await sleep(1000);
  }
}

// NVIDIA's client library is served by the GPU server rather than committed to this site.
function loadClient(base) {
  if (window.OVWebRTC) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `${base}/vendor/ov-web-rtc.umd.js`;
    script.onload = resolve;
    script.onerror = () => reject(new Error('Could not load the streaming client from the server.'));
    document.head.appendChild(script);
  });
}

function connect(claim) {
  const url = new URL(claim.base);
  // Over https the server's proxy routes every slot through one port by path;
  // locally each slot listens on its own signaling port.
  const secure = url.protocol === 'https:';
  const { AppStreamer, StreamType } = window.OVWebRTC;
  streamer = new AppStreamer();

  return streamer.connect({
    streamSource: StreamType.DIRECT,
    streamConfig: {
      videoElementId: 'remote-video',
      audioElementId: 'remote-audio',
      signalingServer: url.hostname,
      signalingPort: secure ? Number(url.port || 443) : claim.signal_port,
      signalingPath: secure ? `slot/${claim.slot}` : undefined,
      forceWSS: secure,
      width: claim.width,
      height: claim.height,
      fps: 60,
      // No silent reconnects: they can re-grab a slot after the server dropped this viewer.
      maxReconnects: 0,
      onStart: (message) => {
        if (message.action !== 'start') return;
        if (message.status === 'success') {
          overlay.hidden = true;
          badge.textContent = `Viewer ${claim.slot + 1} · live`;
          badge.hidden = false;
          video.focus();
        } else if (message.status === 'error') {
          console.error('Stream start error:', message.info);
          disconnect('Could not start the stream. Please try again.');
        }
      },
      onStop: () => disconnect('The viewport was disconnected.'),
    },
  });
}

function disconnect(message) {
  if (streamer) {
    const current = streamer;
    streamer = null;
    current.terminate().catch(() => {});
  }
  showOverlay(message, 'Launch viewport');
}

async function start() {
  showProgress('Finding a free viewport…');

  const claim = await claimSlot();
  if (claim.error === 'full') return showOverlay('All viewports are in use right now. Please try again in a minute.', 'Try again');
  if (claim.error === 'warming_up') return showOverlay('The GPU server is starting up. Please try again in a minute.', 'Try again');
  if (claim.error) return showOverlay('The Omniverse server is offline right now.', 'Try again');

  try {
    await loadClient(claim.base);
    if (selectedFile) {
      showProgress(`Uploading ${selectedFile.name}…`);
      await uploadFile(claim, selectedFile);
      await waitForModel(claim, selectedFile);
    }
    showProgress('Connecting to the GPU…');
    await connect(claim);
  } catch (error) {
    console.error(error);
    releaseSlot(claim);
    disconnect(error.message || 'Could not connect to the viewport. Please try again.');
  }
}

startButton.addEventListener('click', start);

// The client library only forwards keyboard and mouse input while the video has focus.
video.addEventListener('mouseenter', () => {
  if (streamer) video.focus();
});

document.addEventListener('visibilitychange', () => {
  clearTimeout(hiddenTimer);
  if (document.hidden && streamer) {
    hiddenTimer = setTimeout(() => disconnect('Disconnected while the tab was in the background.'), HIDDEN_DISCONNECT_MS);
  }
});

window.addEventListener('beforeunload', () => streamer?.terminate());
