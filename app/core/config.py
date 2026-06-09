"""系统配置。

正式部署时请使用 MySQL DATABASE_URL。
本文件 v1.5 继续保留 v1.4 的百炼 LLM 配置，并新增 Twelve Data 自抓取配置。
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

    # 阿里云百炼 / DashScope 应用 API 配置。v1.4 已接入。
    dashscope_api_key: str | None = None
    bailian_enable: bool = False
    bailian_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    bailian_news_app_id: str | None = None
    bailian_report_app_id: str | None = None
    bailian_workspace_id: str | None = None
    bailian_timeout_seconds: int = 45

    # v1.5 Twelve Data 行情自抓取配置。
    twelvedata_api_key: str | None = None
    twelvedata_base_url: str = "https://api.twelvedata.com"
    twelvedata_timezone: str = "America/New_York"
    twelvedata_daily_interval: str = "1day"
    twelvedata_intraday_interval: str = "1min"
    twelvedata_daily_outputsize: int = 5000
    twelvedata_intraday_outputsize: int = 5000
    twelvedata_timeout_seconds: int = 30
    twelvedata_request_sleep_seconds: float = 8.0
    twelvedata_intraday_prepost: bool = False

    # 默认只跑用户最关注的 7 只股票，可通过 .env.docker 覆盖。
    # 推荐：AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META
    finsight_core_tickers: str = "AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META"

    # 没有任何历史行情时，脚本第一次回补的默认窗口。
    twelvedata_daily_initial_backfill_days: int = 1260
    twelvedata_intraday_initial_backfill_days: int = 7

    # 预测/详情缺数据时，是否允许后端脚本现场尝试补数据。
    finsight_enable_on_demand_ingest: bool = True

    # 仍保留 Alpha Vantage 作为新闻源；行情不再使用 Alpha/Yahoo/AKShare。
    alpha_vantage_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
