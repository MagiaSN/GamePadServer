"""BlueZ Agent1 for auto-accepting SSP (Secure Simple Pairing) requests.

The Nintendo Switch uses SSP "Just Works" pairing when connecting to a
Pro Controller.  BlueZ requires a registered Agent1 on the D-Bus to
handle the pairing confirmation — without one, authentication always
fails with status 0x05.

This module registers a NoInputNoOutput agent that auto-accepts all
pairing and service-authorization requests.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_AGENT_PATH = "/gamepadserver/agent"
_CAPABILITY = "NoInputNoOutput"


class BlueZAgent:
    """Register a D-Bus Agent1 that auto-accepts pairing."""

    def __init__(self) -> None:
        self._registered = False
        self._mainloop: Any = None
        self._loop_thread: threading.Thread | None = None

    def register(self) -> None:
        """Register the agent with BlueZ and start the D-Bus listener."""
        import dbus  # type: ignore[import-untyped]
        import dbus.mainloop.glib  # type: ignore[import-untyped]
        import dbus.service  # type: ignore[import-untyped]

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()

        # Create the Agent1 D-Bus object
        _AutoAcceptAgent(bus, _AGENT_PATH)

        mgr = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.AgentManager1",
        )

        try:
            mgr.RegisterAgent(_AGENT_PATH, _CAPABILITY)
        except Exception as exc:
            if "Already Exists" in str(exc):
                log.info("Agent already registered, re-using")
            else:
                raise RuntimeError(f"RegisterAgent failed: {exc}")

        try:
            mgr.RequestDefaultAgent(_AGENT_PATH)
        except Exception as exc:
            log.warning("RequestDefaultAgent failed: %s (continuing)", exc)

        self._registered = True
        log.info("BlueZ Agent registered (capability=%s)", _CAPABILITY)

        # Run the GLib main loop so D-Bus callbacks fire
        self._start_mainloop()

    def unregister(self) -> None:
        """Unregister the agent and stop the main loop."""
        self._stop_mainloop()
        if not self._registered:
            return
        try:
            import dbus
            bus = dbus.SystemBus()
            mgr = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1",
            )
            mgr.UnregisterAgent(_AGENT_PATH)
            log.info("Agent unregistered")
        except Exception:
            log.debug("UnregisterAgent failed (may already be gone)")
        self._registered = False

    def _start_mainloop(self) -> None:
        from gi.repository import GLib  # type: ignore[import-untyped]
        self._mainloop = GLib.MainLoop()
        self._loop_thread = threading.Thread(
            target=self._mainloop.run, daemon=True
        )
        self._loop_thread.start()

    def _stop_mainloop(self) -> None:
        if self._mainloop is not None:
            self._mainloop.quit()
            self._mainloop = None
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=2)
            self._loop_thread = None


# ---------------------------------------------------------------------------
# D-Bus Agent1 object — auto-accepts all pairing requests
# ---------------------------------------------------------------------------

class _AutoAcceptAgent:
    """Minimal org.bluez.Agent1 — accepts everything."""

    INTERFACE = "org.bluez.Agent1"

    def __init__(self, bus: Any, path: str) -> None:
        import dbus.service  # type: ignore[import-untyped]

        class Impl(dbus.service.Object):
            def __init__(self) -> None:
                super().__init__(bus, path)

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="", out_signature="",
            )
            def Release(self) -> None:
                log.info("Agent1.Release")

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="os", out_signature="",
            )
            def AuthorizeService(self, device: str, uuid: str) -> None:
                log.info("Agent1.AuthorizeService  device=%s  uuid=%s",
                         device, uuid)

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="o", out_signature="",
            )
            def RequestAuthorization(self, device: str) -> None:
                log.info("Agent1.RequestAuthorization  device=%s", device)

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="ou", out_signature="",
            )
            def RequestConfirmation(self, device: str, passkey: int) -> None:
                log.info("Agent1.RequestConfirmation  device=%s  passkey=%d",
                         device, passkey)

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="o", out_signature="s",
            )
            def RequestPinCode(self, device: str) -> str:
                log.info("Agent1.RequestPinCode  device=%s", device)
                return "0000"

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="o", out_signature="u",
            )
            def RequestPasskey(self, device: str) -> int:
                log.info("Agent1.RequestPasskey  device=%s", device)
                return 0

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="ou", out_signature="",
            )
            def DisplayPasskey(self, device: str, passkey: int) -> None:
                log.info("Agent1.DisplayPasskey  device=%s  passkey=%06d",
                         device, passkey)

            @dbus.service.method(  # type: ignore[misc]
                _AutoAcceptAgent.INTERFACE,
                in_signature="", out_signature="",
            )
            def Cancel(self) -> None:
                log.info("Agent1.Cancel")

        self._impl = Impl()
