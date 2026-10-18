"""One viewer slot: renders one visitor's scene with ovrtx and streams it to their browser.

viewer.py starts one of these processes per slot. Each has its own scene, so every
visitor sees only their own model. Commands arrive on stdin as JSON lines:
    {"cmd": "load", "usd": "<placed.usda>", "bounds": [...]}   add the visitor's model
    {"cmd": "reset"}                                             back to the empty grid
Events go to stdout as lines starting with "@@" followed by JSON:
    {"state": "starting" | "idle" | "loading" | "ready"}
    {"connected": true | false}
    {"error": "<message for the visitor>"}   a load failed (the slot is idle again)
"""

import argparse
import json
import math
import os
import queue
import sys
import threading
import time

os.environ.setdefault("OVRTX_SKIP_USD_CHECK", "1")

import numpy as np
import ovrtx
import ovstage
import ovstream
import ovstream_utils

from fly_camera import FlyCamera
from scene import BGRA_VAR, CAMERA_PATH, HELPERS_PATH, MODEL_PATH, PRODUCT_PATH, build_root_layer

WARMUP_STEPS = 20  # renders after a scene change so shaders and textures are ready


def emit(**event) -> None:
    print("@@" + json.dumps(event), flush=True)


def log(message: str) -> None:
    print(message, flush=True)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--stage", help="base USD scene shown to every visitor (optional)")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--signal-port", type=int, default=49100)
    parser.add_argument("--media-port", type=int, default=47998)
    parser.add_argument("--public-ip")
    parser.add_argument("--idle-minutes", type=float, default=10)
    parser.add_argument("--no-sky", action="store_true")
    return parser.parse_args()


class XformWriter:
    """Publishes a prim's transform to ovstage as `omni:xform`."""

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


def helpers_scale(bounds) -> float:
    """Grid/axes scale (a power of ten) so the 20-unit grid suits the model's footprint."""
    size = max(bounds[3] - bounds[0], bounds[4] - bounds[1], 1e-3)
    return 10.0 ** math.ceil(math.log10(size / 20))


