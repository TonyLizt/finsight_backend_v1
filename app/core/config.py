"""系统配置。

第一版代码默认支持 MySQL；为了方便本地快速验证，也允许通过 .env 切换到 SQLite。
正式部署时请使用 MySQL DATABASE_URL。
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = "Finsight Backend"
    environment: str = "dev"
    database_url: str = "sqlite:///./finsight_dev.db"
    secret_key: str = "change-this-secret-key"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
