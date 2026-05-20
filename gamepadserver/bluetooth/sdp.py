"""SDP HID record registration + L2CAP socket listener.

Registers a BlueZ Profile1 with ``Role: "server"`` to get the SDP
record advertised.  Note: the ``NewConnection`` callback is unreliable
on many BlueZ versions (FS#46687), so we do NOT depend on it.

Instead, actual connections are accepted via raw L2CAP sockets
(bind/listen/accept on PSM 17 + 19), which is the proven approach used
by both joycontrol and nxbt.

The caller should:
  1. Create an SDPService and call register()
  2. Call wait_for_connection() which listens on raw L2CAP sockets
  3. Use the returned (ctrl_socket, itr_socket) pair
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from .constants import (
    PSM_CONTROL,
    PSM_INTERRUPT,
    SWITCH_CONNECTION_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)

# AF_BLUETOOTH and BTPROTO_L2CAP are not in Python's socket module
AF_BLUETOOTH = 31
BTPROTO_L2CAP = 0

# ---------------------------------------------------------------------------
# SDP Record
# ---------------------------------------------------------------------------
SDP_RECORD_XML = """\
<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence><uuid value="0x1124"/></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100"/><uint16 value="0x0011"/></sequence>
      <sequence><uuid value="0x0011"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence><uuid value="0x1002"/></sequence>
  </attribute>
  <attribute id="0x0006">
    <sequence>
      <uint16 value="0x656E"/>
      <uint16 value="0x006A"/>
      <uint16 value="0x0100"/>
    </sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence><uuid value="0x1124"/><uint16 value="0x0100"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x000D">
    <sequence><sequence>
      <sequence><uuid value="0x0100"/><uint16 value="0x0013"/></sequence>
      <sequence><uuid value="0x0011"/></sequence>
    </sequence></sequence>
  </attribute>
  <attribute id="0x0100"><text value="Wireless Gamepad"/></attribute>
  <attribute id="0x0101"><text value="Gamepad"/></attribute>
  <attribute id="0x0102"><text value="Nintendo"/></attribute>
  <attribute id="0x0201"><uint16 value="0x0111"/></attribute>
  <attribute id="0x0202"><uint8 value="0x08"/></attribute>
  <attribute id="0x0203"><uint8 value="0x21"/></attribute>
  <attribute id="0x0204"><boolean value="true"/></attribute>
  <attribute id="0x0205"><boolean value="true"/></attribute>
  <attribute id="0x0206">
    <sequence><sequence>
      <uint8 value="0x22"/>
      <text encoding="hex" value="05010905a1010601ff8521092175089530810285300930750895308102853109317508966901810285320932750896690181028533093375089669018102853f05091901291015002501750195108102050109391500250775049501814205097504950181010501093009310933093416000027ffff0000751095048102c00601ff85010901750895309102851009107508953091028511091175089530910285120912750895309102c0"/>
    </sequence></sequence>
  </attribute>
  <attribute id="0x020C"><uint16 value="0x0C80"/></attribute>
  <attribute id="0x020D"><boolean value="false"/></attribute>
  <attribute id="0x020E"><boolean value="false"/></attribute>
