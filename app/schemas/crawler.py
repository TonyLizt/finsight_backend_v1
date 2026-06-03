from datetime import date

from pydantic import BaseModel, Field


class StockUniverseSyncRequest(BaseModel):
    force: bool = False


class DailyDataRefreshRequest(BaseModel):
    """每日数据补全任务请求。

    tickers 为空时，后端会自动选择：
    1. 环境变量 DAILY_AUTO_REFRESH_TICKERS 指定的股票；
    2. 核心股票池；
    3. 用户自选股；
    4. 兜底选择已有行情的 supported 股票。
    """

    tickers: list[str] | None = None
    target_date: date | None = None
    force_refresh: bool = False
    limit: int = Field(default=50, ge=1, le=500)
