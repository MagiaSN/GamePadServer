# GamePadServer Technical Specification

## 1. Project Overview

GamePadServer is a game controller emulation service deployed on a Linux server (Raspberry Pi). It exposes a unified API to receive controller operation commands from external programs, and internally selects the appropriate underlying implementation (Bluetooth / USB) based on the target console platform, converting commands into controller signals sent to the game console.

**Core philosophy: unified interface, multiple backend implementations.** Callers do not need to know what the target console is or whether Bluetooth or USB is used -- they simply send commands like "press A" or "tilt left stick" through a single API.

### Supported Target Consoles

| Console | Connection | Emulated Controller | Authentication |
|---------|-----------|-------------------|----------------|
| Nintendo Switch | Bluetooth | Pro Controller | None |
| PlayStation 4 | USB | DualShock 4 | RSA-2048 signature, requires auth device passthrough |
| PlayStation 5 | USB | DualSense | Stricter authentication, requires specific adapter (experimental) |
| Xbox One / Series | USB | Xbox Controller | GIP protocol encrypted handshake, requires auth device passthrough |

### Technology Stack

- **Language:** Python 3.10+
- **Web framework:** FastAPI (async, auto-generated OpenAPI docs)
- **Communication protocols:** REST (controller management + single-shot actions) + WebSocket (real-time input stream)
- **Switch backend:** Self-implemented Bluetooth HID stack within the project (L2CAP + Switch Pro Controller protocol)
- **PS / Xbox backend:** Linux USB Gadget ConfigFS + `/dev/hidg0`

---

## 2. System Architecture

### 2.1 Overall Architecture

```
    External Callers (App / Script / Other Services)
         |
         |  HTTP REST / WebSocket
         v
+---------------------------------------------+
|              GamePadServer                   |
|                                             |
|  +---------------------------------------+  |
|  |           API Layer (FastAPI)          |  |
|  |  REST: Controller management +        |  |
|  |        single-shot actions            |  |
|  |  WebSocket: Real-time input stream    |  |
|  +--------------+------------------------+  |
|                 |                            |
|  +--------------v------------------------+  |
|  |        Controller Manager             |  |
|  |  Instance management, state tracking, |  |
|  |  request routing                      |  |
|  +--+----------+--+----------+--+--------+  |
|     |              |              |          |
|  +--v----+   +-----v------+  +---v------+   |
|  |Switch |   |PlayStation |  |   Xbox   |   |
|  |Backend|   |  Backend   |  | Backend  |   |
|  | (BT)  |   |(USB Gadget)|  |(USB Gad.)|   |
|  +--+----+   +-----+------+  +---+------+   |
|     |              |              |          |
|  +--v----------+   |              |          |
|  |  bluetooth/ |   |              |          |
|  |  Adapter    |   |              |          |
|  |  management |   |              |          |
|  |  SDP reg.   |   |              |          |
|  |  L2CAP conn.|   |              |          |
|  |  Switch     |   |              |          |
|  |  protocol   |   |              |          |
|  |  Report     |   |              |          |
|  |  encode/dec.|   |              |          |
|  +--+----------+   |              |          |
+-----+----------+---+----------+--+----------+
      |              |              |
      v              v              v
  Bluetooth HID  USB /dev/hidg0  USB /dev/hidg0
  (BlueZ/D-Bus)  + auth passthru + auth passthru
      v              v              v
   Switch          PS4/PS5      Xbox One/Series
```

### 2.2 Strategy Pattern

All backends implement the same `GamepadBackend` abstract interface. The `Controller Manager` instantiates the appropriate backend based on the `platform` parameter specified when creating a controller.

### 2.3 Process Model

- **Main process:** FastAPI application (uvicorn), handling HTTP/WebSocket requests
- **Blocking operations:** Blocking calls such as Bluetooth L2CAP socket bind/listen/accept are delegated to a thread pool via `asyncio.get_running_loop().run_in_executor()`, keeping the main event loop non-blocking
- **State management:** Each SwitchBackend instance maintains its own connection state internally; the Controller Manager reads it through async methods

---

## 3. Supported Platforms and Connection Methods

### 3.1 Nintendo Switch (Bluetooth)

