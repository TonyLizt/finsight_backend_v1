"""每日自动数据补全调度服务 v1.3。

本文件把旧 daily_refresh_service 统一迁移到 v1.3 Data Pipeline。

核心原则：
1. 每日任务统一调用 data_pipeline_service.run_data_pipeline_job；
2. 默认执行 market -> technical -> news -> sentiment -> features；
3. 数据库优先，缺数据才访问外部 API；
4. 保留旧函数名 run_daily_data_refresh / start_daily_refresh_scheduler，
   避免原有 /api/crawler/daily-refresh/run 和 app.main 启动逻辑失效。
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.services.data_pipeline_service import run_data_pipeline_job


_SCHEDULER_THREAD: threading.Thread | None = None
_SCHEDULER_STOP_EVENT = threading.Event()
_LAST_RUN_SUMMARY: dict[str, Any] | None = None
_LAST_RUN_LOCK = threading.Lock()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        ticker = item.strip().upper()
        if ticker and ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        return table_name in inspect(db.bind).get_table_names()
    except Exception:
        return False


def _ticker_query(db: Session, sql: str) -> list[str]:
    try:
        return [str(r[0]).upper() for r in db.execute(text(sql)).all() if r[0]]
    except Exception:
        return []


def get_default_refresh_modules() -> list[str]:
    """读取每日自动任务模块列表。"""
    modules = _csv(os.getenv("DAILY_AUTO_REFRESH_MODULES"))
    if not modules:
        modules = ["market", "intraday", "technical", "news", "news_fulltext", "sentiment", "features"]

    allowed = {"market", "intraday", "technical", "news", "news_fulltext", "sentiment", "fundamentals", "features"}
    return [m for m in modules if m in allowed]


def get_default_refresh_tickers(db: Session, limit: int | None = None) -> list[str]:
    """获取每日自动补全股票池。

    优先级：
    1. DAILY_AUTO_REFRESH_TICKERS；
    2. watchlists 中用户关注股票；
    3. runtime_v1_2_auto 已覆盖股票；
    4. stocks 表 supported 股票；
    5. 兜底核心池。
    """
    env_tickers = _csv(os.getenv("DAILY_AUTO_REFRESH_TICKERS"))
    if env_tickers:
        tickers = _dedupe(env_tickers)
        return tickers[:limit] if limit else tickers

    tickers: list[str] = []

    if _table_exists(db, "watchlists"):
        tickers += _ticker_query(
            db,
            "SELECT DISTINCT ticker FROM watchlists WHERE ticker IS NOT NULL ORDER BY ticker LIMIT 200",
        )

    if _table_exists(db, "model_feature_snapshots"):
        tickers += _ticker_query(
            db,
            """
            SELECT DISTINCT ticker
            FROM model_feature_snapshots
            WHERE dataset_version = 'runtime_v1_2_auto'
            ORDER BY ticker
            LIMIT 200
            """,
        )

    if _table_exists(db, "stocks"):
        cols = {c["name"] for c in inspect(db.bind).get_columns("stocks")}
        if "is_supported" in cols:
            tickers += _ticker_query(
                db,
                """
                SELECT DISTINCT ticker
                FROM stocks
                WHERE ticker IS NOT NULL AND is_supported = 1
                ORDER BY ticker
                LIMIT 200
                """,
            )
        else:
            tickers += _ticker_query(
                db,
                "SELECT DISTINCT ticker FROM stocks WHERE ticker IS NOT NULL ORDER BY ticker LIMIT 200",
            )

    if not tickers:
        tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]

    tickers = _dedupe(tickers)
    return tickers[:limit] if limit else tickers


def _save_last(summary: dict[str, Any]) -> None:
    global _LAST_RUN_SUMMARY
    with _LAST_RUN_LOCK:
        _LAST_RUN_SUMMARY = summary


def get_last_daily_refresh_summary() -> dict[str, Any] | None:
    """返回最近一次每日任务结果。"""
    with _LAST_RUN_LOCK:
        return dict(_LAST_RUN_SUMMARY) if _LAST_RUN_SUMMARY else None


def run_daily_data_refresh(
    db: Session,
    tickers: list[str] | None = None,
    force_refresh: bool | None = None,
    limit: int | None = None,
    target_date: Any | None = None,
    modules: list[str] | None = None,
) -> dict[str, Any]:
    """执行一次每日补全任务。

    兼容旧 daily_refresh_service 调用方式，但内部已经改为 v1.3 Data Pipeline。
    """
    refresh_date = _to_date(target_date) or date.today()

    if force_refresh is None:
        force_refresh = _env_bool("DAILY_AUTO_REFRESH_FORCE", False)

    limit = limit or _env_int("DAILY_AUTO_REFRESH_LIMIT", 50)
    modules = modules or get_default_refresh_modules()

    selected_tickers = _dedupe(
        [t.upper() for t in (tickers or get_default_refresh_tickers(db, limit=limit))]
    )[:limit]

    started_at = datetime.now()

    pipeline_result = run_data_pipeline_job(
        db=db,
        tickers=selected_tickers,
        modules=modules,
        start_date=None,
        end_date=refresh_date,
        force_refresh=force_refresh,
        run_async=False,
    )

    summary = {
        "task_type": "daily_data_refresh_v13_pipeline",
        "status": pipeline_result.get("status", "unknown"),
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": refresh_date.isoformat(),
        "force_refresh": force_refresh,
        "limit": limit,
        "tickers": selected_tickers,
        "modules": modules,
        "pipeline_result": pipeline_result,
        # 兼容旧返回字段
        "tickers_total": len(selected_tickers),
        "success_count": pipeline_result.get("success_steps", 0),
        "failed_count": pipeline_result.get("failed_steps", 0),
        "partial_count": 0,
        "items": pipeline_result.get("items", []),
    }

    _save_last(summary)
    return summary


# 兼容可能存在的旧调用名。
run_daily_refresh_once = run_daily_data_refresh


def _seconds_until_next_run(hour: int, minute: int) -> int:
    now = datetime.now()
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return max(1, int((next_run - now).total_seconds()))


def _scheduler_loop() -> None:
    """后台每日自动任务循环。"""
    from app.db.session import SessionLocal

    hour = _env_int("DAILY_AUTO_REFRESH_HOUR", 18)
    minute = _env_int("DAILY_AUTO_REFRESH_MINUTE", 30)

    if _env_bool("DAILY_AUTO_REFRESH_RUN_ON_STARTUP", False):
        db = SessionLocal()
        try:
            run_daily_data_refresh(db)
        except Exception as exc:
            _save_last({
                "task_type": "daily_data_refresh_v13_pipeline",
                "status": "failed",
                "stage": "run_on_startup",
                "error": str(exc),
                "time": datetime.now().isoformat(timespec="seconds"),
            })
        finally:
            db.close()

    while not _SCHEDULER_STOP_EVENT.is_set():
        wait_seconds = _seconds_until_next_run(hour, minute)
        while wait_seconds > 0 and not _SCHEDULER_STOP_EVENT.is_set():
            step = min(wait_seconds, 60)
            _SCHEDULER_STOP_EVENT.wait(step)
            wait_seconds -= step

        if _SCHEDULER_STOP_EVENT.is_set():
            break

        db = SessionLocal()
        try:
            run_daily_data_refresh(db)
        except Exception as exc:
            _save_last({
                "task_type": "daily_data_refresh_v13_pipeline",
                "status": "failed",
                "stage": "scheduled_run",
                "error": str(exc),
                "time": datetime.now().isoformat(timespec="seconds"),
            })
        finally:
            db.close()


def start_daily_refresh_scheduler(*args: Any, **kwargs: Any) -> bool:
    """启动每日自动补全后台线程。

    ENABLE_DAILY_AUTO_REFRESH=1 时启动，否则不启动。
    """
    global _SCHEDULER_THREAD

    if not _env_bool("ENABLE_DAILY_AUTO_REFRESH", False):
        return False

    if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
        return True

    _SCHEDULER_STOP_EVENT.clear()
    _SCHEDULER_THREAD = threading.Thread(
        target=_scheduler_loop,
        name="finsight-v13-data-pipeline-scheduler",
        daemon=True,
    )
    _SCHEDULER_THREAD.start()
    return True


def stop_daily_refresh_scheduler() -> None:
    """停止每日自动补全后台线程。"""
    _SCHEDULER_STOP_EVENT.set()
