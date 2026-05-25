from pydantic import BaseModel, Field


class PredictionRunRequest(BaseModel):
    ticker: str
    forecast_days: int = Field(default=5, ge=1, le=30)
    analysis_mode: str = Field(default="full", pattern="^(quick|full)$")
    risk_profile: str = Field(default="balanced", pattern="^(conservative|balanced|aggressive)$")
    news_window_days: int = Field(default=7, ge=1, le=60)
    force_refresh: bool = False
