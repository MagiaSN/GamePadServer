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


def unpair(switch_address: str) -> bool:
    """Remove a paired Switch bond from BlueZ (e.g. when it has gone stale).

    Returns True if the bond is gone after the call (either removed or
    never present), False on D-Bus error.

    Why this exists: when the Switch-side bond is cleared (the user
    chose "Disconnect" in the Switch's Controllers menu) but the
    Pi-side bond remains, outbound reconnect fails fast
    (ECONNREFUSED / ECONNRESET).  The stale Pi-side bond also blocks
    the inbound listen path — bluetoothd appears to get confused about
    which link key to use, and post-SSP encryption never starts, so
    the Switch drops the link ~3 s after pairing.  Removing the bond
    before falling back to listen avoids that.
    """
    try:
        import dbus  # type: ignore[import-untyped]
    except ImportError:
        log.warning("python-dbus unavailable — cannot remove bond %s",
                    switch_address)
        return False

    try:
        bus = dbus.SystemBus()
        om = dbus.Interface(
            bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )
        objects = om.GetManagedObjects()
    except Exception as exc:
        log.warning("Failed to query BlueZ ObjectManager: %s", exc)
        return False

    target_path: str | None = None
    adapter_path: str | None = None
    for path, interfaces in objects.items():
        dev = interfaces.get(_DEVICE_INTERFACE)
        if not dev:
            continue
        addr = str(dev.get("Address", "")).upper()
        if addr == switch_address.upper():
            target_path = str(path)
            adapter_path = str(dev.get("Adapter", ""))
            break

    if not target_path or not adapter_path:
        log.info("Bond %s not present in BlueZ; nothing to remove",
                 switch_address)
        return True

    try:
        adapter = dbus.Interface(
            bus.get_object("org.bluez", adapter_path),
            "org.bluez.Adapter1",
        )
        adapter.RemoveDevice(target_path)
        log.info("Removed stale Pi-side bond for %s", switch_address)
        return True
    except Exception as exc:
        log.warning("RemoveDevice(%s) failed: %s", target_path, exc)
        return False
