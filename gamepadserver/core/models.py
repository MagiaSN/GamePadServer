from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    SWITCH = "switch"
    PS4 = "ps4"
    PS5 = "ps5"
    XBOX = "xbox"


class Transport(str, Enum):
    BLUETOOTH = "bluetooth"
    USB = "usb"


class ControllerState(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Button definitions
# ---------------------------------------------------------------------------

# Face buttons – platform-specific
SWITCH_XBOX_FACE_BUTTONS = {"A", "B", "X", "Y"}
PS_FACE_BUTTONS = {"CROSS", "CIRCLE", "SQUARE", "TRIANGLE"}

# Common buttons – shared across all platforms
COMMON_BUTTONS = {
    "L", "R", "ZL", "ZR",
    "PLUS", "MINUS",
    "HOME", "CAPTURE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
    "L_STICK", "R_STICK",
}

ALL_SWITCH_XBOX_BUTTONS = SWITCH_XBOX_FACE_BUTTONS | COMMON_BUTTONS
ALL_PS_BUTTONS = PS_FACE_BUTTONS | COMMON_BUTTONS
ALL_BUTTONS = SWITCH_XBOX_FACE_BUTTONS | PS_FACE_BUTTONS | COMMON_BUTTONS

VALID_STICKS = {"left", "right"}


def valid_buttons_for_platform(platform: Platform) -> set[str]:
    if platform in (Platform.SWITCH, Platform.XBOX):
        return ALL_SWITCH_XBOX_BUTTONS
    return ALL_PS_BUTTONS


def validate_buttons(buttons: list[str], platform: Platform) -> None:
    """Raise ValueError if any button is invalid for the given platform."""
    allowed = valid_buttons_for_platform(platform)
    for b in buttons:
        if b not in allowed:
            if platform in (Platform.SWITCH, Platform.XBOX) and b in PS_FACE_BUTTONS:
                raise ValueError(
                    f"Button '{b}' is a PlayStation button. "
                    f"Use A/B/X/Y for {platform.value}."
                )
            if platform in (Platform.PS4, Platform.PS5) and b in SWITCH_XBOX_FACE_BUTTONS:
                raise ValueError(
                    f"Button '{b}' is a Switch/Xbox button. "
                    f"Use CROSS/CIRCLE/SQUARE/TRIANGLE for {platform.value}."
                )
            raise ValueError(f"Unknown button '{b}' for platform {platform.value}.")


def validate_stick(stick: str) -> None:
    if stick not in VALID_STICKS:
        raise ValueError(f"Invalid stick '{stick}'. Must be 'left' or 'right'.")


def validate_stick_value(value: int, name: str) -> None:
    if not (-100 <= value <= 100):
        raise ValueError(f"Stick {name} must be between -100 and 100, got {value}.")


# ---------------------------------------------------------------------------
# Input state (internal representation)
# ---------------------------------------------------------------------------

@dataclass
class InputState:
    """Complete gamepad input state frame."""
    buttons: dict[str, bool] = field(default_factory=dict)
    left_stick: tuple[int, int] = (0, 0)
    right_stick: tuple[int, int] = (0, 0)


# ---------------------------------------------------------------------------
# Controller metadata (internal)
# ---------------------------------------------------------------------------

@dataclass
class ControllerInfo:
    """Internal bookkeeping for a controller instance."""
    id: int
    platform: Platform
    transport: Transport = Transport.BLUETOOTH
    state: ControllerState = ControllerState.CONNECTING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class CreateControllerRequest(BaseModel):
    platform: Platform
    transport: Transport = Transport.BLUETOOTH


class ControllerResponse(BaseModel):
    id: int
    platform: Platform
    transport: Transport
    state: ControllerState
    created_at: datetime
    error: Optional[str] = None


class ButtonsRequest(BaseModel):
    buttons: list[str]
    action: str = "press"  # "press" | "down" | "up"
    duration: float = 0.1

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("press", "down", "up"):
            raise ValueError("action must be 'press', 'down', or 'up'")
        return v

    @field_validator("buttons")
    @classmethod
    def validate_buttons_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("buttons list must not be empty")
        return v


class StickRequest(BaseModel):
    stick: str  # "left" | "right"
    x: int = 0
    y: int = 0

    @field_validator("stick")
    @classmethod
    def validate_stick_name(cls, v: str) -> str:
        validate_stick(v)
        return v

    @field_validator("x", "y")
    @classmethod
    def validate_range(cls, v: int) -> int:
        if not (-100 <= v <= 100):
            raise ValueError("Value must be between -100 and 100")
        return v


class InputFrame(BaseModel):
    """WebSocket input frame from client."""
    buttons: dict[str, bool] = {}
    left_stick: dict[str, int] = {"x": 0, "y": 0}
    right_stick: dict[str, int] = {"x": 0, "y": 0}

    def to_input_state(self) -> InputState:
        return InputState(
            buttons=self.buttons,
            left_stick=(self.left_stick.get("x", 0), self.left_stick.get("y", 0)),
            right_stick=(self.right_stick.get("x", 0), self.right_stick.get("y", 0)),
        )


class StatusResponse(BaseModel):
    status: str = "ok"
