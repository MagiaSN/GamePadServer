"""L2CAP connection wrapper.

This module wraps a pair of already-connected L2CAP sockets (control +
interrupt channels) and provides send/recv helpers for the protocol layer.

The sockets are obtained either from:
  a) SDPService.wait_for_connection() (D-Bus Profile1 NewConnection)
  b) Direct socket bind/listen/accept (standalone mode)
"""

from __future__ import annotations

import fcntl
import logging
import os
import socket

log = logging.getLogger(__name__)


class L2CAPConnection:
    """Manage a connected pair of L2CAP HID Control + Interrupt sockets."""

    def __init__(
        self,
        ctrl: socket.socket,
        itr: socket.socket,
        client_address: str | None = None,
    ) -> None:
        self.ctrl = ctrl
        self.itr = itr
        self.client_address = client_address
        # Set interrupt socket to non-blocking for recv during protocol loop
        fcntl.fcntl(self.itr.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)

    # ------------------------------------------------------------------
    # Data transfer (interrupt channel)
    # ------------------------------------------------------------------

    def send(self, data: bytes) -> None:
        """Send data on the interrupt channel."""
        self.itr.sendall(data)

    def recv(self, bufsize: int = 128) -> bytes | None:
        """Non-blocking receive from the interrupt channel.

        Returns None if no data is available.
        """
        try:
            data = self.itr.recv(bufsize)
            return data if data else None
        except (BlockingIOError, OSError):
            return None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close both sockets."""
        for s in (self.itr, self.ctrl):
            try:
                s.close()
            except OSError:
                pass
        log.info("L2CAP sockets closed")
