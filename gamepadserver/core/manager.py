from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from gamepadserver.core.backend import GamepadBackend
from gamepadserver.core.models import (
    ControllerInfo,
    ControllerState,
    InputState,
    Platform,
)

logger = logging.getLogger(__name__)


class ControllerManager:
    """Manages gamepad backend instances."""

    def __init__(self) -> None:
        self._next_id: int = 0
        self._controllers: dict[int, tuple[ControllerInfo, GamepadBackend]] = {}

    def _allocate_id(self) -> int:
        cid = self._next_id
        self._next_id += 1
        return cid

    def _create_backend(self, platform: Platform) -> GamepadBackend:
        if platform == Platform.SWITCH:
            from gamepadserver.backends.switch import SwitchBackend
            return SwitchBackend()
        raise ValueError(f"Platform '{platform.value}' is not yet supported.")

    async def create_controller(self, platform: Platform) -> ControllerInfo:
        cid = self._allocate_id()
        backend = self._create_backend(platform)
        info = ControllerInfo(
            id=cid,
            platform=platform,
            state=ControllerState.CONNECTING,
            created_at=datetime.now(timezone.utc),
        )
        self._controllers[cid] = (info, backend)

        # Start connection in background so the API returns immediately
        asyncio.create_task(self._connect(cid))
        return info

    async def _connect(self, cid: int) -> None:
        entry = self._controllers.get(cid)
        if entry is None:
            return
        info, backend = entry
        try:
            await backend.connect()
            info.state = ControllerState.CONNECTED
        except Exception as exc:
            logger.error("Controller %d connection failed: %s", cid, exc)
            info.state = ControllerState.ERROR
            info.error = str(exc)

    def get_controller(self, cid: int) -> Optional[tuple[ControllerInfo, GamepadBackend]]:
        return self._controllers.get(cid)

    def list_controllers(self) -> list[ControllerInfo]:
        return [info for info, _ in self._controllers.values()]

    async def remove_controller(self, cid: int) -> bool:
        entry = self._controllers.pop(cid, None)
        if entry is None:
            return False
        info, backend = entry
        try:
            await backend.disconnect()
        except Exception as exc:
            logger.warning("Error disconnecting controller %d: %s", cid, exc)
        info.state = ControllerState.DISCONNECTED
        return True
