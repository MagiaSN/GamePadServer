[English](README.md) | [中文](docs/i18n/zh/README.md)

# GamePadServer

GamePadServer is a game controller emulation service that runs on a Linux server (Raspberry Pi). It exposes a unified REST / WebSocket API, receives controller commands from external programs, and translates them into controller signals sent to game consoles.

**Core idea: one API, multiple backends.** Callers don't need to know whether the target console uses Bluetooth or USB -- they just send "press A" or "tilt left stick" through the same API.

## Supported Platforms

| Console | Connection | Emulated Controller | Status |
|---------|-----------|-------------------|--------|
| Nintendo Switch | Bluetooth | Pro Controller | Implemented |
| PlayStation 4 | USB | DualShock 4 | Planned |
| PlayStation 5 | USB | DualSense | Planned |
| Xbox One / Series | USB | Xbox Controller | Planned |

## Quick Start

### Requirements

- **Linux** (Raspberry Pi 4 recommended) -- The Switch backend uses a self-implemented Bluetooth HID stack (L2CAP + Switch Pro Controller protocol) via BlueZ D-Bus to emulate a virtual controller. **macOS and Windows are not supported.** The server can start on non-Linux systems, but creating controller connections will fail.
- Python 3.10+
- Bluetooth adapter (for the Switch backend)
- Root privileges (required for Bluetooth HID operations)

### Installation

```bash
git clone <repo-url> GamePadServer
cd GamePadServer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Host Setup

New machines require one-time system configuration before running:

```bash
sudo ./deploy/setup-host.sh
```

This installs the bluetoothd systemd override and patches `/etc/bluetooth/main.conf`. Idempotent -- safe to run multiple times.

### Running

```bash
sudo .venv/bin/python -m gamepadserver
```

The server listens on `0.0.0.0:8080` by default. Configure via environment variables:

```bash
GAMEPAD_HOST=127.0.0.1 GAMEPAD_PORT=9090 sudo -E .venv/bin/python -m gamepadserver
```

### Test Page

After starting the server, visit `http://<host>:8080/` to open the interactive test page, which supports:

- Platform switching (Switch / PS4 / PS5 / Xbox) with auto-adapting button labels
- Click buttons to send commands
- Drag virtual sticks for real-time control
- Controller connection status display

## API Usage

Full API docs are available at `http://<host>:8080/docs` (Swagger UI) after starting the server.

### 1. Create and Connect a Controller

For Switch, first enter "Change Grip/Order" (Settings > Controllers > Change Grip/Order), then call:

```bash
curl -X POST http://localhost:8080/api/v1/controllers \
  -H "Content-Type: application/json" \
  -d '{"platform": "switch"}'
```

Response:

```json
{"id": 0, "platform": "switch", "state": "connecting", "created_at": "..."}
```

### 2. Check Connection Status

```bash
curl http://localhost:8080/api/v1/controllers/0
```

Wait for `state` to become `connected` before sending commands.

### 3. Button Actions

```bash
# Press A (auto-release after 0.1s)
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["A"], "action": "press", "duration": 0.1}'

# Hold B
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["B"], "action": "down"}'

# Release B
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["B"], "action": "up"}'

# Press multiple buttons simultaneously
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["L", "R"], "action": "press"}'
```

### 4. Stick Actions

```bash
# Push left stick to upper-right
curl -X POST http://localhost:8080/api/v1/controllers/0/stick \
  -H "Content-Type: application/json" \
  -d '{"stick": "left", "x": 100, "y": 100}'

# Return to center
curl -X POST http://localhost:8080/api/v1/controllers/0/stick \
  -H "Content-Type: application/json" \
  -d '{"stick": "left", "x": 0, "y": 0}'
```

Stick x/y range is `-100` to `100`. `0` is center. The position persists until the next call.

### 5. WebSocket Real-Time Input

For low-latency continuous input (e.g., real-time control):

```javascript
const ws = new WebSocket("ws://localhost:8080/ws/controllers/0/input");

// Send a full controller state frame
ws.send(JSON.stringify({
  buttons: { A: true },
  left_stick: { x: 50, y: 0 },
  right_stick: { x: 0, y: 0 }
}));

// Receive acknowledgment
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  // { "type": "ack", "timestamp": 1681380000000 }
};
```

### 6. Disconnect Controller

```bash
curl -X DELETE http://localhost:8080/api/v1/controllers/0
```

## Button Enums

Button names follow hardware labels, not physical positions.

### Switch / Xbox

`A` `B` `X` `Y` -- same names but different physical positions (Switch A is on the right, Xbox A is on the bottom)

### PlayStation

`CROSS` `CIRCLE` `SQUARE` `TRIANGLE` -- PS platforms do not accept A/B/X/Y

### Cross-Platform

`L` `R` `ZL` `ZR` `PLUS` `MINUS` `HOME` `CAPTURE` `DPAD_UP` `DPAD_DOWN` `DPAD_LEFT` `DPAD_RIGHT` `L_STICK` `R_STICK`

## Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Project Structure

```
gamepadserver/
├── __main__.py              # Entry point (uvicorn)
├── app.py                   # FastAPI app + ControllerManager singleton
├── config.py                # Settings via GAMEPAD_* env vars
├── api/
│   ├── controllers.py       # /api/v1/controllers REST endpoints
│   ├── system.py            # /health, /api/v1/system/adapters
│   └── ws.py                # WebSocket /ws/controllers/{id}/input
├── core/
│   ├── backend.py           # GamepadBackend ABC
│   ├── manager.py           # ControllerManager lifecycle
│   └── models.py            # Enums, Pydantic models, validation
├── bluetooth/               # Self-implemented Bluetooth HID stack
│   ├── adapter.py           # BlueZ adapter config (D-Bus + hciconfig)
│   ├── sdp.py               # SDP registration (sdptool / D-Bus dual path)
│   ├── l2cap.py             # L2CAP socket management (PSM 17+19)
│   ├── switch_protocol.py   # Switch HID handshake state machine
│   ├── switch_report.py     # 50-byte input report encode/decode
│   └── constants.py         # Protocol constants, button maps, SPI templates
├── backends/
│   └── switch.py            # SwitchBackend (uses bluetooth/ module)
└── static/
    └── index.html           # Interactive test page
```

## Technical Documentation

See [SPEC.md](SPEC.md).

## License

MIT
