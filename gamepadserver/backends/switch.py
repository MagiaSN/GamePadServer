from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any, Optional

from gamepadserver.core.backend import GamepadBackend
from gamepadserver.core.models import ControllerState, InputState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Button / stick mapping:  API enum value  →  NXBT constant string
# NXBT's Buttons class uses string constants, and press_buttons() expects
# a list of those constants.
# ---------------------------------------------------------------------------

_BUTTON_MAP: dict[str, str] = {
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "L": "L",
    "R": "R",
    "ZL": "ZL",
    "ZR": "ZR",
    "PLUS": "PLUS",
    "MINUS": "MINUS",
    "HOME": "HOME",
    "CAPTURE": "CAPTURE",
    "DPAD_UP": "DPAD_UP",
    "DPAD_DOWN": "DPAD_DOWN",
    "DPAD_LEFT": "DPAD_LEFT",
    "DPAD_RIGHT": "DPAD_RIGHT",
    "L_STICK": "L_STICK_PRESS",
    "R_STICK": "R_STICK_PRESS",
}

# Keys used in NXBT's input_packet for buttons
_INPUT_PACKET_BUTTON_MAP: dict[str, str] = {
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "L": "L",
    "R": "R",
    "ZL": "ZL",
    "ZR": "ZR",
    "PLUS": "PLUS",
    "MINUS": "MINUS",
    "HOME": "HOME",
    "CAPTURE": "CAPTURE",
    "DPAD_UP": "DPAD_UP",
    "DPAD_DOWN": "DPAD_DOWN",
    "DPAD_LEFT": "DPAD_LEFT",
    "DPAD_RIGHT": "DPAD_RIGHT",
}

_NXBT_STATE_MAP: dict[str, ControllerState] = {
    "initializing": ControllerState.CONNECTING,
    "connecting": ControllerState.CONNECTING,
    "reconnecting": ControllerState.CONNECTING,
    "connected": ControllerState.CONNECTED,
    "crashed": ControllerState.ERROR,
}


def _map_buttons(buttons: list[str]) -> list[str]:
    """Map API button names to NXBT button constants."""
    result = []
    for b in buttons:
        mapped = _BUTTON_MAP.get(b)
        if mapped is None:
            raise ValueError(f"Unsupported button for Switch: {b}")
        result.append(mapped)
    return result


class SwitchBackend(GamepadBackend):
    """Switch Pro Controller backend powered by NXBT."""

    def __init__(self) -> None:
        self._nxbt: Any = None
        self._controller_index: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_nxbt_module(self) -> Any:
        """Lazy import so the module is only loaded when actually used."""
        import nxbt as _nxbt_module
        return _nxbt_module

    async def _run_sync(self, func, *args, **kwargs):
        """Run a blocking NXBT call in a thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def connect(self) -> None:
        nxbt_mod = self._get_nxbt_module()
        self._nxbt = nxbt_mod.Nxbt()
        self._controller_index = await self._run_sync(
            self._nxbt.create_controller,
            nxbt_mod.PRO_CONTROLLER,
        )
        await self._run_sync(
            self._nxbt.wait_for_connection,
            self._controller_index,
        )
        logger.info("Switch controller %d connected", self._controller_index)

    async def disconnect(self) -> None:
        if self._nxbt is not None and self._controller_index is not None:
            try:
                await self._run_sync(
                    self._nxbt.remove_controller,
                    self._controller_index,
                )
            except Exception as exc:
                logger.warning("Error removing NXBT controller: %s", exc)
            self._controller_index = None
            self._nxbt = None

    async def get_state(self) -> ControllerState:
        if self._nxbt is None or self._controller_index is None:
            return ControllerState.DISCONNECTED
        try:
            state_dict = self._nxbt.state
            ctrl_state = state_dict.get(self._controller_index, {})
            raw = ctrl_state.get("state", "crashed")
            return _NXBT_STATE_MAP.get(raw, ControllerState.ERROR)
        except Exception:
            return ControllerState.ERROR

    async def press_buttons(self, buttons: list[str], duration: float = 0.1) -> None:
        self._ensure_connected()
        nxbt_mod = self._get_nxbt_module()
        mapped = _map_buttons(buttons)
        nxbt_buttons = [getattr(nxbt_mod.Buttons, b) for b in mapped]
        await self._run_sync(
            self._nxbt.press_buttons,
            self._controller_index,
            nxbt_buttons,
            down=duration,
            up=0.05,
            block=True,
        )

    async def hold_buttons(self, buttons: list[str]) -> None:
        self._ensure_connected()
        nxbt_mod = self._get_nxbt_module()
        mapped = _map_buttons(buttons)
        nxbt_buttons = [getattr(nxbt_mod.Buttons, b) for b in mapped]
        await self._run_sync(
            self._nxbt.press_buttons,
            self._controller_index,
            nxbt_buttons,
            down=0.0,
            up=0.0,
            block=False,
        )

    async def release_buttons(self, buttons: list[str]) -> None:
        # NXBT doesn't have an explicit release API.
        # We achieve release by sending a neutral input packet.
        self._ensure_connected()
        packet = self._nxbt.create_input_packet()
        self._nxbt.set_controller_input(self._controller_index, packet)

    async def set_stick(self, stick: str, x: int, y: int) -> None:
        self._ensure_connected()
        nxbt_mod = self._get_nxbt_module()
        nxbt_stick = (
            nxbt_mod.Sticks.LEFT_STICK if stick == "left"
            else nxbt_mod.Sticks.RIGHT_STICK
        )
        await self._run_sync(
            self._nxbt.tilt_stick,
            self._controller_index,
            nxbt_stick,
            x,
            y,
            tilted=0.0,
            released=0.0,
            block=False,
        )

    async def send_input(self, state: InputState) -> None:
        self._ensure_connected()
        packet = self._nxbt.create_input_packet()

        # Set buttons
        for api_name, pressed in state.buttons.items():
            pkt_key = _INPUT_PACKET_BUTTON_MAP.get(api_name)
            if pkt_key and pkt_key in packet:
                packet[pkt_key] = pressed

        # Handle stick presses
        if state.buttons.get("L_STICK", False):
            packet["L_STICK"]["PRESSED"] = True
        if state.buttons.get("R_STICK", False):
            packet["R_STICK"]["PRESSED"] = True

        # Set sticks
        lx, ly = state.left_stick
        rx, ry = state.right_stick
        packet["L_STICK"]["X_VALUE"] = lx
        packet["L_STICK"]["Y_VALUE"] = ly
        packet["R_STICK"]["X_VALUE"] = rx
        packet["R_STICK"]["Y_VALUE"] = ry

        self._nxbt.set_controller_input(self._controller_index, packet)

    def _ensure_connected(self) -> None:
        if self._nxbt is None or self._controller_index is None:
            raise RuntimeError("Switch controller is not connected.")
