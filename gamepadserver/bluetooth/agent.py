"""BlueZ Agent1 for auto-accepting SSP (Secure Simple Pairing) requests.

The Nintendo Switch uses SSP "Just Works" pairing when connecting to a
Pro Controller.  BlueZ requires a registered Agent1 on the D-Bus to
handle the pairing confirmation — without one, authentication always
fails with status 0x05.

The Agent1 D-Bus object, the BlueZ-side registration, and the GLib
MainLoop are all **process-wide singletons** — there is only one
Bluetooth adapter, only one default Agent at a time, and only one
mainloop per process.  The ``BlueZAgent`` instance method ``register``
is therefore idempotent: the first caller in the process actually
sets things up, subsequent callers just see them already in place.
``unregister`` is similarly a no-op for the singletons — they live
until the process exits — and only clears per-instance flags.

This matters because each ``SwitchBackend.connect()`` creates a new
``BlueZAgent`` instance.  If each instance tried to recreate the D-Bus
object at ``/gamepadserver/agent``, the second call would fail with
"there is already a handler" from libdbus.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_AGENT_PATH = "/gamepadserver/agent"
_CAPABILITY = "NoInputNoOutput"

# Process-wide singletons.  Guarded by _module_lock — ``register`` may
# be called concurrently from different ``SwitchBackend`` instances in
# different threads (in practice we serialise, but cheap to be safe).
_module_lock = threading.Lock()
_agent_obj: Any = None
_mainloop: Any = None
_mainloop_thread: threading.Thread | None = None
_bluez_registered = False


class BlueZAgent:
    """Idempotent registration façade for the process-wide Agent1.

    Per-instance state only tracks whether *this* instance considers
    itself registered; the underlying D-Bus object / mainloop / BlueZ
    registration are shared across all instances for the lifetime of
    the process.
    """

    def __init__(self) -> None:
        self._registered = False

    def register(self) -> None:
        """Ensure the singleton agent is registered with BlueZ."""
        global _agent_obj, _mainloop, _mainloop_thread, _bluez_registered

        import dbus  # type: ignore[import-untyped]
        import dbus.mainloop.glib  # type: ignore[import-untyped]
        import dbus.service  # type: ignore[import-untyped]

        with _module_lock:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()

            # 1. D-Bus object: create exactly once per process.
            if _agent_obj is None:
                _agent_obj = _AutoAcceptAgent(bus, _AGENT_PATH)

            # 2. GLib MainLoop: start exactly once per process so D-Bus
            #    callbacks for User Confirmation actually fire.
            if _mainloop is None:
                from gi.repository import GLib  # type: ignore[import-untyped]
                _mainloop = GLib.MainLoop()
                _mainloop_thread = threading.Thread(
                    target=_mainloop.run, daemon=True,
                    name="BlueZAgentMainLoop",
                )
                _mainloop_thread.start()

            # 3. BlueZ registration: idempotent.  We re-call every
            #    instance so a previous ``unregister`` (or BlueZ
            #    restart) is recovered from automatically.
            mgr = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1",
            )
            try:
                mgr.RegisterAgent(_AGENT_PATH, _CAPABILITY)
            except dbus.exceptions.DBusException as exc:
                if "Already Exists" in str(exc):
                    log.debug("Agent already registered with BlueZ")
                else:
                    raise RuntimeError(f"RegisterAgent failed: {exc}")
            try:
                mgr.RequestDefaultAgent(_AGENT_PATH)
            except Exception as exc:
                log.warning("RequestDefaultAgent failed: %s (continuing)",
                            exc)
            _bluez_registered = True

        self._registered = True
        log.info("BlueZ Agent registered (capability=%s)", _CAPABILITY)

    def unregister(self) -> None:
        """No-op for the singletons.

        We intentionally keep the D-Bus object, the GLib mainloop, and
        the BlueZ-side agent registration alive across SwitchBackend
        lifetimes — there is no harm in leaving them in place, and
        tearing them down would just create a window during which an
        incoming SSP could land before the next ``register`` runs.
        """
        self._registered = False


# ---------------------------------------------------------------------------
# D-Bus Agent1 object — auto-accepts all pairing requests
# ---------------------------------------------------------------------------

class _AutoAcceptAgent:
    """Minimal org.bluez.Agent1 — accepts everything.

    Instantiated exactly once per process by ``BlueZAgent.register``.
    """

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
