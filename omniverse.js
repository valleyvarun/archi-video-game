// The Omniverse RTX viewer: claim a viewer slot on the GPU server, upload the visitor's file
// (the server converts it to USD and loads it), then play the WebRTC stream. The 3D scene is
// rendered on the server; this page only shows video and forwards keyboard and mouse input.

import { FORMATS, extensionOf } from './formats.js';

const video = document.getElementById('remote-video');
const HIDDEN_DISCONNECT_MS = 60000; // free the slot if the tab stays hidden this long

let streamer = null;
let hiddenTimer = null;
let onEnded = () => {};

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

/**
 * Start a viewer. `onStatus(text)` gets progress, `onLive(badge)` fires when video plays,
 * `onEnd(message)` fires if the stream stops on its own. Throws with a message for the visitor.
 */
export async function launchOmniverse({ file, servers, onStatus, onLive, onEnd }) {
  onEnded = onEnd;
  onStatus('Finding a free viewport…');
  const claim = await claimSlot(servers);
  if (claim.error === 'full') throw new Error('All Omniverse viewports are in use right now. Please try again in a minute, or use the web viewer.');
  if (claim.error === 'warming_up') throw new Error('The GPU server is starting up. Please try again in a minute.');
  if (claim.error) throw new Error('The Omniverse server is offline right now. The web viewer works without it.');

  try {
    await loadClient(claim.base);
    if (file) {
      onStatus(`Uploading ${file.name}…`);
      await uploadFile(claim, file, onStatus);
      await waitForModel(claim, file, onStatus);
    }
    onStatus('Connecting to the GPU…');
    await connect(claim, onLive);
  } catch (error) {
    releaseSlot(claim);
    stopOmniverse();
    throw error;
  }
}

/** Disconnect (the visitor left the viewer). */
export function stopOmniverse() {
  clearTimeout(hiddenTimer);
  if (!streamer) return;
  const current = streamer;
  streamer = null;
  current.terminate().catch(() => {});
}

// The stream stopped by itself (idle timeout, network, server restart).
function end(message) {
  if (!streamer) return;
  stopOmniverse();
  onEnded(message);
}

// Ask each server for a free slot, in random order to spread viewers across GPUs.
async function claimSlot(servers) {
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
function uploadFile(claim, file, onStatus) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${claim.base}/api/upload?${slotQuery(claim)}&name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onStatus(`Uploading ${file.name}… ${Math.round((event.loaded / event.total) * 100)}%`);
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
async function waitForModel(claim, file, onStatus) {
  const kind = FORMATS[extensionOf(file.name)];
  for (;;) {
    const response = await fetch(`${claim.base}/api/job?${slotQuery(claim)}`, { cache: 'no-store' });
    const job = await response.json();
    if (job.state === 'ready') return;
    if (job.state === 'error') throw new Error(job.message || 'The model could not be prepared.');
    if (job.state === 'expired') throw new Error('The viewport reservation expired. Please try again.');
    onStatus(job.state === 'loading'
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

function connect(claim, onLive) {
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
          onLive(`Omniverse RTX · viewer ${claim.slot + 1}`);
          video.focus();
        } else if (message.status === 'error') {
          console.error('Stream start error:', message.info);
          end('Could not start the stream. Please try again.');
        }
      },
      onStop: () => end('The viewport was disconnected.'),
    },
  });
}

// The client library only forwards keyboard and mouse input while the video has focus.
video.addEventListener('mouseenter', () => {
  if (streamer) video.focus();
});

document.addEventListener('visibilitychange', () => {
  clearTimeout(hiddenTimer);
  if (document.hidden && streamer) {
    hiddenTimer = setTimeout(() => end('Disconnected while the tab was in the background.'), HIDDEN_DISCONNECT_MS);
  }
});

window.addEventListener('beforeunload', () => streamer?.terminate());
