import uvicorn

from gamepadserver.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "gamepadserver.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
