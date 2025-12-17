"""Headless loop to send left/right Y stick values over UDP or Serial.
"""
from __future__ import annotations
import argparse
import time
import sys
from .udp_sender import UdpWheelSender, UdpTarget
from .serial_sender import SerialWheelSender, SerialTarget
from .xinput import XInputController


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Send Xbox controller Y axes via UDP or Serial")
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
    p.add_argument("--serial-port", help="Use serial instead of UDP (e.g. COM3)")
    p.add_argument("--baud", type=int, default=115200, help="Serial baud rate when using --serial-port")
    return p.parse_args(argv)


def run_loop(args):
    if args.serial_port:
        sender = SerialWheelSender(
            SerialTarget(args.serial_port, args.baud),
            enable_checksum=not args.no_checksum,
        )
    else:
        sender = UdpWheelSender(UdpTarget(args.ip, args.port), enable_checksum=not args.no_checksum)
    ctrl = XInputController(args.controller, invert_y=not args.no_invert_y)
    out_invert = args.invert_output_y

    # Maximum allowed difference between left and right commands in int8 units.
    # 150 / 127 ~= 1.18 in float space.
    max_diff_float = 150.0 / 127.0

    # Assist parameters: smooth changes and gently pull L/R together so
    # human input feels less twitchy and more straight.
    max_step = 0.25   # max change per tick in -1..1 space
    blend_toward_avg = 0.3  # how strongly to encourage L and R to match

    prev_left = 0.0
    prev_right = 0.0

    def clamp_lr_difference(l: float, r: float) -> tuple[float, float]:
        """Limit |L-R| so the int8 difference is <= 150.

        The wheel that is further ahead is pulled back so that the
        difference does not exceed the limit.
        """
        diff = l - r
        if diff > max_diff_float:
            # left ahead, pull it back
            l = r + max_diff_float
        elif diff < -max_diff_float:
            # right ahead, pull it back
            r = l + max_diff_float
        return l, r

    def apply_assist(l: float, r: float) -> tuple[float, float]:
        nonlocal prev_left, prev_right
        # 1) Rate limit: don't let values jump too fast between ticks.
        dl = max(-max_step, min(max_step, l - prev_left))
        dr = max(-max_step, min(max_step, r - prev_right))
        l_smooth = prev_left + dl
        r_smooth = prev_right + dr

        # 2) Straightening: pull both sides slightly toward their average
        # so they tend to be closer/equal unless user really holds a turn.
        avg = 0.5 * (l_smooth + r_smooth)
        l_blend = l_smooth + (avg - l_smooth) * blend_toward_avg
        r_blend = r_smooth + (avg - r_smooth) * blend_toward_avg

        prev_left, prev_right = l_blend, r_blend
        return l_blend, r_blend

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
            # Assist: smooth rapid changes and gently encourage L/R to match
            left, right = apply_assist(left, right)
            # Enforce maximum difference between left and right
            left, right = clamp_lr_difference(left, right)
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
