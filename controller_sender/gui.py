"""Tkinter GUI to visualize stick Y values, connection, and send over UDP."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
from dataclasses import dataclass
from .udp_sender import UdpWheelSender, UdpTarget
from .xinput import XInputController

@dataclass
class GuiConfig:
    ip: str = "192.168.0.23"
    port: int = 4210
    rate: float = 30.0
    invert_y: bool = True
    checksum: bool = True

class App:
    def __init__(self, root: tk.Tk, cfg: GuiConfig):
        self.root = root
        self.cfg = cfg
        self.root.title("Xbox Wheel Sender")
        self.sender = UdpWheelSender(UdpTarget(cfg.ip, cfg.port), enable_checksum=cfg.checksum)
        self.ctrl = XInputController(0, invert_y=cfg.invert_y)

        self.running = False
        self.thread: threading.Thread | None = None
        self.last_packet: bytes | None = None
        self.last_connected = False
        self.left_val = 0.0
        self.right_val = 0.0
        # Runtime toggles
        self.output_invert = tk.BooleanVar(value=False)
        self.verbose = tk.BooleanVar(value=False)

        self._build_ui()
        self._schedule_ui_update()

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Connection
        self.conn_var = tk.StringVar(value="Disconnected")
        self.conn_label = ttk.Label(frm, textvariable=self.conn_var, font=("Segoe UI", 12, "bold"))
        self.conn_label.grid(row=0, column=0, columnspan=2, pady=(0,8))

        # Bars
        self.left_bar = ttk.Progressbar(frm, orient="vertical", length=200, mode="determinate", maximum=100, value=0)
        self.right_bar = ttk.Progressbar(frm, orient="vertical", length=200, mode="determinate", maximum=100, value=0)
        self.left_bar.grid(row=1, column=0, padx=20)
        self.right_bar.grid(row=1, column=1, padx=20)

        self.left_label = ttk.Label(frm, text="Left Y: 0.000")
        self.right_label = ttk.Label(frm, text="Right Y: 0.000")
        self.left_label.grid(row=2, column=0, pady=5)
        self.right_label.grid(row=2, column=1, pady=5)

        # Packet label
        self.packet_var = tk.StringVar(value="Packet: -")
        ttk.Label(frm, textvariable=self.packet_var).grid(row=3, column=0, columnspan=2, pady=(5,5))

        # Controls
        self.start_btn = ttk.Button(frm, text="Start", command=self.start)
        self.stop_btn = ttk.Button(frm, text="Stop", command=self.stop, state="disabled")
        self.start_btn.grid(row=4, column=0, pady=10)
        self.stop_btn.grid(row=4, column=1, pady=10)

        # Toggles
        ttk.Checkbutton(frm, text="Invert Output", variable=self.output_invert).grid(row=6, column=0, pady=(4,0))
        ttk.Checkbutton(frm, text="Verbose", variable=self.verbose).grid(row=6, column=1, pady=(4,0))

        # Footer target + change button
        self.target_var = tk.StringVar(value=f"Target: {self.cfg.ip}:{self.cfg.port}")
        ttk.Label(frm, textvariable=self.target_var).grid(row=5, column=0, pady=(5,0))
        ttk.Button(frm, text="Change IP", command=self.change_target).grid(row=5, column=1, pady=(5,0))

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
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def stop(self):
        self.running = False
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
            left, right, connected = self.ctrl.get_left_right_y()
            if self.output_invert.get():
                left, right = -left, -right
            if not connected:
                left = right = 0.0
            try:
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
            self.sender.send(0.0, 0.0)
        except OSError:
            pass

    def _schedule_ui_update(self):
        self._update_ui()
        self.root.after(100, self._schedule_ui_update)

    def _update_ui(self):
        # Update bars (map -1..1 to 0..100)
        l = (self.left_val + 1) * 50
        r = (self.right_val + 1) * 50
        self.left_bar['value'] = l
        self.right_bar['value'] = r
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

    def on_close(self):
        self.stop()
        self.sender.close()
        self.root.destroy()


def launch():
    root = tk.Tk()
    app = App(root, GuiConfig())
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":  # pragma: no cover
    launch()
