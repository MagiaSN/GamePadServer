"""USB gadget configuration via ConfigFS.

The gadget is created once (idempotent) and then "soft connected" /
"soft disconnected" via UDC binding.  Writing the UDC name to the
gadget's UDC file pulls D+ high — the host (Switch dock) sees a USB
attach event.  Writing an empty string releases D+ — the host sees a
detach.  This means the physical USB cable can stay plugged in while
the controller is software-controlled at the connect/disconnect API
level, exactly mirroring the BT backend's lifecycle.
"""

from __future__ import annotations

import errno
import logging
import os
import subprocess
import time

from .constants import (
    CONFIG_NAME,
    CONFIGFS_GADGET_ROOT,
    GADGET_NAME,
    HID_FUNCTION_NAME,
    HID_PROTOCOL,
    HID_REPORT_DESCRIPTOR,
    HID_REPORT_LENGTH,
    HID_SUBCLASS,
    HIDG_DEVICE,
    UDC_DIR,
    USB_BCD_DEVICE,
    USB_BCD_USB,
    USB_HIDG_READY_TIMEOUT_SECONDS,
    USB_MANUFACTURER,
    USB_PID,
    USB_PRODUCT,
    USB_SERIAL,
    USB_VID,
)

log = logging.getLogger(__name__)


def _gadget_dir() -> str:
    return os.path.join(CONFIGFS_GADGET_ROOT, GADGET_NAME)


def _function_dir() -> str:
    return os.path.join(_gadget_dir(), "functions", HID_FUNCTION_NAME)


def _config_dir() -> str:
    return os.path.join(_gadget_dir(), "configs", CONFIG_NAME)


def _write(path: str, data: str | bytes) -> None:
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(path, mode) as f:
        f.write(data)


def _read(path: str) -> str:
    with open(path, "r") as f:
        return f.read().strip()


