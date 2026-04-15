"""Query already-paired Switch devices via BlueZ D-Bus.

Used by the reconnect path in ``SwitchBackend._connect_sync`` to decide
whether to dial out to a known Switch (PSM 17 + 19) or listen for a
first-pair inbound connection.  The Switch remembers the Pi as a paired
controller, and once both sides have a bond record it refuses to enter
the first-pair handshake again — it only does "dedicated bonding", then
disconnects — so we must proactively dial into it.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_DEVICE_INTERFACE = "org.bluez.Device1"
_SWITCH_NAME = "Nintendo Switch"


def list_paired_switches() -> list[str]:
    """Return MAC addresses of paired devices whose name is the Switch.

    Uses BlueZ's D-Bus ObjectManager — bluetoothd is the single source
    of truth for bond state, so no additional persistence is needed.
    Returns an empty list on any error.
    """
    try:
        import dbus  # type: ignore[import-untyped]
    except ImportError:
        log.warning("python-dbus unavailable — assuming no paired devices")
        return []

    try:
        bus = dbus.SystemBus()
        om = dbus.Interface(
            bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )
        objects = om.GetManagedObjects()
    except Exception as exc:  # pragma: no cover — depends on live bluetoothd
        log.warning("Failed to query BlueZ ObjectManager: %s", exc)
        return []

    addresses: list[str] = []
    for _path, interfaces in objects.items():
        dev = interfaces.get(_DEVICE_INTERFACE)
        if not dev:
            continue
        if not bool(dev.get("Paired", False)):
            continue
        name = str(dev.get("Name", ""))
        if name != _SWITCH_NAME:
            continue
        addr = str(dev.get("Address", ""))
        if addr:
            addresses.append(addr)

    log.info("Paired Switch devices: %s", addresses or "(none)")
    return addresses