- **Underlying implementation:** Self-implemented Bluetooth HID stack within the project (`gamepadserver/bluetooth/` module)
- **Connection method:** Bluetooth L2CAP (PSM 17 control channel + PSM 19 interrupt channel)
- **Emulated device:** Switch Pro Controller
- **Authentication:** None required
- **Pairing flow:** Switch enters "Change Grip/Order" menu -> Server initiates Bluetooth connection -> Handshake completes
- **Simultaneous connections:** 1 controller per Bluetooth adapter; multiple adapters enable multiple controllers
- **System dependencies:** BlueZ Bluetooth stack, D-Bus (via dbus-python or subprocess calls to hciconfig/sdptool)
- **Protocol details:**
  - Device class set to `0x002508` (HID Gamepad)
  - Device name set to `"Pro Controller"`
  - Must handle BlueZ input plugin interception of L2CAP HID ports (via sdptool SDP record registration or removing the `Role: "server"` parameter from D-Bus RegisterProfile)
  - After connection, must complete the Switch HID handshake: respond to subcommand requests (device info, SPI Flash reads, calibration data, etc.), approximately 60-120 packets
  - Input report is 50 bytes, containing buttons (3 bytes), sticks (3 bytes each, 12-bit encoding), IMU data
  - Timer tick frequency ~4.96ms
  - Must implement SPI Flash read responses (serial number, colors, calibration data)

### 3.2 PlayStation 4 (USB Gadget)

- **Underlying implementation:** Linux USB Gadget ConfigFS
- **Connection method:** USB wired (Raspberry Pi OTG port -> PS4)
- **Emulated device:** DualShock 4 (VID/PID must match Sony official values)
- **Authentication:** RSA-2048 + SHA-256 PSS signature, re-authentication every ~30 seconds
- **Auth passthrough:** Requires a genuine DS4 or MagicBoots adapter connected via USB Host port
- **Consequence of failed auth:** Disconnected after 8 minutes
- **HID report:** Must implement the DS4 HID Report Descriptor, including buttons, sticks, touchpad, IMU data

### 3.3 PlayStation 5 (USB Gadget)

- **Underlying implementation:** Same as PS4, but with protocol differences
- **Emulated device:** DualSense
- **Authentication:** Stricter than PS4; standard DualSense cannot be used for third-party passthrough
- **Auth passthrough:** Requires a specific PS5 adapter (e.g., Brook Wingman FGC)
- **Limitations:** Open-source solutions for PS5 native game authentication are not yet mature; PS4 games running on PS5 can use PS4 auth devices
- **Status:** Experimental support

### 3.4 Xbox One / Series (USB Gadget)

- **Underlying implementation:** Linux USB Gadget ConfigFS
- **Connection method:** USB wired (Raspberry Pi OTG port -> Xbox)
- **Emulated device:** Xbox One Controller
- **Authentication:** GIP protocol encrypted handshake, one-time authentication at connection
- **Auth passthrough:** Requires MagicBoots for Xbox One or Magic-X adapter + genuine controller
- **Consequence of failed auth:** Connection immediately rejected
- **Protocol:** Uses GIP (Gaming Input Protocol) instead of standard HID, requires additional adaptation

---

## 4. Hardware Requirements

### 4.1 Raspberry Pi

| Model | USB Gadget | Bluetooth | Recommendation | Notes |
|-------|:----------:|:---------:|:--------------:|-------|
| **Pi 4 Model B** | USB-C supported | Built-in | Recommended | USB-C to console, USB-A ports for auth devices, sufficient performance |
| **Pi Zero 2 W** | micro-USB supported | Built-in | Usable | Requires USB Hub to split Host/OTG, adequate performance |
| **Pi 5** | Not supported | Built-in | Not usable | USB-C port does not support Gadget mode |
| **Pi 3** | Not supported | Built-in | Not usable | No OTG support |

**Recommended configuration: Raspberry Pi 4 Model B (4GB+)**

### 4.2 Additional Hardware for Switch

- Bluetooth adapter: Built-in Raspberry Pi Bluetooth is sufficient; multiple controllers require additional USB Bluetooth adapters

### 4.3 Additional Hardware for PS / Xbox

- USB-C to USB-A/C cable (connecting Raspberry Pi to console)
- Authentication device (select based on target console):

| Target Console | Auth Device | Reference Price |
|---------------|-------------|-----------------|
| PS4 | MagicBoots for PS4 or genuine DualShock 4 | $15-30 |
| PS5 (PS4 games) | Same as PS4 auth devices | $15-30 |
| PS5 (PS5 native) | Brook Wingman FGC or Besavior P5General | $40+ |
| Xbox One/Series | MagicBoots for Xbox One + genuine Xbox Controller | $15-30 + controller |

---

## 5. API Specification

### 5.1 Design Principles

