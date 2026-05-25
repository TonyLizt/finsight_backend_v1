from datetime import date
from pydantic import BaseModel, Field


class BacktestRunRequest(BaseModel):
    run_name: str | None = None
    tickers: list[str] = Field(min_length=1)
    start_date: date
    end_date: date
    initial_cash: float = Field(gt=0)
    forecast_days: int = Field(default=5, ge=1, le=30)
    max_position_ratio: float = Field(default=0.3, gt=0, le=1)
    max_holding_count: int = Field(default=3, ge=1, le=100)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.1)
    benchmark: str | None = "SPY"
    save_daily_positions: bool = True
    save_event_logs: bool = True
    animation_mode: str = Field(default="realtime", pattern="^(realtime|fast)$")
