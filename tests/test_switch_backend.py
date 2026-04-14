import pytest
from unittest.mock import MagicMock, patch

from gamepadserver.backends.switch import (
    SwitchBackend,
    _map_buttons,
    _BUTTON_MAP,
)
from gamepadserver.bluetooth.switch_report import (
    LEFT_STICK_CENTER_BYTES,
    RIGHT_STICK_CENTER_BYTES,
    stick_from_api,
)
from gamepadserver.core.models import ControllerState, InputState


# ---------------------------------------------------------------------------
# Button mapping
# ---------------------------------------------------------------------------

class TestButtonMapping:

    def test_all_api_buttons_mapped(self):
        """Every API button that Switch supports should have a mapping."""
        expected = {
            "A", "B", "X", "Y",
            "L", "R", "ZL", "ZR",
            "PLUS", "MINUS", "HOME", "CAPTURE",
            "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
            "L_STICK", "R_STICK",
        }
        assert set(_BUTTON_MAP.keys()) == expected

    def test_map_single(self):
        assert _map_buttons(["A"]) == ["A"]
        assert _map_buttons(["L_STICK"]) == ["L_STICK"]
        assert _map_buttons(["R_STICK"]) == ["R_STICK"]

    def test_map_multiple(self):
        result = _map_buttons(["A", "B", "L"])
        assert result == ["A", "B", "L"]

    def test_map_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported button"):
            _map_buttons(["CROSS"])


# ---------------------------------------------------------------------------
# SwitchBackend with mocked bluetooth/ layer
# ---------------------------------------------------------------------------

def _make_connected_backend() -> SwitchBackend:
    """Create a SwitchBackend that appears connected (no real BT)."""
    backend = SwitchBackend()
    # Simulate connected state with mock objects
    backend._conn = MagicMock()
    backend._protocol = MagicMock()
    backend._protocol.player_number = 1
    backend._state = ControllerState.CONNECTED
    return backend


class TestSwitchBackendState:

    @pytest.mark.asyncio
    async def test_get_state_disconnected(self):
        backend = SwitchBackend()
        assert await backend.get_state() == ControllerState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_get_state_connected(self):
        backend = _make_connected_backend()
        assert await backend.get_state() == ControllerState.CONNECTED

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        backend = SwitchBackend()
        with pytest.raises(RuntimeError, match="not connected"):
            await backend.press_buttons(["A"])


class TestSwitchBackendButtons:

    @pytest.mark.asyncio
    async def test_hold_and_release(self):
        backend = _make_connected_backend()

        await backend.hold_buttons(["A", "B"])
        assert "A" in backend._report.buttons
        assert "B" in backend._report.buttons

        await backend.release_buttons(["A"])
        assert "A" not in backend._report.buttons
        assert "B" in backend._report.buttons

        await backend.release_buttons(["B"])
        assert len(backend._report.buttons) == 0

    @pytest.mark.asyncio
    async def test_release_only_specified(self):
        """release_buttons should not clear other held buttons."""
        backend = _make_connected_backend()
        await backend.hold_buttons(["L", "R", "A"])
        await backend.release_buttons(["A"])
        assert backend._report.buttons == {"L", "R"}


class TestSwitchBackendStick:

    @pytest.mark.asyncio
    async def test_set_stick_center(self):
        backend = _make_connected_backend()
        await backend.set_stick("left", 0, 0)
        assert backend._report.left_stick == stick_from_api(0, 0, "left")

    @pytest.mark.asyncio
    async def test_set_stick_extreme(self):
        backend = _make_connected_backend()
        await backend.set_stick("right", 100, -100)
        assert backend._report.right_stick == stick_from_api(100, -100, "right")

    @pytest.mark.asyncio
    async def test_set_stick_left_vs_right(self):
        backend = _make_connected_backend()
        await backend.set_stick("left", 50, 50)
        await backend.set_stick("right", -50, -50)
        assert backend._report.left_stick == stick_from_api(50, 50, "left")
        assert backend._report.right_stick == stick_from_api(-50, -50, "right")


class TestSwitchBackendSendInput:

    @pytest.mark.asyncio
    async def test_send_input_applies_state(self):
        backend = _make_connected_backend()
        state = InputState(
            buttons={"A": True, "L": True, "B": False},
            left_stick=(80, -50),
            right_stick=(0, 0),
        )
        await backend.send_input(state)
        assert backend._report.buttons == {"A", "L"}
        assert backend._report.left_stick == stick_from_api(80, -50, "left")
        assert backend._report.right_stick == stick_from_api(0, 0, "right")

    @pytest.mark.asyncio
    async def test_send_input_clears_previous_buttons(self):
        backend = _make_connected_backend()
        # First frame: A pressed
        await backend.send_input(InputState(buttons={"A": True}))
        assert "A" in backend._report.buttons
        # Second frame: A released, B pressed
        await backend.send_input(InputState(buttons={"B": True}))
        assert "A" not in backend._report.buttons
        assert "B" in backend._report.buttons


class TestSwitchBackendDisconnect:

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        backend = _make_connected_backend()
        backend._sdp = MagicMock()
        backend._agent = MagicMock()
        backend._keepalive_stop = MagicMock()
        backend._keepalive_thread = None

        await backend.disconnect()

        assert backend._conn is None
        assert backend._protocol is None
        assert backend._sdp is None
        assert backend._agent is None
        assert await backend.get_state() == ControllerState.DISCONNECTED