- **REST** for controller lifecycle management and single-shot actions (automation scripts, debugging -- callable via curl)
- **WebSocket** for real-time control scenarios (push complete controller state per frame, low-latency continuous input)
- Both interfaces use the same button enum values (see 5.5); data structures differ due to different semantics

### 5.2 RESTful Endpoints

Base path: `/api/v1`

#### Controller Management

```
POST   /controllers              Create controller instance and begin connection
GET    /controllers              List all controller instances
GET    /controllers/{id}         Query controller state
DELETE /controllers/{id}         Disconnect and destroy controller instance
```

#### Controller Actions

```
POST   /controllers/{id}/buttons     Press/release buttons
POST   /controllers/{id}/stick       Move stick
```

#### System Information

```
GET    /system/adapters          List available Bluetooth adapters
GET    /system/usb-gadgets       List USB Gadget device status
GET    /health                   Health check
```

### 5.3 WebSocket Endpoint

```
WS     /ws/controllers/{id}/input    Real-time input stream
```

### 5.4 Request/Response Formats

#### Create Controller

```
POST /api/v1/controllers

Request:
{
  "platform": "switch"            // "switch" | "ps4" | "ps5" | "xbox"
}

Response: 201 Created
{
  "id": 0,
  "platform": "switch",
  "state": "connecting",          // "connecting" | "connected" | "disconnected" | "error"
  "created_at": "2026-04-13T12:00:00Z"
}
```

Each `platform` maps to a fixed controller type (switch -> Pro Controller, ps4 -> DualShock 4, ps5 -> DualSense, xbox -> Xbox Controller); no additional specification is needed.

#### Query Controller State

```
GET /api/v1/controllers/{id}

Response: 200 OK
{
  "id": 0,
  "platform": "switch",
  "state": "connected",
  "created_at": "2026-04-13T12:00:00Z"
}
```

#### Button Actions

```
POST /api/v1/controllers/{id}/buttons

Request:
{
  "buttons": ["A", "B"],          // Button enum values, see 5.5
  "action": "press",              // "press" (press+release) | "down" (hold) | "up" (release)
  "duration": 0.1                 // Seconds, only applies when action is "press"
}

Response: 200 OK
{
  "status": "ok"
}
```

#### Stick Actions

```
POST /api/v1/controllers/{id}/stick

Request:
{
  "stick": "left",                // "left" | "right"
  "x": 50,                       // -100 to 100, 0 is center
  "y": 100                       // -100 to 100, 0 is center
}

Response: 200 OK
{
  "status": "ok"
}
```

Once set, the stick maintains its position until overridden by the next call or reset to center with `{"stick": "left", "x": 0, "y": 0}`.

#### WebSocket Real-time Input

```
WS /ws/controllers/{id}/input

// Client -> Server: complete controller state frame (Switch / Xbox example)
{
  "buttons": {
    "A": true, "B": false, "X": false, "Y": false,
    "L": false, "R": false, "ZL": false, "ZR": false,
    "PLUS": false, "MINUS": false,
    "DPAD_UP": false, "DPAD_DOWN": false,
    "DPAD_LEFT": false, "DPAD_RIGHT": false,
    "L_STICK": false, "R_STICK": false,
    "HOME": false, "CAPTURE": false
  },
  "left_stick": {"x": 0, "y": 0},
  "right_stick": {"x": 0, "y": 0}
}

// Client -> Server: complete controller state frame (PS4 / PS5 example)
{
  "buttons": {
    "CROSS": true, "CIRCLE": false, "SQUARE": false, "TRIANGLE": false,
    "L": false, "R": false, "ZL": false, "ZR": false,
    "PLUS": false, "MINUS": false,
    "DPAD_UP": false, "DPAD_DOWN": false,
    "DPAD_LEFT": false, "DPAD_RIGHT": false,
    "L_STICK": false, "R_STICK": false,
    "HOME": false, "CAPTURE": false
  },
  "left_stick": {"x": 0, "y": 0},
  "right_stick": {"x": 0, "y": 0}
}

// Server -> Client: acknowledgement/error
{"type": "ack", "timestamp": 1681380000000}
{"type": "error", "message": "controller disconnected"}
```

In WebSocket state frames, buttons not included are treated as not pressed (`false`); the client may send only changed buttons.

### 5.5 Button Enums

Button enum values follow hardware labels, not physical positions. Callers send commands using the actual button names of the target platform.

#### Switch / Xbox Shared Buttons

Switch and Xbox use the same enum names, but the physical positions differ:

