"""Nintendo Switch Pro Controller HID report encoding / decoding.

Input report layout (50 bytes, 0xA1 header prepended on send = 51 total):
  [0]  Report ID (0x21 or 0x30)
  [1]  Timer (0x00-0xFF, wrapping)
  [2]  Battery + connection info
  [3]  Button byte: right (Y X B A _ _ R ZR)
  [4]  Button byte: shared (- + RS LS Home Capture _ _)
  [5]  Button byte: left (Down Up Right Left _ _ L ZL)
  [6-8]   Left stick  (3 bytes, two 12-bit values)
  [9-11]  Right stick (3 bytes, two 12-bit values)
  [12] Vibrator byte

  --- 0x21 (subcommand reply) ---
  [13] ACK
  [14] Subcommand ID
  [15-49] Subcommand data

  --- 0x30 (full standard input) ---
  [13-48] IMU data (36 bytes)
"""

from __future__ import annotations

from .constants import (
    BATTERY_FULL,
    BUTTON_MAP,
    CONNECTION_INFO_PRO,
    IMU_DATA,
    INPUT_FULL,
    INPUT_SUBCMD_REPLY,
    LEFT_STICK_CENTER_BYTES,
    RIGHT_STICK_CENTER_BYTES,
    VIBRATOR_STANDARD,
    random_vibrator,
)


def encode_buttons(pressed: set[str]) -> bytes:
    """Encode a set of button names into the 3-byte button field."""
    buf = [0, 0, 0]
    for name in pressed:
        entry = BUTTON_MAP.get(name)
        if entry is None:
            continue
        offset, mask = entry
        buf[offset] |= mask
    return bytes(buf)


def encode_stick(h: int, v: int) -> bytes:
    """Encode a pair of 12-bit stick values into 3 bytes.

    *h* and *v* are raw 12-bit values (0x000 – 0xFFF).
    """
    b0 = h & 0xFF
    b1 = ((v & 0xF) << 4) | (h >> 8)
    b2 = v >> 4
    return bytes([b0, b1, b2])


# ---------------------------------------------------------------------------
# Stick calibration for API mapping (-100..100 → 12-bit raw values)
# Derived from _SPI_STICK_FACTORY_CAL in constants.py.
# ---------------------------------------------------------------------------
_STICK_CAL = {
    "left": {
        "h_center": 0x86F, "h_min": 0x282, "h_max": 0xE29,
        "v_center": 0x77C, "v_min": 0x1C3, "v_max": 0xDAB,
    },
    "right": {
        "h_center": 0x816, "h_min": 0x224, "h_max": 0xD9C,
        "v_center": 0x7DD, "v_min": 0x1E2, "v_max": 0xDC3,
    },
}


def _map_axis(value: int, center: int, low: int, high: int) -> int:
    clamped = max(-100, min(100, value))
    if clamped < 0:
        return center + int(clamped * (center - low) / 100)
    if clamped > 0:
        return center + int(clamped * (high - center) / 100)
    return center


def stick_from_api(x: int, y: int, stick: str = "left") -> bytes:
    """Map API stick values (-100..100) to 3-byte encoded stick data."""
    cal = _STICK_CAL[stick]
    h = _map_axis(x, cal["h_center"], cal["h_min"], cal["h_max"])
    v = _map_axis(y, cal["v_center"], cal["v_min"], cal["v_max"])
    return encode_stick(h, v)


class ReportBuilder:
    """Stateful builder for Switch input reports."""

    def __init__(self) -> None:
        self.timer: int = 0
        self.buttons: set[str] = set()
        self.left_stick: bytes = LEFT_STICK_CENTER_BYTES
        self.right_stick: bytes = RIGHT_STICK_CENTER_BYTES

    def _advance_timer(self) -> int:
        t = self.timer
        self.timer = (self.timer + 1) & 0xFF
        return t

    def _base_report(self, report_id: int) -> bytearray:
        """Build the common prefix (bytes 0-12) of an input report."""
        r = bytearray(50)
        r[0] = report_id
        r[1] = self._advance_timer()
        r[2] = BATTERY_FULL | CONNECTION_INFO_PRO
        # Buttons
        btn = encode_buttons(self.buttons)
        r[3] = btn[0]
        r[4] = btn[1]
        r[5] = btn[2]
        # Sticks
        r[6:9] = self.left_stick
        r[9:12] = self.right_stick
        return r

    def standard_report(self) -> bytes:
        """Build a 0x30 full standard input report (50 bytes + 0xA1)."""
        r = self._base_report(INPUT_FULL)
        r[12] = VIBRATOR_STANDARD
        r[13:49] = IMU_DATA
        return bytes([0xA1]) + bytes(r)

    def subcommand_report(
        self,
        ack: int,
        subcmd_id: int,
        data: bytes = b"",
    ) -> bytes:
        """Build a 0x21 subcommand reply report (50 bytes + 0xA1)."""
        r = self._base_report(INPUT_SUBCMD_REPLY)
        r[12] = random_vibrator()
        r[13] = ack
        r[14] = subcmd_id
        end = min(15 + len(data), 50)
        r[15:end] = data[:end - 15]
        return bytes([0xA1]) + bytes(r)

    def empty_report(self) -> bytes:
        """Build a standard report with no buttons pressed and centered sticks."""
        saved_buttons = self.buttons
        self.buttons = set()
        report = self.standard_report()
        self.buttons = saved_buttons
        return report


# ---------------------------------------------------------------------------
# Output report parsing (data received from Switch)
# ---------------------------------------------------------------------------

def parse_output(data: bytes) -> dict | None:
    """Parse an output report from the Switch.

    Returns a dict with keys: report_id, timer, subcommand, subcmd_data, raw.
    Returns None if data is too short or not a subcommand report.
    """
    if not data or len(data) < 12:
        return None

    offset = 0
    # Strip HID header if present
    if data[0] == 0xA2:
        offset = 1

    report_id = data[offset]
    if report_id != 0x01:
        # Not a rumble+subcommand report — ignore
        return None

    if len(data) < offset + 12:
        return None

    return {
        "report_id": report_id,
        "timer": data[offset + 1],
        "subcommand": data[offset + 10],
        "subcmd_data": data[offset + 11:],
        "raw": data,
    }
