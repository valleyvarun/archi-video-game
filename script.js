// Connects the viewport to an Omniverse GPU server: claim a free viewer slot, load
// NVIDIA's browser streaming client from that server, then play the WebRTC stream.
// The 3D scene is rendered on the server; this page only shows video and forwards input.

const params = new URLSearchParams(window.location.search);
const servers = params.has('server') ? params.get('server').split(',') : window.TWINDROP_CONFIG.servers;

const video = document.getElementById('remote-video');
const overlay = document.getElementById('overlay');
const statusText = document.getElementById('status');
const startButton = document.getElementById('start');
const badge = document.getElementById('badge');

const HIDDEN_DISCONNECT_MS = 60000; // free the slot if the tab stays hidden this long

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

function showOverlay(message, buttonLabel) {
  overlay.hidden = false;
  badge.hidden = true;
  statusText.textContent = message;
  startButton.hidden = !buttonLabel;
  startButton.disabled = false;
  if (buttonLabel) startButton.textContent = buttonLabel;
}

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

// NVIDIA's client library is served by the GPU server rather than committed to this site.
function loadClient(base) {
  if (window.OVWebRTC) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `${base}/vendor/ov-web-rtc.umd.js`;
    script.onload = resolve;
    script.onerror = reject;
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
  showOverlay(message, 'Reconnect');
}

async function start() {
  startButton.disabled = true;
  statusText.textContent = 'Finding a free viewport…';

  const claim = await claimSlot();
  if (claim.error === 'full') return showOverlay('All viewports are in use right now. Please try again in a minute.', 'Try again');
  if (claim.error === 'warming_up') return showOverlay('The GPU server is starting up. Please try again in a minute.', 'Try again');
  if (claim.error) return showOverlay('The Omniverse server is offline right now.', 'Try again');

  try {
    await loadClient(claim.base);
    statusText.textContent = 'Connecting to the GPU…';
    await connect(claim);
  } catch (error) {
    console.error(error);
    disconnect('Could not connect to the viewport. Please try again.');
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
