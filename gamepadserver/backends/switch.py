"""Switch Pro Controller backend using the in-house bluetooth/ HID stack."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time

from gamepadserver.bluetooth.adapter import BluetoothAdapter
from gamepadserver.bluetooth.agent import BlueZAgent
from gamepadserver.bluetooth.constants import (
    DEVICE_CLASS,
    SWITCH_CONNECTION_TIMEOUT_SECONDS,
)
from gamepadserver.bluetooth.l2cap import L2CAPConnection
from gamepadserver.bluetooth.sdp import SDPService
from gamepadserver.bluetooth.switch_protocol import SwitchProtocol
from gamepadserver.bluetooth.switch_report import ReportBuilder, stick_from_api
from gamepadserver.core.backend import GamepadBackend
from gamepadserver.core.models import ControllerState, InputState

logger = logging.getLogger(__name__)

# API button name → ReportBuilder button name (BUTTON_MAP keys in constants.py)
_BUTTON_MAP: dict[str, str] = {
    "A": "A", "B": "B", "X": "X", "Y": "Y",
    "L": "L", "R": "R", "ZL": "ZL", "ZR": "ZR",
    "PLUS": "PLUS", "MINUS": "MINUS",
    "HOME": "HOME", "CAPTURE": "CAPTURE",
    "DPAD_UP": "DPAD_UP", "DPAD_DOWN": "DPAD_DOWN",
    "DPAD_LEFT": "DPAD_LEFT", "DPAD_RIGHT": "DPAD_RIGHT",
    "L_STICK": "L_STICK", "R_STICK": "R_STICK",
}


def _map_buttons(buttons: list[str]) -> list[str]:
    """Map API button names to protocol button names."""
    result = []
    for b in buttons:
        mapped = _BUTTON_MAP.get(b)
        if mapped is None:
            raise ValueError(f"Unsupported button for Switch: {b}")
        result.append(mapped)
    return result


class SwitchBackend(GamepadBackend):
    """Switch Pro Controller backend using bluetooth/ HID stack."""

    def __init__(self) -> None:
        self._adapter: BluetoothAdapter | None = None
        self._agent: BlueZAgent | None = None
        self._sdp: SDPService | None = None
        self._conn: L2CAPConnection | None = None
        self._protocol: SwitchProtocol | None = None
        self._report = ReportBuilder()
        self._state = ControllerState.DISCONNECTED
        self._send_lock = threading.Lock()
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # GamepadBackend interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._state = ControllerState.CONNECTING
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._connect_sync)
            self._state = ControllerState.CONNECTED
        except Exception:
            self._state = ControllerState.ERROR
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
        # Apply buttons
        self._report.buttons = {
            _BUTTON_MAP[name]
            for name, pressed in state.buttons.items()
            if pressed and name in _BUTTON_MAP
        }
        # Apply sticks
        lx, ly = state.left_stick
        rx, ry = state.right_stick
        self._report.left_stick = stick_from_api(lx, ly, "left")
        self._report.right_stick = stick_from_api(rx, ry, "right")

    # ------------------------------------------------------------------
    # Sync connection flow (runs in executor thread)
    # ------------------------------------------------------------------

    def _connect_sync(self) -> None:
        # 1. Adapter setup
        self._adapter = BluetoothAdapter()
        self._adapter.setup()
        bd_addr = self._adapter.get_address()
        logger.info("Adapter address: %s", bd_addr)

        # 2. Agent registration (for SSP pairing)
        self._agent = BlueZAgent()
        self._agent.register()

        # 3. SDP HID profile registration
        self._sdp = SDPService()
        self._sdp.register()

        # 4. Re-apply device class (bluetoothd resets it during RegisterProfile)
        dev = self._adapter.device
        for cmd in [
            ["hciconfig", dev, "class", DEVICE_CLASS],
            ["hciconfig", dev, "piscan"],
            ["hciconfig", dev, "class", DEVICE_CLASS],
        ]:
            subprocess.run(cmd, capture_output=True, timeout=5, check=False)
        logger.info("Device class re-applied")

        # 5. Wait for Switch to connect via raw L2CAP sockets
        logger.info("Waiting for Switch connection…")
        ctrl_sock, itr_sock = self._sdp.wait_for_connection(
            adapter_address=bd_addr,
            timeout=SWITCH_CONNECTION_TIMEOUT_SECONDS,
        )
        self._conn = L2CAPConnection(ctrl_sock, itr_sock)
        logger.info("Switch connected")

        # 6. Handshake
        self._protocol = SwitchProtocol(self._conn, bd_addr)
        self._protocol.handshake(timeout=SWITCH_CONNECTION_TIMEOUT_SECONDS)
        # Adopt the protocol's report builder so we keep the timer in sync
        self._report = self._protocol.report

        # 7. Start keep-alive thread
        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True,
        )
        self._keepalive_thread.start()

        # 8. Press L+R to pass "Change Grip/Order" screen
        self._press_sync(["L", "R"], 0.5)
        logger.info("Switch controller ready (player=%s)",
                     self._protocol.player_number)

    def _disconnect_sync(self) -> None:
        # Stop keep-alive
        self._keepalive_stop.set()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=2)
            self._keepalive_thread = None

        # Close connection
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._protocol = None

        # Unregister services
        if self._sdp is not None:
            self._sdp.unregister()
            self._sdp = None
        if self._agent is not None:
            self._agent.unregister()
            self._agent = None

        logger.info("Switch controller disconnected")

    # ------------------------------------------------------------------
    # Keep-alive loop (background thread)
    # ------------------------------------------------------------------

    def _keepalive_loop(self) -> None:
        while not self._keepalive_stop.is_set():
            try:
                with self._send_lock:
                    # Drain and respond to any post-handshake subcommands
                    # from the Switch (it may send additional requests after
                    # the initial handshake — ignoring them causes disconnect)
                    self._protocol.process_incoming()
                    # Send current input state
                    self._conn.send(self._report.standard_report())
            except OSError:
                logger.warning("Keep-alive send failed — connection lost")
                self._state = ControllerState.ERROR
                break
            self._keepalive_stop.wait(1 / 15)

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
            raise RuntimeError("Switch controller is not connected.")