| Enum Value | Switch Hardware Label | Switch Physical Position | Xbox Hardware Label | Xbox Physical Position |
|------------|----------------------|-------------------------|--------------------|-----------------------|
| `A` | A | Right | A | Bottom |
| `B` | B | Bottom | B | Right |
| `X` | X | Top | X | Left |
| `Y` | Y | Left | Y | Top |

#### PlayStation Exclusive Buttons

PS platforms use independent enum values and do not accept A/B/X/Y:

| Enum Value | Hardware Label | Physical Position |
|------------|---------------|-------------------|
| `CROSS` | x | Bottom |
| `CIRCLE` | o | Right |
| `SQUARE` | [] | Left |
| `TRIANGLE` | /\ | Top |

#### Cross-platform Shared Buttons

The following buttons use the same enum values across all platforms:

| Enum Value | Switch | PS4/PS5 | Xbox | Description |
|------------|--------|---------|------|-------------|
| `L` | L | L1 | LB | Left shoulder |
| `R` | R | R1 | RB | Right shoulder |
| `ZL` | ZL | L2 | LT | Left trigger |
| `ZR` | ZR | R2 | RT | Right trigger |
| `PLUS` | + | Options | Menu | Start/Options |
| `MINUS` | - | Share/Create | View | Select/Share |
| `HOME` | Home | PS | Xbox | Home button |
| `CAPTURE` | Capture | Touchpad Press | Share | Capture/Touchpad |
| `DPAD_UP` | Up | Up | Up | D-pad up |
| `DPAD_DOWN` | Down | Down | Down | D-pad down |
| `DPAD_LEFT` | Left | Left | Left | D-pad left |
| `DPAD_RIGHT` | Right | Right | Right | D-pad right |
| `L_STICK` | L Stick Press | L3 | LS | Left stick press |
| `R_STICK` | R Stick Press | R3 | RS | Right stick press |

#### Sticks

Unified across all platforms: `left` / `right`, x/y range `-100 to 100` (integers), `0` is center position. The backend is responsible for converting to each platform's actual encoding.

#### Validation Rules

- Switch / Xbox platforms: face buttons must use `A` / `B` / `X` / `Y`; sending PS enum values like `CROSS` will return a 400 error
- PS platforms: face buttons must use `CROSS` / `CIRCLE` / `SQUARE` / `TRIANGLE`; sending enum values like `A` will return a 400 error
- Shared buttons (`L` / `R` / `DPAD_UP`, etc.) are valid on all platforms

---

## 6. Backend Interface Definition

### 6.1 Abstract Interface

```python
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass

class Platform(str, Enum):
    SWITCH = "switch"
    PS4 = "ps4"
    PS5 = "ps5"
    XBOX = "xbox"

class ControllerState(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"

@dataclass
class InputState:
    """Complete controller input state frame"""
    buttons: dict[str, bool]       # Button enum value -> pressed state
    left_stick: tuple[int, int]    # (x, y), range -100 to 100
    right_stick: tuple[int, int]   # (x, y), range -100 to 100

class GamepadBackend(ABC):

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection with the console"""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and release resources"""

    @abstractmethod
    async def get_state(self) -> ControllerState:
        """Get current connection state"""

    @abstractmethod
    async def press_buttons(self, buttons: list[str], duration: float = 0.1) -> None:
        """Press and release buttons"""

    @abstractmethod
    async def hold_buttons(self, buttons: list[str]) -> None:
        """Hold buttons down"""

    @abstractmethod
    async def release_buttons(self, buttons: list[str]) -> None:
        """Release buttons"""

    @abstractmethod
    async def set_stick(self, stick: str, x: int, y: int) -> None:
        """Set stick position, maintained until next call"""

    @abstractmethod
    async def send_input(self, state: InputState) -> None:
        """Send complete controller input state frame (for WebSocket real-time input)"""
```

### 6.2 SwitchBackend (Self-implemented Bluetooth HID)

```python
class SwitchBackend(GamepadBackend):
    """
    Switch controller backend based on the project's self-implemented Bluetooth HID stack.

    Does not depend on external Bluetooth controller emulation libraries such as nxbt;
    directly operates BlueZ/D-Bus and L2CAP sockets.

    Implementation details:
    - Internally configures the Bluetooth adapter via the bluetooth.adapter module
      (D-Bus for Powered/Discoverable, hciconfig for class 0x002508 and name "Pro Controller")
    - Internally registers HID SDP service records via the bluetooth.sdp module
      (sdptool or D-Bus dual path)
    - connect() creates L2CAP sockets (PSM 17 + 19), bind -> listen -> accept to wait
      for the Switch to connect, then completes the HID handshake via
      bluetooth.switch_protocol (responding to subcommands, SPI reads, etc.)
    - press_buttons() / set_stick() / send_input() encode button/stick state into
      50-byte HID input reports via bluetooth.switch_report, written to the interrupt
      channel socket
    - disconnect() closes L2CAP sockets and cleans up adapter state
    - Blocking operations (socket accept, etc.) are delegated to the thread pool
      via run_in_executor()
    """
```

