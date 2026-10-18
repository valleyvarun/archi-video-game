"""Z-up fly camera driven by ovstream input events.

W/S forward/back, A/D left/right, Q/E down/up, left-drag to look,
mouse wheel to move along the view direction, Shift to move faster.
"""

import math
import threading

import numpy as np
import ovstream

SPEED = 5.0           # metres per second
FAST_MULTIPLIER = 3.0
LOOK_GAIN = 0.004     # radians per pixel of drag
WHEEL_STEP = 1.0      # metres per wheel notch
PITCH_LIMIT = 1.55

# ovstream key codes are uppercase ASCII for letters; Shift is 0x0301-0x0303.
KEY_W, KEY_A, KEY_S, KEY_D, KEY_Q, KEY_E = (ord(c) for c in "WASDQE")
SHIFT_KEYS = {0x0301, 0x0302, 0x0303}
WORLD_UP = np.array([0.0, 0.0, 1.0])


class FlyCamera:
    def __init__(self, eye=(10.0, -10.0, 8.0), target=(0.0, 0.0, 0.0)):
        self._home_eye = np.array(eye, dtype=np.float64)
        self._home_target = np.array(target, dtype=np.float64)
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.eye = self._home_eye.copy()
            direction = self._home_target - self.eye
            direction /= np.linalg.norm(direction)
            self.yaw = math.atan2(direction[1], direction[0])
            self.pitch = math.asin(direction[2])
            self.keys = set()
            self.dragging = False
            self.last_mouse = None
            self.pending_wheel = 0.0

    def handle_input(self, event) -> None:
        """Called on an ovstream callback thread; only records state."""
        with self._lock:
            if event.type == ovstream.InputEventType.KEYBOARD:
                key = event.keyboard.key_code
                if event.keyboard.key_state == ovstream.KeyState.DOWN:
                    self.keys.add(key)
                else:
                    self.keys.discard(key)
            elif event.type == ovstream.InputEventType.MOUSE:
                mouse = event.mouse
                if mouse.type == ovstream.MouseEventType.BUTTON and mouse.data == ovstream.MouseButton.LEFT:
                    self.dragging = mouse.button_state == ovstream.KeyState.DOWN
                    self.last_mouse = (mouse.x, mouse.y)
                elif mouse.type == ovstream.MouseEventType.MOVE and self.dragging:
                    if self.last_mouse is not None:
                        self.yaw -= (mouse.x - self.last_mouse[0]) * LOOK_GAIN
                        self.pitch -= (mouse.y - self.last_mouse[1]) * LOOK_GAIN
                        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch))
                    self.last_mouse = (mouse.x, mouse.y)
                elif mouse.type == ovstream.MouseEventType.WHEEL:
                    self.pending_wheel += mouse.scroll_y

    def update(self, dt: float) -> np.ndarray:
        """Advance by dt seconds and return the camera-to-world matrix (row-major, USD convention)."""
        with self._lock:
            forward = np.array([
                math.cos(self.pitch) * math.cos(self.yaw),
                math.cos(self.pitch) * math.sin(self.yaw),
                math.sin(self.pitch),
            ])
            flat_forward = np.array([math.cos(self.yaw), math.sin(self.yaw), 0.0])
            flat_right = np.array([math.sin(self.yaw), -math.cos(self.yaw), 0.0])

            step = SPEED * dt * (FAST_MULTIPLIER if self.keys & SHIFT_KEYS else 1.0)
            if KEY_W in self.keys: self.eye += flat_forward * step
            if KEY_S in self.keys: self.eye -= flat_forward * step
            if KEY_D in self.keys: self.eye += flat_right * step
            if KEY_A in self.keys: self.eye -= flat_right * step
            if KEY_E in self.keys: self.eye[2] += step
            if KEY_Q in self.keys: self.eye[2] -= step
            self.eye += forward * self.pending_wheel * WHEEL_STEP
            self.pending_wheel = 0.0

            right = np.cross(forward, WORLD_UP)
            right /= np.linalg.norm(right)
            up = np.cross(right, forward)

            # Rows are the camera's right, up and back (-forward) axes, then its position.
            matrix = np.eye(4, dtype=np.float64)
            matrix[0, :3] = right
            matrix[1, :3] = up
            matrix[2, :3] = -forward
            matrix[3, :3] = self.eye
            return matrix
