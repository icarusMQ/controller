"""Simple UDP listener to print received wheel speeds for testing.
Run: python robot_sim.py --port 4210
"""
from __future__ import annotations
import argparse
import socket


def int8_to_float(b: int) -> float:
    # Convert unsigned byte 0..255 to signed -128..127 then scale
    if b > 127:
        b = b - 256
    # limit range just in case
    if b < -128: b = -128
    if b > 127: b = 127
    return b / 127.0


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=4210)
    p.add_argument("--bind", default="0.0.0.0")
    return p.parse_args()


def main():
    args = parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    print(f"Listening on {args.bind}:{args.port}")
    while True:
        data, addr = sock.recvfrom(64)
        if len(data) >= 2:
            l = int8_to_float(data[0])
            r = int8_to_float(data[1])
            cs_ok = "?"
            if len(data) == 3:
                cs_ok = "OK" if (data[0] ^ data[1]) & 0xFF == data[2] else "BAD"
            print(f"{addr[0]} l={l:+.3f} r={r:+.3f} bytes={data.hex()} checksum={cs_ok}")
        else:
            print(f"{addr} short packet: {data.hex()}")

if __name__ == "__main__":  # pragma: no cover
    main()
