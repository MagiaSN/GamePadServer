import os

import uvicorn

from gamepadserver.config import settings

if __name__ == "__main__":
    level = os.environ.get("GAMEPAD_LOG_LEVEL", "INFO").upper()

    # Custom log config that keeps gamepadserver.* loggers visible
    # (uvicorn's default log_config silences non-uvicorn loggers)
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)-5s %(name)s: %(message)s",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "root": {
            "handlers": ["default"],
            "level": level,
        },
    }

    print("NOTE: Make sure you have run 'sudo ./deploy/setup-host.sh' on this machine.")
    uvicorn.run(
        "gamepadserver.app:app",
        host=settings.host,
        port=settings.port,
        log_config=log_config,
        reload=False,
    )
