"""Tiny client for the BlueZ Management (mgmt) API.

The mgmt API is the kernel/userspace interface that ``bluetoothctl``
and ``btmgmt`` ultimately speak.  It's a packet-oriented protocol over
an ``AF_BLUETOOTH / SOCK_RAW / BTPROTO_HCI`` socket bound to
``HCI_CHANNEL_CONTROL`` (channel 3).

We use it for *one* thing: setting the kernel's IO Capability so the
HCI IO Capability Reply carries ``NoInputNoOutput`` (see
``.claude/docs/bluetooth/pitfalls.md`` for why this matters on Pi 5 /
BlueZ 5.82).  Doing it here in-process avoids fork+exec to ``btmgmt``
and the inherited-``/dev/null``-stdin gotcha that hangs that binary.

Wire format (all little-endian):

  Command  | opcode (u16) | index (u16) | plen (u16) | params (plen bytes) |
  Event    | event  (u16) | index (u16) | plen (u16) | params (plen bytes) |

References:
  - kernel: ``include/net/bluetooth/mgmt.h``
  - userspace: ``bluez/src/shared/mgmt.c``
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import socket
import struct

log = logging.getLogger(__name__)

# socket(2) constants — not in Python's socket module on every build.
_AF_BLUETOOTH = 31
_BTPROTO_HCI = 1
_HCI_DEV_NONE = 0xFFFF
_HCI_CHANNEL_CONTROL = 3  # the mgmt channel

# mgmt opcodes / events we care about.
_OP_SET_IO_CAPABILITY = 0x0018
_EV_CMD_COMPLETE = 0x0001
_EV_CMD_STATUS = 0x0002

# Capability values (mgmt.h: enum mgmt_io_capability).
IO_CAP_DISPLAY_ONLY = 0
IO_CAP_DISPLAY_YES_NO = 1
IO_CAP_KEYBOARD_ONLY = 2
IO_CAP_NO_INPUT_NO_OUTPUT = 3
IO_CAP_KEYBOARD_DISPLAY = 4

_HEADER_FMT = "<HHH"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_CMD_REPLY_FMT = "<HB"  # opcode, status — payload of Cmd Complete / Status

_DEFAULT_TIMEOUT_S = 2.0

# Python's high-level socket.bind() for AF_BLUETOOTH/BTPROTO_HCI only
# accepts a (device_id,) tuple and gives no way to set hci_channel, so
# bind() is called through libc with a full sockaddr_hci struct.
_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.bind.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
_libc.bind.restype = ctypes.c_int


class MgmtError(RuntimeError):
    """An mgmt command completed with a non-zero status, or timed out."""


def set_io_capability(adapter_index: int,
                      capability: int = IO_CAP_NO_INPUT_NO_OUTPUT,
                      timeout: float = _DEFAULT_TIMEOUT_S) -> None:
    """Set the kernel IO Capability for ``adapter_index`` (e.g. 0 for hci0).

    Raises ``MgmtError`` on a non-success mgmt status; ``OSError`` on
    socket failure (e.g. CAP_NET_ADMIN missing, no Bluetooth subsystem).
    """
    sock = _open_mgmt_socket()
    try:
        cmd = struct.pack(_HEADER_FMT + "B",
                          _OP_SET_IO_CAPABILITY, adapter_index, 1,
                          capability)
        sock.send(cmd)
        sock.settimeout(timeout)
        _wait_for_reply(sock, _OP_SET_IO_CAPABILITY)
        log.info("mgmt: io-cap set (index=%d cap=%d)",
                 adapter_index, capability)
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _open_mgmt_socket() -> socket.socket:
    sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_RAW, _BTPROTO_HCI)
    # sockaddr_hci = { sa_family_t family; __u16 dev; __u16 channel; }
    sockaddr = struct.pack("<HHH", _AF_BLUETOOTH,
                           _HCI_DEV_NONE, _HCI_CHANNEL_CONTROL)
    if _libc.bind(sock.fileno(), sockaddr, len(sockaddr)) != 0:
        err = ctypes.get_errno()
        sock.close()
        raise OSError(err, os.strerror(err),
                      "bind(HCI_CHANNEL_CONTROL)")
    return sock


def _wait_for_reply(sock: socket.socket, expected_opcode: int) -> None:
    """Drain mgmt events until a Command Complete/Status for our opcode.

    Other events (asynchronous notifications) are skipped.
    """
    while True:
        data = sock.recv(4096)
        if len(data) < _HEADER_SIZE:
            raise MgmtError(f"short mgmt packet: {len(data)}B")
        event, _index, plen = struct.unpack_from(_HEADER_FMT, data, 0)
        if plen < struct.calcsize(_CMD_REPLY_FMT):
            continue  # asynchronous event with no opcode field, ignore
        opcode, status = struct.unpack_from(_CMD_REPLY_FMT,
                                            data, _HEADER_SIZE)
        if opcode != expected_opcode:
            continue
        if event == _EV_CMD_COMPLETE:
            if status != 0:
                raise MgmtError(
                    f"opcode 0x{opcode:04x} Cmd Complete status=0x{status:02x}"
                )
            return
        if event == _EV_CMD_STATUS:
            # Cmd Status with non-zero = terminal failure; status=0
            # means the command is in progress, keep waiting.
            if status != 0:
                raise MgmtError(
                    f"opcode 0x{opcode:04x} Cmd Status status=0x{status:02x}"
                )
            continue