### 6.3 USBGadgetBackend (PS4 / Xbox)

```python
class USBGadgetBackend(GamepadBackend):
    """
    PS/Xbox controller backend based on Linux USB Gadget.

    Implementation details:
    - connect() creates a USB Gadget device via ConfigFS, sets the HID Report Descriptor
    - Opens the /dev/hidg0 file handle
    - press_buttons() / set_stick() / send_input() construct HID reports and write to /dev/hidg0
    - Different platforms use different HID Report Descriptors and report formats
    - PS4 requires an additional auth passthrough thread
    - disconnect() closes the file handle and removes the ConfigFS configuration
    """
```

USB Gadget initialization flow:

```bash
# 1. Load kernel modules
modprobe dwc2
modprobe libcomposite

# 2. Create gadget
cd /sys/kernel/config/usb_gadget
mkdir gamepad && cd gamepad

# 3. Set VID/PID (PS4 example, must match Hori or other licensed manufacturer)
echo 0x0f0d > idVendor
echo 0x00c1 > idProduct

# 4. Create HID function
mkdir -p functions/hid.usb0
echo 0 > functions/hid.usb0/protocol
echo 0 > functions/hid.usb0/subclass
echo 64 > functions/hid.usb0/report_length
# Write binary HID Report Descriptor
cat ds4_descriptor.bin > functions/hid.usb0/report_desc

# 5. Bind to UDC
ls /sys/class/udc > UDC
```

### 6.4 Backend Comparison

| Feature | SwitchBackend | USBGadgetBackend (PS4) | USBGadgetBackend (Xbox) |
|---------|---------------|----------------------|------------------------|
| Connection | Bluetooth L2CAP | USB /dev/hidg0 | USB /dev/hidg0 |
| Protocol | Switch Pro Controller | DualShock 4 HID | GIP (non-standard HID) |
| Authentication | None | RSA-2048 passthrough | GIP encrypted handshake passthrough |
| Multiple controllers | Multiple BT adapters | Typically 1 | Typically 1 |
| Input latency | ~8ms (Bluetooth) | <1ms (USB) | <1ms (USB) |
| Underlying dependency | BlueZ + D-Bus (system-level) | Linux kernel ConfigFS | Linux kernel ConfigFS |
| Raspberry Pi required | No | Yes | Yes |

---

## 7. Authentication Passthrough Mechanism

### 7.1 PS4 Authentication Flow

```
PS4 Console               Raspberry Pi (USB Gadget)      Genuine DS4 / Adapter
    |                           |                           |
    |  HID SET_REPORT (0xF0)    |                           |
    |  256-byte nonce challenge |                           |
    | ------------------------->|                           |
    |                           |  Forward challenge        |
    |                           | ------------------------->|
    |                           |                           |
    |                           |  Return signature         |
    |                           |  (signature + cert chain) |
    |                           | <-------------------------|
    |  HID GET_REPORT (0xF1)    |                           |
    | <-------------------------|                           |
    |  1040-byte signed response|                           |
    |                           |                           |
    |       ... repeat every ~30 seconds ...                |
```

Implementation details:
- When the USB Gadget receives `SET_REPORT (0xF0)`, it identifies it as an auth request
- Forwards the challenge to the auth device via libusb / hidapi through the USB Host port
- Reads the auth device's signed response
- Wraps the response as `GET_REPORT (0xF1)` and sends it back to the console
- A separate thread/coroutine must be started for continuous auth polling

### 7.2 Xbox Authentication Flow

Xbox authentication occurs at connection time (one-time), using the GIP protocol:

- Console sends an authentication challenge
- Passed through to the auth adapter (MagicBoots + genuine controller)
- Adapter completes the encrypted handshake
- Subsequent input data is sent by the Raspberry Pi

### 7.3 Auth Device Connection

The Raspberry Pi must simultaneously act as:
- **USB Device** (OTG port) -> connects to the game console, emulating a controller
- **USB Host** (USB-A port) -> connects to the auth device, reading/writing via libusb/hidapi

The Pi 4 natively supports this configuration (USB-C as device, USB-A as host).

---

## 8. Bluetooth HID Stack Design

