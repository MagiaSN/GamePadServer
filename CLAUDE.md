# CLAUDE.md

> This file is always written in English. Keep it that way.

## Project Overview

GamePadServer is a Python REST/WebSocket service that emulates game controllers via Bluetooth/USB, enabling remote control of game consoles. It runs on Linux (Raspberry Pi) and exposes a unified API — callers send "press A" / "tilt left stick" without caring about the target platform.

- **Framework:** FastAPI (async) + uvicorn
- **API:** REST for lifecycle + single actions, WebSocket for real-time input streams
- **Platforms:** Switch over Bluetooth and USB (both implemented), PS4/PS5/Xbox (USB Gadget, planned)
- **Transport selection:** controller create requests carry an optional `transport` field (`bluetooth` | `usb`, default `bluetooth`); the same `GamepadBackend` interface backs both.

## Key Architecture Decisions

### Abandon nxbt — self-implement Bluetooth HID stack (decided 2026-04-14)

The Switch backend originally used the nxbt library. We decided to replace it with an in-house `gamepadserver/bluetooth/` module because:

- nxbt has been unmaintained for 3+ years (PyPI v0.1.4 released 2021-10-03, GitHub master last touched 2023-07)
- Incompatible with Python 3.12+ (uses removed `imp` module)
- Its multiprocessing architecture conflicts with our asyncio model
- BlueZ 5.82+ breaks nxbt's L2CAP binding (we already had to write patches)

The community claim about a "v12 branch" on GitHub is incorrect — no such branch exists. "v12" refers to SwitchOS firmware v12 compatibility, which is already in PyPI v0.1.4. For reference code, use **GitHub master** (has webapp fix + plus/minus button fix not on PyPI).

### Reference code for reimplementation

- **nxbt** GitHub master: `controller/server.py` (connection), `controller/protocol.py` (handshake) — primary reference
- **dekuNukem/Nintendo_Switch_Reverse_Engineering**: complete BT HID protocol docs (subcommands, SPI data, report format)
- **joycontrol**: alternative Switch BT implementation reference

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
├── bluetooth/               # Self-implemented BT HID stack (NEW, replacing nxbt)
│   ├── adapter.py           # BlueZ adapter config (D-Bus + hciconfig)
│   ├── sdp.py               # SDP registration (sdptool / D-Bus dual path)
│   ├── l2cap.py             # L2CAP socket management (PSM 17+19)
│   ├── switch_protocol.py   # Switch HID handshake state machine
│   ├── switch_report.py     # 50-byte input report encode/decode
│   └── constants.py         # Protocol constants, button maps, SPI templates
├── usb/                     # USB Gadget HID stack (mirror of bluetooth/)
│   ├── gadget.py            # ConfigFS gadget setup + UDC bind/unbind
│   ├── hid_device.py        # /dev/hidg0 read/write wrapper (L2CAPConnection-shaped)
│   ├── switch_protocol.py   # Switch USB-only 0x80 handshake; reuses BT subcommand handlers
│   └── constants.py         # VID/PID, HID report descriptor, USB cmd codes
├── backends/
│   ├── switch.py            # SwitchBackend (Bluetooth, uses bluetooth/)
│   └── switch_usb.py        # SwitchUSBBackend (USB, uses usb/)
└── static/
    └── index.html           # Interactive test page
