from datetime import date

from pydantic import BaseModel, Field


class PredictionRunRequest(BaseModel):
    """单股预测请求。

    说明：
    - v1.2 回归模型当前输出未来 1~5 个交易日收益率路径，因此 forecast_days 限制为 1~5；
    - base_trading_date 为空时，系统会自动使用最新可用交易日；
    - base_trading_date 不为空时，系统会使用该日期或该日期之前最近一个有行情的交易日作为预测基准日。
    """

    ticker: str
    forecast_days: int = Field(default=5, ge=1, le=5)
    base_trading_date: date | None = None
    analysis_mode: str = Field(default="full", pattern="^(quick|full)$")
    risk_profile: str = Field(default="balanced", pattern="^(conservative|balanced|aggressive)$")
    news_window_days: int = Field(default=7, ge=1, le=60)
    force_refresh: bool = False
