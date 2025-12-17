"""Tkinter GUI to visualize stick Y values, connection, and send over UDP."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
from dataclasses import dataclass
from .udp_sender import UdpWheelSender, UdpTarget
from .serial_sender import SerialWheelSender, SerialTarget
from .xinput import (
    XInputController,
    XINPUT_GAMEPAD_LEFT_SHOULDER,
    XINPUT_GAMEPAD_RIGHT_SHOULDER,
    XINPUT_GAMEPAD_A,
    XINPUT_GAMEPAD_B,
    XINPUT_GAMEPAD_X,
    XINPUT_GAMEPAD_Y,
    XINPUT_GAMEPAD_LEFT_THUMB,
    XINPUT_GAMEPAD_DPAD_LEFT,
    XINPUT_GAMEPAD_DPAD_RIGHT,
    XINPUT_GAMEPAD_DPAD_UP,
    XINPUT_GAMEPAD_DPAD_DOWN,
)

@dataclass
class GuiConfig:
    ip: str = "192.168.0.23"
    port: int = 4210
    rate: float = 30.0
    invert_y: bool = True
    checksum: bool = True
    use_serial: bool = False
    serial_port: str = "COM3"
    baud: int = 115200

class App:
    def __init__(self, root: tk.Tk, cfg: GuiConfig):
        self.root = root
        self.cfg = cfg
        self.root.title("Xbox Wheel Sender")
        self.sender = None
        self.ctrl = XInputController(0, invert_y=cfg.invert_y)

        self.running = False
        self.thread: threading.Thread | None = None
        self.last_packet: bytes | None = None
        self.last_connected = False
        self.left_val = 0.0
        self.right_val = 0.0
        # Raw stick positions for visual analog sticks
        self.left_stick_x = 0.0
        self.left_stick_y = 0.0
        self.right_stick_x = 0.0
        self.right_stick_y = 0.0
        # Runtime toggles
        self.output_invert = tk.BooleanVar(value=False)
        self.verbose = tk.BooleanVar(value=False)
        self.transport_var = tk.StringVar(value="serial" if cfg.use_serial else "udp")
        self.serial_port_var = tk.StringVar(value=cfg.serial_port)

        # Stick-mode and button edge state
        self._single_stick_mode = tk.BooleanVar(value=False)
        self._prev_x_pressed: bool = False
        self._prev_b_pressed: bool = False
        self._prev_y_pressed: bool = False
        self._prev_left_thumb_pressed: bool = False

        # Serial monitor state
        self.serial_text: tk.Text | None = None
        self._serial_reader_thread: threading.Thread | None = None
        self._serial_reader_running = False

        # Max allowed difference between left and right commands in int8 units
        # (150 / 127 ~= 1.18 in float space).
        self._max_diff_float = 150.0 / 127.0

        # Snap threshold: if L/R differ by <= 15 int8 units, force them equal.
        self._snap_eps_float = 15.0 / 127.0

        # Assist parameters: smooth changes and gently pull L/R together
        # so human input feels less twitchy and more straight.
        self._assist_ramp_time = 0.5      # seconds to move from 0 -> 1.0 for normal stick changes
        self._assist_blend = 0.3          # how strongly to encourage L/R to match
        self._prev_left = 0.0
        self._prev_right = 0.0

        # Normal cap for human driving: clamp to +/-40 in int8 space.
        self._normal_cap_int = 40
        self._normal_cap_min = 5
        self._normal_cap_max = 100
        self._normal_cap_float = self._normal_cap_int / 127.0
        self._trigger_thresh = 0.5  # L2/R2 threshold to count as pressed

        # Ramp timings
        self._bumper_ramp_time = 1.0
        self._trigger_ramp_time = 3.0
        self._bumper_target_float = 90.0 / 127.0

        # A-button timed override state
        self._a_active_until: float = 0.0
        self._prev_a_pressed: bool = False

        # Ramp state for bumpers (L1/R1) and triggers (L2/R2)
        self._ramp_mode: str | None = None  # "forward" (R1), "spin_left" (L1), "trig_forward" (R2), "trig_reverse" (L2)
        self._ramp_value: float = 0.0
        self._bumper_start_eps: float = 0.1  # sticks must be close to zero to start ramp

        self._build_ui()
        self._schedule_ui_update()

    def _build_ui(self):
        # Basic dark theme colors
        bg = "#1e1e1e"
        fg = "#f0f0f0"
        accent = "#0e639c"
        outline = "#aaaaaa"  # light grey for all custom outlines

        self.root.configure(bg=bg)

        style = ttk.Style(self.root)
        # Use a theme that respects style changes
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TButton", background=bg, foreground=fg)
        style.map("TButton", background=[("active", accent)])
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TRadiobutton", background=bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        # Progress bars for sticks: blue on dark background
        style.configure(
            "Blue.Vertical.TProgressbar",
            troughcolor=bg,
            background=accent,
            bordercolor=bg,
            lightcolor=accent,
            darkcolor=accent,
        )

        frm = ttk.Frame(self.root, padding=10, style="TFrame")
        frm.grid(row=0, column=0, sticky="nsew")
        # Make top-level window and main frame expand with resize
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        # Give the stick bars and serial monitor vertical stretch
        frm.rowconfigure(1, weight=1)   # bars
        frm.rowconfigure(10, weight=2)  # serial monitor

        # Connection
        self.conn_var = tk.StringVar(value="Disconnected")
        self.conn_label = ttk.Label(frm, textvariable=self.conn_var, font=("Segoe UI", 12, "bold"))
        self.conn_label.grid(row=0, column=0, columnspan=2, pady=(0,8), sticky="w")

        # Analog stick visuals in outlined containers
        left_frame = tk.Frame(
            frm,
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground=outline,
            highlightcolor=outline,
            bg=bg,
        )
        right_frame = tk.Frame(
            frm,
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground=outline,
            highlightcolor=outline,
            bg=bg,
        )
        left_frame.grid(row=1, column=0, padx=20, sticky="nsew")
        right_frame.grid(row=1, column=1, padx=20, sticky="nsew")
        left_frame.rowconfigure(0, weight=1)
        left_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=1)
        right_frame.columnconfigure(0, weight=1)

        # Use canvases to draw circular sticks with a moving thumb marker
        self.left_canvas = tk.Canvas(
            left_frame,
            width=180,
            height=180,
            bg=bg,
            highlightthickness=0,
            bd=0,
        )
        self.right_canvas = tk.Canvas(
            right_frame,
            width=180,
            height=180,
            bg=bg,
            highlightthickness=0,
            bd=0,
        )
        self.left_canvas.grid(row=0, column=0, sticky="nsew")
        self.right_canvas.grid(row=0, column=0, sticky="nsew")

        self.left_label = ttk.Label(frm, text="Left Y: 0.000")
        self.right_label = ttk.Label(frm, text="Right Y: 0.000")
        self.left_label.grid(row=2, column=0, pady=5, sticky="w")
        self.right_label.grid(row=2, column=1, pady=5, sticky="e")

        # Packet label
        self.packet_var = tk.StringVar(value="Packet: -")
        ttk.Label(frm, textvariable=self.packet_var).grid(row=3, column=0, columnspan=2, pady=(5,2), sticky="w")

        # Current stick limit (adjusted with X/B)
        self.stick_cap_var = tk.StringVar(value=f"Stick limit: {self._normal_cap_int}")
        ttk.Label(frm, textvariable=self.stick_cap_var).grid(row=3, column=1, padx=(0,5), pady=(0,2), sticky="e")

        # Controls
        self.start_btn = ttk.Button(frm, text="Start", command=self.start)
        self.stop_btn = ttk.Button(frm, text="Stop", command=self.stop, state="disabled")
        self.start_btn.grid(row=4, column=0, pady=10, sticky="ew")
        self.stop_btn.grid(row=4, column=1, pady=10, sticky="ew")

        # Toggles
        ttk.Checkbutton(frm, text="Invert Output", variable=self.output_invert).grid(row=6, column=0, pady=(4,0), sticky="w")
        ttk.Checkbutton(frm, text="Verbose", variable=self.verbose).grid(row=6, column=1, pady=(4,0), sticky="e")

        # Transport selection
        ttk.Label(frm, text="Transport:").grid(row=5, column=0, pady=(5,0), sticky="w")
        tr_frame = ttk.Frame(frm)
        tr_frame.grid(row=5, column=1, pady=(5,0), sticky="e")
        ttk.Radiobutton(tr_frame, text="UDP", value="udp", variable=self.transport_var).grid(row=0, column=0, padx=2)
        ttk.Radiobutton(tr_frame, text="Serial", value="serial", variable=self.transport_var).grid(row=0, column=1, padx=2)

        # Footer target / hub info
        self.target_var = tk.StringVar(value=self._format_target_label())
        ttk.Label(frm, textvariable=self.target_var).grid(row=7, column=0, columnspan=2, pady=(5,0), sticky="w")

        # Serial port selection
        serial_frame = ttk.Frame(frm)
        serial_frame.grid(row=8, column=0, columnspan=2, pady=(4,0), sticky="ew")
        serial_frame.columnconfigure(1, weight=1)
        ttk.Label(serial_frame, text="Hub COM port:").grid(row=0, column=0, padx=2)
        # Serial port text input: white text on dark grey background
        dark_entry_bg = "#2b2b2b"
        style.configure(
            "Serial.TEntry",
            fieldbackground=dark_entry_bg,
            foreground=fg,
            background=dark_entry_bg,
        )
        self.serial_entry = ttk.Entry(
            serial_frame,
            textvariable=self.serial_port_var,
            width=10,
            style="Serial.TEntry",
        )
        self.serial_entry.grid(row=0, column=1, padx=2, sticky="ew")

        # UDP target change button (only relevant in UDP mode)
        ttk.Button(frm, text="Change UDP IP", command=self.change_target).grid(row=9, column=0, columnspan=2, pady=(5,0), sticky="ew")

        # Serial monitor (dark text box with grey outline, no scrollbar)
        monitor_frame = ttk.LabelFrame(frm, text="Serial monitor (hub output)")
        monitor_frame.grid(row=10, column=0, columnspan=2, pady=(8,0), sticky="nsew")

        txt = tk.Text(
            monitor_frame,
            height=8,
            wrap="none",
            state="disabled",
            bg="#2b2b2b",
            fg=fg,
            insertbackground=fg,
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=outline,
            highlightcolor=outline,
        )
        txt.grid(row=0, column=0, sticky="nsew")
        monitor_frame.rowconfigure(0, weight=1)
        monitor_frame.columnconfigure(0, weight=1)
        self.serial_text = txt

        # Stick mode toggle (both sticks vs left-stick-only mix)
        mode_frame = ttk.Frame(frm)
        mode_frame.grid(row=11, column=0, columnspan=2, pady=(4,0), sticky="w")
        ttk.Label(mode_frame, text="Stick mode:").grid(row=0, column=0, padx=(0,4))
        ttk.Radiobutton(mode_frame, text="Both sticks", value=False, variable=self._single_stick_mode).grid(row=0, column=1, padx=2)
        ttk.Radiobutton(mode_frame, text="Left stick only", value=True, variable=self._single_stick_mode).grid(row=0, column=2, padx=2)

    def _format_target_label(self) -> str:
        if self.transport_var.get() == "serial":
            return f"Target: Serial {self.serial_port_var.get()} @ {self.cfg.baud}"
        return f"Target: UDP {self.cfg.ip}:{self.cfg.port}"

    def change_target(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Change Target")
        dialog.grab_set()
        tk.Label(dialog, text="IP:").grid(row=0, column=0, padx=5, pady=5, sticky='e')
        ip_entry = tk.Entry(dialog)
        ip_entry.insert(0, self.cfg.ip)
        ip_entry.grid(row=0, column=1, padx=5, pady=5)
        tk.Label(dialog, text="Port:").grid(row=1, column=0, padx=5, pady=5, sticky='e')
        port_entry = tk.Entry(dialog)
        port_entry.insert(0, str(self.cfg.port))
        port_entry.grid(row=1, column=1, padx=5, pady=5)

        status_var = tk.StringVar(value="")
        status_lbl = tk.Label(dialog, textvariable=status_var, fg="red")
        status_lbl.grid(row=2, column=0, columnspan=2)

        def apply():
            new_ip = ip_entry.get().strip()
            try:
                new_port = int(port_entry.get().strip())
            except ValueError:
                status_var.set("Invalid port")
                return
            if not new_ip:
                status_var.set("IP required")
                return
            # Replace sender
            try:
                old = self.sender
                self.sender = UdpWheelSender(UdpTarget(new_ip, new_port), enable_checksum=self.cfg.checksum)
                old.close()
            except OSError as e:
                status_var.set(f"Socket err: {e}")
                return
            self.cfg.ip = new_ip
            self.cfg.port = new_port
            self.target_var.set(f"Target: {self.cfg.ip}:{self.cfg.port}")
            dialog.destroy()

        btn_frame = tk.Frame(dialog)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=8)
        tk.Button(btn_frame, text="Apply", command=apply).grid(row=0, column=0, padx=5)
        tk.Button(btn_frame, text="Cancel", command=dialog.destroy).grid(row=0, column=1, padx=5)
        dialog.transient(self.root)
        dialog.wait_visibility()
        ip_entry.focus_set()

    def start(self):
        if self.running:
            return
        # (Re)create sender for current transport
        try:
            if self.sender is not None:
                self.sender.close()
        except OSError:
            pass
        try:
            if self.transport_var.get() == "serial":
                port = self.serial_port_var.get().strip()
                if not port:
                    messagebox.showerror("Serial", "Please enter hub COM port (e.g. COM3)")
                    return
                self.cfg.use_serial = True
                self.cfg.serial_port = port
                self.sender = SerialWheelSender(SerialTarget(port, self.cfg.baud), enable_checksum=self.cfg.checksum)
            else:
                self.cfg.use_serial = False
                self.sender = UdpWheelSender(UdpTarget(self.cfg.ip, self.cfg.port), enable_checksum=self.cfg.checksum)
            self.target_var.set(self._format_target_label())
        except Exception as e:  # broad: surface any serial/socket error
            messagebox.showerror("Start", f"Failed to open sender: {e}")
            self.sender = None
            return
        # Start serial monitor reader if using serial
        if isinstance(self.sender, SerialWheelSender):
            self._start_serial_reader()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def stop(self):
        self.running = False
        self._stop_serial_reader()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    def _loop(self):
        period = 1.0 / max(1e-3, self.cfg.rate)
        next_time = time.perf_counter()
        while self.running:
            now = time.perf_counter()
            if now < next_time:
                time.sleep(min(0.002, next_time - now))
                continue
            next_time += period

            # Read full controller state
            reading = self.ctrl.poll()
            connected = reading.connected
            lt_trig = reading.left_trigger
            rt_trig = reading.right_trigger
            buttons = reading.buttons

            lb_pressed = bool(buttons & XINPUT_GAMEPAD_LEFT_SHOULDER)
            rb_pressed = bool(buttons & XINPUT_GAMEPAD_RIGHT_SHOULDER)
            a_pressed = bool(buttons & XINPUT_GAMEPAD_A)
            x_pressed = bool(buttons & XINPUT_GAMEPAD_X)
            b_pressed = bool(buttons & XINPUT_GAMEPAD_B)
            y_pressed = bool(buttons & XINPUT_GAMEPAD_Y)
            left_thumb_pressed = bool(buttons & XINPUT_GAMEPAD_LEFT_THUMB)
            dpad_left = bool(buttons & XINPUT_GAMEPAD_DPAD_LEFT)
            dpad_right = bool(buttons & XINPUT_GAMEPAD_DPAD_RIGHT)
            dpad_up = bool(buttons & XINPUT_GAMEPAD_DPAD_UP)
            dpad_down = bool(buttons & XINPUT_GAMEPAD_DPAD_DOWN)

            # Base stick axes
            left_x = reading.sticks.left_x
            left_y = reading.sticks.left_y
            right_x = reading.sticks.right_x
            right_y = reading.sticks.right_y

            # Store for analog stick visualization
            self.left_stick_x = left_x
            self.left_stick_y = left_y
            self.right_stick_x = right_x
            self.right_stick_y = right_y

            # Choose left/right inputs based on stick mode
            if self._single_stick_mode.get():
                # Mix left stick X/Y into tank-style L/R, normalized
                l = left_y + left_x
                r = left_y - left_x
                max_abs = max(1.0, abs(l), abs(r))
                left_raw = l / max_abs
                right_raw = r / max_abs
                # In one-stick mode, invert L/R so the mixed outputs
                # are swapped between left and right wheels.
                left_raw, right_raw = right_raw, left_raw
            else:
                left_raw = left_y
                right_raw = right_y

            if not connected:
                left = right = 0.0
                self._ramp_mode = None
                self._ramp_value = 0.0
                self._a_active_until = 0.0
                self._prev_a_pressed = False
                self._prev_x_pressed = False
                self._prev_b_pressed = False
            else:
                left = left_raw
                right = right_raw

            if self.output_invert.get():
                left, right = -left, -right

            # Adjust normal cap via X/B buttons (edge-detected)
            if x_pressed and not self._prev_x_pressed:
                self._normal_cap_int = max(self._normal_cap_min, self._normal_cap_int - 1)
            if b_pressed and not self._prev_b_pressed:
                self._normal_cap_int = min(self._normal_cap_max, self._normal_cap_int + 1)
            self._prev_x_pressed = x_pressed
            self._prev_b_pressed = b_pressed
            self._normal_cap_float = self._normal_cap_int / 127.0
            # Reflect current stick limit in the UI label
            self.stick_cap_var.set(f"Stick limit: {self._normal_cap_int}")

            # Toggle invert-output via Y button (edge-detected)
            if y_pressed and not self._prev_y_pressed:
                self.output_invert.set(not self.output_invert.get())
            self._prev_y_pressed = y_pressed

            # Toggle stick mode via left-stick click (edge-detected)
            if left_thumb_pressed and not self._prev_left_thumb_pressed:
                self._single_stick_mode.set(not self._single_stick_mode.get())
            self._prev_left_thumb_pressed = left_thumb_pressed

            # Bumper- and trigger-controlled ramps (L1/R1 and L2/R2)

            # Edge-detect A button to trigger a 5s fixed-output override
            if a_pressed and not self._prev_a_pressed:
                self._a_active_until = now + 5.0
            self._prev_a_pressed = a_pressed

            if self._ramp_mode is None:
                # Only allow ramp start if sticks near zero
                if abs(left_raw) <= self._bumper_start_eps and abs(right_raw) <= self._bumper_start_eps:
                    # First priority: triggers L2/R2 for straight motion
                    if lt_trig > self._trigger_thresh and rt_trig <= self._trigger_thresh:
                        self._ramp_mode = "trig_forward"  # L=+1, R=+1 (L2)
                        self._ramp_value = 0.0
                    elif rt_trig > self._trigger_thresh and lt_trig <= self._trigger_thresh:
                        self._ramp_mode = "trig_reverse"  # L=-1, R=-1 (R2)
                        self._ramp_value = 0.0
                    # Second priority: bumpers L1/R1 for turning
                    elif lb_pressed and not rb_pressed:
                        self._ramp_mode = "spin_left"      # L=+1, R=-1
                        self._ramp_value = 0.0
                    elif rb_pressed and not lb_pressed:
                        self._ramp_mode = "forward"       # L=-1, R=+1
            else:
                # Cancel ramp if the initiating control is no longer uniquely active
                if self._ramp_mode in ("trig_forward", "trig_reverse"):
                    if not (lt_trig > self._trigger_thresh) and not (rt_trig > self._trigger_thresh):
                        self._ramp_mode = None
                        self._ramp_value = 0.0
                elif self._ramp_mode in ("forward", "spin_left"):
                    if not (lb_pressed ^ rb_pressed):
                        self._ramp_mode = None
                        self._ramp_value = 0.0

            if self._ramp_mode is not None:
                # Advance ramp toward full magnitude. Use a longer ramp
                # for trigger-based straight motion than for bumper turns.
                if self._ramp_mode in ("trig_forward", "trig_reverse"):
                    ramp_step = period / self._trigger_ramp_time
                else:
                    ramp_step = period / self._bumper_ramp_time
                self._ramp_value = min(1.0, self._ramp_value + ramp_step)

                # Shoulder (bumper) ramps are capped at ~+/-90 in int8 space,
                # while trigger ramps go to the full +/-127.
                bumper_scaled = self._bumper_target_float * self._ramp_value
                full_scaled = self._ramp_value

                if self._ramp_mode == "forward":
                    # R1: turn mode, left wheel negative, right wheel positive
                    left = -bumper_scaled
                    right = bumper_scaled
                elif self._ramp_mode == "spin_left":
                    # Spin in place: left forward, right reverse
                    left = bumper_scaled
                    right = -bumper_scaled
                elif self._ramp_mode == "trig_forward":
                    # R2: straight forward, both wheels positive
                    left = full_scaled
                    right = full_scaled
                elif self._ramp_mode == "trig_reverse":
                    # L2: straight reverse, both wheels negative
                    left = -full_scaled
                    right = -full_scaled
            else:
                # In both stick modes, apply L/R directly from the current
                # stick readings without additional assist smoothing.
                self._prev_left, self._prev_right = left, right

                # If L/R are already very close, snap them exactly equal so
                # small human asymmetries don't cause unintended turning.
                if abs(left - right) <= self._snap_eps_float:
                    avg_lr = 0.5 * (left + right)
                    left = right = avg_lr

                # Enforce maximum difference between left and right
                diff = left - right
                if diff > self._max_diff_float:
                    left = right + self._max_diff_float
                elif diff < -self._max_diff_float:
                    right = left + self._max_diff_float

                # Final speed cap for regular driving
                left = max(-self._normal_cap_float, min(self._normal_cap_float, left))
                right = max(-self._normal_cap_float, min(self._normal_cap_float, right))

            # D-pad overrides: small fixed values while held
            if connected:
                dpad_l = rpad_l = None
                if dpad_left:
                    # Left: now sends L=+7, R=-7
                    dpad_l = 7.0 / 127.0
                    rpad_l = -7.0 / 127.0
                elif dpad_right:
                    # Right: now sends L=-7, R=+7
                    dpad_l = -7.0 / 127.0
                    rpad_l = 7.0 / 127.0
                elif dpad_up:
                    # Up: now sends L=-7, R=-7
                    dpad_l = -7.0 / 127.0
                    rpad_l = -7.0 / 127.0
                elif dpad_down:
                    # Down: now sends L=+7, R=+7
                    dpad_l = 7.0 / 127.0
                    rpad_l = 7.0 / 127.0
                if dpad_l is not None and rpad_l is not None:
                    if self.output_invert.get():
                        dpad_l, rpad_l = -dpad_l, -rpad_l
                    left, right = dpad_l, rpad_l

            # If A mode is active, override outputs with fixed small values
            if now < self._a_active_until:
                base_l = -1.0 / 127.0
                base_r = 3.0 / 127.0
                if self.output_invert.get():
                    base_l, base_r = -base_l, -base_r
                left, right = base_l, base_r

            try:
                if self.sender is not None:
                    self.last_packet = self.sender.send(left, right)
            except OSError as e:
                self.packet_var.set(f"Send error: {e}")
            self.left_val = left
            self.right_val = right
            self.last_connected = connected
            if self.verbose.get():
                print(f"L={left:+.3f} R={right:+.3f} conn={int(connected)}")
        # On stop send zeros
        try:
            if self.sender is not None:
                self.sender.send(0.0, 0.0)
        except OSError:
            pass

    def _schedule_ui_update(self):
        self._update_ui()
        self.root.after(100, self._schedule_ui_update)

    def _update_ui(self):
        # Redraw analog sticks based on latest raw stick positions
        self._draw_stick(self.left_canvas, self.left_stick_x, self.left_stick_y)
        self._draw_stick(self.right_canvas, self.right_stick_x, self.right_stick_y)
        self.left_label.config(text=f"Left Y: {self.left_val:+.3f}")
        self.right_label.config(text=f"Right Y: {self.right_val:+.3f}")
        if self.last_packet is not None:
            self.packet_var.set("Packet: " + ' '.join(f"{b:02X}" for b in self.last_packet))
        if self.last_connected:
            self.conn_var.set("Connected")
            self.conn_label.config(foreground="green")
        else:
            self.conn_var.set("Disconnected")
            self.conn_label.config(foreground="red")

    def _draw_stick(self, canvas: tk.Canvas, x: float, y: float) -> None:
        """Draw a simple analog stick: outer circle, crosshair, and thumb dot.

        x and y are in -1..1 controller space. Positive y is up on screen.
        """
        if canvas is None:
            return
        canvas.delete("all")
        try:
            w = int(canvas.winfo_width())
            h = int(canvas.winfo_height())
        except tk.TclError:
            return
        size = min(w, h)
        if size <= 0:
            return
        cx = w // 2
        cy = h // 2
        radius = max(10, size // 2 - 8)
        # Outer circle
        canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline="#aaaaaa",
        )
        # Crosshair
        canvas.create_line(cx - radius, cy, cx + radius, cy, fill="#555555")
        canvas.create_line(cx, cy - radius, cx, cy + radius, fill="#555555")

        # Thumb position (clamped to circle). Invert Y visually so pushing
        # the stick forward moves the dot downward on screen.
        max_offset = radius - 8
        px = cx + max(-1.0, min(1.0, x)) * max_offset
        py = cy + max(-1.0, min(1.0, y)) * max_offset
        canvas.create_oval(
            px - 8,
            py - 8,
            px + 8,
            py + 8,
            fill="#0e639c",
            outline="",
        )

    def _append_serial_text(self, text: str):
        if self.serial_text is None:
            return
        self.serial_text.configure(state="normal")

        # If we see a carriage return without a newline, treat it like
        # a status-line update (overwrite last line) similar to Arduino's
        # Serial Monitor behavior.
        if "\r" in text and "\n" not in text:
            content = text.split("\r")[-1]
            # If buffer is empty, just add a new line.
            if self.serial_text.compare("end-1c", "==", "1.0"):
                self.serial_text.insert("end", content + "\n")
            else:
                line_start = self.serial_text.index("end-1c linestart")
                line_end = self.serial_text.index("end-1c lineend")
                self.serial_text.delete(line_start, line_end)
                self.serial_text.insert(line_start, content)
        else:
            self.serial_text.insert("end", text)

        # Keep current view position; do not auto-scroll
        self.serial_text.configure(state="disabled")

    def _serial_reader_loop(self):
        # Runs in background thread, reads lines from hub over serial.
        while self._serial_reader_running:
            sender = self.sender
            if not isinstance(sender, SerialWheelSender):
                break
            ser = sender.serial
            if ser is None:
                break
            try:
                line = ser.readline()
            except Exception:
                # Stop on serial errors
                break
            if not line:
                # Avoid busy loop; small sleep
                time.sleep(0.01)
                continue
            try:
                decoded = line.decode("utf-8", errors="replace")
            except Exception:
                decoded = repr(line) + "\n"
            # marshal UI update to main thread
            self.root.after(0, self._append_serial_text, decoded)

        self._serial_reader_running = False

    def _start_serial_reader(self):
        if self._serial_reader_running:
            return
        self._serial_reader_running = True
        self._serial_reader_thread = threading.Thread(target=self._serial_reader_loop, daemon=True)
        self._serial_reader_thread.start()

    def _stop_serial_reader(self):
        self._serial_reader_running = False

    def on_close(self):
        self.stop()
        if self.sender is not None:
            self.sender.close()
        self.root.destroy()


def launch():
    root = tk.Tk()
    app = App(root, GuiConfig())
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":  # pragma: no cover
    launch()