def _ensure_libcomposite() -> None:
    """Best-effort modprobe.  Not fatal if it fails (already-loaded /
    built-in / non-Linux).  ConfigFS access errors below give the real
    diagnostic if the module truly isn't present."""
    try:
        subprocess.run(
            ["modprobe", "libcomposite"],
            capture_output=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _list_udcs() -> list[str]:
    try:
        return sorted(os.listdir(UDC_DIR))
    except FileNotFoundError:
        return []


def detect_udc() -> str:
    """Return the first available UDC name.

    Raises RuntimeError with a clear remediation hint if no UDC is found
    (typical when dwc2 overlay isn't enabled on a Pi).
    """
    udcs = _list_udcs()
    if not udcs:
        raise RuntimeError(
            "No USB Device Controller found in /sys/class/udc. "
            "On Raspberry Pi, enable USB device mode by adding "
            "'dtoverlay=dwc2,dr_mode=peripheral' to /boot/firmware/config.txt "
            "and 'dwc2' to /etc/modules, then reboot."
        )
    return udcs[0]


# ---------------------------------------------------------------------------
# Gadget lifecycle
# ---------------------------------------------------------------------------

class USBGadget:
    """Manages the ConfigFS USB gadget and its UDC binding."""

    def __init__(self, udc_name: str | None = None) -> None:
        self._udc: str | None = udc_name  # resolved lazily on setup()
        self._gadget = _gadget_dir()
        self._function = _function_dir()
        self._config = _config_dir()
        self._strings = os.path.join(self._gadget, "strings", "0x409")
        self._config_strings = os.path.join(self._config, "strings", "0x409")
        self._function_link = os.path.join(self._config, HID_FUNCTION_NAME)

    # -- Public API --------------------------------------------------

    def setup(self) -> None:
        """Create the ConfigFS gadget tree if missing.  Idempotent."""
        _ensure_libcomposite()
        if not os.path.isdir(CONFIGFS_GADGET_ROOT):
            raise RuntimeError(
                f"{CONFIGFS_GADGET_ROOT} not found.  Mount configfs: "
                "`mount -t configfs none /sys/kernel/config` (usually done "
                "automatically when libcomposite loads)."
            )

        if self._udc is None:
            self._udc = detect_udc()

        os.makedirs(self._gadget, exist_ok=True)

        self._set_attr("idVendor", f"0x{USB_VID:04x}")
        self._set_attr("idProduct", f"0x{USB_PID:04x}")
        self._set_attr("bcdDevice", f"0x{USB_BCD_DEVICE:04x}")
        self._set_attr("bcdUSB", f"0x{USB_BCD_USB:04x}")

        os.makedirs(self._strings, exist_ok=True)
        self._set_string("manufacturer", USB_MANUFACTURER)
        self._set_string("product", USB_PRODUCT)
        self._set_string("serialnumber", USB_SERIAL)

        os.makedirs(self._config, exist_ok=True)
        self._set_config_attr("MaxPower", "500")
        os.makedirs(self._config_strings, exist_ok=True)
        self._set_config_string("configuration", USB_PRODUCT)

        os.makedirs(self._function, exist_ok=True)
        self._set_function_attr("protocol", str(HID_PROTOCOL))
        self._set_function_attr("subclass", str(HID_SUBCLASS))
        self._set_function_attr("report_length", str(HID_REPORT_LENGTH))
        self._set_function_attr_bytes("report_desc", HID_REPORT_DESCRIPTOR)

        if not os.path.islink(self._function_link):
            os.symlink(self._function, self._function_link)

        log.info(
            "USB gadget configured  vid=0x%04x pid=0x%04x udc=%s",
            USB_VID, USB_PID, self._udc,
        )

    def bind(self) -> None:
        """Soft-connect: bind the gadget to the UDC.

        After this returns the host (Switch dock) sees a USB attach
        event and starts enumeration; /dev/hidg0 becomes writable once
        SET_CONFIGURATION completes.
        """
        if self._udc is None:
            raise RuntimeError("USBGadget.setup() must be called before bind()")
        current = self._current_udc()
        if current == self._udc:
            log.debug("UDC already bound (%s)", current)
            return
        if current:
            raise RuntimeError(
                f"Gadget already bound to a different UDC ({current!r}); "
                "refuse to overwrite."
            )
        self._write_udc(self._udc)
        log.info("UDC bound  %s", self._udc)

    def unbind(self) -> None:
        """Soft-disconnect: release the UDC.

        The host sees a USB detach.  ConfigFS state is preserved so the
        next bind() is fast.  Idempotent — no-op if already unbound.
        """
        current = self._current_udc()
        if not current:
            return
        try:
            self._write_udc("")
            log.info("UDC released")
        except OSError as exc:
            # Some kernels reject writing empty when already unbinding —
            # treat as best-effort.
            log.warning("UDC release returned %s; continuing", exc)

    def is_bound(self) -> bool:
        return bool(self._current_udc())

    def wait_for_hidg(self, timeout: float = USB_HIDG_READY_TIMEOUT_SECONDS) -> str:
        """Block until /dev/hidg0 is ready for I/O after bind().

        Returns the device path.  Raises RuntimeError on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(HIDG_DEVICE):
                # Need RW access — fail fast with a clear message if not.
                if os.access(HIDG_DEVICE, os.R_OK | os.W_OK):
                    return HIDG_DEVICE
            time.sleep(0.05)
        raise RuntimeError(
            f"{HIDG_DEVICE} not available within {timeout}s after UDC bind. "
            "Check that the USB cable is connected to the host and that the "
            "gadget HID function is exposed."
        )

    # -- Internal helpers --------------------------------------------

    def _current_udc(self) -> str:
        path = os.path.join(self._gadget, "UDC")
        if not os.path.exists(path):
            return ""
        try:
            return _read(path)
        except OSError:
            return ""

    def _write_udc(self, value: str) -> None:
        _write(os.path.join(self._gadget, "UDC"), value + "\n")

    def _set_attr(self, name: str, value: str) -> None:
        path = os.path.join(self._gadget, name)
        if _safe_read(path) == value:
            return
        _write(path, value)

    def _set_string(self, name: str, value: str) -> None:
        path = os.path.join(self._strings, name)
        if _safe_read(path) == value:
            return
        _write(path, value)

    def _set_config_attr(self, name: str, value: str) -> None:
        path = os.path.join(self._config, name)
        if _safe_read(path) == value:
            return
        _write(path, value)

    def _set_config_string(self, name: str, value: str) -> None:
        path = os.path.join(self._config_strings, name)
        if _safe_read(path) == value:
            return
        _write(path, value)

    def _set_function_attr(self, name: str, value: str) -> None:
        path = os.path.join(self._function, name)
        if _safe_read(path) == value:
            return
        try:
            _write(path, value)
        except OSError as exc:
            # Some attrs can't be changed once the function is linked
            # to a config; silently accept if value already matches.
            if exc.errno == errno.EBUSY and _safe_read(path) == value:
                return
            raise

    def _set_function_attr_bytes(self, name: str, value: bytes) -> None:
        path = os.path.join(self._function, name)
        try:
            with open(path, "rb") as f:
                if f.read() == value:
                    return
        except OSError:
            pass
        try:
            _write(path, value)
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                # Already configured with a (possibly different) descriptor
                # while bound — can't rewrite, but the existing one is what
                # the host will see anyway.
                log.warning(
                    "report_desc is busy; keeping current value "
                    "(unbind first if you need to change it)"
                )
                return
            raise


def _safe_read(path: str) -> str:
    try:
        return _read(path)
    except OSError:
        return ""
