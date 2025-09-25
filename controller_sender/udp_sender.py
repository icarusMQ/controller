import socket
from dataclasses import dataclass


def float_to_int8(x: float) -> int:
    x = max(-1.0, min(1.0, x))
    return int(round(x * 127))  # -127..127

@dataclass
class UdpTarget:
    host: str = "192.168.0.23"
    port: int = 4210

class UdpWheelSender:
    def __init__(self, target: UdpTarget, enable_checksum: bool = True):
        self.target = target
        self.enable_checksum = enable_checksum
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)

    def build_packet(self, left: float, right: float) -> bytes:
        bL = float_to_int8(left) & 0xFF
        bR = float_to_int8(right) & 0xFF
        if self.enable_checksum:
            return bytes([bL, bR, (bL ^ bR) & 0xFF])
        return bytes([bL, bR])

    def send(self, left: float, right: float):
        pkt = self.build_packet(left, right)
        self.sock.sendto(pkt, (self.target.host, self.target.port))
        return pkt

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
