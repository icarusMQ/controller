"""Headless loop to send left/right Y stick values over UDP.
"""
from __future__ import annotations
import argparse
import time
import sys
from .udp_sender import UdpWheelSender, UdpTarget
from .xinput import XInputController


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Send Xbox controller Y axes via UDP")
    p.add_argument("--ip", default="192.168.0.23", help="Target IP (robot)")
    p.add_argument("--port", type=int, default=4210, help="Target UDP port")
    p.add_argument("--rate", type=float, default=30.0, help="Send frequency (Hz)")
    p.add_argument("--no-checksum", action="store_true", help="Disable checksum byte")
    p.add_argument("--controller", type=int, default=0, help="Controller index 0-3")
    p.add_argument("--no-invert-y", action="store_true", help="Don't invert Y axes (default inverted)")
    p.add_argument("--duration", type=float, default=0, help="Run for N seconds then exit (0 = infinite)")
    p.add_argument("--print", action="store_true", help="Print values being sent (deprecated alias for --verbose)")
    p.add_argument("--verbose", action="store_true", help="Verbose output of sent values")
    p.add_argument("--invert-output-y", action="store_true", help="Force invert output Y regardless of controller setting")
    p.add_argument("--stop-on-disconnect", action="store_true", help="Exit if controller disconnects")
    return p.parse_args(argv)


def run_loop(args):
    sender = UdpWheelSender(UdpTarget(args.ip, args.port), enable_checksum=not args.no_checksum)
    ctrl = XInputController(args.controller, invert_y=not args.no_invert_y)
    out_invert = args.invert_output_y

    period = 1.0 / max(1e-3, args.rate)
    next_time = time.perf_counter()
    start = time.perf_counter()

    try:
        while True:
            now = time.perf_counter()
            if args.duration and (now - start) >= args.duration:
                break

            if now < next_time:
                time.sleep(min(0.002, next_time - now))
                continue
            next_time += period

            left, right, connected = ctrl.get_left_right_y()
            if not connected:
                if args.stop_on_disconnect:
                    print("Controller disconnected, exiting.")
                    break
                # Send zeros when disconnected
                left = right = 0.0
            if out_invert:
                left, right = -left, -right
            sender.send(left, right)
            if args.print or args.verbose:
                print(f"L={left:+.3f} R={right:+.3f} conn={int(connected)}")
    finally:
        # Stop robot
        try:
            sender.send(0.0, 0.0)
        except OSError:
            pass
        sender.close()


def main(argv=None):
    args = parse_args(argv)
    run_loop(args)

if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
