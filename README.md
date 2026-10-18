# archi-video-game

TwinDrop — a web page with a live **NVIDIA Omniverse** USD viewport.

Visitors drop a 3D file (Rhino, Revit, Blender, IFC, STEP, FBX, glTF, USD, …) on the page
and click **Launch viewport**. The GPU server converts the file to USD, renders it with
**ovrtx** (Omniverse RTX renderer) and streams it to the browser over WebRTC with
**ovstream**. The browser only plays the video and sends mouse and keyboard back, so the
website itself is plain HTML/CSS/JS on GitHub Pages.

```
Visitors' browsers ── https ──> GitHub Pages  (index.html, styles.css, script.js, config.js)
        │
        │ https / wss (control, uploads)    UDP (video)
        ▼                                     ▲
GPU server (Brev cloud GPU, or your RTX PC for testing)
   cloudflared tunnel → Caddy ─┬─ /api/*     → viewer.py: claim a slot, upload, conversion progress
                               ├─ /vendor/*  → viewer.py: NVIDIA's browser client library
                               └─ /slot/N/*  → WebRTC signaling for viewer N (port 49100 + N)
   viewer.py (manager) ─┬─ convert.py: uploaded file → USD (NVIDIA CAD Converter or Blender)
                        └─ slot.py × N: ovstage (scene) → ovrtx (RTX render) → ovstream (WebRTC)
```

**Many people at once:** one ovstream stream can only serve one browser, so the server runs
several **slots** (`SLOTS`, default 4 on the cloud), each its own process with its own scene,
camera and stream — every visitor sees only their own model. For more visitors, run more GPU
servers and list them all in `config.js`; the page picks one with a free slot.

## Supported 3D files

| Converted by | File types |
|---|---|
| Nothing needed (already USD) | `.usd` `.usda` `.usdc` `.usdz` |
| **NVIDIA CAD Converter** (`usd-convert-cad`, built on HOOPS Exchange) | Rhino `.3dm` · Revit `.rvt` `.rfa` · IFC `.ifc` · AutoCAD `.dwg` `.dxf` · MicroStation `.dgn` · STEP `.stp` `.step` · IGES `.igs` `.iges` · ACIS `.sat` · Parasolid `.x_t` `.x_b` · JT `.jt` · 3MF `.3mf` · SolidWorks `.sldprt` `.sldasm` · Inventor `.ipt` `.iam` · CATIA `.catpart` `.catproduct` `.3dxml` · NX/Creo `.prt` · Navisworks `.nwd` |
| **Blender 4.5** (headless) | Blender `.blend` · FBX `.fbx` · OBJ `.obj` · glTF `.gltf` `.glb` · STL `.stl` · PLY `.ply` · Alembic `.abc` · Collada `.dae` |
| Unzipped first | `.zip` holding one of the above plus its textures (e.g. `.gltf` + `.bin` + images, `.obj` + `.mtl`, USD + textures) |

