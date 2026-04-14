from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gamepadserver.core.models import (
    ControllerState,
    InputFrame,
    validate_buttons,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


def _get_manager():
    from gamepadserver.app import controller_manager
    return controller_manager


@router.websocket("/ws/controllers/{controller_id}/input")
async def ws_input(websocket: WebSocket, controller_id: int):
    manager = _get_manager()
    entry = manager.get_controller(controller_id)

    if entry is None:
        await websocket.close(code=4004, reason="Controller not found")
        return

    info, backend = entry

    await websocket.accept()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                frame = InputFrame(**data)

                # Validate buttons if any are pressed
                pressed = [k for k, v in frame.buttons.items() if v]
                if pressed:
                    validate_buttons(pressed, info.platform)

                state = frame.to_input_state()
                await backend.send_input(state)

                await websocket.send_json({
                    "type": "ack",
                    "timestamp": int(time.time() * 1000),
                })
            except (ValueError, TypeError) as exc:
                await websocket.send_json({
                    "type": "error",
                    "message": str(exc),
                })
            except RuntimeError as exc:
                await websocket.send_json({
                    "type": "error",
                    "message": str(exc),
                })
                break
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected for controller %d", controller_id)
