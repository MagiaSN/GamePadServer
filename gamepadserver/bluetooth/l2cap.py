"""L2CAP connection wrapper.

This module wraps a pair of already-connected L2CAP sockets (control +
interrupt channels) and provides send/recv helpers for the protocol layer.

The sockets are obtained either from:
  a) SDPService.wait_for_connection() — first-pair path (Switch dials in)
  b) connect_outbound() — reconnect path (we dial into the Switch)
"""

from __future__ import annotations

import errno as _errno
import fcntl
import logging
import os
import socket

from .constants import (
    PSM_CONTROL,
    PSM_INTERRUPT,
    SWITCH_RECONNECT_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)

# AF_BLUETOOTH / BTPROTO_L2CAP aren't in Python's socket module on all
# platforms; mirror the constants from sdp.py so both paths share them.
AF_BLUETOOTH = 31
BTPROTO_L2CAP = 0


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
        # Diagnostic: log peer-FIN / recv OSError once, not on every poll.
        self._peer_closed_logged = False

    # ------------------------------------------------------------------
    # Data transfer (interrupt channel)
    # ------------------------------------------------------------------

    def send(self, data: bytes) -> None:
        """Send data on the interrupt channel."""
        self.itr.sendall(data)

    def recv(self, bufsize: int = 128) -> bytes | None:
        """Non-blocking receive from the interrupt channel.

        Returns None if no data is available, the peer has closed, or the
        socket raised an OSError.  The first peer-close / OSError event is
        logged exactly once so we can later correlate with the keep-alive
        send that fails next.
        """
        try:
            data = self.itr.recv(bufsize)
            if data:
                return data
            # recv returned b"" → peer sent FIN on the interrupt channel.
            if not self._peer_closed_logged:
                log.warning("peer closed itr channel (recv returned EOF)")
                self._peer_closed_logged = True
            return None
        except BlockingIOError:
            return None
        except OSError as exc:
            if not self._peer_closed_logged:
                name = _errno.errorcode.get(exc.errno, "?") if exc.errno else "?"
                log.warning(
                    "itr recv OSError errno=%s(%s): %s", name, exc.errno, exc,
                )
                self._peer_closed_logged = True
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


def connect_outbound(
    switch_address: str,
    timeout: float = SWITCH_RECONNECT_TIMEOUT_SECONDS,
) -> tuple[socket.socket, socket.socket]:
    """Dial out to a paired Switch on PSM 17 + 19 (reconnect path).

    Matches the approach in nxbt ``controller/server.py::reconnect``:
    the kernel auto-negotiates authentication/encryption against the
    stored link key, so no extra HCI commands are required here.

    Returns ``(ctrl, itr)``.  Raises ``OSError`` if either channel fails
    to connect — caller is responsible for cleanup and for falling back
    to the listen-based first-pair path.
    """
    log.info("Dialing Switch %s on PSM %d + %d (timeout=%.1fs each)",
             switch_address, PSM_CONTROL, PSM_INTERRUPT, timeout)

    ctrl = socket.socket(AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)
    itr = socket.socket(AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)
    try:
        ctrl.settimeout(timeout)
        ctrl.connect((switch_address, PSM_CONTROL))
        log.info("Control channel (PSM %d) connected", PSM_CONTROL)

        itr.settimeout(timeout)
        itr.connect((switch_address, PSM_INTERRUPT))
        log.info("Interrupt channel (PSM %d) connected", PSM_INTERRUPT)

        # Blocking for post-connect I/O; L2CAPConnection will flip the
        # interrupt socket to non-blocking itself.
        ctrl.settimeout(None)
        itr.settimeout(None)
        return ctrl, itr
    except OSError:
        for s in (itr, ctrl):
            try:
                s.close()
            except OSError:
                pass
        raise
