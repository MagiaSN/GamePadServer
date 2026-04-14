from __future__ import annotations

from abc import ABC, abstractmethod

from gamepadserver.core.models import ControllerState, InputState


class GamepadBackend(ABC):

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection with the game console."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and release all resources."""

    @abstractmethod
    async def get_state(self) -> ControllerState:
        """Return current connection state."""

    @abstractmethod
    async def press_buttons(self, buttons: list[str], duration: float = 0.1) -> None:
        """Press and release buttons."""

    @abstractmethod
    async def hold_buttons(self, buttons: list[str]) -> None:
        """Hold buttons down."""

    @abstractmethod
    async def release_buttons(self, buttons: list[str]) -> None:
        """Release held buttons."""

    @abstractmethod
    async def set_stick(self, stick: str, x: int, y: int) -> None:
        """Set stick position. Holds until next call."""

    @abstractmethod
    async def send_input(self, state: InputState) -> None:
        """Send a complete input state frame (for WebSocket real-time input)."""
