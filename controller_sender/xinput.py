"""Minimal XInput polling (Windows only) for Xbox controller.

Adapted to avoid external dependencies. Provides normalized stick values -1.0..1.0.
"""
from __future__ import annotations
import ctypes
import math
from dataclasses import dataclass
from typing import Optional

# XInput constants
XINPUT_MAX_DEVICES = 4
ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167

# Deadzones (official recommended)
LEFT_THUMB_DEADZONE = 7849
RIGHT_THUMB_DEADZONE = 8689
MAX_THUMB = 32767

# Button bitmasks (XInput)
XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100   # L1 / LB
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200  # R1 / RB
XINPUT_GAMEPAD_A = 0x1000               # A button

class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", ctypes.c_ubyte * 16),  # We'll reinterpret manually
    ]

class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]

# Load XInput DLL (try several names in order)
_xinput_dll_names = [
    "XInput1_4.dll",  # Win 8+ 
    "XInput1_3.dll",
    "XInput9_1_0.dll",
]

_xinput = None
for name in _xinput_dll_names:
    try:
        _xinput = ctypes.WinDLL(name)
        break
    except OSError:
        continue

if _xinput is None:
    raise OSError("Could not load any XInput DLL. Ensure Xbox controller drivers are installed.")

# Define function prototype
XInputGetState = _xinput.XInputGetState
XInputGetState.argtypes = [ctypes.c_uint, ctypes.POINTER(XINPUT_STATE)]
XInputGetState.restype = ctypes.c_uint

@dataclass
class Sticks:
    left_x: float
    left_y: float
    right_x: float
    right_y: float

@dataclass
class ControllerReading:
    connected: bool
    sticks: Sticks
    packet_number: int
    buttons: int
    left_trigger: float
    right_trigger: float

class XInputController:
    def __init__(self, index: int = 0, invert_y: bool = True):
        if not (0 <= index < XINPUT_MAX_DEVICES):
            raise ValueError("Controller index must be 0..3")
        self.index = index
        self.invert_y = invert_y
        self._last_packet: Optional[int] = None

    def _normalize_axis(self, raw: int, deadzone: int) -> float:
        if abs(raw) < deadzone:
            return 0.0
        # Re-range after deadzone
        if raw > 0:
            norm = (raw - deadzone) / (MAX_THUMB - deadzone)
        else:
            norm = (raw + deadzone) / (MAX_THUMB - deadzone)
        return max(-1.0, min(1.0, norm))

    def poll(self) -> ControllerReading:
        state = XINPUT_STATE()
        res = XInputGetState(self.index, ctypes.byref(state))
        if res != ERROR_SUCCESS:
            return ControllerReading(False, Sticks(0,0,0,0), 0, 0, 0.0, 0.0)

        # Reinterpret the gamepad struct
        gp = XINPUT_GAMEPAD.from_buffer_copy(state.Gamepad)
        lx = self._normalize_axis(gp.sThumbLX, LEFT_THUMB_DEADZONE)
        ly = self._normalize_axis(gp.sThumbLY, LEFT_THUMB_DEADZONE)
        rx = self._normalize_axis(gp.sThumbRX, RIGHT_THUMB_DEADZONE)
        ry = self._normalize_axis(gp.sThumbRY, RIGHT_THUMB_DEADZONE)

        # Triggers as normalized 0.0..1.0 (kept for compatibility)
        lt = gp.bLeftTrigger / 255.0
        rt = gp.bRightTrigger / 255.0

        if self.invert_y:
            ly = -ly
            ry = -ry

        sticks = Sticks(lx, ly, rx, ry)
        return ControllerReading(True, sticks, state.dwPacketNumber, gp.wButtons, lt, rt)

    def get_left_right_y(self) -> tuple[float, float, bool]:
        reading = self.poll()
        return reading.sticks.left_y, reading.sticks.right_y, reading.connected

    def get_left_right_y_with_triggers(self) -> tuple[float, float, bool, float, float]:
        """Convenience helper returning Y axes plus trigger values.

        Triggers are normalized 0.0..1.0.
        """
        reading = self.poll()
        return (
            reading.sticks.left_y,
            reading.sticks.right_y,
            reading.connected,
            reading.left_trigger,
            reading.right_trigger,
        )

    def get_full_state(self) -> tuple[float, float, bool, float, float, bool, bool, bool]:
        """Return Y axes, connection, triggers (L2/R2), bumpers (L1/R1), and A.

        Layout: (left_y, right_y, connected, lt, rt, lb_pressed, rb_pressed, a_pressed)
        """
        reading = self.poll()
        buttons = reading.buttons
        lb = bool(buttons & XINPUT_GAMEPAD_LEFT_SHOULDER)
        rb = bool(buttons & XINPUT_GAMEPAD_RIGHT_SHOULDER)
        a = bool(buttons & XINPUT_GAMEPAD_A)
        return (
            reading.sticks.left_y,
            reading.sticks.right_y,
            reading.connected,
            reading.left_trigger,
            reading.right_trigger,
            lb,
            rb,
            a,
        )

    def get_left_right_y_with_bumpers(self) -> tuple[float, float, bool, bool, bool]:
        """Return Y axes plus whether L1/LB and R1/RB are pressed."""
        reading = self.poll()
        buttons = reading.buttons
        lb = bool(buttons & XINPUT_GAMEPAD_LEFT_SHOULDER)
        rb = bool(buttons & XINPUT_GAMEPAD_RIGHT_SHOULDER)
        return (
            reading.sticks.left_y,
            reading.sticks.right_y,
            reading.connected,
            lb,
            rb,
        )
