"""Crawler API：管理员查看爬虫状态、股票基础库同步状态。"""

from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from app.core.deps import get_current_admin
from app.core.responses import ok
from app.db.session import get_db, SessionLocal
from app.models.all_models import CrawlerLog, StockUniverseSyncLog, User
from app.schemas.crawler import StockUniverseSyncRequest
from app.services.crawler_service import sync_stock_universe, NASDAQ_LISTED_URL, OTHER_LISTED_URL

router = APIRouter(prefix="/api/crawler", tags=["Crawler API"])


@router.get("/status")
def crawler_status(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    rows = db.query(CrawlerLog).order_by(CrawlerLog.start_time.desc()).limit(10).all()
    return ok(
        {
            "latest_tasks": [
                {
                    "task_type": r.task_type,
                    "ticker": r.ticker,
                    "start_time": r.start_time.isoformat() if r.start_time else None,
                    "end_time": r.end_time.isoformat() if r.end_time else None,
                    "status": r.status,
                    "fetched_count": r.fetched_count,
                    "message": r.message,
                }
                for r in rows
            ],
            "missing_data_summary": [],
        }
    )


@router.get("/stock-universe/status")
def stock_universe_status(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    latest = db.query(StockUniverseSyncLog).order_by(StockUniverseSyncLog.finished_at.desc()).first()
    latest_sync = None
    if latest:
        latest_sync = {
            "source_name": latest.source_name,
            "started_at": latest.started_at.isoformat() if latest.started_at else None,
            "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
            "status": latest.status,
            "fetched_count": latest.fetched_count,
            "inserted_count": latest.inserted_count,
            "updated_count": latest.updated_count,
            "message": latest.message,
        }
    return ok(
        {
            "latest_sync": latest_sync,
            "source_files": [
                {"source_name": "nasdaqlisted", "url": NASDAQ_LISTED_URL},
                {"source_name": "otherlisted", "url": OTHER_LISTED_URL},
            ],
        }
    )


def _run_stock_universe_sync_task(task_log_id: int) -> None:
    """后台执行股票基础库同步，避免 API 请求阻塞或超时。

    后台任务必须创建自己的数据库 Session，不能复用请求生命周期中的 db。
    """
    task_db = SessionLocal()
    try:
        sync_stock_universe(task_db)
        log = task_db.query(CrawlerLog).filter(CrawlerLog.id == task_log_id).first()
        if log:
            log.status = "success"
            log.end_time = datetime.utcnow()
            log.message = "stock universe sync finished in background"
            task_db.commit()
    except Exception as exc:  # 后台任务不能把异常抛回请求，只写日志。
        log = task_db.query(CrawlerLog).filter(CrawlerLog.id == task_log_id).first()
        if log:
            log.status = "error"
            log.end_time = datetime.utcnow()
            log.message = str(exc)
            task_db.commit()
    finally:
        task_db.close()


@router.post("/stock-universe/sync")
def trigger_stock_universe_sync(
    req: StockUniverseSyncRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """手动触发股票基础库同步。

    与 04 API 文档保持一致：接口立即返回 running，真实同步在后台执行。
    这样即使 Nasdaq Trader 文件下载较慢，也不会导致自动化测试 15 秒超时。
    """
    started = datetime.utcnow()
    task_log = CrawlerLog(
        task_type="stock_universe_sync",
        start_time=started,
        status="running",
        message="stock universe sync started",
        fetched_count=0,
    )
    db.add(task_log)
    db.commit()
    db.refresh(task_log)

    task_id = f"stock_universe_sync_{started.strftime('%Y%m%d_%H%M%S')}_{task_log.id}"
    background_tasks.add_task(_run_stock_universe_sync_task, task_log.id)
    return ok({"task_id": task_id, "status": "running", "message": "stock universe sync started"}, "ok")
