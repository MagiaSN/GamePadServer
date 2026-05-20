"""Switch Pro Controller backend using the in-house bluetooth/ HID stack."""

from __future__ import annotations

import asyncio
import errno as _errno
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
from gamepadserver.bluetooth.l2cap import L2CAPConnection, connect_outbound
from gamepadserver.bluetooth.paired import list_paired_switches, unpair
from gamepadserver.bluetooth.sdp import SDPService
from gamepadserver.bluetooth.switch_protocol import SwitchProtocol
from gamepadserver.bluetooth.switch_report import ReportBuilder, stick_from_api
from gamepadserver.core.backend import GamepadBackend
from gamepadserver.core.models import ControllerState, InputState

logger = logging.getLogger(__name__)

# How often the keep-alive thread emits a healthy heartbeat log line.
KEEPALIVE_HEARTBEAT_INTERVAL_SECONDS = 300


def _fmt_age(seconds: float) -> str:
    """Render a duration as e.g. '32s' / '4m17s' / '3h05m'. Diagnostic only."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _errno_name(e: int | None) -> str:
    """errno number → symbolic name (e.g. 104 → 'ECONNRESET')."""
    if e is None:
        return "?"
    return _errno.errorcode.get(e, "?")


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
        # Wall-clock-ish reference for "how long has this connection lived",
        # set after the handshake completes.  None ⇒ never connected.
        self._connected_at: float | None = None

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

        # 5. Establish L2CAP channels.  If Pi already has a bond record
        #    for a Switch, the Switch will only do "dedicated bonding"
        #    on inbound connects and never open PSM 17/19 — so we must
        #    dial out instead (reconnect path).  See TASK_RECONNECT.md.
        ctrl_sock, itr_sock, switch_addr = self._open_l2cap(bd_addr)
        self._conn = L2CAPConnection(ctrl_sock, itr_sock,
                                     client_address=switch_addr)
        logger.info("Switch connected (%s)", switch_addr or "unknown")

        # 6. Handshake
        self._protocol = SwitchProtocol(self._conn, bd_addr)
        self._protocol.handshake(timeout=SWITCH_CONNECTION_TIMEOUT_SECONDS)
        # Adopt the protocol's report builder so we keep the timer in sync
        self._report = self._protocol.report
        self._connected_at = time.monotonic()

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

    def _open_l2cap(self, bd_addr: str):
        """Return (ctrl_sock, itr_sock, switch_address) via the most
        appropriate path for the current bond state.

        Prefers the outbound reconnect path when any Switch is paired
        with this adapter; falls back to the listen (first-pair) path
        on reconnect failure or if no bond is known.
        """
        import socket as _socket

        paired = list_paired_switches()
        for mac in paired:
            logger.info("Trying reconnect path to paired Switch %s", mac)
            try:
                ctrl, itr = connect_outbound(mac)
                return ctrl, itr, mac
            except (OSError, _socket.timeout) as exc:
                errno = getattr(exc, "errno", None)
                # ECONNREFUSED / ECONNRESET ⇒ Switch is reachable but
                # actively refused us, i.e. its side of the bond is
                # gone (user picked "Disconnect" on the Switch).  Drop
                # the Pi-side bond so the inbound SSP that follows
                # isn't blocked by a half-bond — see paired.unpair().
                if errno in (_errno.ECONNREFUSED, _errno.ECONNRESET):
                    logger.warning(
                        "Reconnect to %s refused (%s) — Switch-side bond is "
                        "likely gone; removing Pi-side bond and falling back "
                        "to listen path.", mac, _errno_name(errno),
                    )
                    unpair(mac)
                else:
                    logger.warning(
                        "Reconnect to %s failed (%s) — falling back to listen "
                        "path. This run will be slow; if it still fails, clear "
                        "the Switch-side bond (Controllers → disconnect) and "
                        "try again.", mac, exc,
                    )

        logger.info("Listening for Switch on L2CAP PSM 17+19 (first-pair path)")
        ctrl, itr = self._sdp.wait_for_connection(
            adapter_address=bd_addr,
            timeout=SWITCH_CONNECTION_TIMEOUT_SECONDS,
        )
        try:
            peer = itr.getpeername()[0]
        except OSError:
            peer = ""
        return ctrl, itr, peer

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

        age_s = (
            _fmt_age(time.monotonic() - self._connected_at)
            if self._connected_at is not None else "n/a"
        )
        logger.info("Switch controller disconnected (age=%s)", age_s)
        self._connected_at = None

    # ------------------------------------------------------------------
    # Keep-alive loop (background thread)
    # ------------------------------------------------------------------

    def _keepalive_loop(self) -> None:
        started_at = time.monotonic()
        last_send_ok = started_at
        last_heartbeat = started_at
        sends = 0
        logger.info("keep-alive started")

        while not self._keepalive_stop.is_set():
            try:
                with self._send_lock:
                    # Drain and respond to any post-handshake subcommands
                    # from the Switch (it may send additional requests after
                    # the initial handshake — ignoring them causes disconnect)
                    self._protocol.process_incoming()
                    # Send current input state
                    self._conn.send(self._report.standard_report())
                sends += 1
                last_send_ok = time.monotonic()
            except OSError as exc:
                ref = self._connected_at if self._connected_at is not None else started_at
                age = time.monotonic() - ref
                since_ok = time.monotonic() - last_send_ok
                logger.warning(
                    "keep-alive OSError errno=%s(%s) age=%s sends=%d "
                    "since_last_ok=%.2fs: %s",
                    _errno_name(exc.errno), exc.errno, _fmt_age(age),
                    sends, since_ok, exc,
                )
                self._state = ControllerState.ERROR
                break
            except Exception:
                # Catch-all so a non-OSError (parse bug, AttributeError, …)
                # does not silently kill the daemon thread.
                ref = self._connected_at if self._connected_at is not None else started_at
                age = time.monotonic() - ref
                logger.exception(
                    "keep-alive crashed (non-OSError) age=%s sends=%d",
                    _fmt_age(age), sends,
                )
                self._state = ControllerState.ERROR
                break

            now = time.monotonic()
            if now - last_heartbeat >= KEEPALIVE_HEARTBEAT_INTERVAL_SECONDS:
                ref = self._connected_at if self._connected_at is not None else started_at
                logger.info(
                    "keep-alive healthy age=%s sends=%d",
                    _fmt_age(now - ref), sends,
                )
                last_heartbeat = now

            self._keepalive_stop.wait(1 / 15)
        else:
            # Loop exited because the stop event was set (clean shutdown).
            ref = self._connected_at if self._connected_at is not None else started_at
            logger.info(
                "keep-alive stopped cleanly age=%s sends=%d",
                _fmt_age(time.monotonic() - ref), sends,
            )

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
            age_s = (
                _fmt_age(time.monotonic() - self._connected_at)
                if self._connected_at is not None else "n/a"
            )
            logger.warning(
                "_press_sync OSError errno=%s(%s) age=%s: %s",
                _errno_name(exc.errno), exc.errno, age_s, exc,
            )
            self._state = ControllerState.ERROR
            raise RuntimeError(f"Connection lost: {exc}") from exc

    def _ensure_connected(self) -> None:
        if self._conn is None or self._state != ControllerState.CONNECTED:
            state_name = self._state.name
            if self._connected_at is None:
                ctx = "never_connected"
            else:
                age = time.monotonic() - self._connected_at
                ctx = f"was_connected_for={_fmt_age(age)}"
            raise RuntimeError(
                f"Switch controller is not connected "
                f"(state={state_name}, {ctx})."
            )
