"""Read / write wrapper around /dev/hidgN.

Mirrors the .send() / .recv() / .close() shape of
gamepadserver.bluetooth.l2cap.L2CAPConnection so SwitchProtocol can be
reused unchanged.

Wire format note: the BT report builder prefixes every payload with the
BT HID transaction header (0xA1 for input / 0xA2 for output).  USB has
no such header — the report ID is the first byte on the wire.  So .send()
strips a leading 0xA1/0xA2 if present, and .recv() returns the raw HID
report (whose first byte is the report ID directly).  parse_output() in
switch_report.py already handles both with-and-without-0xA2 forms, so
no caller-side change is needed.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os

log = logging.getLogger(__name__)


class HIDGDevice:
    """Non-blocking I/O over /dev/hidgN."""

    def __init__(self, path: str) -> None:
        self._path = path
        # O_RDWR for full-duplex; O_NONBLOCK so recv() never stalls the
        # keep-alive loop when the host has nothing to send.
        flags = os.O_RDWR | os.O_NONBLOCK
        self._fd = os.open(path, flags)
        log.debug("Opened %s as fd=%d", path, self._fd)

    def send(self, data: bytes) -> None:
        if not data:
            return
        # Strip the BT HID transaction header — see module docstring.
        if data[0] in (0xA1, 0xA2):
            data = data[1:]
        try:
            written = os.write(self._fd, data)
        except BlockingIOError:
            # USB endpoint busy (host hasn't drained yet).  Drop the
            # frame; the next 60 Hz tick will overwrite it anyway.
            log.debug("hidg write would block; dropping report")
            return
        if written != len(data):
            log.debug("Short write %d/%d on %s", written, len(data), self._path)

    def recv(self, max_size: int = 64) -> bytes:
        try:
            return os.read(self._fd, max_size)
        except BlockingIOError:
            return b""
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return b""
            raise

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1

    @property
    def fd(self) -> int:
        return self._fd


def open_hidg(path: str) -> HIDGDevice:
    """Open a /dev/hidgN device.  Wrapper for symmetry with bluetooth/."""
    return HIDGDevice(path)


def _set_nonblocking(fd: int) -> None:
    """Belt-and-braces: ensure O_NONBLOCK even if O_NONBLOCK at open() was
    silently ignored by an exotic kernel/driver."""
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
