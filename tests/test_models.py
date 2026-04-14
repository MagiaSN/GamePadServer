import pytest

from gamepadserver.core.models import (
    ALL_PS_BUTTONS,
    ALL_SWITCH_XBOX_BUTTONS,
    COMMON_BUTTONS,
    ButtonsRequest,
    InputFrame,
    Platform,
    StickRequest,
    validate_buttons,
    validate_stick,
)


class TestButtonValidation:

    def test_switch_accepts_abxy(self):
        validate_buttons(["A", "B", "X", "Y"], Platform.SWITCH)

    def test_xbox_accepts_abxy(self):
        validate_buttons(["A", "B", "X", "Y"], Platform.XBOX)

    def test_ps4_accepts_ps_buttons(self):
        validate_buttons(["CROSS", "CIRCLE", "SQUARE", "TRIANGLE"], Platform.PS4)

    def test_ps5_accepts_ps_buttons(self):
        validate_buttons(["CROSS", "CIRCLE", "SQUARE", "TRIANGLE"], Platform.PS5)

    def test_switch_rejects_ps_buttons(self):
        with pytest.raises(ValueError, match="PlayStation button"):
            validate_buttons(["CROSS"], Platform.SWITCH)

    def test_xbox_rejects_ps_buttons(self):
        with pytest.raises(ValueError, match="PlayStation button"):
            validate_buttons(["TRIANGLE"], Platform.XBOX)

    def test_ps4_rejects_abxy(self):
        with pytest.raises(ValueError, match="Switch/Xbox button"):
            validate_buttons(["A"], Platform.PS4)

    def test_ps5_rejects_abxy(self):
        with pytest.raises(ValueError, match="Switch/Xbox button"):
            validate_buttons(["B"], Platform.PS5)

    def test_common_buttons_accepted_on_all_platforms(self):
        for btn in COMMON_BUTTONS:
            for platform in Platform:
                validate_buttons([btn], platform)

    def test_unknown_button_rejected(self):
        with pytest.raises(ValueError, match="Unknown button"):
            validate_buttons(["FAKE_BUTTON"], Platform.SWITCH)

    def test_all_switch_xbox_buttons_complete(self):
        assert "A" in ALL_SWITCH_XBOX_BUTTONS
        assert "L" in ALL_SWITCH_XBOX_BUTTONS
        assert "DPAD_UP" in ALL_SWITCH_XBOX_BUTTONS
        assert "CROSS" not in ALL_SWITCH_XBOX_BUTTONS

    def test_all_ps_buttons_complete(self):
        assert "CROSS" in ALL_PS_BUTTONS
        assert "L" in ALL_PS_BUTTONS
        assert "A" not in ALL_PS_BUTTONS


class TestStickValidation:

    def test_valid_sticks(self):
        validate_stick("left")
        validate_stick("right")

    def test_invalid_stick(self):
        with pytest.raises(ValueError, match="Invalid stick"):
            validate_stick("middle")


class TestPydanticModels:

    def test_buttons_request_valid(self):
        req = ButtonsRequest(buttons=["A"], action="press", duration=0.1)
        assert req.buttons == ["A"]

    def test_buttons_request_invalid_action(self):
        with pytest.raises(ValueError):
            ButtonsRequest(buttons=["A"], action="smash")

    def test_buttons_request_empty_buttons(self):
        with pytest.raises(ValueError):
            ButtonsRequest(buttons=[], action="press")

    def test_stick_request_valid(self):
        req = StickRequest(stick="left", x=50, y=-100)
        assert req.x == 50

    def test_stick_request_invalid_stick(self):
        with pytest.raises(ValueError):
            StickRequest(stick="middle", x=0, y=0)

    def test_stick_request_out_of_range(self):
        with pytest.raises(ValueError):
            StickRequest(stick="left", x=200, y=0)

    def test_input_frame_to_input_state(self):
        frame = InputFrame(
            buttons={"A": True, "B": False},
            left_stick={"x": 50, "y": -30},
            right_stick={"x": 0, "y": 0},
        )
        state = frame.to_input_state()
        assert state.buttons == {"A": True, "B": False}
        assert state.left_stick == (50, -30)
        assert state.right_stick == (0, 0)

    def test_input_frame_defaults(self):
        frame = InputFrame()
        state = frame.to_input_state()
        assert state.buttons == {}
        assert state.left_stick == (0, 0)
