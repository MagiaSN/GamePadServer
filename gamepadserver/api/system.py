from __future__ import annotations

import subprocess

from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/api/v1/system/adapters")
async def list_adapters():
    """List available Bluetooth adapters."""
    adapters = []
    try:
        result = subprocess.run(
            ["hciconfig"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        current: dict | None = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith(" ") and ":" in line:
                if current:
                    adapters.append(current)
                name = line.split(":")[0]
                current = {"name": name, "address": "", "status": ""}
            elif current:
                if "BD Address:" in line:
                    parts = line.split("BD Address:")[1].strip().split()
                    current["address"] = parts[0] if parts else ""
                if "UP RUNNING" in line:
                    current["status"] = "up"
                elif "DOWN" in line:
                    current["status"] = "down"
        if current:
            adapters.append(current)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return {"adapters": adapters}