This section describes the project's self-implemented Bluetooth HID stack, which replaces the nxbt external dependency.

### 8.1 Design Motivation

nxbt (PyPI v0.1.4, last released 2021-10-03) has been unmaintained for over 3 years. GitHub master has minor fixes from 2023 (webapp, button mapping), but is also no longer maintained. Additionally:
- Incompatible with Python 3.12+ (depends on the removed `imp` module)
- BlueZ version upgrades introduced L2CAP port interception issues, requiring patches to work
- Multiprocessing architecture conflicts with this project's asyncio model

The self-implemented Bluetooth stack only needs to cover approximately 60% of nxbt's functionality (no Flask webapp, macro system, or multiprocessing manager needed), and integrates fully into the project's asyncio architecture.

### 8.2 Module Structure

```
gamepadserver/bluetooth/
├── __init__.py
├── adapter.py          # BlueZ adapter management
├── sdp.py              # SDP service record registration
├── l2cap.py            # L2CAP socket connection management
├── switch_protocol.py  # Switch HID handshake state machine + subcommand responses
├── switch_report.py    # Input report encode/decode (50-byte HID report)
└── constants.py        # Protocol constants, button maps, SPI data templates
```

### 8.3 Module Responsibilities

#### adapter.py -- Bluetooth Adapter Management

- Controls BlueZ adapter via D-Bus (`Powered`, `Discoverable`, `Pairable`, `PairableTimeout`)
- Sets device class (`0x002508`) and name (`"Pro Controller"`) via `hciconfig`
- Enumerates available Bluetooth adapters
- Restores adapter to its original state after disconnection

#### sdp.py -- SDP Service Record Registration

Provides two registration strategies, with automatic runtime detection:

1. **sdptool path (preferred):** Registers SDP records directly via `sdptool add HID`, bypassing BlueZ D-Bus interception of L2CAP connections
2. **D-Bus RegisterProfile path (fallback):** Registers HID Profile via D-Bus, but removes the `Role: "server"` parameter to avoid BlueZ interception

#### l2cap.py -- L2CAP Connection Management

- Creates two `AF_BLUETOOTH + SOCK_SEQPACKET + BTPROTO_L2CAP` sockets
- PSM 17 (control channel) + PSM 19 (interrupt channel)
- bind -> listen(1) -> accept, with timeout and cancellation support
- Connection order: interrupt channel (PSM 19) accepts first, control channel (PSM 17) accepts second
- Falls back to `BDADDR_ANY` if binding to a specific adapter address fails
- Provides async interface; blocking operations execute via `run_in_executor()`

#### switch_protocol.py -- Switch HID Handshake State Machine

Handshake flow after connection is established (~15Hz loop, ~66ms per tick):

1. Send empty input reports to trigger Switch response
2. Receive and parse subcommand requests (device info, SPI Flash reads, etc.)
3. Respond with predefined template data:
   - Device info (MAC address, firmware version)
   - SPI Flash data (serial number, controller colors, stick calibration parameters)
   - IMU calibration data
4. Continue exchanging approximately 60-120 packets
5. Receiving a vibration command = handshake complete, enter normal input mode

#### switch_report.py -- Input Report Encode/Decode

Converts high-level input state into 50-byte binary HID reports:

- Button state: packed into 3-byte bit fields
- Sticks: map -100~100 to 12-bit values (0x000~0xFFF), stored across 3 bytes per Switch encoding
  - `stick_h = byte[0] | ((byte[1] & 0xF) << 8)`
  - `stick_v = (byte[1] >> 4) | (byte[2] << 4)`
- IMU data: accelerometer/gyroscope (can be filled with zeros)

#### constants.py -- Protocol Constants

- Button enum to bit field position mapping
- Subcommand type definitions
- SPI Flash addresses and default data templates (colors, calibration values, etc.)
- Device info constants (firmware version numbers, device type codes)
- Protocol-level shared timeout defaults (e.g., connection wait, handshake timeout)

Constants definition conventions:

- Semantically meaningful magic numbers must not be scattered through business logic; repeated values or values with fixed protocol meaning must be extracted into named constants
- `gamepadserver/bluetooth/constants.py` stores only Bluetooth / Switch protocol-level constants, including PSM ports, device class, subcommand numbers, button maps, SPI templates, and shared timeout defaults
- Runtime settings that may vary by deployment environment do not belong in `constants.py`; they should go in `config.py` and be injected via `GAMEPAD_*` environment variables
- Constants with the same semantic meaning must be reused across production code, test code, and manual test scripts -- avoid duplicating literal values in multiple files
- Time-related constants must include unit suffixes in their names, e.g., `*_SECONDS`, `*_MS`, to prevent callers from misinterpreting units

