from __future__ import annotations
"""Serial-based wheel sender.

Sends the same 2- or 3-byte packet as UdpWheelSender, but over a
USB serial connection to an external hub (e.g., ESP32-C3).
"""

from dataclasses import dataclass
from typing import Optional

import serial  # type: ignore[import]

from .udp_sender import float_to_int8


@dataclass
class SerialTarget:
    port: str = "COM3"
    baudrate: int = 115200
    timeout: float = 0.0  # non-blocking writes


class SerialWheelSender:
    def __init__(self, target: SerialTarget, enable_checksum: bool = True):
        self.target = target
        self.enable_checksum = enable_checksum
        self._ser: Optional[serial.Serial] = serial.Serial(
            port=target.port,
            baudrate=target.baudrate,
            timeout=target.timeout,
            write_timeout=1.0,
        )

    @property
    def serial(self) -> Optional[serial.Serial]:
        """Expose underlying Serial object for read-only monitoring.

        GUI code can use this to read hub debug output (like a serial
        monitor) while this class handles packet writes.
        """
        return self._ser

    def build_packet(self, left: float, right: float) -> bytes:
        bL = float_to_int8(left) & 0xFF
        bR = float_to_int8(right) & 0xFF
        if self.enable_checksum:
            return bytes([bL, bR, (bL ^ bR) & 0xFF])
        return bytes([bL, bR])

    def send(self, left: float, right: float) -> bytes:
        if self._ser is None or not self._ser.is_open:
            raise OSError("Serial port is not open")
        pkt = self.build_packet(left, right)
        self._ser.write(pkt)
        self._ser.flush()
        return pkt

    def close(self) -> None:
        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except OSError:
                pass
            self._ser = None
