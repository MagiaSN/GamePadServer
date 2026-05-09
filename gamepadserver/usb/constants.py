"""Protocol & ConfigFS constants for the USB Switch Pro Controller backend.

References:
  - https://github.com/dekuNukem/Nintendo_Switch_Reverse_Engineering
  - https://docs.kernel.org/usb/gadget_configfs.html
  - mzyy94/nx-controller HID descriptor (verified against real Pro Controller)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# USB device identification (matches a genuine Switch Pro Controller)
# ---------------------------------------------------------------------------
USB_VID = 0x057E              # Nintendo Co., Ltd.
USB_PID = 0x2009              # Switch Pro Controller
USB_BCD_DEVICE = 0x0200       # device release 2.00
USB_BCD_USB = 0x0200          # USB 2.0
USB_MANUFACTURER = "Nintendo Co., Ltd."
USB_PRODUCT = "Pro Controller"
USB_SERIAL = "000000000001"

# ---------------------------------------------------------------------------
# ConfigFS layout
# ---------------------------------------------------------------------------
CONFIGFS_GADGET_ROOT = "/sys/kernel/config/usb_gadget"
GADGET_NAME = "gamepadserver"
HID_FUNCTION_NAME = "hid.usb0"
CONFIG_NAME = "c.1"
HIDG_DEVICE = "/dev/hidg0"
UDC_DIR = "/sys/class/udc"

# HID function settings
HID_PROTOCOL = 0              # 0 = none (custom HID, not boot keyboard/mouse)
HID_SUBCLASS = 0              # 0 = no subclass
HID_REPORT_LENGTH = 64        # IN/OUT max packet size

# ---------------------------------------------------------------------------
# HID Report Descriptor for Switch Pro Controller (USB mode)
#
# Layout:
#   Input  0x21 (subcommand reply)  — 49 data bytes
#   Input  0x30 (full standard)     — 49 data bytes
#   Input  0x31 (NFC/IR)            — 361 data bytes
#   Input  0x32 (NFC/IR data)       — 49 data bytes
#   Input  0x33 (NFC/IR data)       — 49 data bytes
#   Input  0x3F (legacy joystick)   — buttons + hat + sticks
#   Output 0x01 (rumble + subcmd)   — 48 data bytes
#   Output 0x10 (rumble only)       — 48 data bytes
#   Output 0x11 (NFC/IR)            — 48 data bytes
#   Output 0x12 (unused)            — 48 data bytes
# ---------------------------------------------------------------------------
HID_REPORT_DESCRIPTOR: bytes = bytes([
    0x05, 0x01,                    # Usage Page (Generic Desktop)
    0x09, 0x05,                    # Usage (Game Pad)
    0xA1, 0x01,                    # Collection (Application)

    # --- Vendor-defined input reports 0x21, 0x30, 0x31, 0x32, 0x33 ---
    0x06, 0x01, 0xFF,              #   Usage Page (Vendor 0xFF01)
    0x85, 0x21,                    #   Report ID 0x21
    0x09, 0x21, 0x75, 0x08, 0x95, 0x30, 0x81, 0x02,
    0x85, 0x30,                    #   Report ID 0x30
    0x09, 0x30, 0x75, 0x08, 0x95, 0x30, 0x81, 0x02,
    0x85, 0x31,                    #   Report ID 0x31
    0x09, 0x31, 0x75, 0x08, 0x96, 0x69, 0x01, 0x81, 0x02,
    0x85, 0x32,                    #   Report ID 0x32
    0x09, 0x32, 0x75, 0x08, 0x96, 0x69, 0x01, 0x81, 0x02,
    0x85, 0x33,                    #   Report ID 0x33
    0x09, 0x33, 0x75, 0x08, 0x96, 0x69, 0x01, 0x81, 0x02,

    # --- Legacy 0x3F input report (buttons + hat + sticks) ---
    0x85, 0x3F,                    #   Report ID 0x3F
    0x05, 0x09,                    #   Usage Page (Button)
    0x19, 0x01, 0x29, 0x10,        #   Usage Min/Max 1..16
    0x15, 0x00, 0x25, 0x01,        #   Logical Min 0, Max 1
    0x75, 0x01, 0x95, 0x10,        #   Report Size 1, Count 16
    0x81, 0x02,                    #   Input (Data,Var,Abs)
    0x05, 0x01,                    #   Usage Page (Generic Desktop)
    0x09, 0x39,                    #   Usage (Hat switch)
    0x15, 0x00, 0x25, 0x07,        #   Logical Min 0, Max 7
    0x75, 0x04, 0x95, 0x01,        #   Report Size 4, Count 1
    0x81, 0x42,                    #   Input (Data,Var,Abs,Null)
    0x05, 0x09,                    #   Usage Page (Button)
    0x75, 0x04, 0x95, 0x01,        #   pad
    0x81, 0x01,                    #   Input (Const)
    0x05, 0x01,                    #   Usage Page (Generic Desktop)
    0x09, 0x30, 0x09, 0x31, 0x09, 0x33, 0x09, 0x34,  # X, Y, Rx, Ry
    0x16, 0x00, 0x00, 0x26, 0xFF, 0xFF,
    0x75, 0x10, 0x95, 0x04,
    0x81, 0x02,

    # --- Vendor-defined output reports 0x01, 0x10, 0x11, 0x12 ---
    0x06, 0x01, 0xFF,              #   Usage Page (Vendor 0xFF01)
    0x85, 0x01,                    #   Report ID 0x01
    0x09, 0x01, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,
    0x85, 0x10,                    #   Report ID 0x10
    0x09, 0x10, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,
    0x85, 0x11,                    #   Report ID 0x11
    0x09, 0x11, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,
    0x85, 0x12,                    #   Report ID 0x12
    0x09, 0x12, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,

    0xC0,                          # End Collection
])

# ---------------------------------------------------------------------------
# USB-specific report IDs (host ↔ controller commands during enumeration)
#
# Pro Controller in USB mode handshakes via 0x80/0x81 reports before
# falling through to the same 0x01/0x21 subcommand exchange used over
# Bluetooth.  See dekuNukem reverse-engineering doc §"USB protocol".
# ---------------------------------------------------------------------------
USB_REPORT_HOST_CMD = 0x80    # host → controller
USB_REPORT_DEV_REPLY = 0x81   # controller → host

# Controller-type byte returned in the 0x81 0x01 status reply.
# Switch Pro Controller reports as 0x03 (same as BT device-info subcommand).
CONTROLLER_PRO_USB_REPLY = 0x03

# 0x80 sub-command codes (data[1])
USB_CMD_STATUS = 0x01         # request status (controller replies w/ MAC)
USB_CMD_HANDSHAKE = 0x02      # handshake
USB_CMD_BAUDRATE = 0x03       # set baud rate
USB_CMD_DISABLE_TIMEOUT = 0x04  # disable USB timeout — enter normal mode
USB_CMD_ENABLE_TIMEOUT = 0x05   # enable USB timeout
USB_CMD_RESET = 0x06            # reset controller

# ---------------------------------------------------------------------------
# Timing (USB allows higher rates than BT — 1 ms polling vs 7.5 ms)
# ---------------------------------------------------------------------------
USB_HANDSHAKE_TIMEOUT_SECONDS = 30.0
USB_USB_PHASE_TIMEOUT_SECONDS = 5.0
USB_KEEPALIVE_HZ = 60         # safe between BT 15 Hz and USB 125 Hz max
USB_HIDG_READY_TIMEOUT_SECONDS = 5.0
