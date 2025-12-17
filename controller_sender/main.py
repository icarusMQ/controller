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

    # Snap threshold: if L/R differ by <= 15 int8 units, force them equal.
    snap_eps_float = 15.0 / 127.0

    # Normal cap for human driving: clamp to +/-40 in int8 space for
    # regular joystick-driven motion.
    normal_cap_float = 40.0 / 127.0
    trigger_thresh = 0.5  # L2/R2 threshold to count as pressed
    bumper_ramp_time = 1.0
    trigger_ramp_time = 3.0
    bumper_target_float = 90.0 / 127.0

    # Assist parameters: smooth changes and gently pull L/R together so
    # human input feels less twitchy and more straight.
    assist_ramp_time = 1.0  # seconds to move from 0 -> 1.0 for normal stick changes
    blend_toward_avg = 0.3  # how strongly to encourage L and R to match

    prev_left = 0.0
    prev_right = 0.0

    # Bumper-based ramp state (L1/R1) and triggers (L2/R2)
    ramp_mode: str | None = None  # "forward" (R1), "spin_left" (L1), "trig_forward" (R2), "trig_reverse" (L2)
    ramp_value = 0.0
    bumper_start_eps = 0.1  # sticks must be close to zero to start ramp

    # A-button timed override state
    a_active_until = 0.0
    prev_a_pressed = False

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
        # Use a time-based step so a full-scale change 0 -> 1 takes
        # about assist_ramp_time seconds.
        step = period / assist_ramp_time
        dl = max(-step, min(step, l - prev_left))
        dr = max(-step, min(step, r - prev_right))
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

            # Read raw stick values plus triggers (L2/R2), bumpers (L1/R1), and A button
            left_raw, right_raw, connected, lt_trig, rt_trig, lb_pressed, rb_pressed, a_pressed = ctrl.get_full_state()
            if not connected:
                if args.stop_on_disconnect:
                    print("Controller disconnected, exiting.")
                    break
                # Send zeros when disconnected
                left = right = 0.0
                ramp_mode = None
                ramp_value = 0.0
            else:
                left = left_raw
                right = right_raw
            if out_invert:
                left, right = -left, -right

            # Edge-detect A button to trigger a 5s fixed-output override
            if a_pressed and not prev_a_pressed:
                a_active_until = now + 5.0
            prev_a_pressed = a_pressed

            # Bumper- and trigger-controlled ramps (L1/R1 and L2/R2)
            # L1 (LB): spin in place (L=+1, R=-1)
            # R1 (RB): turn mode (L=-1, R=+1)
            # L2 (LT): straight reverse (L=-1, R=-1)
            # R2 (RT): straight forward (L=+1, R=+1)
            # Ramps only start if both joysticks are close to zero.

            # Decide whether to (re)start or cancel ramp
            if ramp_mode is None:
                # Only allow ramp start if sticks near zero
                if abs(left_raw) <= bumper_start_eps and abs(right_raw) <= bumper_start_eps:
                    # First priority: triggers L2/R2 for straight motion
                    if lt_trig > trigger_thresh and rt_trig <= trigger_thresh:
                        ramp_mode = "trig_forward"  # L=+1, R=+1 (L2)
                        ramp_value = 0.0
                    elif rt_trig > trigger_thresh and lt_trig <= trigger_thresh:
                        ramp_mode = "trig_reverse"  # L=-1, R=-1 (R2)
                        ramp_value = 0.0
                    # Second priority: bumpers L1/R1 for turning
                    elif lb_pressed and not rb_pressed:
                        ramp_mode = "spin_left"      # L=+1, R=-1
                        ramp_value = 0.0
                    elif rb_pressed and not lb_pressed:
                        ramp_mode = "forward"       # L=-1, R=+1
                        ramp_value = 0.0
            else:
                # Cancel ramp if the initiating control is no longer uniquely active
                if ramp_mode in ("trig_forward", "trig_reverse"):
                    if not (lt_trig > trigger_thresh) and not (rt_trig > trigger_thresh):
                        ramp_mode = None
                        ramp_value = 0.0
                elif ramp_mode in ("forward", "spin_left"):
                    if not (lb_pressed ^ rb_pressed):
                        ramp_mode = None
                        ramp_value = 0.0

            if ramp_mode is not None:
                # Advance ramp toward full magnitude. Use a longer ramp
                # for trigger-based straight motion than for bumper turns.
                if ramp_mode in ("trig_forward", "trig_reverse"):
                    ramp_step = period / trigger_ramp_time
                else:
                    ramp_step = period / bumper_ramp_time
                ramp_value = min(1.0, ramp_value + ramp_step)

                # Shoulder (bumper) ramps are capped at ~+/-90 in int8 space,
                # while trigger ramps go to the full +/-127.
                bumper_scaled = bumper_target_float * ramp_value
                full_scaled = ramp_value

                if ramp_mode == "forward":
                    # R1: turn mode, left wheel negative, right wheel positive
                    left = -bumper_scaled
                    right = bumper_scaled
                elif ramp_mode == "spin_left":
                    # Spin in place: left forward, right reverse
                    left = bumper_scaled
                    right = -bumper_scaled
                elif ramp_mode == "trig_forward":
                    # R2: straight forward, both wheels positive
                    left = full_scaled
                    right = full_scaled
                elif ramp_mode == "trig_reverse":
                    # L2: straight reverse, both wheels negative
                    left = -full_scaled
                    right = -full_scaled
            else:
                # Normal human driving path: assist, snap, clamp, then cap to +/-32.
                left, right = apply_assist(left, right)

                # If L/R are already very close, snap them exactly equal so
                # small human asymmetries don't cause unintended turning.
                if abs(left - right) <= snap_eps_float:
                    avg_lr = 0.5 * (left + right)
                    left = right = avg_lr

                # Enforce maximum difference between left and right
                left, right = clamp_lr_difference(left, right)

                # Final speed cap for regular driving
                left = max(-normal_cap_float, min(normal_cap_float, left))
                right = max(-normal_cap_float, min(normal_cap_float, right))
            # If A mode is active, override outputs with fixed small values
            if now < a_active_until:
                base_l = -6.0 / 127.0
                base_r = 7.0 / 127.0
                if out_invert:
                    base_l, base_r = -base_l, -base_r
                left, right = base_l, base_r

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
