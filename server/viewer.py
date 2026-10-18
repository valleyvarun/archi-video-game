"""TwinDrop Omniverse viewer server.

ovstage owns the USD scene, ovrtx renders it on the GPU, and ovstream streams it to
browsers over WebRTC. One ovstream server can only stream to one browser, so the
viewer runs several "slots": each slot has its own camera, RenderProduct and
WebRTC server, and only slots with a connected viewer are rendered.

A small HTTP API (default 127.0.0.1:8090) lets the web page find a free slot:
    GET /api/status   -> readiness and which slots are busy
    GET /api/claim    -> reserve a free slot for the caller
    GET /vendor/ov-web-rtc.umd.js -> NVIDIA's browser client (see fetch_client.py)

Usage:
    python viewer.py                          # local test, 2 slots
    python viewer.py --stage path/or/url.usd  # also show a USD scene
    python viewer.py --slots 4 --public-ip 1.2.3.4   # cloud GPU
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("OVRTX_SKIP_USD_CHECK", "1")

import numpy as np
import ovrtx
import ovstage
import ovstream
import ovstream_utils

from fly_camera import FlyCamera
from scene import build_root_layer, camera_path, product_path

CLIENT_LIBRARY = Path(__file__).with_name("vendor") / "ov-web-rtc.umd.js"
CLAIM_SECONDS = 30  # how long a claimed slot is held for the browser to connect


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", help="USD file path or URL to show (optional)")
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
    parser.add_argument("--no-sky", action="store_true", help="do not add the white sky light")
    return parser.parse_args()


class Slot:
    """One viewer: a camera, a RenderProduct and a WebRTC server."""

    def __init__(self, index: int, args):
        self.index = index
        self.camera = FlyCamera()
        self.product = product_path(index)
        self.bgra_var = self.product + "/BgraColor"
        self.signal_port = args.signal_port + index
        self.config = ovstream.ServerConfig(
            width=args.width,
            height=args.height,
            cuda_device=0,
            webrtc_signal_port=self.signal_port,
            # Each WebRTC server needs its own media port when a public IP is pinned.
            stream_port=args.media_port + index,
        )
        if args.public_ip:
            self.config.webrtc_public_ip = args.public_ip
        self.connected = False
        self.claimed_until = 0.0
        self.last_input = 0.0
        self.server = ovstream.Server(ovstream.ServerType.WEBRTC)
        self.server.on_connection = self._on_connection
        self.server.on_input = self._on_input

    def _on_connection(self, connected: bool) -> None:
        if connected:
            self.camera.reset()
            self.claimed_until = 0.0
            self.last_input = time.monotonic()
        self.connected = connected
        print(f"[slot {self.index}] viewer {'connected' if connected else 'disconnected'}", flush=True)

    def _on_input(self, event) -> None:
        self.last_input = time.monotonic()
        self.camera.handle_input(event)

    @property
    def busy(self) -> bool:
        return self.connected or time.monotonic() < self.claimed_until

    def start(self) -> None:
        self.server.start(self.config)

    def restart(self) -> None:
        """Drop the current viewer so the slot frees up."""
        self.server.stop()
        self.connected = False
        self.server.start(self.config)

    def close(self) -> None:
        try:
            self.server.stop()
        except ovstream.OvstreamError:
            pass
        self.server.close()


class CameraWriter:
    """Publishes a camera's transform to ovstage as `omni:xform`."""

    def __init__(self, stage, prim_path: str):
        self._stage = stage
        self._paths = ovstage.PathDictionary(stage)
        self._path_list = self._paths.create_path_list_from_strings([prim_path])
        self._query = stage.query_from_path_list(self._path_list)
        self._attribute = self._paths.intern_token("omni:xform")
        self._dtype = ovstage.numpy_to_dldatatype(np.dtype(np.float64), lanes=16)

    def write(self, matrix: np.ndarray, ordinal: int) -> None:
        tensor = ovstage.make_dltensor(matrix.reshape(1, 4, 4), dtype=self._dtype, shape=[1], ndim=1)
        self._stage.write_attribute(self._query, self._attribute, ordinal=ordinal,
                                    tensors=tensor, is_array=False).wait()

    def release(self) -> None:
        self._query.release().wait()
        self._paths.destroy_path_list(self._path_list)
        self._paths.destroy()


