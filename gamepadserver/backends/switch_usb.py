"""Switch Pro Controller backend over USB Gadget.

Same public API as backends/switch.py (Bluetooth) — only the transport
layer differs.  The USB cable is expected to stay physically connected
to the Switch dock; connect() / disconnect() perform a *soft* attach
by binding / unbinding the gadget to the UDC, which the host sees as
a USB plug / unplug event.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from gamepadserver.bluetooth.switch_report import ReportBuilder, stick_from_api
from gamepadserver.core.backend import GamepadBackend
from gamepadserver.core.models import ControllerState, InputState
from gamepadserver.usb.constants import (
    USB_HANDSHAKE_TIMEOUT_SECONDS,
    USB_KEEPALIVE_HZ,
)
from gamepadserver.usb.gadget import USBGadget
from gamepadserver.usb.hid_device import HIDGDevice, open_hidg
from gamepadserver.usb.switch_protocol import SwitchUSBProtocol

logger = logging.getLogger(__name__)

# API button name → ReportBuilder button name.  Identical to the BT
# backend — this is the platform/Switch-level mapping, not transport.
_BUTTON_MAP: dict[str, str] = {
    "A": "A", "B": "B", "X": "X", "Y": "Y",
    "L": "L", "R": "R", "ZL": "ZL", "ZR": "ZR",
    "PLUS": "PLUS", "MINUS": "MINUS",
    "HOME": "HOME", "CAPTURE": "CAPTURE",
    "DPAD_UP": "DPAD_UP", "DPAD_DOWN": "DPAD_DOWN",
    "DPAD_LEFT": "DPAD_LEFT", "DPAD_RIGHT": "DPAD_RIGHT",
    "L_STICK": "L_STICK", "R_STICK": "R_STICK",
}

# The gadget needs *some* MAC-shaped value to populate the USB status
# reply.  USB controllers don't have a real BD_ADDR — Nintendo's Pro
# Controller in dock mode reports a fixed-but-arbitrary value.  Match
# that shape with a sentinel.
_USB_FAKE_MAC = "00:17:AB:00:00:01"


def _map_buttons(buttons: list[str]) -> list[str]:
    result = []
    for b in buttons:
        mapped = _BUTTON_MAP.get(b)
        if mapped is None:
            raise ValueError(f"Unsupported button for Switch: {b}")
        result.append(mapped)
    return result


class SwitchUSBBackend(GamepadBackend):
    """Switch Pro Controller backend using USB Gadget HID."""

    def __init__(self) -> None:
        self._gadget: USBGadget | None = None
        self._conn: HIDGDevice | None = None
        self._protocol: SwitchUSBProtocol | None = None
        self._report = ReportBuilder()
        self._state = ControllerState.DISCONNECTED
        self._send_lock = threading.Lock()
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # GamepadBackend interface (mirror of SwitchBackend)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._state = ControllerState.CONNECTING
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._connect_sync)
            self._state = ControllerState.CONNECTED
        except Exception:
            self._state = ControllerState.ERROR
            # Best-effort cleanup so the next connect() starts fresh.
            try:
                self._teardown()
            except Exception:
                pass
            raise

    async def disconnect(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._disconnect_sync)
        self._state = ControllerState.DISCONNECTED

    async def get_state(self) -> ControllerState:
        return self._state

    async def press_buttons(self, buttons: list[str], duration: float = 0.1) -> None:
        self._ensure_connected()
        mapped = _map_buttons(buttons)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._press_sync, mapped, duration)

    async def hold_buttons(self, buttons: list[str]) -> None:
        self._ensure_connected()
        mapped = _map_buttons(buttons)
        self._report.buttons.update(mapped)

    async def release_buttons(self, buttons: list[str]) -> None:
        self._ensure_connected()
        mapped = _map_buttons(buttons)
        self._report.buttons.difference_update(mapped)

    async def set_stick(self, stick: str, x: int, y: int) -> None:
        self._ensure_connected()
        encoded = stick_from_api(x, y, stick)
        if stick == "left":
            self._report.left_stick = encoded
        else:
            self._report.right_stick = encoded

    async def send_input(self, state: InputState) -> None:
        self._ensure_connected()
        self._report.buttons = {
            _BUTTON_MAP[name]
            for name, pressed in state.buttons.items()
            if pressed and name in _BUTTON_MAP
        }
        lx, ly = state.left_stick
        rx, ry = state.right_stick
        self._report.left_stick = stick_from_api(lx, ly, "left")
        self._report.right_stick = stick_from_api(rx, ry, "right")

    # ------------------------------------------------------------------
    # Sync connection flow (executor thread)
    # ------------------------------------------------------------------

    def _connect_sync(self) -> None:
        # 1. Configure gadget tree (idempotent)
        self._gadget = USBGadget()
        self._gadget.setup()

        # 2. Soft-attach: bind UDC.  Host now sees a USB plug-in event.
        self._gadget.bind()

        # 3. Wait for /dev/hidg0 (gadget enumeration completes)
        path = self._gadget.wait_for_hidg()
        self._conn = open_hidg(path)

        # 4. Run the USB + subcommand handshake
        self._protocol = SwitchUSBProtocol(self._conn, _USB_FAKE_MAC)
        self._protocol.handshake(timeout=USB_HANDSHAKE_TIMEOUT_SECONDS)
        self._report = self._protocol.report

        # 5. Start keep-alive thread
        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True,
        )
        self._keepalive_thread.start()

        logger.info(
            "Switch USB controller ready (player=%s)",
            self._protocol.player_number,
        )

    def _disconnect_sync(self) -> None:
        self._teardown()
        logger.info("Switch USB controller disconnected")

    def _teardown(self) -> None:
        # Stop keep-alive
        self._keepalive_stop.set()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=2)
            self._keepalive_thread = None

        # Close hidg fd
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._protocol = None

        # Soft-detach: release UDC.  ConfigFS state is preserved so the
        # next connect() is a fast rebind.
        if self._gadget is not None:
            try:
                self._gadget.unbind()
            except Exception as exc:
                logger.warning("UDC unbind failed: %s", exc)
            self._gadget = None

    # ------------------------------------------------------------------
    # Keep-alive loop (background thread)
    # ------------------------------------------------------------------

    def _keepalive_loop(self) -> None:
        interval = 1.0 / USB_KEEPALIVE_HZ
        while not self._keepalive_stop.is_set():
            try:
                with self._send_lock:
                    self._protocol.process_incoming()
                    self._conn.send(self._report.standard_report())
            except OSError:
                logger.warning("USB keep-alive send failed — host detached?")
                self._state = ControllerState.ERROR
                break
            self._keepalive_stop.wait(interval)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _press_sync(self, buttons: list[str], duration: float) -> None:
        try:
            self._report.buttons.update(buttons)
            with self._send_lock:
                self._conn.send(self._report.standard_report())
            time.sleep(duration)
            self._report.buttons.difference_update(buttons)
            with self._send_lock:
                self._conn.send(self._report.standard_report())
        except OSError as exc:
            self._state = ControllerState.ERROR
            raise RuntimeError(f"Connection lost: {exc}") from exc

    def _ensure_connected(self) -> None:
        if self._conn is None or self._state != ControllerState.CONNECTED:
            raise RuntimeError("Switch USB controller is not connected.")
