"""BlueZ Bluetooth adapter management via subprocess.

Uses hciconfig (for device class / name) and bluetoothctl (for power /
discoverable / pairable) since dbus-python may not be available on
Python 3.12+.
"""

from __future__ import annotations

import logging
import subprocess

from .constants import DEVICE_CLASS, DEVICE_NAME
from . import mgmt

log = logging.getLogger(__name__)


class BluetoothAdapter:
    """Configure a local Bluetooth adapter for Pro Controller emulation."""

    def __init__(self, device: str = "hci0") -> None:
        self.device = device
        self._original_class: str | None = None
        self._original_name: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Prepare the adapter: power on, set class/name, make discoverable."""
        self._save_original_state()

        # Power on
        self._hci("up")

        # Discoverable + pairable first (BlueZ may reset class on these)
        self._hci("piscan")

        # Then set class and name (after piscan so they aren't overwritten)
        self._hci("class", DEVICE_CLASS)
        self._hci("name", DEVICE_NAME)

        # Verify class stuck
        current = self._read_class()
        if current and current != DEVICE_CLASS:
            log.warning("Device class reset to %s, retrying…", current)
            self._hci("class", DEVICE_CLASS)

        # Force the kernel IO Capability to NoInputNoOutput.  The D-Bus
        # Agent1 declares this too, but on BlueZ 5.82 / Pi 5 that value
        # is not propagated into the IO Capability Reply for inbound
        # SSP — bluetoothd sends the kernel default (DisplayYesNo), the
        # Switch then sees a "wrong" peer, never enables encryption,
        # and drops the link ~3 s after pairing.  Idempotent on Pi 3B,
        # required on Pi 5.  See .claude/docs/bluetooth/pitfalls.md.
        self._set_kernel_io_cap_no_io()

        log.info("Adapter %s ready  class=%s  name=%s",
                 self.device, DEVICE_CLASS, DEVICE_NAME)

    def teardown(self) -> None:
        """Restore adapter to its pre-setup state."""
        self._hci("noscan")
        if self._original_class:
            self._hci("class", self._original_class)
        if self._original_name:
            self._hci("name", self._original_name)
        log.info("Adapter %s restored", self.device)

    def get_address(self) -> str:
        """Return the BD address of the adapter (e.g. 'B8:27:EB:52:0C:A4')."""
        out = self._hci_output()
        for line in out.splitlines():
            if "BD Address:" in line:
                return line.split("BD Address:")[1].strip().split()[0]
        raise RuntimeError(f"Cannot determine BD address for {self.device}")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _save_original_state(self) -> None:
        out = self._hci_output("-a")
        for line in out.splitlines():
            if "Class:" in line:
                for tok in line.split():
                    if tok.startswith("0x"):
                        self._original_class = tok
                        break
            if "Name:" in line and "'" in line:
                self._original_name = line.split("'")[1]

    def _read_class(self) -> str | None:
        out = self._hci_output()
        # hciconfig prints something like "Class: 0x002508"
        # but the detailed output (-a) has it on a different line
        for line in out.splitlines():
            for tok in line.split():
                if tok.startswith("0x") and len(tok) >= 8:
                    return tok
        return None

    def _hci(self, *args: str) -> None:
        cmd = ["hciconfig", self.device, *args]
        log.debug("run: %s", " ".join(cmd))
        subprocess.run(cmd, capture_output=True, timeout=10, check=False)

    def _hci_output(self, *extra: str) -> str:
        cmd = ["hciconfig", self.device, *extra]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                           check=False)
        return r.stdout

    def _set_kernel_io_cap_no_io(self) -> None:
        """Set kernel IO Capability to NoInputNoOutput for this adapter.

        Best-effort: warns and continues on any error.  Older BlueZ
        (e.g. Pi 3B / BlueZ 5.55) may still work via the Agent1
        capability alone, so a failure here is not fatal.
        """
        if self.device.startswith("hci") and self.device[3:].isdigit():
            index = int(self.device[3:])
        else:
            log.warning("Cannot derive adapter index from %r; skipping io-cap",
                        self.device)
            return
        try:
            mgmt.set_io_capability(index, mgmt.IO_CAP_NO_INPUT_NO_OUTPUT)
        except (OSError, mgmt.MgmtError) as exc:
            log.warning("mgmt set io-cap failed: %s — relying on Agent1 only",
                        exc)