class Api:
    """Tiny HTTP API so the web page can find a free slot."""

    def __init__(self, slots, args):
        self.slots = slots
        self.args = args
        self.ready = False
        self.lock = threading.Lock()

    def status(self) -> dict:
        return {
            "ready": self.ready,
            "width": self.args.width,
            "height": self.args.height,
            "slots": [{"id": s.index, "busy": s.busy, "signal_port": s.signal_port} for s in self.slots],
        }

    def claim(self):
        if not self.ready:
            return 503, {"error": "warming_up"}
        with self.lock:
            for slot in self.slots:
                if not slot.busy:
                    slot.claimed_until = time.monotonic() + CLAIM_SECONDS
                    return 200, {"slot": slot.index, "signal_port": slot.signal_port,
                                 "width": self.args.width, "height": self.args.height}
        return 503, {"error": "full"}

    def serve(self) -> ThreadingHTTPServer:
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body: bytes, content_type: str):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/status":
                    self._send(200, json.dumps(api.status()).encode(), "application/json")
                elif path == "/api/claim":
                    code, body = api.claim()
                    self._send(code, json.dumps(body).encode(), "application/json")
                elif path == "/vendor/ov-web-rtc.umd.js" and CLIENT_LIBRARY.exists():
                    self._send(200, CLIENT_LIBRARY.read_bytes(), "text/javascript")
                else:
                    self._send(404, b"not found", "text/plain")

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer((self.args.http_host, self.args.http_port), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd


def main():
    args = parse_args()
    if not CLIENT_LIBRARY.exists():
        print("Browser client missing: run `python fetch_client.py` first.", file=sys.stderr)

    ovstream.initialize()
    slots = [Slot(i, args) for i in range(args.slots)]
    api = Api(slots, args)
    httpd = api.serve()
    print(f"API on http://{args.http_host}:{args.http_port}/api/status", flush=True)

    renderer = stage = None
    writers = []
    try:
        # The stage is created before the renderer, then the renderer is attached to it.
        stage = ovstage.Stage("twindrop")
        print("Creating ovrtx renderer (the first run compiles shaders and can take several minutes)...", flush=True)
        renderer = ovrtx.Renderer()
        renderer.attach_ovstage(stage)

        print(f"Loading scene: {args.stage or '(grid only)'}", flush=True)
        ordinal = 1
        root_layer = build_root_layer(args.stage, args.slots, args.width, args.height, sky=not args.no_sky)
        ovstage.population.open_usd_from_string(stage, root_layer, ordinal=ordinal)
        stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()
        writers = [CameraWriter(stage, camera_path(s.index)) for s in slots]

        # Warm up: render every slot once so shaders compile before anyone connects.
        ordinal += 1
        for slot, writer in zip(slots, writers):
            writer.write(slot.camera.update(0.0), ordinal)
        stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()
        all_products = {s.product for s in slots}
        for _ in range(20):
            products = renderer.step(render_products=all_products, delta_time=1 / args.fps, ordinal=ordinal)
            if all_products <= set(products.keys()):
                break
        else:
            raise RuntimeError("RenderProducts did not become available")
        del products

        for slot in slots:
            slot.start()
            print(f"[slot {slot.index}] WebRTC signaling on port {slot.signal_port}", flush=True)
        api.ready = True
        print(f"Ready: {args.slots} viewer slot(s), {args.width}x{args.height}. Press Ctrl+C to stop.", flush=True)

        idle_seconds = args.idle_minutes * 60
        with ovstream_utils.Loop(ovstream_utils.LoopConfig(fps_target=args.fps)) as loop:
            while True:
                tick = loop.tick()
                active = [s for s in slots if s.connected]
                if not active:
                    continue

                dt = tick.delta_time_seconds or 1 / args.fps
                ordinal += 1
                for slot in active:
                    writers[slot.index].write(slot.camera.update(dt), ordinal)
                stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()

                products = renderer.step(render_products={s.product for s in active},
                                         delta_time=dt, ordinal=ordinal)
                rendered = set(products.keys())
                for slot in active:
                    if slot.product not in rendered:
                        continue
                    for frame in products[slot.product].frames:
                        mapping = frame.render_vars[slot.bgra_var].map(device=ovrtx.Device.CUDA)
                        try:
                            slot.server.stream_video(ovstream.VideoFrame(
                                buffer=int(mapping.data),
                                width=args.width,
                                height=args.height,
                                pitch_bytes=args.width * 4,
                                sync=ovstream.CudaSync(wait_event=int(mapping.wait_event or 0)),
                            ))
                        except ovstream.OvstreamError:
                            pass  # viewer disconnected mid-frame
                        finally:
                            mapping.unmap()
                del products

                now = time.monotonic()
                for slot in active:
                    if now - slot.last_input > idle_seconds:
                        print(f"[slot {slot.index}] idle for {args.idle_minutes:g} min, disconnecting", flush=True)
                        slot.restart()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        for slot in slots:
            slot.close()
        for writer in writers:
            writer.release()
        if renderer is not None and stage is not None:
            renderer.detach_ovstage()
        if stage is not None:
            stage.destroy()
        if renderer is not None:
            renderer.destroy()
        ovstream.shutdown()


if __name__ == "__main__":
    main()
