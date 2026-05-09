"""Unit tests for SwitchUSBBackend (no real USB gadget required).

The transport layer (USBGadget + HIDGDevice) and the protocol handshake
are mocked.  These tests verify the backend's public API surface stays
identical to SwitchBackend (BT) and that the lifecycle wires through to
the gadget bind/unbind primitives.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from gamepadserver.backends.switch_usb import (
    SwitchUSBBackend,
    _BUTTON_MAP,
    _map_buttons,
)
from gamepadserver.bluetooth.switch_report import stick_from_api
from gamepadserver.core.models import ControllerState, InputState


# ---------------------------------------------------------------------------
# Button mapping (must match BT backend's mapping)
# ---------------------------------------------------------------------------

class TestUSBButtonMapping:

    def test_all_api_buttons_mapped(self):
        expected = {
            "A", "B", "X", "Y",
            "L", "R", "ZL", "ZR",
            "PLUS", "MINUS", "HOME", "CAPTURE",
            "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
            "L_STICK", "R_STICK",
        }
        assert set(_BUTTON_MAP.keys()) == expected

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported button"):
            _map_buttons(["CROSS"])


# ---------------------------------------------------------------------------
# Connected-state fixture (no real USB)
# ---------------------------------------------------------------------------

def _make_connected_backend() -> SwitchUSBBackend:
    backend = SwitchUSBBackend()
    backend._gadget = MagicMock()
    backend._conn = MagicMock()
    backend._protocol = MagicMock()
    backend._protocol.player_number = 1
    backend._state = ControllerState.CONNECTED
    return backend


# ---------------------------------------------------------------------------
# State / connection guards
# ---------------------------------------------------------------------------

class TestUSBState:

    @pytest.mark.asyncio
    async def test_get_state_disconnected(self):
        backend = SwitchUSBBackend()
        assert await backend.get_state() == ControllerState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_get_state_connected(self):
        backend = _make_connected_backend()
        assert await backend.get_state() == ControllerState.CONNECTED

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        backend = SwitchUSBBackend()
        with pytest.raises(RuntimeError, match="not connected"):
            await backend.press_buttons(["A"])

    @pytest.mark.asyncio
    async def test_error_state_rejects_input(self):
        backend = _make_connected_backend()
        backend._state = ControllerState.ERROR
        with pytest.raises(RuntimeError, match="not connected"):
            await backend.press_buttons(["A"])


# ---------------------------------------------------------------------------
# Buttons / sticks (shared encoding paths with BT — sanity-check only)
# ---------------------------------------------------------------------------

class TestUSBButtonsAndSticks:

    @pytest.mark.asyncio
    async def test_hold_and_release(self):
        backend = _make_connected_backend()
        await backend.hold_buttons(["A", "B"])
        assert "A" in backend._report.buttons
        assert "B" in backend._report.buttons
        await backend.release_buttons(["A"])
        assert "A" not in backend._report.buttons
        assert "B" in backend._report.buttons

    @pytest.mark.asyncio
    async def test_set_stick(self):
        backend = _make_connected_backend()
        await backend.set_stick("left", 80, -50)
        assert backend._report.left_stick == stick_from_api(80, -50, "left")

    @pytest.mark.asyncio
    async def test_send_input_replaces_state(self):
        backend = _make_connected_backend()
        await backend.send_input(InputState(buttons={"A": True}))
        assert backend._report.buttons == {"A"}
        await backend.send_input(InputState(buttons={"B": True}))
        assert backend._report.buttons == {"B"}


# ---------------------------------------------------------------------------
# Connect / disconnect lifecycle
# ---------------------------------------------------------------------------

class TestUSBLifecycle:

    @pytest.mark.asyncio
    async def test_connect_binds_udc_and_starts_keepalive(self):
        backend = SwitchUSBBackend()

        gadget = MagicMock()
        gadget.wait_for_hidg.return_value = "/dev/hidg0"
        conn = MagicMock()
        proto = MagicMock()
        proto.player_number = 1
        proto.report = backend._report

        with patch(
            "gamepadserver.backends.switch_usb.USBGadget",
            return_value=gadget,
        ), patch(
            "gamepadserver.backends.switch_usb.open_hidg",
            return_value=conn,
        ), patch(
            "gamepadserver.backends.switch_usb.SwitchUSBProtocol",
            return_value=proto,
        ):
            await backend.connect()

        gadget.setup.assert_called_once()
        gadget.bind.assert_called_once()
        gadget.wait_for_hidg.assert_called_once()
        proto.handshake.assert_called_once()
        assert backend._state == ControllerState.CONNECTED
        assert backend._keepalive_thread is not None

        # Tear down so the keep-alive thread doesn't leak
        backend._keepalive_stop.set()
        backend._keepalive_thread.join(timeout=1)

    @pytest.mark.asyncio
    async def test_connect_failure_unbinds(self):
        backend = SwitchUSBBackend()

        gadget = MagicMock()
        gadget.wait_for_hidg.side_effect = RuntimeError("hidg never appeared")

        with patch(
            "gamepadserver.backends.switch_usb.USBGadget",
            return_value=gadget,
        ):
            with pytest.raises(RuntimeError, match="hidg"):
                await backend.connect()

        # Ensure soft-detach was attempted on the failure path
        gadget.unbind.assert_called_once()
        assert backend._state == ControllerState.ERROR

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        backend = _make_connected_backend()
        gadget = backend._gadget
        conn = backend._conn

        backend._keepalive_stop = MagicMock()
        backend._keepalive_thread = None

        await backend.disconnect()

        conn.close.assert_called_once()
        gadget.unbind.assert_called_once()
        assert backend._conn is None
        assert backend._gadget is None
        assert backend._protocol is None
        assert await backend.get_state() == ControllerState.DISCONNECTED