</record>
"""

_PROFILE_PATH = "/gamepadserver/hid"
_HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"

# Process-wide singleton for the Profile1 D-Bus object.  Like the
# BlueZ Agent (see agent.py), this object can only be registered once
# per process at a given path — successive SwitchBackend instances
# must reuse it.
import threading as _threading  # noqa: E402
_profile_lock = _threading.Lock()
_profile_obj: Any = None


class SDPService:
    """Register an HID SDP record and accept connections via raw L2CAP."""

    def __init__(self) -> None:
        self._registered = False
        # Listening sockets (for cleanup)
        self._listen_ctrl: socket.socket | None = None
        self._listen_itr: socket.socket | None = None

    def register(self) -> None:
        """Ensure the singleton HID profile is registered with BlueZ.

        Idempotent: subsequent calls in the same process are cheap
        no-ops at the D-Bus-object layer; we still re-call
        ``RegisterProfile`` in case bluetoothd restarted in between.
        """
        global _profile_obj

        import dbus  # type: ignore[import-untyped]
        import dbus.mainloop.glib  # type: ignore[import-untyped]
        import dbus.service  # type: ignore[import-untyped]

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()

        with _profile_lock:
            # D-Bus object: create exactly once per process.  Trying to
            # register the same path twice raises "there is already a
            # handler" from libdbus.
            if _profile_obj is None:
                _profile_obj = _Profile1(bus, _PROFILE_PATH)

            manager = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.ProfileManager1",
            )

            opts: dict[str, object] = {
                "ServiceRecord": dbus.String(SDP_RECORD_XML),
                "Role": dbus.String("server"),
                "RequireAuthentication": dbus.Boolean(False),
                "RequireAuthorization": dbus.Boolean(False),
                "AutoConnect": dbus.Boolean(True),
            }

            try:
                manager.RegisterProfile(_PROFILE_PATH, _HID_UUID, opts)
            except dbus.exceptions.DBusException as exc:
                if "Already Exists" in str(exc):
                    log.debug("HID profile already registered with BlueZ")
                else:
                    raise RuntimeError(f"RegisterProfile failed: {exc}")

        self._registered = True
        log.info("HID profile registered (Role=server, SDP only)")
        # Note: no GLib mainloop here — the BlueZAgent's mainloop handles
        # all D-Bus events.  We don't rely on NewConnection callbacks.

    def wait_for_connection(
        self,
        adapter_address: str = "",
        timeout: float = SWITCH_CONNECTION_TIMEOUT_SECONDS,
    ) -> tuple[socket.socket, socket.socket]:
        """Listen on PSM 17+19 and accept the Switch's connection.

        Uses raw L2CAP sockets (bind/listen/accept) — the proven approach
        from joycontrol/nxbt that works on all BlueZ versions.

        Returns (ctrl_socket, itr_socket).
        """
        log.info("Listening on L2CAP PSM %d + %d …", PSM_CONTROL, PSM_INTERRUPT)

        # Bind address: empty string = BDADDR_ANY
        bind_addr = adapter_address or ""

        # Create listening sockets
        ctrl_server = socket.socket(AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)
        itr_server = socket.socket(AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)
        self._listen_ctrl = ctrl_server
        self._listen_itr = itr_server

        try:
            ctrl_server.settimeout(timeout)
            itr_server.settimeout(timeout)

            ctrl_server.bind((bind_addr, PSM_CONTROL))
            itr_server.bind((bind_addr, PSM_INTERRUPT))

            ctrl_server.listen(1)
            itr_server.listen(1)

            log.info("Waiting for Switch connection (timeout=%.0fs)…", timeout)

            # Accept control channel first (Switch connects ctrl before itr)
            ctrl_sock, ctrl_info = ctrl_server.accept()
            log.info("Control channel connected from %s", ctrl_info)

            # Accept interrupt channel
            itr_sock, itr_info = itr_server.accept()
            log.info("Interrupt channel connected from %s", itr_info)

            log.info("Both L2CAP channels connected")
            return ctrl_sock, itr_sock

        except socket.timeout:
            raise RuntimeError(
                f"Timeout waiting for Switch connection ({timeout:.0f}s)"
            )
        finally:
            # Close listening sockets (we don't need them after accept)
            for s in (ctrl_server, itr_server):
                try:
                    s.close()
                except OSError:
                    pass
            self._listen_ctrl = None
            self._listen_itr = None

    def unregister(self) -> None:
        """Close any listening sockets; leave the BlueZ profile registered.

        The Profile1 D-Bus object and the BlueZ-side profile registration
        are intentionally process-wide singletons (see ``register``).
        Tearing them down would just create a window during which the
        Switch can't see our HID record — and the SDP profile is cheap
        to leave in place.
        """
        for s in (self._listen_ctrl, self._listen_itr):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._listen_ctrl = None
        self._listen_itr = None
        self._registered = False


# ---------------------------------------------------------------------------
# D-Bus Profile1 stub (needed for RegisterProfile, but NewConnection unused)
# ---------------------------------------------------------------------------

class _Profile1:
    """Minimal org.bluez.Profile1 — only exists to satisfy RegisterProfile.

    Instantiated exactly once per process by ``SDPService.register``.
    """

    INTERFACE = "org.bluez.Profile1"

    def __init__(self, bus: Any, path: str) -> None:
        import dbus.service  # type: ignore[import-untyped]

        class Impl(dbus.service.Object):
            def __init__(self) -> None:
                super().__init__(bus, path)

            @dbus.service.method(  # type: ignore[misc]
                _Profile1.INTERFACE,
                in_signature="oha{sv}",
                out_signature="",
            )
            def NewConnection(self, device: str, fd: Any, properties: Any) -> None:
                log.info("Profile1.NewConnection (unused) device=%s", device)

            @dbus.service.method(  # type: ignore[misc]
                _Profile1.INTERFACE,
                in_signature="o",
                out_signature="",
            )
            def RequestDisconnection(self, device: str) -> None:
                log.info("Profile1.RequestDisconnection  device=%s", device)

            @dbus.service.method(  # type: ignore[misc]
                _Profile1.INTERFACE,
                in_signature="",
                out_signature="",
            )
            def Release(self) -> None:
                log.info("Profile1.Release")

        self._impl = Impl()