### 8.4 Complete Connection Flow

```
+-------------+                           +----------+
| GamePadServer|                           |  Switch  |
+------+------+                           +----+-----+
       |                                       |
       |  1. adapter.setup()                   |
       |     D-Bus: Powered, Discoverable      |
       |     hciconfig: class, name            |
       |                                       |
       |  2. sdp.register()                    |
       |     sdptool add HID / D-Bus Profile   |
       |                                       |
       |  3. l2cap.listen()                    |
       |     bind PSM 17+19, listen(1)         |
       |                                       |
       |  <--- User selects controller on      |
       |       Switch                     -----|
       |                                       |
       |  4. l2cap.accept()                    |
       |     PSM 19 accept (interrupt ch.)     |<------- L2CAP connect
       |     PSM 17 accept (control ch.)       |<------- L2CAP connect
       |                                       |
       |  5. switch_protocol.handshake()       |
       |     ---- empty input report -------->|
       |     <--- subcommand: device info -----|
       |     ---- response: MAC/FW/type ----->|
       |     <--- subcommand: SPI read --------|
       |     ---- response: color/calib. ---->|
       |     ... ~60-120 rounds ...            |
       |     <--- vibration cmd (handshake     |
       |          complete) -------------------|
       |                                       |
       |  6. Normal input mode                 |
       |     ---- input report (50B) -------->|
       |     ---- input report (50B) -------->|
       |     ... ~4.96ms/tick ...               |
```

---

## 9. Test Page

The server includes a built-in visual test page accessible via browser at `http://<host>:8080/`, used for manual testing of controller connection and button operations.

### Features

- **Platform selection:** Dropdown to switch between Switch / PS4 / PS5 / Xbox; button labels automatically change based on platform (e.g., Switch shows A/B/X/Y, PS shows x/o/[]/triangle)
- **Controller management:** Create connection, view state, disconnect and destroy
- **Button area:** Visual controller layout; clicking buttons sends REST single-shot button commands; supports face buttons, shoulder buttons, triggers, D-pad, and function buttons
- **Stick area:** Two draggable virtual sticks; stick data is sent in real-time via WebSocket during drag; auto-centers on release
- **Connection status:** Real-time display of current controller connection state

### Technical Implementation

- Single HTML file (inline CSS + JS), served by FastAPI static file route
- Button clicks call REST API (`POST /api/v1/controllers/{id}/buttons`)
- Stick dragging sends real-time state frames via WebSocket (`/ws/controllers/{id}/input`)
- No external dependencies, pure vanilla HTML/CSS/JS

---

## 10. Project Structure

```
GamePadServer/
├── SPEC.md                         # This document
├── requirements.txt
├── gamepadserver/
│   ├── __main__.py                 # Entry point, starts uvicorn
│   ├── app.py                      # FastAPI application instance
│   ├── config.py                   # Configuration management
│   ├── api/
│   │   ├── controllers.py          # /controllers REST endpoints
│   │   ├── system.py               # /system endpoints
│   │   └── ws.py                   # WebSocket endpoints
│   ├── core/
│   │   ├── manager.py              # Controller Manager, manages all controller instances
│   │   ├── backend.py              # GamepadBackend abstract interface
│   │   └── models.py               # Data models (Pydantic)
│   ├── bluetooth/                   # Self-implemented Bluetooth HID stack
│   │   ├── __init__.py
│   │   ├── adapter.py              # BlueZ adapter management (D-Bus + hciconfig)
│   │   ├── sdp.py                  # SDP service record registration (sdptool / D-Bus dual path)
│   │   ├── l2cap.py                # L2CAP socket connection management
│   │   ├── switch_protocol.py      # Switch HID handshake state machine + subcommand responses
│   │   ├── switch_report.py        # Input report encode/decode
│   │   └── constants.py            # Protocol constants, button maps, SPI data templates
│   ├── backends/
│   │   ├── switch.py               # SwitchBackend (uses bluetooth/ module)
│   │   ├── usb_gadget.py           # USBGadgetBackend base class
│   │   ├── ps4.py                  # PS4Backend (extends USBGadgetBackend)
│   │   ├── ps5.py                  # PS5Backend (extends USBGadgetBackend)
│   │   ├── xbox.py                 # XboxBackend (extends USBGadgetBackend)
│   │   └── auth/
│   │       ├── ps4_auth.py         # PS4 auth passthrough
│   │       └── xbox_auth.py        # Xbox auth passthrough
│   ├── static/
│   │   └── index.html              # Visual test page
│   └── utils/
│       ├── bluetooth.py            # Bluetooth utility functions
│       └── usb.py                  # USB Gadget utility functions
└── tests/
    ├── test_api.py
    ├── test_switch_backend.py
    └── test_usb_backend.py
```

