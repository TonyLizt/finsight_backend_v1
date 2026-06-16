"""Data Pipeline API Schemas.

v1.3 目标：
- 用统一 API 管理行情、技术指标、新闻、情绪、财报、模型特征快照的数据准备流程；
- 当前第一版先接入 market / technical / features；
- news / sentiment / fundamentals 先保留模块接口，后续再接 B 同学脚本。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


PipelineModule = Literal[
    "market",
    "technical",
    "news",
    "sentiment",
    "fundamentals",
    "features",
]


class DataPipelineJobRequest(BaseModel):
    """启动数据准备任务请求。"""

    tickers: list[str] = Field(..., description="股票代码列表，例如 ['AAPL', 'MSFT']")
    start_date: date | None = Field(None, description="开始日期，部分模块暂不使用")
    end_date: date | None = Field(None, description="目标结束日期 / 目标基准日")
    modules: list[PipelineModule] = Field(
        default_factory=lambda: ["market", "technical", "features"],
        description="需要执行的数据模块",
    )
    force_refresh: bool = Field(False, description="是否强制访问外部数据源")
    run_async: bool = Field(False, description="是否异步运行。第一版先同步执行并返回结果。")


class DataPipelineCoverageQuery(BaseModel):
    ticker: str


class DataPipelineJobResult(BaseModel):
    job_id: str
    status: str
    tickers: list[str]
    modules: list[str]
    start_date: str | None = None
    end_date: str | None = None
    force_refresh: bool
    total_steps: int
    success_steps: int
    failed_steps: int
    skipped_steps: int
    items: list[dict[str, Any]]
