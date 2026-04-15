# Bluetooth HCI captures (reference)

btmon/btsnoop captures of known-good flows. Use them as ground truth
when diagnosing regressions — compare a failing run's trace against
the matching capture byte-for-byte.

Open with:

```bash
btmon -r <file>.btsnoop        # text dump
# or in Wireshark: File → Open → select .btsnoop (BT Snoop format)
```

## Files

- `switch_connect_success.btsnoop` — **Switch Pro Controller
  emulation, full successful flow.**
  Captured 2026-04-15 on a Raspberry Pi 3B (BlueZ 5.82, Debian 13)
  while running `tests/switch_e2e.py`. Pi adapter
  `B8:27:EB:52:0C:A4`, Switch `64:B5:C6:80:01:33`. Contains: SSP
  pairing (JustWorks via Agent1, NoInputNoOutput), L2CAP PSM 17 + 19
  setup, complete handshake subcommand exchange (device info →
  shipment state → SPI reads → input-report mode → trigger buttons
  → IMU enable → player lights → vibration enable → NFC/IR config →
  Set Player Lights player=1), a HOME press + release, ~3s of
  keep-alive standard reports at 15 Hz, then a clean disconnect.
