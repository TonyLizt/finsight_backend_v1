"""每日数据自动补全服务。

用于：
1. FastAPI 启动后按配置每日自动执行；
2. 管理员手动触发 /api/crawler/daily-refresh/run；
3. 命令行脚本 python -m app.scripts.run_daily_data_refresh。

日志：
- 使用 crawler_logs 表；
- task_type = daily_data_refresh_batch / daily_data_refresh_ticker；
- 抓取失败、缓存异常、跳过 feature snapshot 都会写入日志。

启动说明：
- app.main 当前会导入 start_daily_refresh_scheduler；
- 因此本文件必须暴露 start_daily_refresh_scheduler，否则后端启动时会 ImportError。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models.all_models import Stock
from app.services.prediction_input_service import ensure_prediction_inputs


_SCHEDULER_THREAD: threading.Thread | None = None
_SCHEDULER_STOP_EVENT = threading.Event()


def _table_columns(db: Session, table_name: str) -> set[str]:
    """读取 MySQL 表字段；如果表不存在则返回空集合。"""
    try:
        return {c["name"] for c in inspect(db.bind).get_columns(table_name)}
    except Exception:
        return set()


def _insert_crawler_log(
    db: Session,
    task_type: str,
    status: str,
    message: str,
    ticker: str | None = None,
    fetched_count: int = 0,
    detail: dict[str, Any] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> None:
    """写 crawler_logs 日志。

    由于不同版本数据库表字段可能略有不同，这里会先读取表字段，然后只写入
    当前表中真实存在的字段，避免因为 detail_json / detail / error_message 等字段
    不一致导致日志写入失败。
    """
    cols = _table_columns(db, "crawler_logs")
    if not cols:
        return

    payload: dict[str, Any] = {
        "task_type": task_type,
        "ticker": ticker,
        "status": status,
        "message": message,
        "fetched_count": fetched_count,
        "start_time": start_time or datetime.now(),
        "end_time": end_time or datetime.now(),
    }

    detail_text = json.dumps(detail or {}, ensure_ascii=False, default=str)

    if "detail_json" in cols:
        payload["detail_json"] = detail_text
    if "detail" in cols:
        payload["detail"] = detail_text
    if "error_message" in cols and status in {"failed", "partial_success"}:
        payload["error_message"] = message

    usable = {k: v for k, v in payload.items() if k in cols}
    if not usable:
        return

    sql = text(
        f"""
        INSERT INTO crawler_logs ({", ".join(f"`{k}`" for k in usable)})
        VALUES ({", ".join(f":{k}" for k in usable)})
        """
    )
    db.execute(sql, usable)
    db.commit()


def _parse_ticker_env(value: str | None) -> list[str]:
    """解析 DAILY_AUTO_REFRESH_TICKERS=AAPL,MSFT,NVDA 这种配置。"""
    if not value:
        return []
    return [x.strip().upper() for x in value.split(",") if x.strip()]


def get_default_refresh_tickers(db: Session, limit: int | None = None) -> list[str]:
    """获取每日自动补全股票池。

    优先级：
    1. DAILY_AUTO_REFRESH_TICKERS 环境变量；
    2. stocks 表中 is_supported=true 的股票；
    3. AAPL/MSFT/NVDA/TSLA 兜底。
    """
    env_tickers = _parse_ticker_env(os.getenv("DAILY_AUTO_REFRESH_TICKERS"))
    if env_tickers:
        return env_tickers[:limit] if limit else env_tickers

    query = db.query(Stock)

    # 不同版本 Stock ORM 字段可能存在差异，尽量兼容。
    try:
        query = query.filter(Stock.is_supported.is_(True))
    except Exception:
        pass

    rows = query.limit(limit or int(os.getenv("DAILY_AUTO_REFRESH_LIMIT", "50"))).all()
    tickers = [r.ticker.upper() for r in rows if getattr(r, "ticker", None)]

    if not tickers:
        tickers = ["AAPL", "MSFT", "NVDA", "TSLA"]

    return tickers[:limit] if limit else tickers


def run_daily_data_refresh(
    db: Session,
    tickers: list[str] | None = None,
    force_refresh: bool | None = None,
    limit: int | None = None,
    target_date: Any | None = None,
) -> dict[str, Any]:
    """执行一次每日数据补全任务。

    这个函数既可以被：
    - API 手动触发；
    - 命令行脚本调用；
    - 后台每日定时器调用。

    对每只股票执行 ensure_prediction_inputs。该函数内部会：
    1. 补齐 price_data；
    2. 重算 technical_indicators；
    3. 生成或读取 model_feature_snapshots；
    4. 如果行情抓取失败且缓存不安全，会停止后续生成，避免假数据污染预测。
    """
    started_at = datetime.now()

    if force_refresh is None:
        force_refresh = os.getenv("DAILY_AUTO_REFRESH_FORCE", "0") == "1"

    # crawler router 可能会传入 target_date。这里统一兼容：
    # - None：补全到最新可用日期；
    # - 'YYYY-MM-DD' 字符串：转换为 date；
    # - date/datetime：直接使用。
    refresh_target_date = None
    if target_date is not None:
        if hasattr(target_date, "date"):
            refresh_target_date = target_date.date()
        else:
            from datetime import date as _date
            refresh_target_date = _date.fromisoformat(str(target_date)[:10])

    limit = limit or int(os.getenv("DAILY_AUTO_REFRESH_LIMIT", "50"))
    tickers = [t.upper() for t in (tickers or get_default_refresh_tickers(db, limit=limit))][:limit]

    results: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0
    partial_count = 0

    _insert_crawler_log(
        db=db,
        task_type="daily_data_refresh_batch",
        status="running",
        message=f"daily data refresh started, tickers={len(tickers)}",
        fetched_count=0,
        detail={"tickers": tickers, "force_refresh": force_refresh, "target_date": refresh_target_date.isoformat() if refresh_target_date else None},
        start_time=started_at,
        end_time=started_at,
    )

    for ticker in tickers:
        ticker_started_at = datetime.now()

        try:
            result = ensure_prediction_inputs(
                db=db,
                ticker=ticker,
                forecast_days=5,
                news_window_days=int(os.getenv("DAILY_AUTO_REFRESH_NEWS_WINDOW_DAYS", "14")),
                force_refresh=force_refresh,
                base_trading_date=refresh_target_date,
            )

            status = "success" if result.get("can_continue") else "failed"

            # 外部抓取失败但使用可靠缓存继续时，记为 partial_success，便于管理员排查。
            price_status = (result.get("price_data") or {}).get("status")
            if price_status in {"cached_with_fetch_failed"}:
                status = "partial_success"

            if status == "success":
                success_count += 1
            elif status == "partial_success":
                partial_count += 1
            else:
                failed_count += 1

            message = result.get("message") or f"daily refresh {status} for {ticker}"

            _insert_crawler_log(
                db=db,
                task_type="daily_data_refresh_ticker",
                ticker=ticker,
                status=status,
                message=message,
                fetched_count=int((result.get("price_data") or {}).get("fetched_count") or 0),
                detail=result,
                start_time=ticker_started_at,
                end_time=datetime.now(),
            )

            results.append(result)

        except Exception as exc:
            failed_count += 1
            detail = {"ticker": ticker, "error": str(exc)}
            _insert_crawler_log(
                db=db,
                task_type="daily_data_refresh_ticker",
                ticker=ticker,
                status="failed",
                message=str(exc),
                fetched_count=0,
                detail=detail,
                start_time=ticker_started_at,
                end_time=datetime.now(),
            )
            results.append(
                {
                    "ticker": ticker,
                    "status": "failed",
                    "can_continue": False,
                    "error": str(exc),
                }
            )

    overall_status = "success"
    if failed_count and success_count:
        overall_status = "partial_success"
    elif failed_count and not success_count:
        overall_status = "failed"
    elif partial_count:
        overall_status = "partial_success"

    summary = {
        "status": overall_status,
        "tickers_total": len(tickers),
        "success_count": success_count,
        "partial_count": partial_count,
        "failed_count": failed_count,
        "force_refresh": force_refresh,
        "target_date": refresh_target_date.isoformat() if refresh_target_date else None,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "items": results,
    }

    _insert_crawler_log(
        db=db,
        task_type="daily_data_refresh_batch",
        status=overall_status,
        message=(
            "daily data refresh finished: "
            f"success={success_count}, partial={partial_count}, failed={failed_count}"
        ),
        fetched_count=success_count + partial_count,
        detail=summary,
        start_time=started_at,
        end_time=datetime.now(),
    )

    return summary


def _seconds_until_next_run(hour: int, minute: int) -> int:
    """计算距离下一次每日任务执行时间的秒数。"""
    now = datetime.now()
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if next_run <= now:
        next_run += timedelta(days=1)

    return max(1, int((next_run - now).total_seconds()))


def _scheduler_loop() -> None:
    """后台每日自动补全循环。

    注意：
    - 这是轻量级线程调度，适合课程项目和单实例部署；
    - 如果将来多实例部署，建议换成 cron / Celery / APScheduler，并避免多实例重复执行；
    - 数据库 Session 在每次任务执行时创建，用完关闭。
    """
    from app.db.session import SessionLocal

    hour = int(os.getenv("DAILY_AUTO_REFRESH_HOUR", "18"))
    minute = int(os.getenv("DAILY_AUTO_REFRESH_MINUTE", "30"))

    # 容器启动后是否立即跑一次。默认不立即跑，避免服务启动变慢。
    run_on_startup = os.getenv("DAILY_AUTO_REFRESH_RUN_ON_STARTUP", "0") == "1"

    if run_on_startup and not _SCHEDULER_STOP_EVENT.is_set():
        db = SessionLocal()
        try:
            run_daily_data_refresh(db)
        except Exception:
            # 调度线程不能因为一次任务异常而退出。
            pass
        finally:
            db.close()

    while not _SCHEDULER_STOP_EVENT.is_set():
        wait_seconds = _seconds_until_next_run(hour, minute)

        # 分段等待，便于 stop_daily_refresh_scheduler 尽快停止。
        while wait_seconds > 0 and not _SCHEDULER_STOP_EVENT.is_set():
            step = min(wait_seconds, 60)
            _SCHEDULER_STOP_EVENT.wait(step)
            wait_seconds -= step

        if _SCHEDULER_STOP_EVENT.is_set():
            break

        db = SessionLocal()
        try:
            run_daily_data_refresh(db)
        except Exception:
            # 出错不让线程死亡；具体单 ticker 错误会由 run_daily_data_refresh 写 crawler_logs。
            try:
                _insert_crawler_log(
                    db=db,
                    task_type="daily_data_refresh_batch",
                    status="failed",
                    message="daily refresh scheduler execution failed",
                    detail={"source": "scheduler_loop"},
                )
            except Exception:
                pass
        finally:
            db.close()


def start_daily_refresh_scheduler(*args: Any, **kwargs: Any) -> bool:
    """启动每日自动补全后台线程。

    app.main 会导入并调用这个函数。为了兼容不同 main.py 写法，这里接受
    *args / **kwargs，但实际不依赖这些参数。

    环境变量：
    - ENABLE_DAILY_AUTO_REFRESH=1 才启动；
    - DAILY_AUTO_REFRESH_HOUR / DAILY_AUTO_REFRESH_MINUTE 控制每日执行时间；
    - DAILY_AUTO_REFRESH_RUN_ON_STARTUP=1 表示启动后立即跑一次。

    返回：
    - True：已启动或已在运行；
    - False：配置关闭，未启动。
    """
    global _SCHEDULER_THREAD

    enabled = os.getenv("ENABLE_DAILY_AUTO_REFRESH", "0") == "1"
    if not enabled:
        return False

    if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
        return True

    _SCHEDULER_STOP_EVENT.clear()

    _SCHEDULER_THREAD = threading.Thread(
        target=_scheduler_loop,
        name="finsight-daily-refresh-scheduler",
        daemon=True,
    )
    _SCHEDULER_THREAD.start()
    return True


def stop_daily_refresh_scheduler() -> None:
    """停止每日自动补全后台线程。

    当前 FastAPI 退出时不一定调用它，但保留此函数方便后续 lifespan/shutdown 使用。
    """
    _SCHEDULER_STOP_EVENT.set()
