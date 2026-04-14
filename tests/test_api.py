import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from gamepadserver.core.models import ControllerState, Platform


# ---------------------------------------------------------------------------
# Fixtures: create a TestClient with a mocked backend
# ---------------------------------------------------------------------------

class MockBackend:
    def __init__(self):
        self.state = ControllerState.CONNECTED

    async def connect(self):
        self.state = ControllerState.CONNECTED

    async def disconnect(self):
        self.state = ControllerState.DISCONNECTED

    async def get_state(self):
        return self.state

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
def client():
    """Create a test client with mocked ControllerManager."""
    from gamepadserver import app as app_module
    from gamepadserver.core.manager import ControllerManager

    # Replace the global manager with one that uses MockBackend
    manager = ControllerManager()
    manager._create_backend = lambda platform: MockBackend()
    app_module.controller_manager = manager

    return TestClient(app_module.app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Controller lifecycle
# ---------------------------------------------------------------------------

class TestControllerLifecycle:

    def test_create_controller(self, client):
        res = client.post("/api/v1/controllers", json={"platform": "switch"})
        assert res.status_code == 201
        data = res.json()
        assert data["platform"] == "switch"
        assert "id" in data
        assert "created_at" in data

    def test_list_controllers(self, client):
        client.post("/api/v1/controllers", json={"platform": "switch"})
        res = client.get("/api/v1/controllers")
        assert res.status_code == 200
        assert len(res.json()) >= 1

    def test_get_controller(self, client):
        create_res = client.post("/api/v1/controllers", json={"platform": "switch"})
        cid = create_res.json()["id"]
        res = client.get(f"/api/v1/controllers/{cid}")
        assert res.status_code == 200
        assert res.json()["id"] == cid

    def test_get_nonexistent(self, client):
        res = client.get("/api/v1/controllers/999")
        assert res.status_code == 404

    def test_delete_controller(self, client):
        create_res = client.post("/api/v1/controllers", json={"platform": "switch"})
        cid = create_res.json()["id"]
        res = client.delete(f"/api/v1/controllers/{cid}")
        assert res.status_code == 200
        # Verify it's gone
        res2 = client.get(f"/api/v1/controllers/{cid}")
        assert res2.status_code == 404

    def test_delete_nonexistent(self, client):
        res = client.delete("/api/v1/controllers/999")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# Button operations
# ---------------------------------------------------------------------------

class TestButtons:

    def _create(self, client, platform="switch"):
        res = client.post("/api/v1/controllers", json={"platform": platform})
        return res.json()["id"]

    def test_press_button(self, client):
        cid = self._create(client)
        res = client.post(f"/api/v1/controllers/{cid}/buttons", json={
            "buttons": ["A"],
            "action": "press",
            "duration": 0.1,
        })
        assert res.status_code == 200

    def test_hold_and_release(self, client):
        cid = self._create(client)
        res = client.post(f"/api/v1/controllers/{cid}/buttons", json={
            "buttons": ["B"],
            "action": "down",
        })
        assert res.status_code == 200
        res = client.post(f"/api/v1/controllers/{cid}/buttons", json={
            "buttons": ["B"],
            "action": "up",
        })
        assert res.status_code == 200

    def test_invalid_button_for_platform(self, client):
        cid = self._create(client, "switch")
        res = client.post(f"/api/v1/controllers/{cid}/buttons", json={
            "buttons": ["CROSS"],
            "action": "press",
        })
        assert res.status_code == 400

    def test_nonexistent_controller(self, client):
        res = client.post("/api/v1/controllers/999/buttons", json={
            "buttons": ["A"],
            "action": "press",
        })
        assert res.status_code == 404

    def test_empty_buttons(self, client):
        cid = self._create(client)
        res = client.post(f"/api/v1/controllers/{cid}/buttons", json={
            "buttons": [],
            "action": "press",
        })
        assert res.status_code == 422  # Pydantic validation

    def test_invalid_action(self, client):
        cid = self._create(client)
        res = client.post(f"/api/v1/controllers/{cid}/buttons", json={
            "buttons": ["A"],
            "action": "smash",
        })
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# Stick operations
# ---------------------------------------------------------------------------

class TestStick:

    def _create(self, client):
        res = client.post("/api/v1/controllers", json={"platform": "switch"})
        return res.json()["id"]

    def test_set_stick(self, client):
        cid = self._create(client)
        res = client.post(f"/api/v1/controllers/{cid}/stick", json={
            "stick": "left",
            "x": 50,
            "y": -30,
        })
        assert res.status_code == 200

    def test_invalid_stick_name(self, client):
        cid = self._create(client)
        res = client.post(f"/api/v1/controllers/{cid}/stick", json={
            "stick": "middle",
            "x": 0,
            "y": 0,
        })
        assert res.status_code == 422

    def test_stick_out_of_range(self, client):
        cid = self._create(client)
        res = client.post(f"/api/v1/controllers/{cid}/stick", json={
            "stick": "left",
            "x": 200,
            "y": 0,
        })
        assert res.status_code == 422

    def test_nonexistent_controller(self, client):
        res = client.post("/api/v1/controllers/999/stick", json={
            "stick": "left",
            "x": 0,
            "y": 0,
        })
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

class TestWebSocket:

    def _create(self, client):
        res = client.post("/api/v1/controllers", json={"platform": "switch"})
        return res.json()["id"]

    def test_ws_input(self, client):
        cid = self._create(client)
        with client.websocket_connect(f"/ws/controllers/{cid}/input") as ws:
            ws.send_json({
                "buttons": {"A": True},
                "left_stick": {"x": 50, "y": 0},
                "right_stick": {"x": 0, "y": 0},
            })
            resp = ws.receive_json()
            assert resp["type"] == "ack"
            assert "timestamp" in resp

    def test_ws_invalid_button(self, client):
        cid = self._create(client)
        with client.websocket_connect(f"/ws/controllers/{cid}/input") as ws:
            ws.send_json({
                "buttons": {"CROSS": True},  # Invalid for Switch
            })
            resp = ws.receive_json()
            assert resp["type"] == "error"

    def test_ws_nonexistent_controller(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/controllers/999/input") as ws:
                pass