```

## Development Conventions

### Host Setup

New machines require one-time system configuration before running:

```bash
sudo ./deploy/setup-host.sh
```

This installs the bluetoothd systemd override and patches `/etc/bluetooth/main.conf`. Idempotent — safe to run multiple times. See `deploy/` for the managed config files.

### Running

```bash
sudo .venv/bin/python -m gamepadserver
# Or with custom settings:
GAMEPAD_HOST=127.0.0.1 GAMEPAD_PORT=9090 sudo -E .venv/bin/python -m gamepadserver
```

Root is required for Bluetooth HID operations.

### Testing

Unit tests (no hardware, mock the Bluetooth layer):

```bash
.venv/bin/python -m pytest tests/
```

End-to-end Switch test (requires a real Pi + Switch in "Change Grip/Order" mode, must run as root):

```bash
sudo .venv/bin/python tests/switch_e2e.py
```

`tests/switch_e2e.py` is the canonical end-to-end probe — it drives `SwitchBackend` exactly like the production server, connects to the Switch, presses HOME, then disconnects. Use this script (not ad-hoc scripts) whenever you need to verify a real-hardware Switch flow end to end. The filename drops the `test_` prefix so pytest does not collect it.

USB end-to-end (requires Pi gadget mode + cable to Switch dock, must run as root):

```bash
sudo .venv/bin/python tests/switch_usb_e2e.py
```

`tests/switch_usb_e2e.py` is the USB equivalent — it exercises `SwitchUSBBackend` end to end: ConfigFS setup, UDC bind, handshake, HOME press, UDC unbind. The Switch should be on a screen that accepts USB controllers (Home menu / any game; "Change Grip/Order" is *not* needed for USB).

### Code Style

- Async throughout — blocking BT/USB calls go through `asyncio.run_in_executor()`
- Backend abstraction: all platforms implement `GamepadBackend` ABC
- Single HTML test page with inline CSS/JS, no frontend build step
- API button enums follow hardware labels, not physical positions
- No magic numbers for protocol / timing / hardware identifiers. Promote repeated or semantically meaningful values to named constants.
- Put protocol-level constants, button maps, PSM values, SPI templates, and shared timeout defaults in `gamepadserver/bluetooth/constants.py`.
- Put deployment/runtime settings that may vary by environment in `config.py` (`GAMEPAD_*`), not in protocol constants modules.
- Reuse shared constants across production code and test scripts when they describe the same behavior. Do not duplicate timeout literals in multiple files.
- Name duration constants with explicit units (for example `*_SECONDS`, `*_MS`) so call sites stay unambiguous.

### Documentation Organization

Two categories of documentation, organized by audience:

**AI-only docs** (English only):
- `CLAUDE.md` — AI primary instructions
- `.claude/docs/` — implementation knowledge, troubleshooting, captures

**Human-facing docs** (English default, translations in `docs/i18n/{lang}/`):
- `README.md` (English) — project overview and quick start
- `SPEC.md` (English) — authoritative technical specification
- `docs/i18n/zh/README.md` — Chinese translation of README

Translation sync rules:
- English versions in the root are the source of truth.
- Each translation file has an `<!-- i18n-sync: {file} @ {commit} -->` comment at the top recording the source commit it was translated from.
- When updating an English doc, check whether a translation exists in `docs/i18n/` and note the sync status, but do not auto-translate unless asked.

Other reference files:
- `.claude/docs/` contains implementation experience and troubleshooting knowledge. When debugging platform-specific issues (especially Bluetooth/BlueZ), check this directory for past pitfalls and verified solutions before investigating from scratch. Current contents:
  - `.claude/docs/bluetooth/setup.md` — BlueZ host configuration guide
  - `.claude/docs/bluetooth/pitfalls.md` — Documented failure modes and root causes
  - `.claude/docs/bluetooth/captures/` — Known-good btmon/btsnoop captures
    (e.g. `switch_connect_success.btsnoop`). Diff a failing run against
    the matching capture to localize regressions.

### Connection Paths (Switch USB)

`SwitchUSBBackend` uses **soft connect/disconnect** via UDC bind/unbind
rather than physical cable plug events. The flow:

- **connect()** — `USBGadget.setup()` ensures the ConfigFS tree exists
  (idempotent), `bind()` writes the UDC name to activate D+ pullup, then
  the backend opens `/dev/hidg0` and runs the USB-then-subcommand
  handshake. The Switch dock sees a USB attach event.
- **disconnect()** — keep-alive thread stopped, `/dev/hidg0` closed,
  UDC released. The Switch dock sees a USB detach event. The ConfigFS
  state is preserved so the next connect() is a fast rebind.

This means **the USB cable can stay plugged in indefinitely** — the
attach/detach events are entirely driven by the API. Only one USB gadget
can bind to a UDC at a time, so each Pi supports a single
SwitchUSBBackend instance.

USB hardware constraints:
- Pi 4: USB-C port supports peripheral mode via `dwc2` overlay; the port
  also carries power, so power must come from GPIO/PoE when used.
- Pi Zero 2W: micro-USB data port supports peripheral mode natively.
- Pi 5: USB-C port is power-only — **USB backend not supported on Pi 5**.

### Connection Paths (Switch Bluetooth)

`SwitchBackend._open_l2cap` picks between two paths based on BlueZ bond state:

- **First-pair (listen)** — no bond exists. Listens on L2CAP PSM 17+19
  and waits for the Switch to dial in. Switch must be on
  "Change Grip/Order" (Settings → Controllers → Change Grip/Order).
- **Reconnect (outbound)** — at least one Switch is paired with the
  adapter. Dials out to the first paired Switch on PSM 17+19. Switch
  must be **awake** and on a screen that accepts paired controllers
  (Home menu, any game — *not* "Change Grip/Order"). A sleeping Switch
  cannot be woken by an outbound BR/EDR page.

On reconnect failure (timeout / ECONNREFUSED / auth rejected because
Switch-side bond was cleared independently), the code transparently
falls back to the listen path and logs a warning. To force a full
re-pair, clear the Switch-side bond (Controllers → Pro Controller →
Disconnect) **and** the Pi-side bond (`bluetoothctl remove <mac>`).

### Phase 1 Implementation Plan

Phase 1 (Switch Bluetooth) is split into three sub-phases:
- **1a:** Minimal connection — adapter setup, SDP, L2CAP, hardcoded handshake, input report. Goal: connect + press HOME.
- **1b:** Full integration — complete subcommand table, SwitchBackend, API, test page.
- **1c:** Stability — error handling, reconnection, BlueZ compat detection, multi-firmware testing.
