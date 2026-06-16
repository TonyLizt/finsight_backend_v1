from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class BacktestRunRequest(BaseModel):
    """前端创建回测任务时允许用户填写的字段。

    其他策略字段由后端固定默认值控制，前端传入会被拒绝，
    避免普通用户修改未开放参数。
    """

    model_config = ConfigDict(extra="forbid")

    tickers: list[str] = Field(min_length=1)
    start_date: date
    end_date: date
    initial_cash: float = Field(gt=0)

    max_position_ratio: float = Field(default=0.2, gt=0, le=1)
    max_holding_count: int = Field(default=5, ge=1, le=100)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.1)

    take_profit_pct: float = Field(default=0.18, gt=0, le=5)
    stop_loss_pct: float = Field(default=-0.08, ge=-1, lt=0)