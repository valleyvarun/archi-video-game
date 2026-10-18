"""TwinDrop Omniverse server.

Starts one slot process (slot.py) per simultaneous viewer, converts uploaded 3D files to
USD (convert.py), and serves a small HTTP API (default 127.0.0.1:8090) for the web page:

    GET  /api/status                         readiness and which slots are busy
    GET  /api/claim                          reserve a free slot -> {slot, token, signal_port, ...}
    POST /api/upload?slot=&token=&name=      upload a 3D file into the claimed slot (body = file)
    GET  /api/job?slot=&token=               that upload's progress: converting/loading/ready/error
    GET  /api/release?slot=&token=           give a claimed slot back (e.g. after an error)
    GET  /vendor/ov-web-rtc.umd.js           NVIDIA's browser client (see fetch_client.py)

Usage:
    python viewer.py                                   # local test, 2 slots
    python viewer.py --slots 4 --public-ip 1.2.3.4     # cloud GPU
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from formats import SUPPORTED

SERVER_DIR = Path(__file__).resolve().parent
CLIENT_LIBRARY = SERVER_DIR / "vendor" / "ov-web-rtc.umd.js"
UPLOADS = SERVER_DIR / "uploads"
CLAIM_SECONDS = 60          # a claimed slot is held this long without the browser checking in
CONVERSIONS = threading.Semaphore(2)  # conversions are CPU-heavy; run at most two at once


def converter_python() -> str:
    for relative in (".venv-convert/Scripts/python.exe", ".venv-convert/bin/python"):
        if (SERVER_DIR / relative).exists():
            return str(SERVER_DIR / relative)
    return ""


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", help="base USD scene shown to every visitor (optional)")
    parser.add_argument("--slots", type=int, default=2, help="max simultaneous viewers (default 2)")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--signal-port", type=int, default=49100, help="slot N signals on this port + N")
    parser.add_argument("--media-port", type=int, default=47998, help="slot N streams UDP media on this port + N")
    parser.add_argument("--public-ip", help="public IP browsers reach for media (set on cloud machines)")
    parser.add_argument("--http-host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8090)
    parser.add_argument("--idle-minutes", type=float, default=10, help="disconnect viewers idle this long")
    parser.add_argument("--max-upload-mb", type=int, default=100)
    parser.add_argument("--no-sky", action="store_true", help="do not add the white sky light")
    return parser.parse_args()


class Slot:
    """Manager-side view of one slot process."""

    def __init__(self, index: int, args):
        self.index = index
        self.args = args
        self.signal_port = args.signal_port + index
        self.state = "starting"      # reported by slot.py: starting/idle/loading/ready/error
        self.connected = False
        self.token = None            # set while a browser has claimed this slot
        self.claimed_until = 0.0
        self.job = None              # {"state": converting/loading/ready/error, "message": ...}
        self.job_dir = None
        self.stale_dirs = []         # upload folders to delete once the slot is idle again
        self.process = None

    @property
    def free(self) -> bool:
        return self.state == "idle" and not self.connected and self.token is None

    def start(self) -> None:
        args = self.args
        command = [sys.executable, "-u", str(SERVER_DIR / "slot.py"), "--index", str(self.index),
                   "--width", str(args.width), "--height", str(args.height), "--fps", str(args.fps),
                   "--signal-port", str(self.signal_port), "--media-port", str(args.media_port + self.index),
                   "--idle-minutes", str(args.idle_minutes)]
        if args.public_ip:
            command += ["--public-ip", args.public_ip]
        if args.stage:
            command += ["--stage", args.stage]
        if args.no_sky:
            command.append("--no-sky")
        env = dict(os.environ, OVRTX_SKIP_USD_CHECK="1", PYTHONIOENCODING="utf-8")
        self.state = "starting"
        self.process = subprocess.Popen(command, cwd=SERVER_DIR, env=env, text=True, encoding="utf-8",
                                        errors="replace", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
        threading.Thread(target=self._read_output, daemon=True).start()

    def send(self, **command) -> None:
        try:
            self.process.stdin.write(json.dumps(command) + "\n")
            self.process.stdin.flush()
        except (OSError, AttributeError):
            pass

    def _read_output(self) -> None:
        for line in self.process.stdout:
            # Native libraries sometimes leave partial output (colour codes) before an event.
            marker = line.find("@@{")
            if marker >= 0:
                self._on_event(json.loads(line[marker + 2:]))
            elif line.strip():
                print(f"[slot {self.index}] {line.rstrip()}", flush=True)
        if not stopping.is_set():
            print(f"[slot {self.index}] process exited; restarting", flush=True)
            self.state, self.connected, self.token, self.job = "starting", False, None, None
            time.sleep(3)
            self.start()

    def _on_event(self, event: dict) -> None:
        if "connected" in event:
            self.connected = event["connected"]
            print(f"[slot {self.index}] viewer {'connected' if self.connected else 'disconnected'}", flush=True)
            if self.connected:
                self.token, self.claimed_until = None, 0.0
            else:
                self.release(reset=False)  # slot.py resets its own scene on disconnect
        if "error" in event and self.job:
            self.job = {"state": "error", "message": event["error"]}
        if "state" in event:
            self.state = event["state"]
            if self.job and self.state in ("loading", "ready"):
                self.job = {"state": self.state, "message": ""}
            if self.state == "idle":
                for folder in self.stale_dirs:
                    shutil.rmtree(folder, ignore_errors=True)
                self.stale_dirs.clear()

    def release(self, reset: bool = True) -> None:
        """Forget the claim and its upload; clear the visitor's model from the scene."""
        self.token, self.claimed_until, self.job = None, 0.0, None
        if self.job_dir:
            self.stale_dirs.append(self.job_dir)
            self.job_dir = None
        if reset:
            self.send(cmd="reset")


