from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = 'QTP'
    APP_HOST: str = '0.0.0.0'
    APP_PORT: int = 8000

    DATABASE_URL: str = 'postgresql+asyncpg://postgres:admin@localhost:5432/qtp'


settings = Settings()
