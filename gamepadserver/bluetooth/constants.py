"""Protocol constants for Nintendo Switch Pro Controller Bluetooth HID emulation.

References:
  - https://github.com/dekuNukem/Nintendo_Switch_Reverse_Engineering
  - https://github.com/Brikwerk/nxbt (controller/protocol.py, controller/input.py)
"""

import random

# ---------------------------------------------------------------------------
# Bluetooth adapter settings
# ---------------------------------------------------------------------------
DEVICE_CLASS = "0x002508"  # Minor: Gamepad, Major: Peripheral
DEVICE_NAME = "Pro Controller"

# L2CAP PSM ports
PSM_CONTROL = 17
PSM_INTERRUPT = 19

# ---------------------------------------------------------------------------
# HID report constants
# ---------------------------------------------------------------------------
# Input report IDs (controller → Switch)
INPUT_SUBCMD_REPLY = 0x21
INPUT_FULL = 0x30

# Output report IDs (Switch → controller)
OUTPUT_RUMBLE_SUBCMD = 0x01
OUTPUT_RUMBLE_ONLY = 0x10

# Battery + connection info byte
BATTERY_FULL = 0x80            # full battery, not charging
CONNECTION_INFO_PRO = 0x00     # Pro Controller

# Vibrator input report byte candidates (for 0x21 reports)
VIBRATOR_CHOICES = [0xA0, 0xB0, 0xC0, 0x90]
VIBRATOR_STANDARD = 0x80  # for 0x30 reports


def random_vibrator() -> int:
    return random.choice(VIBRATOR_CHOICES)


# ---------------------------------------------------------------------------
# Button bit mapping
#   Each entry: (byte_offset, bitmask)
#   byte_offset 0 = right buttons (report byte 4 / array index 3)
#   byte_offset 1 = shared buttons (report byte 5 / array index 4)
#   byte_offset 2 = left buttons  (report byte 6 / array index 5)
# ---------------------------------------------------------------------------
BUTTON_MAP: dict[str, tuple[int, int]] = {
    # Right buttons (byte 0)
    "Y":          (0, 0x01),
    "X":          (0, 0x02),
    "B":          (0, 0x04),
    "A":          (0, 0x08),
    "R":          (0, 0x40),
    "ZR":         (0, 0x80),
    # Shared buttons (byte 1)
    "MINUS":      (1, 0x01),
    "PLUS":       (1, 0x02),
    "R_STICK":    (1, 0x04),
    "L_STICK":    (1, 0x08),
    "HOME":       (1, 0x10),
    "CAPTURE":    (1, 0x20),
    # Left buttons (byte 2)
    "DPAD_DOWN":  (2, 0x01),
    "DPAD_UP":    (2, 0x02),
    "DPAD_RIGHT": (2, 0x04),
    "DPAD_LEFT":  (2, 0x08),
    "L":          (2, 0x40),
    "ZL":         (2, 0x80),
}

# ---------------------------------------------------------------------------
# Stick calibration / center values
#   12-bit values packed into 3 bytes each.
#   Encoding:
#     byte[0] = H & 0xFF
#     byte[1] = ((V & 0xF) << 4) | (H >> 8)
#     byte[2] = V >> 4
# ---------------------------------------------------------------------------
LEFT_STICK_CENTER_BYTES = bytes([0x6F, 0xC8, 0x77])   # H=0x86F V=0x77C
RIGHT_STICK_CENTER_BYTES = bytes([0x16, 0xD8, 0x7D])   # H=0x816 V=0x7DD

# ---------------------------------------------------------------------------
# Subcommand IDs
# ---------------------------------------------------------------------------
SUBCMD_DEVICE_INFO = 0x02
SUBCMD_SET_INPUT_MODE = 0x03
SUBCMD_TRIGGER_BUTTONS = 0x04
SUBCMD_SET_SHIPMENT = 0x08
SUBCMD_SPI_READ = 0x10
SUBCMD_SET_NFC_IR_CONFIG = 0x21
SUBCMD_SET_NFC_IR_STATE = 0x22
SUBCMD_SET_PLAYER_LIGHTS = 0x30
SUBCMD_ENABLE_IMU = 0x40
SUBCMD_ENABLE_VIBRATION = 0x48

# Controller type
CONTROLLER_PRO = 0x03