Tested end to end: `.3dm` (incl. blocks), `.rvt`, `.ifc`, `.stp`, `.blend`, `.fbx`, `.obj`,
`.glb`, `.usdz`, and a zipped `.gltf`. Uploads are limited to 100 MB (Cloudflare's limit).
Models are turned Z-up and into metres; models far from the origin are centred on the grid.
Lights and cameras inside uploaded files are ignored in favour of the viewer's own.

## Controls

W/S forward/back · A/D left/right · Q/E down/up · drag to look · mouse wheel moves along the
view · hold Shift to move faster. Click into the viewport first so it receives the keys.

## Files

| Path | What it is |
|---|---|
| `index.html`, `styles.css`, `script.js` | The website |
| `config.js` | Which GPU server(s) the website connects to |
| `server/viewer.py` | The manager: starts the slots, HTTP API, uploads, conversion jobs |
| `server/slot.py` | One viewer slot: its own scene, RTX renderer and WebRTC stream |
| `server/convert.py` | Uploaded file → USD, placed upright and in metres (runs in `.venv-convert`) |
| `server/blender_export.py` | Runs inside Blender to export USD |
| `server/formats.py` | Which file types go to which converter |
| `server/scene.py` | The viewer's USD layer: camera, XY grid, XYZ axes, white sky light |
| `server/fly_camera.py` | WASDQE fly camera |
| `server/fetch_client.py` | Downloads NVIDIA's browser client into `server/vendor/` |
| `server/requirements.txt`, `server/requirements-convert.txt` | Renderer and converter Python packages |
| `server/setup.sh`, `server/run.sh` | Install / start on a Linux cloud GPU (Brev) |
| `server/setup.ps1`, `server/run.ps1` | Install / start on a Windows PC with an RTX GPU |
| `deploy/Caddyfile` | Routes the public https traffic on the GPU server |

NVIDIA's browser client (`@nvidia/ov-web-rtc`) is proprietary NVIDIA code, so it is **not**
committed here; each GPU server downloads it from NVIDIA's registry and serves it to the page.

---

## 1. Run it on your own PC (Windows + NVIDIA RTX GPU)

```powershell
.\server\setup.ps1      # once: renderer + converters + Blender (several GB)
.\server\run.ps1        # start; the very first start compiles shaders (a few minutes)
```

Wait for `Ready: 2 viewer slot(s)`, then double-click `index.html` and click
**Launch viewport**. `config.js` points at `http://localhost:8090` by default.
Stop the server with Ctrl+C.

Show a USD file too (path or URL; the viewer is Z-up):

```powershell
.\server\run.ps1 --stage C:\path\to\scene.usd
.\server\run.ps1 --stage https://omniverse-content-production.s3.us-west-2.amazonaws.com/Samples/Robot-OVRTX/robot-ovrtx.usda
```

Other options: `--slots`, `--width/--height` (default 1280×720), `--idle-minutes`
(default 10), `--no-sky`. Run `.\server\run.ps1 --help` for all of them.

## 2. Run it on a cloud GPU (Brev)

Uses NVIDIA's `omniverse-realtime-viewer` launchable (an NVIDIA A10G GPU, AWS `g5.xlarge`):
<https://brev.nvidia.com/launchable/deploy?launchableID=env-3EHjQXkUNYv2pOa3idjBeJOauvH>

1. **Push this repo to GitHub first** (the cloud machine downloads the server code from it).
2. **Create the instance.** Open the launchable link, sign in, click **Deploy**, and wait
   until the instance is **Running**. You are billed per hour while it runs.
3. **Open the video ports.** Video goes over UDP straight to the instance's public IP. In
   the instance's port / firewall rules, make sure **UDP 47998, 47999, 48000, 48001** are
   open (one per viewer slot; the launchable already opens 47998). Nothing else is needed:
   all other traffic goes through the https tunnel.
4. **Open a terminal on the instance** (Brev's browser terminal, or `brev shell <name>` with
   the Brev CLI) and install everything (about 10 minutes):
   ```bash
   git clone https://github.com/valleyvarun/archi-video-game.git
   cd archi-video-game
   bash server/setup.sh
   ```
5. **Start the server** inside `tmux` so it keeps running after you close the terminal:
   ```bash
   tmux new -s twindrop
   bash server/run.sh                 # or: bash server/run.sh --stage <your USD file or URL>
   ```
   It prints a box with **Public server address: https://….trycloudflare.com**. The first
   start compiles shaders for 5–10 minutes before `Ready:` appears. Leave tmux running with
   **Ctrl+B, then D** (reattach later with `tmux attach -t twindrop`).
6. **Test it:** open your site with `?server=<that address>` added, e.g.
   `index.html?server=https://abc-def.trycloudflare.com`.
7. **Point the site at it:** put the address in `config.js` and push:
   ```js
   servers: ['https://abc-def.trycloudflare.com'],
   ```
8. **Stop the instance when you are done** (Brev console → Stop). A stopped instance has no
   GPU charges (storage may still be billed); delete it to stop all charges.

The quick-tunnel address changes every time `run.sh` starts, so update `config.js` after a
restart. For a permanent address, use a named Cloudflare tunnel on your own domain, or a
public Brev link to port 80 with `TUNNEL=none bash server/run.sh`.

## 3. Publish the website (GitHub Pages)

1. Push the repo (with `config.js` pointing at your GPU server) to GitHub.
2. On GitHub: **Settings → Pages → Build and deployment → Deploy from a branch →
   `main` / `(root)` → Save**.
3. After a minute, share **https://valleyvarun.github.io/archi-video-game/**.

When the GPU server is off, visitors see "The Omniverse server is offline right now."

## How many people can use it?

- Each GPU server serves `SLOTS` people at the same time (default 4, set with
  `SLOTS=6 bash server/run.sh`). Each visitor gets their own scene, model and camera.
  More slots share the same GPU, so each viewer's frame rate drops as slots fill up.
- Visitor number 5 sees "All viewports are in use right now" and can retry. Slots free
  themselves when a visitor closes the page, leaves the tab in the background for a minute,
  or stops interacting for `--idle-minutes` (default 10).
- To serve more people, start another GPU instance the same way, open the matching UDP
  ports, and add its address to `config.js`:
  `servers: ['https://first….trycloudflare.com', 'https://second….trycloudflare.com']`.
- For auto-scaling to hundreds of users (one GPU container per visitor, started on demand),
  NVIDIA's route is a session orchestrator such as NVCF; that is a much bigger setup.

## Troubleshooting

| Symptom | Check |
|---|---|
| "The Omniverse server is offline" | Is `run.sh` running? Is `config.js` (or `?server=`) the current tunnel address? |
| "The GPU server is starting up" | First start is compiling shaders; wait for `Ready:` in the server log. |
| Viewport connects but stays black (cloud) | UDP ports 47998+ not open to the instance, or wrong public IP. |
| Keys do nothing | Click inside the viewport so the video has focus. |
| "… could not read this file" | The converter rejected it. Try exporting another format (e.g. IFC from Revit, STEP or `.3dm` from Rhino) or a `.zip` with its textures. |
| Model without textures | Separate texture files were not uploaded: zip the model together with its textures. |
| "Upload failed (error 413)" | File is over 100 MB. Zip it, or export a lighter version. |
| Server log | `server/logs/viewer.log` (and `server/logs/tunnel.log`). |
