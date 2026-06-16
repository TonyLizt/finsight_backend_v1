"""Data Pipeline Router v1.3.

统一数据链路 API：
- POST /api/data-pipeline/jobs
- GET  /api/data-pipeline/coverage

第一版先同步执行。后续可以把 run_async=True 接入 BackgroundTasks。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.data_pipeline import DataPipelineJobRequest
from app.services.data_pipeline_service import get_data_coverage, run_data_pipeline_job


router = APIRouter(prefix="/api/data-pipeline", tags=["Data Pipeline"])


def ok(data, message: str = "ok"):
    return {
        "success": True,
        "data": data,
        "message": message,
    }


@router.post("/jobs")
def create_data_pipeline_job(
    req: DataPipelineJobRequest,
    db: Session = Depends(get_db),
):
    """启动一次数据准备任务。

    当前版本同步执行并返回完整结果。
    """
    result = run_data_pipeline_job(
        db=db,
        tickers=req.tickers,
        modules=list(req.modules),
        start_date=req.start_date,
        end_date=req.end_date,
        force_refresh=req.force_refresh,
        run_async=req.run_async,
    )
    return ok(result, "data pipeline job finished")


@router.get("/coverage")
def query_data_coverage(
    ticker: str = Query(..., description="股票代码，例如 AAPL"),
    end_date: date | None = Query(None, description="截止日期，例如 2026-06-02"),
    db: Session = Depends(get_db),
):
    """查询某只股票模型输入相关数据覆盖情况。"""
    return ok(get_data_coverage(db, ticker=ticker, end_date=end_date))