# Firmware version reported to Switch
FW_MAJOR = 0x03
FW_MINOR = 0x48

# ---------------------------------------------------------------------------
# Static IMU data (3 frames x 12 bytes = 36 bytes, used for 0x30 reports)
# ---------------------------------------------------------------------------
IMU_DATA = bytes([
    0x75, 0xFD, 0xFD, 0xFF, 0x09, 0x10, 0x21, 0x00, 0xD5, 0xFF, 0xE0, 0xFF,
    0x72, 0xFD, 0xF9, 0xFF, 0x0A, 0x10, 0x22, 0x00, 0xD5, 0xFF, 0xE0, 0xFF,
    0x76, 0xFD, 0xFC, 0xFF, 0x09, 0x10, 0x23, 0x00, 0xD5, 0xFF, 0xE0, 0xFF,
])

# ---------------------------------------------------------------------------
# SPI Flash data regions
#   Address → data bytes.  Unknown/absent regions default to 0xFF.
# ---------------------------------------------------------------------------
_SPI_SERIAL = bytes([0xFF] * 16)

_SPI_6AXIS_FACTORY_CAL = bytes([
    0xD3, 0xFF, 0xD5, 0xFF, 0x55, 0x01,
    0x00, 0x40, 0x00, 0x40, 0x00, 0x40,
    0x19, 0x00, 0xDD, 0xFF, 0xDC, 0xFF,
    0x3B, 0x34, 0x3B, 0x34, 0x3B, 0x34,
])

_SPI_STICK_FACTORY_CAL = bytes([
    # Left stick: max-above-center, center, max-below-center (9 bytes)
    0xBA, 0xF5, 0x62,
    0x6F, 0xC8, 0x77,
    0xED, 0x95, 0x5B,
    # Right stick: center, max-below-center, max-above-center (9 bytes)
    0x16, 0xD8, 0x7D,
    0xF2, 0xB5, 0x5F,
    0x86, 0x65, 0x5E,
])

_SPI_COLOURS = bytes([
    0x82, 0x82, 0x82,  # Body colour (dark grey)
    0x0F, 0x0F, 0x0F,  # Button colour (near-black)
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,  # Grip colours
])

_SPI_FACTORY_PARAMS = bytes([
    0x50, 0xFD, 0x00, 0x00, 0xC6, 0x0F,
    0x0F, 0x30, 0x61,
    0x96, 0x30, 0xF3,  # 0x96 = 15% dead-zone for Pro Controller
    0xD4, 0x14, 0x54,
    0x41, 0x15, 0x54,
    0xC7, 0x79, 0x9C,
    0x33, 0x36, 0x63,
])

_SPI_USER_STICK_CAL = bytes([0xFF] * 24)

# Ordered list of (start_address, data) for SPI flash lookup
SPI_REGIONS: list[tuple[int, bytes]] = [
    (0x6000, _SPI_SERIAL),
    (0x6020, _SPI_6AXIS_FACTORY_CAL),
    (0x603D, _SPI_STICK_FACTORY_CAL),
    (0x6050, _SPI_COLOURS),
    (0x6080, _SPI_FACTORY_PARAMS),
    (0x6098, _SPI_FACTORY_PARAMS[6:]),  # Stick dead-zone params subset
    (0x8010, _SPI_USER_STICK_CAL),
]


def spi_read(address: int, length: int) -> bytes:
    """Read *length* bytes from virtual SPI flash at *address*.

    Returns known data where available, 0xFF elsewhere.
    """
    result = bytearray([0xFF] * length)
    for base, data in SPI_REGIONS:
        overlap_start = max(address, base)
        overlap_end = min(address + length, base + len(data))
        if overlap_start < overlap_end:
            src_off = overlap_start - base
            dst_off = overlap_start - address
            n = overlap_end - overlap_start
            result[dst_off:dst_off + n] = data[src_off:src_off + n]
    return bytes(result)


# ---------------------------------------------------------------------------
# NFC/IR MCU config reply (for subcommand 0x21)
# ---------------------------------------------------------------------------
NFC_IR_MCU_CONFIG_REPLY = bytes([
    0x01, 0x00, 0xFF, 0x00, 0x08, 0x00, 0x1B, 0x01,
]) + bytes(25) + bytes([0xC8])