class Slot:
    def __init__(self, args):
        self.args = args
        self.camera = FlyCamera()
        self.commands = queue.Queue()
        self.connected = False
        self.just_disconnected = False
        self.last_input = 0.0
        self.model = None  # ovstage handle of the visitor's model
        self.ordinal = 0

        self.config = ovstream.ServerConfig(
            width=args.width,
            height=args.height,
            cuda_device=0,
            webrtc_signal_port=args.signal_port,
            stream_port=args.media_port,
        )
        if args.public_ip:
            self.config.webrtc_public_ip = args.public_ip

    # -- ovstream callbacks (SDK threads): only record state ----------------------------
    def _on_connection(self, connected: bool) -> None:
        if connected:
            self.camera.reset()
            self.last_input = time.monotonic()
        else:
            self.just_disconnected = True
        self.connected = connected
        emit(connected=connected)

    def _on_input(self, event) -> None:
        self.last_input = time.monotonic()
        self.camera.handle_input(event)

    # -- scene ---------------------------------------------------------------------------
    def publish(self) -> None:
        """Make all writes so far visible to the renderer."""
        self.stage.advance_write_floor(self.ordinal, ovstage.Scope.ALL).wait()

    def render(self, dt: float):
        self.ordinal += 1
        self.camera_writer.write(self.camera.update(dt), self.ordinal)
        self.publish()
        return self.renderer.step(render_products={PRODUCT_PATH}, delta_time=dt, ordinal=self.ordinal)

    def warm_up(self) -> None:
        for _ in range(WARMUP_STEPS):
            products = self.render(1 / self.args.fps)
            del products

    def set_helpers_scale(self, scale: float) -> None:
        self.ordinal += 1
        self.helpers_writer.write(np.diag([scale, scale, scale, 1.0]), self.ordinal)
        self.publish()

    def remove_model(self) -> None:
        if self.model is not None:
            ovstage.population.remove_usd(self.stage, self.model)
            self.model = None
            self.ordinal += 1
            ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
            self.publish()

    def load(self, usd: str, bounds) -> None:
        emit(state="loading")
        try:
            self.remove_model()
            self.model = ovstage.population.add_usd_reference(self.stage, usd, MODEL_PATH)
            self.ordinal += 1
            ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
            self.publish()
            self.set_helpers_scale(helpers_scale(bounds))
            self.camera.frame(bounds)
            self.warm_up()
            emit(state="ready")
        except Exception as error:  # a bad file must not take the slot down
            log(f"load failed: {error}")
            self.reset()
            emit(error="The model could not be loaded into the renderer.")

    def reset(self) -> None:
        try:
            self.remove_model()
        except Exception as error:
            log(f"removing model failed: {error}")
        self.set_helpers_scale(1.0)
        self.camera = FlyCamera()
        emit(state="idle")

    def read_commands(self) -> None:
        for line in sys.stdin:
            if line.strip():
                self.commands.put(json.loads(line))
        os._exit(0)  # stdin closed: the manager (viewer.py) has gone, so stop too

    # -- main loop -------------------------------------------------------------------------
    def run(self) -> None:
        args = self.args
        emit(state="starting")
        ovstream.initialize()
        self.stage = ovstage.Stage(f"twindrop-slot{args.index}")
        self.renderer = ovrtx.Renderer()
        self.renderer.attach_ovstage(self.stage)
        self.ordinal = 1
        ovstage.population.open_usd_from_string(
            self.stage, build_root_layer(args.stage, args.width, args.height, sky=not args.no_sky), ordinal=1)
        self.publish()
        self.camera_writer = XformWriter(self.stage, CAMERA_PATH)
        self.helpers_writer = XformWriter(self.stage, HELPERS_PATH)
        self.warm_up()

        self.server = ovstream.Server(ovstream.ServerType.WEBRTC)
        self.server.on_connection = self._on_connection
        self.server.on_input = self._on_input
        self.server.start(self.config)
        threading.Thread(target=self.read_commands, daemon=True).start()
        log(f"WebRTC signaling on port {args.signal_port}")
        emit(state="idle")

        idle_seconds = args.idle_minutes * 60
        try:
            with ovstream_utils.Loop(ovstream_utils.LoopConfig(fps_target=args.fps)) as loop:
                while True:
                    tick = loop.tick()
                    self.handle_events(idle_seconds)
                    if self.connected:
                        self.stream_frame(tick.delta_time_seconds or 1 / args.fps)
        finally:
            self.shutdown()

    def handle_events(self, idle_seconds: float) -> None:
        if self.just_disconnected:
            self.just_disconnected = False
            self.reset()
        while not self.commands.empty():
            command = self.commands.get()
            if command["cmd"] == "load":
                self.load(command["usd"], command["bounds"])
            elif command["cmd"] == "reset":
                self.reset()
        if self.connected and time.monotonic() - self.last_input > idle_seconds:
            log(f"viewer idle for {self.args.idle_minutes:g} min, disconnecting")
            self.server.stop()
            self.server.start(self.config)
            self.connected = False
            emit(connected=False)
            self.reset()

    def stream_frame(self, dt: float) -> None:
        products = self.render(dt)
        if PRODUCT_PATH in set(products.keys()):
            for frame in products[PRODUCT_PATH].frames:
                mapping = frame.render_vars[BGRA_VAR].map(device=ovrtx.Device.CUDA)
                try:
                    self.server.stream_video(ovstream.VideoFrame(
                        buffer=int(mapping.data),
                        width=self.args.width,
                        height=self.args.height,
                        pitch_bytes=self.args.width * 4,
                        sync=ovstream.CudaSync(wait_event=int(mapping.wait_event or 0)),
                    ))
                except ovstream.OvstreamError:
                    pass  # viewer disconnected mid-frame
                finally:
                    mapping.unmap()
        del products

    def shutdown(self) -> None:
        server = getattr(self, "server", None)
        if server is not None:
            try:
                server.stop()
            except ovstream.OvstreamError:
                pass
            server.close()
        for writer in (getattr(self, "camera_writer", None), getattr(self, "helpers_writer", None)):
            if writer:
                writer.release()
        if getattr(self, "renderer", None) is not None:
            self.renderer.detach_ovstage()
            self.stage.destroy()
            self.renderer.destroy()
        ovstream.shutdown()


if __name__ == "__main__":
    try:
        Slot(parse_args()).run()
    except KeyboardInterrupt:
        pass
