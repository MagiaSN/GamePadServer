---
paths:
  - "gamepadserver/bluetooth/**"
  - "test_bt_connect.py"
---

# Bluetooth HID development rules

## BlueZ host config (Pi)
- `/etc/bluetooth/main.conf` must have `ReverseServiceDiscovery = false` and `JustWorksRepairing = always`
- bluetoothd must run with `--compat --noplugin=input` (not `--noplugin=*`)

## SSP pairing
- A D-Bus Agent1 (NoInputNoOutput) must be registered and set as default — `btmgmt io-cap 3` alone is not enough
- Only one GLib MainLoop per process — agent owns it, no other module should start another

## Protocol timing
- Send reports at 1 Hz before Switch sends first message, then 15 Hz
- Never start at 15 Hz — Switch rejects controllers that report too fast during validation

## Device class
- Re-apply `0x002508` via hciconfig after every RegisterProfile call — bluetoothd resets it
- Trust btmon HCI events over `btmgmt info` for actual class value

## L2CAP connections
- Use raw sockets (bind/listen/accept on PSM 17+19), not NewConnection callbacks (broken in BlueZ)

## Debugging
- Always capture with `btmon -w` during testing
- Compare first vs second connection attempts — they often fail differently

## Detailed reference
- Setup steps: `.claude/docs/bluetooth/setup.md`
- Full pitfall analysis: `.claude/docs/bluetooth/pitfalls.md`