---

## 11. Implementation Roadmap

> Acceptance criteria for each Phase includes: the test page can successfully operate the corresponding platform's controller.

### Phase 1: Switch Bluetooth (MVP)

**Goal:** Control the Switch via API, using the project's self-implemented Bluetooth HID stack

Divided into three sub-phases to validate the core connection path as early as possible:

#### Phase 1a: Minimal Connection Validation

- Implement `bluetooth/adapter.py`: adapter configuration (D-Bus + hciconfig)
- Implement `bluetooth/sdp.py`: SDP service registration (sdptool / D-Bus dual path)
- Implement `bluetooth/l2cap.py`: L2CAP socket management
- Implement `bluetooth/switch_protocol.py`: hardcoded handshake responses (get the minimal path working first)
- Implement `bluetooth/switch_report.py`: input report encoding
- **Acceptance criteria:** Successfully connect to the Switch and press a single button (HOME)

#### Phase 1b: Full Backend Integration

- Complete `switch_protocol.py`: full subcommand response table
- Implement `bluetooth/constants.py`: protocol constants and SPI data templates
- Implement SwitchBackend, integrating with the `bluetooth/` module
- Implement FastAPI framework + REST endpoints + WebSocket endpoints
- Implement Controller Manager
- Implement visual test page
- **Acceptance criteria:** Control the Switch through the test page / curl / WebSocket client to perform basic operations

#### Phase 1c: Stability Hardening

- Error handling and reconnection
- Automatic BlueZ version compatibility detection (sdptool vs D-Bus strategy selection)
- Multi-firmware version testing
- Edge case handling and performance optimization
- **Acceptance criteria:** Run continuously for 1 hour without unexpected disconnections

### Phase 2: USB Gadget Infrastructure

**Goal:** Implement USB controller emulation infrastructure on the Raspberry Pi

- Implement USBGadgetBackend base class
- ConfigFS device creation/destruction
- HID report construction and writing to `/dev/hidg0`
- Initially integrate with Switch USB (no authentication needed, used to validate the USB channel)

**Acceptance criteria:** Raspberry Pi connected to Switch via USB cable, controllable via API

### Phase 3: PS4 Support

**Goal:** Support PS4 controller emulation

- Implement PS4 HID Report Descriptor
- Implement PS4 button/stick report format
- Implement PS4 auth passthrough (libusb read/write to auth device)
- Requires MagicBoots or genuine DS4 for authentication

**Acceptance criteria:** Control PS4 via API without authentication timeout

### Phase 4: Xbox Support

**Goal:** Support Xbox One/Series controller emulation

- Implement GIP protocol adaptation
- Implement Xbox auth passthrough
- Requires MagicBoots for Xbox + genuine controller

**Acceptance criteria:** Control Xbox via API

---

## 12. Reference Projects and Resources

| Project | Purpose | Link |
|---------|---------|------|
| NXBT | Reference for Switch Bluetooth protocol implementation (focus on controller/server.py and protocol.py) | https://github.com/Brikwerk/nxbt |
| Nintendo Switch Reverse Engineering | Switch Bluetooth HID protocol reverse engineering docs (subcommands, SPI data, report format) | https://github.com/dekuNukem/Nintendo_Switch_Reverse_Engineering |
| joycontrol | Alternative Switch Bluetooth protocol implementation reference | https://github.com/mart1nro/joycontrol |
| GIMX | PS4/Xbox auth passthrough reference | https://github.com/matlo/GIMX |
| paaas | Network auth passthrough reference | https://github.com/jfedor2/paaas |
| GP2040-CE | Multi-platform controller protocol reference | https://github.com/OpenStickCommunity/GP2040-CE |
| RaspberryPi-Joystick | USB Gadget controller reference | https://github.com/milador/RaspberryPi-Joystick |
| bluetooth-usb-peripheral-relay | USB bridge architecture reference | https://github.com/bahaaador/bluetooth-usb-peripheral-relay |
| Linux USB Gadget ConfigFS | Kernel documentation | https://docs.kernel.org/usb/gadget_configfs.html |
| Linux USB HID Gadget | Kernel documentation | https://docs.kernel.org/usb/gadget_hid.html |
