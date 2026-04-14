import uvicorn

from gamepadserver.config import settings

if __name__ == "__main__":
    print("NOTE: Make sure you have run 'sudo ./deploy/setup-host.sh' on this machine.")
    uvicorn.run(
        "gamepadserver.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
