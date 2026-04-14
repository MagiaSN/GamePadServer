from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8080

    model_config = {"env_prefix": "GAMEPAD_"}


settings = Settings()
