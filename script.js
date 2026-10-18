// The TwinDrop page: choose a viewer, drop a 3D file, launch.
//  - Web viewer (default): the file becomes GLB and three.js renders it on the visitor's computer.
//  - Omniverse RTX viewer: a cloud GPU renders the file with NVIDIA RTX and streams it as video.

import { FORMATS, extensionOf } from './formats.js';
import { launchOmniverse, stopOmniverse } from './omniverse.js';

const params = new URLSearchParams(window.location.search);
const servers = params.has('server') ? params.get('server').split(',') : window.TWINDROP_CONFIG.servers;

const $ = (id) => document.getElementById(id);
const overlay = $('overlay');
const picker = $('mode-picker');
const panel = $('mode-panel');
const modeTitle = $('mode-title');
const statusText = $('status');
const startButton = $('start');
const dropzone = $('dropzone');
const fileInput = $('file-input');
const dropzoneTitle = $('dropzone-title');
const dropzoneDetail = $('dropzone-detail');
const removeButton = $('file-remove');
const toolbar = $('toolbar');
const downloadButton = $('download');
const badge = $('badge');
const webView = $('web-view');

const MAX_UPLOAD_MB = 100;
const DEFAULT_DETAIL = dropzoneDetail.textContent;
const MODES = {
  web: {
    title: 'Web viewer',
    intro: 'Renders in your browser with three.js. Most files are converted on your own computer.',
  },
  omniverse: {
    title: 'Omniverse RTX viewer',
    intro: 'Rendered live on an NVIDIA RTX GPU in the cloud and streamed to your browser.',
  },
};

let mode = null;
let selectedFile = null;
let webViewer = null;
let glbUrl = null;
let convertsInBrowser = () => false;
fileInput.accept = Object.keys(FORMATS).join(',');

// ---------------------------------------------------------------------------------------
// Choosing a viewer

for (const option of document.querySelectorAll('[data-mode]')) {
  option.addEventListener('click', () => chooseMode(option.dataset.mode));
}
$('change-mode').addEventListener('click', () => {
  picker.hidden = false;
  panel.hidden = true;
});

async function chooseMode(chosen) {
  mode = chosen;
  picker.hidden = true;
  panel.hidden = false;
  modeTitle.textContent = MODES[mode].title;
  showOverlay(MODES[mode].intro, 'Launch viewport');
  if (mode === 'web') ({ convertsInBrowser } = await import('./web/convert.js'));
  describeFile();
}

// ---------------------------------------------------------------------------------------
// Choosing a file

function selectFile(file) {
  if (!file) return;
  if (!FORMATS[extensionOf(file.name)]) {
    dropzoneDetail.textContent = `${extensionOf(file.name) || 'That'} files aren't supported. ${DEFAULT_DETAIL}`;
    return;
  }
  // Uploads are limited (Cloudflare's tunnel); files converted in the browser are not.
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024 && !(mode === 'web' && convertsInBrowser(file.name))) {
    dropzoneDetail.textContent = `That file is larger than ${MAX_UPLOAD_MB} MB. Try a smaller or zipped file.`;
    return;
  }
  selectedFile = file;
  describeFile();
}

function describeFile() {
  dropzone.classList.toggle('has-file', Boolean(selectedFile));
  removeButton.hidden = !selectedFile;
  if (!selectedFile) {
    dropzoneTitle.textContent = 'Drop a 3D file here, or click to choose one';
    dropzoneDetail.textContent = DEFAULT_DETAIL;
    return;
  }
  const kind = FORMATS[extensionOf(selectedFile.name)];
  let where = '';
  if (mode === 'web') where = convertsInBrowser(selectedFile.name) ? ' · converted in your browser' : ' · converted on the TwinDrop server';
  dropzoneTitle.textContent = selectedFile.name;
  dropzoneDetail.textContent = `${kind} file · ${(selectedFile.size / 1024 / 1024).toFixed(1)} MB${where}`;
}

fileInput.addEventListener('change', () => selectFile(fileInput.files[0]));
removeButton.addEventListener('click', (event) => {
  event.preventDefault();
  selectedFile = null;
  fileInput.value = '';
  describeFile();
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
// Overlay and toolbar

function showOverlay(message, buttonLabel) {
  overlay.hidden = false;
  overlay.classList.remove('busy');
  toolbar.hidden = true;
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

function showLive(badgeText) {
  overlay.hidden = true;
  toolbar.hidden = false;
  badge.textContent = badgeText;
  badge.hidden = false;
}

// ---------------------------------------------------------------------------------------
// Launching and leaving

startButton.addEventListener('click', () => (mode === 'web' ? launchWeb() : launchRtx()));

async function launchWeb() {
  showProgress('Starting the web viewer…');
  try {
    const { WebViewer } = await import('./web/viewer.js');
    webViewer ??= new WebViewer(webView);
    let badgeText = 'Web viewer';
    downloadButton.hidden = true;
    if (selectedFile) {
      const { toGLB } = await import('./web/convert.js');
      const { glb, where, note } = await toGLB(selectedFile, { servers, onStatus: showProgress });
      showProgress('Opening the model…');
      await webViewer.show(glb);
      if (glbUrl) URL.revokeObjectURL(glbUrl);
      glbUrl = URL.createObjectURL(new Blob([glb], { type: 'model/gltf-binary' }));
      downloadButton.hidden = false;
      badgeText += where === 'browser' ? ' · converted in your browser' : ' · converted on the TwinDrop server';
      if (note) badgeText += ` · ${note}`;
    } else {
      webViewer.clear();
    }
    webView.hidden = false;
    webViewer.start();
    showLive(badgeText);
  } catch (error) {
    console.error(error);
    showOverlay(error.message || 'Could not open this model.', 'Launch viewport');
  }
}

async function launchRtx() {
  try {
    await launchOmniverse({
      file: selectedFile,
      servers,
      onStatus: showProgress,
      onLive: showLive,
      onEnd: (message) => showOverlay(message, 'Launch viewport'),
    });
  } catch (error) {
    console.error(error);
    showOverlay(error.message || 'Could not connect to the viewport. Please try again.', 'Launch viewport');
  }
}

$('back').addEventListener('click', () => {
  if (mode === 'web') {
    webViewer?.stop();
    webView.hidden = true;
  } else {
    stopOmniverse();
  }
  showOverlay(MODES[mode].intro, 'Launch viewport');
});

downloadButton.addEventListener('click', () => {
  const link = document.createElement('a');
  link.href = glbUrl;
  link.download = `${selectedFile.name.replace(/\.[^.]+$/, '')}.glb`;
  link.click();
});
