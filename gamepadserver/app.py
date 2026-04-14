from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from gamepadserver.api.controllers import router as controllers_router
from gamepadserver.api.system import router as system_router
from gamepadserver.api.ws import router as ws_router
from gamepadserver.core.manager import ControllerManager

controller_manager = ControllerManager()

app = FastAPI(title="GamePadServer", version="0.1.0")

app.include_router(controllers_router)
app.include_router(system_router)
app.include_router(ws_router)

# Serve the test page
_static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
