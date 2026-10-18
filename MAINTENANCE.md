# TwinDrop — How to run and maintain the Omniverse viewport

A plain-language guide for keeping the website's Omniverse viewport working.

---

## The big picture (remember this)

There are **two separate parts**:

| Part | Where it lives | Costs money? | Always on? |
|---|---|---|---|
| **The website** (`index.html`, `config.js`, …) | GitHub Pages: https://valleyvarun.github.io/archi-video-game/ | No | Yes |
| **The Omniverse server** (renders the 3D, streams video) | Your Brev cloud GPU | **Yes, $2.25/hour while Running** | Only when you start it |

The website only works while the Brev server is **running**. When the server is off,
visitors see *"The Omniverse server is offline right now."* That is expected, not broken.

The website finds the server through **one address** stored in `config.js`, which looks like
`https://some-words.trycloudflare.com`. **This address changes every time the server
starts**, so after every restart you must put the new address into `config.js`.

---

## Routine 1: Turning the server ON (each time you want people to use it)

### 1. Start the cloud GPU
1. Go to https://brev.nvidia.com and open your instance
   `omniverse-realtime-viewer-launchable-7f59c1`.
2. Click **Start**. Wait until it says **Running** (a few minutes).
3. Billing starts now ($2.25/hour).

### 2. Open a terminal on the cloud GPU
1. Open **Ubuntu** on your laptop (Start menu → type `Ubuntu`), or type `wsl` in PowerShell.
2. Connect:
   ```
   brev shell omniverse-realtime-viewer-launchable-7f59c1
   ```
   Wait until the line starts with `ubuntu@...`.
   - If it says you're not logged in: run `brev login` first (if the browser does not open,
     use `brev login --skip-browser` and paste the link into your browser).

### 3. Start the server inside tmux
```
cd archi-video-game
tmux new -s twindrop
bash server/run.sh
```
- Wait for the box with **Public server address: https://….trycloudflare.com**.
- Wait for **`Ready: 4 viewer slot(s)`** (fast after the first time; the very first start
  took 5–10 minutes).
- Copy the address (select it with the mouse → right-click).

> If `tmux new` says *"duplicate session"*, a session already exists: run
> `tmux attach -t twindrop` instead. If the server inside it is not running, start it with
> `bash server/run.sh`.

### 4. Put the new address into the website
1. Open https://github.com/valleyvarun/archi-video-game/blob/main/config.js
2. Click the **pencil icon** (Edit).
3. Replace the old address in the `servers` line with the new one:
   ```js
   servers: ['https://NEW-ADDRESS.trycloudflare.com'],
   ```
4. Click **Commit changes…** → **Commit changes**.
5. Wait 1–2 minutes, then open https://valleyvarun.github.io/archi-video-game/ and press
   **Ctrl + Shift + R** (reloads without the old cached copy).

**Quick test before editing config.js:** open
`https://valleyvarun.github.io/archi-video-game/?server=https://NEW-ADDRESS.trycloudflare.com`
— the `?server=` part overrides `config.js` just for that visit.

### 5. Leave it running and close your laptop window
- Press **Ctrl + B**, let go, then press **D** → you "detach"; the server keeps running in the cloud.
- Type `exit` to leave the cloud machine, then close Ubuntu.

---

## Routine 2: Turning the server OFF (to stop paying)

1. Go to the Brev page for your instance and click **Stop**.
2. That's it. The website stays online but will show "server offline" until next time.

Notes:
- **Stopped** = no GPU charges (a small storage charge may still apply). Your installed
  files are kept, so you do **not** need to run `setup.sh` again.
- **Delete** = everything is erased and all charges stop. Only do this if you are completely
  done; you would have to redo the full setup (see Routine 4).
- Optional, cleaner: before clicking Stop, reconnect (`brev shell …`, `tmux attach -t twindrop`)
  and press **Ctrl + C** to stop the server. Not required.

---

## Routine 3: Checking on a running server

```
brev shell omniverse-realtime-viewer-launchable-7f59c1
tmux attach -t twindrop
```
You'll see the server's log. Useful lines:
- `[slot 0] viewer connected` / `disconnected` → someone opened/closed the viewport.
- `idle for 10 min, disconnecting` → an inactive visitor was kicked to free their spot.

Leave again with **Ctrl + B**, then **D**. **Never press Ctrl + C** unless you want to stop the server.

Quick health check from any browser: open `https://YOUR-ADDRESS.trycloudflare.com/api/status`.
`"ready": true` means the server is fine; `"busy": true/false` shows which of the 4 spots are taken.

---

## Routine 4: Setting up from scratch (new or deleted Brev instance)

Only needed if you **deleted** the instance or create a new one.

