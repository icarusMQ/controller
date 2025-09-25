# Xbox Controller UDP Wheel Sender

Sends Y-axis (forward/back) of both analog sticks of an Xbox controller to a robot via UDP in a compact 2- or 3-byte packet. Includes:

* Headless CLI sender (`controller_sender.main`)
* Tkinter GUI with live bars & connection indicator (`controller_sender.gui`)
* Simple UDP robot simulator (`robot_sim.py`) for local testing

## Packet Format
```
With checksum (default): [ leftY_int8 , rightY_int8 , checksum ]
Without checksum:        [ leftY_int8 , rightY_int8 ]
checksum = leftY_int8 XOR rightY_int8 (unsigned byte)
```
Value mapping: float range -1.0..1.0  -> signed int8 -127..127 (0 is stop).

## Requirements
* Windows (uses XInput via ctypes)
* Python 3.9+ (should work on 3.8+ but untested)
* Xbox controller (wired or wireless / Bluetooth) recognized by Windows

No third-party dependencies are required.

## Installation
Clone or copy the folder then (optional) create a virtual environment:
```
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt  # (empty placeholder)
```

## Quick Start (GUI)
```
python run.py --gui
```
You will see:
* Connection status (Connected / Disconnected)
* Two vertical bars showing left and right stick Y values
* Hex dump of the last packet

Click Start to begin sending at the default 30 Hz, Stop to halt (sends zero packet on stop).

## Quick Start (CLI / Headless)
```
python run.py --cli -- --ip 192.168.0.23 --port 4210 --rate 40 --print
```
Arguments after `--` are passed to the underlying CLI program.

CLI options:
```
--ip IP                Target IP (default 192.168.0.23)
--port PORT            Target UDP port (default 4210)
--rate HZ              Send frequency (default 30.0)
--no-checksum          Omit checksum byte
--controller INDEX     XInput controller index (0-3)
--no-invert-y          Keep native Y sign (default inverts so forward is +)
--duration SECONDS     Run fixed time then exit
--print                Print each sent value pair
--stop-on-disconnect   Exit if controller disconnects instead of sending zeros
```

## Robot Simulator (Receiver Test)
Run this on the PC to verify packets:
```
python robot_sim.py --port 4210
```
Then run the GUI or CLI sender. You should see lines like:
```
127.0.0.1 l=+0.000 r=-0.000 bytes=000000 checksum=OK
```

## Integration on Robot (ESP Example)
On the robot side read 2 or 3 raw bytes from UDP. If 3 bytes, validate `b2 == b0 XOR b1`. Convert each byte to signed int8 then divide by 127 to get -1..1 float and map to wheel speeds.

## Safety / Failsafe
On exit or stop the sender transmits a zero packet (both wheels stop). The robot should also implement its own timeout (e.g., if no packet for >300 ms -> stop).

## Customization Ideas
* Acceleration limiting / ramping
* Exponential curve for finer low-speed control
* Button-based quick stop or boost
* Telemetry (robot -> PC) overlay

## Troubleshooting
| Issue | Resolution |
|-------|------------|
| "Could not load any XInput DLL" | Ensure you are on Windows and have standard controller drivers (try plugging controller in). |
| Always Disconnected | Check controller index (try `--controller 0`, unplug/replug). |
| No packets on robot | Verify firewall rules; try `robot_sim.py` locally. |
| Values inverted | Use `--no-invert-y` in CLI or adjust code in GUI config. |
| Jittery values | Reduce send rate or add smoothing (not yet implemented). |

## Code Overview
| File | Purpose |
|------|---------|
| `controller_sender/udp_sender.py` | Packet building and UDP transmit |
| `controller_sender/xinput.py` | Raw controller polling via ctypes/XInput |
| `controller_sender/main.py` | CLI loop logic |
| `controller_sender/gui.py` | Tkinter GUI application |
| `robot_sim.py` | Local UDP receiver for testing |
| `run.py` | Convenience launcher (GUI default) |

## License
Public domain / Unlicense – do what you want. Attribution appreciated.

## Disclaimer
Use responsibly. Always test with wheels off the ground first.
