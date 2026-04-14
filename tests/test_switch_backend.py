import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from gamepadserver.backends.switch import (
    SwitchBackend,
    _map_buttons,
    _BUTTON_MAP,
    _NXBT_STATE_MAP,
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
        assert _map_buttons(["L_STICK"]) == ["L_STICK_PRESS"]
        assert _map_buttons(["R_STICK"]) == ["R_STICK_PRESS"]

    def test_map_multiple(self):
        result = _map_buttons(["A", "B", "L"])
        assert result == ["A", "B", "L"]

    def test_map_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported button"):
            _map_buttons(["CROSS"])


class TestStateMapping:

    def test_all_nxbt_states_mapped(self):
        for state in ["initializing", "connecting", "reconnecting", "connected", "crashed"]:
            assert state in _NXBT_STATE_MAP


# ---------------------------------------------------------------------------
# SwitchBackend with mocked nxbt
# ---------------------------------------------------------------------------

def _make_mock_nxbt():
    """Create a mock nxbt module and instance."""
    mock_mod = MagicMock()
    mock_mod.PRO_CONTROLLER = "PRO_CONTROLLER"
    mock_mod.Sticks.LEFT_STICK = "L_STICK"
    mock_mod.Sticks.RIGHT_STICK = "R_STICK"

    # Mock Buttons with attribute access
    for btn in ["A", "B", "X", "Y", "L", "R", "ZL", "ZR",
                "PLUS", "MINUS", "HOME", "CAPTURE",
                "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
                "L_STICK_PRESS", "R_STICK_PRESS"]:
        setattr(mock_mod.Buttons, btn, btn)

    mock_instance = MagicMock()
    mock_instance.create_controller.return_value = 0
    mock_instance.state = {0: {"state": "connected"}}
    mock_instance.create_input_packet.return_value = {
        "A": False, "B": False, "X": False, "Y": False,
        "L": False, "R": False, "ZL": False, "ZR": False,
        "PLUS": False, "MINUS": False, "HOME": False, "CAPTURE": False,
        "DPAD_UP": False, "DPAD_DOWN": False, "DPAD_LEFT": False, "DPAD_RIGHT": False,
        "L_STICK": {"X_VALUE": 0, "Y_VALUE": 0, "PRESSED": False},
        "R_STICK": {"X_VALUE": 0, "Y_VALUE": 0, "PRESSED": False},
    }
    mock_mod.Nxbt.return_value = mock_instance

    return mock_mod, mock_instance


@pytest.fixture
def backend_and_mocks():
    mock_mod, mock_instance = _make_mock_nxbt()
    backend = SwitchBackend()
    backend._get_nxbt_module = lambda: mock_mod
    # Simulate connected state
    backend._nxbt = mock_instance
    backend._controller_index = 0
    return backend, mock_mod, mock_instance


class TestSwitchBackendConnect:

    @pytest.mark.asyncio
    async def test_connect(self):
        mock_mod, mock_instance = _make_mock_nxbt()
        backend = SwitchBackend()
        backend._get_nxbt_module = lambda: mock_mod

        await backend.connect()

        mock_mod.Nxbt.assert_called_once()
        mock_instance.create_controller.assert_called_once_with(mock_mod.PRO_CONTROLLER)
        mock_instance.wait_for_connection.assert_called_once_with(0)
        assert backend._controller_index == 0

    @pytest.mark.asyncio
    async def test_disconnect(self, backend_and_mocks):
        backend, _, mock_instance = backend_and_mocks
        await backend.disconnect()
        mock_instance.remove_controller.assert_called_once_with(0)
        assert backend._nxbt is None
        assert backend._controller_index is None


class TestSwitchBackendState:

    @pytest.mark.asyncio
    async def test_get_state_connected(self, backend_and_mocks):
        backend, _, _ = backend_and_mocks
        state = await backend.get_state()
        assert state == ControllerState.CONNECTED

    @pytest.mark.asyncio
    async def test_get_state_disconnected(self):
        backend = SwitchBackend()
        state = await backend.get_state()
        assert state == ControllerState.DISCONNECTED


class TestSwitchBackendInput:

    @pytest.mark.asyncio
    async def test_press_buttons(self, backend_and_mocks):
        backend, mock_mod, mock_instance = backend_and_mocks
        await backend.press_buttons(["A", "B"], duration=0.2)
        mock_instance.press_buttons.assert_called_once()
        call_args = mock_instance.press_buttons.call_args
        assert call_args[0][0] == 0  # controller_index
        assert call_args[0][1] == ["A", "B"]  # mapped buttons
        assert call_args[1]["down"] == 0.2

    @pytest.mark.asyncio
    async def test_set_stick(self, backend_and_mocks):
        backend, mock_mod, mock_instance = backend_and_mocks
        await backend.set_stick("left", 50, -30)
        mock_instance.tilt_stick.assert_called_once()
        call_args = mock_instance.tilt_stick.call_args
        assert call_args[0][1] == mock_mod.Sticks.LEFT_STICK
        assert call_args[0][2] == 50
        assert call_args[0][3] == -30

    @pytest.mark.asyncio
    async def test_send_input(self, backend_and_mocks):
        backend, _, mock_instance = backend_and_mocks
        state = InputState(
            buttons={"A": True, "L": True, "L_STICK": True},
            left_stick=(80, -50),
            right_stick=(0, 0),
        )
        await backend.send_input(state)
        mock_instance.set_controller_input.assert_called_once()
        packet = mock_instance.set_controller_input.call_args[0][1]
        assert packet["A"] is True
        assert packet["L"] is True
        assert packet["L_STICK"]["PRESSED"] is True
        assert packet["L_STICK"]["X_VALUE"] == 80
        assert packet["L_STICK"]["Y_VALUE"] == -50

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        backend = SwitchBackend()
        with pytest.raises(RuntimeError, match="not connected"):
            await backend.press_buttons(["A"])