1. Deploy the launchable:
   https://brev.nvidia.com/launchable/deploy?launchableID=env-3EHjQXkUNYv2pOa3idjBeJOauvH
2. On the instance's **Access** page → **Cloud Firewall Ports**: type `47998-48001`,
   keep **Allow All IPs**, click **Expose Port**. (These are the video ports; one per viewer.)
3. Connect (`brev shell <new-instance-name>`; the exact command is on the Access page under
   "Using Brev CLI").
4. Install (10–15 minutes):
   ```
   git clone https://github.com/valleyvarun/archi-video-game.git
   cd archi-video-game
   bash server/setup.sh
   ```
5. Continue with **Routine 1, step 3**.

---

## Updating the code

When the code changes on GitHub (for example after editing files on your laptop and pushing),
the cloud machine still has the old copy. To update it:
```
brev shell omniverse-realtime-viewer-launchable-7f59c1
tmux attach -t twindrop        # then Ctrl + C to stop the server
cd ~/archi-video-game
git pull
bash server/run.sh             # new address → update config.js again
```

Website-only changes (`index.html`, `styles.css`, `script.js`, `config.js`) don't need any of
this — GitHub Pages updates by itself 1–2 minutes after you push.

**Keep your laptop copy in sync:** when you edit `config.js` on the GitHub website, your
laptop's copy is out of date. Before changing files on your laptop, run `git pull` in the
project folder (VS Code: Source Control → "…" → Pull).

---

## Showing your own USD model

Start the server with `--stage` and a USD file path or web address:
```
bash server/run.sh --stage https://omniverse-content-production.s3.us-west-2.amazonaws.com/Samples/Robot-OVRTX/robot-ovrtx.usda
```
To use your own file, copy it to the cloud machine first, from Ubuntu **on your laptop**
(not inside `brev shell`). Windows files are under `/mnt/c/...`:
```
brev copy "/mnt/c/Users/Varun SA/Documents/mymodel.usd" omniverse-realtime-viewer-launchable-7f59c1:/home/ubuntu/
```
Then on the cloud machine: `bash server/run.sh --stage /home/ubuntu/mymodel.usd`

Notes: the viewer is **Z-up** (grid on the XY plane). Large or new models take extra time
on first load while shaders compile.

---

## Useful settings

Put these **before** `bash server/run.sh`, e.g. `SLOTS=6 bash server/run.sh`.

| Setting | What it does |
|---|---|
| `SLOTS=6` | Allow 6 viewers at once (default 4). Each extra viewer needs another open video port (47998, 47999, … in Cloud Firewall Ports) and shares the GPU, so everyone's frame rate drops a bit. |
| `--stage <file or URL>` | Show a USD scene (goes after the command). |
| `--idle-minutes 20` | Kick inactive viewers after 20 minutes instead of 10. |
| `--no-sky` | Remove the white sky light (use when your USD has its own lighting). |

---

## Troubleshooting

| What you see | What to do |
|---|---|
| "The Omniverse server is offline right now." | Is Brev **Running**? Is `run.sh` running in tmux? Does `config.js` have the **current** address? Then reload with **Ctrl + Shift + R**. Also check your link doesn't end in an old `?server=…`. |
| "The GPU server is starting up." | The server is still loading (shaders). Wait for `Ready:` and click Try again. |
| "All viewports are in use right now." | All 4 spots are taken. Wait, or use more `SLOTS` (and open more ports). |
| Viewport connects but stays black | The video ports aren't open. Check **Cloud Firewall Ports** lists 47998–48001. |
| Keys (WASD) do nothing | Click inside the viewport first. |
| Works in Chrome/Edge, not another browser | Use Chrome or Edge; NVIDIA's streaming library is tested on those. |
| `brev: command not found` (in Ubuntu) | Run `source ~/.bashrc`, or reopen Ubuntu. |
| `brev shell` says not logged in | Run `brev login` (or `brev login --skip-browser`). |
| `.\server\run.ps1` "running scripts is disabled" (laptop) | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Something else | Look at the tmux window (or `server/logs/viewer.log` on the cloud machine) and search for `Error`. |

---

## Running it on your laptop instead (free, for testing)

Your laptop's RTX 3070 Ti can run the server too — only you can use it, but it costs nothing:
```powershell
.\server\run.ps1
```
Wait for `Ready:`, then double-click `index.html` and click **Launch viewport**.

Note: the laptop's `config.js` points at the cloud address after you `git pull`; for laptop
testing open `index.html?server=http://localhost:8090` instead.

---

## Cost cheat-sheet

- $2.25/hour only while Brev shows **Running** (≈ $54 if left on for a full day).
- **Always click Stop** when you're done. Check the Brev page if you're unsure.
- The website on GitHub Pages is free and always on.
