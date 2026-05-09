import pytest
import pytest_asyncio

from unittest.mock import AsyncMock, patch, MagicMock

from gamepadserver.core.manager import ControllerManager
from gamepadserver.core.models import ControllerState, Platform, Transport


class MockBackend:
    def __init__(self):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def get_state(self):
        return ControllerState.CONNECTED if self.connected else ControllerState.DISCONNECTED

    async def press_buttons(self, buttons, duration=0.1):
        pass

    async def hold_buttons(self, buttons):
        pass

    async def release_buttons(self, buttons):
        pass

    async def set_stick(self, stick, x, y):
        pass

    async def send_input(self, state):
        pass


@pytest.fixture
def manager():
    m = ControllerManager()
    # Patch _create_backend to return MockBackend
    m._create_backend = lambda platform, transport: MockBackend()
    return m


@pytest.mark.asyncio
async def test_create_controller(manager):
    info = await manager.create_controller(Platform.SWITCH)
    assert info.id == 0
    assert info.platform == Platform.SWITCH
    assert info.state == ControllerState.CONNECTING


@pytest.mark.asyncio
async def test_id_increments(manager):
    info1 = await manager.create_controller(Platform.SWITCH)
    info2 = await manager.create_controller(Platform.SWITCH)
    assert info1.id == 0
    assert info2.id == 1


@pytest.mark.asyncio
async def test_get_controller(manager):
    info = await manager.create_controller(Platform.SWITCH)
    entry = manager.get_controller(info.id)
    assert entry is not None
    got_info, backend = entry
    assert got_info.id == info.id


@pytest.mark.asyncio
async def test_get_nonexistent_controller(manager):
    assert manager.get_controller(999) is None


@pytest.mark.asyncio
async def test_list_controllers(manager):
    await manager.create_controller(Platform.SWITCH)
    await manager.create_controller(Platform.SWITCH)
    lst = manager.list_controllers()
    assert len(lst) == 2


@pytest.mark.asyncio
async def test_remove_controller(manager):
    info = await manager.create_controller(Platform.SWITCH)
    removed = await manager.remove_controller(info.id)
    assert removed is True
    assert manager.get_controller(info.id) is None


@pytest.mark.asyncio
async def test_remove_nonexistent(manager):
    removed = await manager.remove_controller(999)
    assert removed is False


@pytest.mark.asyncio
async def test_unsupported_platform():
    manager = ControllerManager()
    with pytest.raises(ValueError, match="not yet supported"):
        await manager.create_controller(Platform.PS4)


@pytest.mark.asyncio
async def test_switch_usb_transport_uses_usb_backend(manager):
    """A switch+usb create request should use the USB backend factory branch."""
    seen: list[Transport] = []

    def fake_factory(platform, transport):
        seen.append(transport)
        return MockBackend()

    manager._create_backend = fake_factory
    info = await manager.create_controller(Platform.SWITCH, Transport.USB)
    assert info.transport == Transport.USB
    assert seen == [Transport.USB]


@pytest.mark.asyncio
async def test_default_transport_is_bluetooth(manager):
    info = await manager.create_controller(Platform.SWITCH)
    assert info.transport == Transport.BLUETOOTH
