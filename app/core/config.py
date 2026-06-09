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

    # 阿里云百炼 / DashScope 应用 API 配置。
    # 默认关闭；未配置或调用失败时，预测接口会自动降级为本地模板文本，
    # 不影响现有股票预测、历史记录、详情接口的正常返回。
    dashscope_api_key: str | None = None
    bailian_enable: bool = False
    bailian_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    bailian_news_app_id: str | None = None
    bailian_report_app_id: str | None = None
    bailian_workspace_id: str | None = None
    bailian_timeout_seconds: int = 45

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
