# Bluetooth HID Emulation: Host Setup Guide

> Target environment: Raspberry Pi 3B / 4, Debian 13 (trixie), BlueZ 5.82, Python 3.13

This document describes the BlueZ configuration required to emulate a
Nintendo Switch Pro Controller over Bluetooth HID.  Every step here
exists because of a real failure — see [pitfalls.md](pitfalls.md) for
the full reasoning behind each one.

---

## 1. bluetoothd service override

Create `/etc/systemd/system/bluetooth.service.d/override.conf`:

```ini
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --compat --noplugin=input
```

| Flag | Purpose |
|------|---------|
| `--compat` | Enables the legacy SDP socket interface (needed for `sdptool` and D-Bus `RegisterProfile`) |
| `--noplugin=input` | Prevents BlueZ's HID input plugin from intercepting L2CAP connections on PSM 17/19 |

> Do **not** use `--noplugin=*`.  It disables too much — SDP registration
> via D-Bus `RegisterProfile` stops working entirely.
> See [pitfalls.md#noplugin-star](pitfalls.md#noplugin-star-breaks-sdp-registration).

Apply:

```bash
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
```

## 2. BlueZ main.conf

Edit `/etc/bluetooth/main.conf` and set (uncomment) the following under
`[General]`:

```ini
ReverseServiceDiscovery = false
JustWorksRepairing = always
```

| Setting | Default | Required value | Why |
|---------|---------|----------------|-----|
| `ReverseServiceDiscovery` | `true` | `false` | Prevents bluetoothd from doing SDP service discovery *to* the Switch after connection.  A real controller never does this; the Switch disconnects if it sees it. |
| `JustWorksRepairing` | `never` | `always` | Allows SSP re-pairing on subsequent connection attempts.  Default `never` causes bluetoothd to auto-reject without consulting the Agent. |

Restart bluetoothd after editing.

## 3. Adapter initialisation (done in code)

The following are applied by `bluetooth/adapter.py` at runtime:

```bash
hciconfig hci0 up
hciconfig hci0 piscan          # Page scan + Inquiry scan (discoverable)
hciconfig hci0 class 0x002508  # Major: Peripheral, Minor: Gamepad
hciconfig hci0 name "Pro Controller"
```

> Device class gets reset by bluetoothd during `RegisterProfile` / `Add UUID`.
> The code re-applies it **after** SDP registration, multiple times.
> See [pitfalls.md#device-class-reset](pitfalls.md#device-class-gets-reset-by-bluetoothd).

## 4. SSP pairing: Agent1 registration (done in code)

BlueZ 5.82 requires a registered D-Bus Agent1 to handle SSP (Secure
Simple Pairing) User Confirmation Requests.

Key requirements:
- Capability: `NoInputNoOutput` (triggers SSP "Just Works" model)
- Must call `RequestDefaultAgent()` after registration
- Must have a GLib MainLoop running in a daemon thread for D-Bus callbacks
- `RequestConfirmation()` method returns without error = accept

> `btmgmt --index 0 io-cap 3` alone is **not** sufficient.  Without a
> registered Agent, bluetoothd auto-rejects confirmations.
> See [pitfalls.md#ssp-agent](pitfalls.md#ssp-pairing-agent-required).

## 5. SDP HID profile registration (done in code)

Register via D-Bus `RegisterProfile` with `Role=server`.  This
advertises the HID SDP record so the Switch can discover us.

> Do **not** rely on the `NewConnection` callback — it is broken on many
> BlueZ versions (upstream bug FS#46687).  Use raw L2CAP sockets instead.
> See [pitfalls.md#newconnection](pitfalls.md#newconnection-callback-unreliable).

## 6. L2CAP connection (done in code)

Accept connections via raw L2CAP sockets:

```python
socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP)
# bind → listen → accept on PSM 17 (control) and PSM 19 (interrupt)
```

## 7. Handshake timing (done in code)

- Send standard input reports at **1 Hz** (1-second intervals) until the
  Switch sends its first message
- Then switch to **15 Hz** (1/15-second intervals) for subcommand
  responses and normal operation

> Both nxbt and joycontrol use this pattern.  Sending at 15 Hz from the
> start causes the Switch to reject the controller within ~1.3 seconds.
> See [pitfalls.md#handshake-timing](pitfalls.md#handshake-timing-1-hz-then-15-hz).

## 8. Verification with btmon

Start btmon before running the test to capture the full HCI trace:

```bash
sudo btmon -w /tmp/btmon.log      # capture
sudo btmon -r /tmp/btmon.log      # replay
```

Key events to verify:

| Event | Expected | Problem if wrong |
|-------|----------|-----------------|
| `Write Class of Device` | `0x002508` after RegisterProfile | Switch won't recognise us as a gamepad |
| `User Confirmation Request Reply` (0x002c) | Positive reply | SSP fails (0x05) if negative (0x002d) |
| `Simple Pairing Complete` | `Status: Success` | Authentication failure |
| `L2CAP: Connection Request` PSM 17 | From Switch | Switch didn't try HID connection |
| `L2CAP: Connection Request` PSM 19 | From Switch | Interrupt channel missing |

## 9. Clearing stale pairing state

Before a fresh test, remove any existing pairing on both sides:

```bash
bluetoothctl remove <SWITCH_MAC>
```

On the Switch: go to System Settings > Controllers > Disconnect Controllers.
