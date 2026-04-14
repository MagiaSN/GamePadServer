from __future__ import annotations

from fastapi import APIRouter, HTTPException

from gamepadserver.core.models import (
    ButtonsRequest,
    ControllerResponse,
    CreateControllerRequest,
    Platform,
    StatusResponse,
    StickRequest,
    validate_buttons,
)

router = APIRouter(prefix="/api/v1/controllers", tags=["controllers"])


def _get_manager():
    from gamepadserver.app import controller_manager
    return controller_manager


def _info_to_response(info) -> ControllerResponse:
    return ControllerResponse(
        id=info.id,
        platform=info.platform,
        state=info.state,
        created_at=info.created_at,
        error=info.error,
    )


# ---- Controller lifecycle ----

@router.post("", status_code=201, response_model=ControllerResponse)
async def create_controller(req: CreateControllerRequest):
    manager = _get_manager()
    try:
        info = await manager.create_controller(req.platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _info_to_response(info)


@router.get("", response_model=list[ControllerResponse])
async def list_controllers():
    manager = _get_manager()
    return [_info_to_response(info) for info in manager.list_controllers()]


@router.get("/{controller_id}", response_model=ControllerResponse)
async def get_controller(controller_id: int):
    manager = _get_manager()
    entry = manager.get_controller(controller_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Controller not found")
    info, backend = entry
    # Refresh state from backend
    try:
        info.state = await backend.get_state()
    except Exception:
        pass
    return _info_to_response(info)


@router.delete("/{controller_id}", response_model=StatusResponse)
async def delete_controller(controller_id: int):
    manager = _get_manager()
    removed = await manager.remove_controller(controller_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Controller not found")
    return StatusResponse()


# ---- Controller input ----

@router.post("/{controller_id}/buttons", response_model=StatusResponse)
async def press_buttons(controller_id: int, req: ButtonsRequest):
    manager = _get_manager()
    entry = manager.get_controller(controller_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Controller not found")
    info, backend = entry

    try:
        validate_buttons(req.buttons, info.platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        if req.action == "press":
            await backend.press_buttons(req.buttons, duration=req.duration)
        elif req.action == "down":
            await backend.hold_buttons(req.buttons)
        elif req.action == "up":
            await backend.release_buttons(req.buttons)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return StatusResponse()


@router.post("/{controller_id}/stick", response_model=StatusResponse)
async def set_stick(controller_id: int, req: StickRequest):
    manager = _get_manager()
    entry = manager.get_controller(controller_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Controller not found")
    _, backend = entry

    try:
        await backend.set_stick(req.stick, req.x, req.y)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return StatusResponse()