class Manager:
    def __init__(self, args):
        self.args = args
        self.slots = [Slot(i, args) for i in range(args.slots)]
        self.lock = threading.Lock()

    def start_slots(self) -> None:
        # Start one slot first so it compiles and caches shaders; the rest then start quickly.
        first, *rest = self.slots
        first.start()
        while first.state == "starting" and not stopping.is_set():
            time.sleep(1)
        for slot in rest:
            slot.start()

    def housekeeping(self) -> None:
        while not stopping.wait(2):
            now = time.monotonic()
            with self.lock:
                for slot in self.slots:
                    if slot.token and not slot.connected and now > slot.claimed_until:
                        print(f"[slot {slot.index}] claim expired", flush=True)
                        slot.release()

    def find(self, query: dict):
        """The slot named in the query, if the token matches its current claim."""
        try:
            slot = self.slots[int(query.get("slot", ["-1"])[0])]
        except (ValueError, IndexError):
            return None
        token = query.get("token", [""])[0]
        return slot if token and token == slot.token else None

    # -- API ---------------------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "ready": any(slot.state != "starting" for slot in self.slots),
            "width": self.args.width,
            "height": self.args.height,
            "slots": [{"id": s.index, "busy": not s.free, "signal_port": s.signal_port} for s in self.slots],
        }

    def claim(self):
        with self.lock:
            for slot in self.slots:
                if slot.free:
                    slot.token = secrets.token_urlsafe(12)
                    slot.claimed_until = time.monotonic() + CLAIM_SECONDS
                    return 200, {"slot": slot.index, "token": slot.token, "signal_port": slot.signal_port,
                                 "width": self.args.width, "height": self.args.height}
        if not any(slot.state != "starting" for slot in self.slots):
            return 503, {"error": "warming_up"}
        return 503, {"error": "full"}

    def upload(self, query: dict, length: int, body) -> tuple:
        slot = self.find(query)
        if slot is None:
            return 403, {"error": "This viewport reservation has expired. Please try again."}
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(query.get("name", ["model"])[0]).name)[-100:]
        if Path(name).suffix.lower() not in SUPPORTED:
            return 400, {"error": f"{Path(name).suffix or 'This'} files are not supported."}
        if length > self.args.max_upload_mb * 1024 * 1024:
            return 413, {"error": f"Files up to {self.args.max_upload_mb} MB are supported."}
        if not converter_python():
            return 500, {"error": "File conversion is not set up on the server."}

        folder = UPLOADS / f"slot{slot.index}" / slot.token
        folder.mkdir(parents=True, exist_ok=True)
        slot.job_dir = folder
        slot.claimed_until = time.monotonic() + 3600  # hold the slot while a slow upload arrives
        path = folder / name
        with open(path, "wb") as out:
            remaining = length
            while remaining > 0:
                chunk = body.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
        if remaining > 0:
            slot.claimed_until = time.monotonic() + CLAIM_SECONDS
            return 400, {"error": "The upload was interrupted. Please try again."}
        slot.job = {"state": "converting", "message": ""}
        slot.claimed_until = time.monotonic() + CLAIM_SECONDS
        threading.Thread(target=self.convert, args=(slot, slot.token, path), daemon=True).start()
        return 202, {"ok": True}

    def convert(self, slot: Slot, token: str, path: Path) -> None:
        with CONVERSIONS:
            started = time.monotonic()
            result = subprocess.run([converter_python(), str(SERVER_DIR / "convert.py"), str(path), str(path.parent)],
                                    cwd=SERVER_DIR, capture_output=True, text=True, errors="replace")
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        outcome = json.loads(lines[-1]) if lines else {"ok": False, "error": "Conversion failed."}
        print(f"[slot {slot.index}] converted {path.name} in {time.monotonic() - started:.0f}s: "
              f"{outcome.get('converter') or outcome.get('error')}", flush=True)
        if slot.token != token:
            return  # the visitor gave up while we were converting
        if outcome["ok"]:
            slot.job = {"state": "loading", "message": ""}
            slot.send(cmd="load", usd=outcome["usd"], bounds=outcome["bounds"])
        else:
            slot.job = {"state": "error", "message": outcome["error"]}

    def job(self, query: dict):
        slot = self.find(query)
        if slot is None:
            return 404, {"state": "expired"}
        slot.claimed_until = time.monotonic() + CLAIM_SECONDS  # the browser is still waiting
        return 200, slot.job or {"state": "none"}

    def release(self, query: dict):
        slot = self.find(query)
        if slot is not None and not slot.connected:
            with self.lock:
                slot.release()
        return 200, {"ok": True}

    def serve(self) -> ThreadingHTTPServer:
        manager = self

        class Handler(BaseHTTPRequestHandler):
            def _headers(self, code: int, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

            def _json(self, code: int, body: dict) -> None:
                self._headers(code, "application/json")
                self.wfile.write(json.dumps(body).encode())

            def do_OPTIONS(self):
                self._headers(204, "text/plain")

            def do_GET(self):
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if url.path == "/api/status":
                    self._json(200, manager.status())
                elif url.path == "/api/claim":
                    self._json(*manager.claim())
                elif url.path == "/api/job":
                    self._json(*manager.job(query))
                elif url.path == "/api/release":
                    self._json(*manager.release(query))
                elif url.path == "/vendor/ov-web-rtc.umd.js" and CLIENT_LIBRARY.exists():
                    self._headers(200, "text/javascript")
                    self.wfile.write(CLIENT_LIBRARY.read_bytes())
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                url = urlparse(self.path)
                if url.path != "/api/upload":
                    return self._json(404, {"error": "not found"})
                length = int(self.headers.get("Content-Length") or 0)
                self._json(*manager.upload(parse_qs(url.query), length, self.rfile))

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer((self.args.http_host, self.args.http_port), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd


stopping = threading.Event()


def main():
    args = parse_args()
    if not CLIENT_LIBRARY.exists():
        print("Browser client missing: run `python fetch_client.py` first.", file=sys.stderr)
    if not converter_python():
        print("Converter environment missing (server/.venv-convert): uploads are disabled.", file=sys.stderr)

    manager = Manager(args)
    httpd = manager.serve()
    print(f"API on http://{args.http_host}:{args.http_port}/api/status", flush=True)
    print("Starting the renderer (the first run compiles shaders and can take several minutes)...", flush=True)
    threading.Thread(target=manager.start_slots, daemon=True).start()
    threading.Thread(target=manager.housekeeping, daemon=True).start()

    announced = False
    try:
        while True:
            time.sleep(1)
            if not announced and all(slot.state != "starting" for slot in manager.slots):
                announced = True
                print(f"Ready: {args.slots} viewer slot(s), {args.width}x{args.height}. Press Ctrl+C to stop.", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        httpd.shutdown()
        for slot in manager.slots:
            if slot.process:
                slot.process.terminate()


if __name__ == "__main__":
    main()
