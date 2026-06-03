"""预测输入准备服务 v1.3：按需 Data Pipeline 版。

职责：
1. 预测前优先读取数据库中已有的 50 维 feature snapshot；
2. 如果请求日期没有 snapshot，则自动调用 v1.3 Data Pipeline 准备完整输入；
3. Data Pipeline 顺序默认是：
   market -> technical -> news -> sentiment -> features
4. 如果外部 API 抓取失败，但数据库存在可用旧 snapshot，则允许降级使用最近可用交易日；
5. 返回 requested_base_trading_date / actual_base_trading_date / warnings，避免前端误解。

核心语义：
- base_trading_date 是模型输入基准日，不是预测开始日；
- 模型使用 base_trading_date 当天及之前的数据预测未来交易日；
- 如果用户请求 2026-06-02，但数据源暂时只能补到 2026-05-29，
  系统可以降级使用 2026-05-29，同时在 warnings 中说明。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.data_pipeline_service import run_data_pipeline_job


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
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


def _snapshot_query(
    db: Session,
    ticker: str,
    target_date: date | None,
    *,
    exact: bool,
) -> dict[str, Any] | None:
    """查询 feature snapshot。

    exact=True:
        只查 base_trading_date = target_date。
    exact=False:
        查 target_date 或之前最近一条可用 snapshot。
    """
    if target_date and exact:
        row = db.execute(
            text(
                """
                SELECT
                    ticker,
                    base_trading_date,
                    dataset_version,
                    current_price,
                    JSON_LENGTH(features_json) AS feature_count
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                  AND base_trading_date = :target_date
                  AND JSON_LENGTH(features_json) >= 50
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker, "target_date": target_date},
        ).mappings().first()
    elif target_date:
        row = db.execute(
            text(
                """
                SELECT
                    ticker,
                    base_trading_date,
                    dataset_version,
                    current_price,
                    JSON_LENGTH(features_json) AS feature_count
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                  AND base_trading_date <= :target_date
                  AND JSON_LENGTH(features_json) >= 50
                ORDER BY base_trading_date DESC, id DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker, "target_date": target_date},
        ).mappings().first()
    else:
        row = db.execute(
            text(
                """
                SELECT
                    ticker,
                    base_trading_date,
                    dataset_version,
                    current_price,
                    JSON_LENGTH(features_json) AS feature_count
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                  AND JSON_LENGTH(features_json) >= 50
                ORDER BY base_trading_date DESC, id DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker},
        ).mappings().first()

    return dict(row) if row else None


def _snapshot_ready_response(
    *,
    ticker: str,
    snapshot: dict[str, Any],
    requested_base_date: date | None,
    status: str,
    message: str,
    pipeline_result: dict[str, Any] | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    actual_date = _to_date(snapshot.get("base_trading_date"))

    warnings: list[str] = []
    if warning:
        warnings.append(warning)

    if requested_base_date and actual_date and actual_date != requested_base_date:
        warnings.append(
            f"requested base date {requested_base_date.isoformat()} is not available; "
            f"using latest available snapshot {actual_date.isoformat()}"
        )

    if requested_base_date is None:
        base_source = "latest_available_snapshot"
    elif actual_date == requested_base_date:
        base_source = "requested_exact_match"
    else:
        base_source = "fallback_to_latest_available"

    data = {
        "ticker": ticker,
        "status": status,
        "can_continue": True,
        "requested_base_trading_date": requested_base_date.isoformat() if requested_base_date else None,
        "actual_base_trading_date": actual_date.isoformat() if actual_date else None,
        "base_trading_date_source": base_source,
        "message": message,
        "warnings": warnings,
        "price_data": {
            "status": "cached_snapshot",
            "can_continue": True,
            "source": "model_feature_snapshots",
            "latest_price_date": actual_date.isoformat() if actual_date else None,
            "message": "using feature snapshot from database",
        },
        "feature_snapshot": {
            "status": "cached",
            "ticker": ticker,
            "can_continue": True,
            "base_trading_date": actual_date.isoformat() if actual_date else None,
            "dataset_version": snapshot.get("dataset_version"),
            "current_price": float(snapshot.get("current_price") or 0.0),
            "feature_count": int(snapshot.get("feature_count") or 0),
        },
    }

    if pipeline_result is not None:
        data["on_demand_pipeline"] = pipeline_result

    return data


def ensure_prediction_inputs(
    db: Session,
    ticker: str,
    forecast_days: int = 5,
    news_window_days: int = 14,
    force_refresh: bool = False,
    base_trading_date: date | str | None = None,
    target_date: date | str | None = None,
) -> dict[str, Any]:
    """确保预测所需 feature snapshot 已准备好。

    当前策略：
    1. 如果有目标日期精确 snapshot 且 force_refresh=False，直接返回；
    2. 否则自动调用 v1.3 Data Pipeline；
    3. Pipeline 成功后优先使用目标日期 snapshot；
    4. 如果目标日期无法准备，但存在更早 snapshot，则降级使用；
    5. 完全没有 snapshot 时返回 failed。

    兼容说明：
    - 旧版 prediction_service.py 可能调用 target_date=...；
    - 新版请求字段叫 base_trading_date；
    - 这里同时支持 base_trading_date 和 target_date，优先使用 base_trading_date。
    """
    ticker = ticker.upper().strip()
    effective_base_date = base_trading_date if base_trading_date is not None else target_date
    requested_base_date = _to_date(effective_base_date)

    if forecast_days < 1 or forecast_days > 5:
        return {
            "ticker": ticker,
            "status": "failed",
            "can_continue": False,
            "error_code": "UNSUPPORTED_FORECAST_DAYS",
            "message": "当前 v1.2 回归模型最多支持 1~5 个交易日预测。",
            "forecast_days": forecast_days,
        }

    exact_snapshot = _snapshot_query(db, ticker, requested_base_date, exact=True) if requested_base_date else None
    if exact_snapshot and not force_refresh:
        return _snapshot_ready_response(
            ticker=ticker,
            snapshot=exact_snapshot,
            requested_base_date=requested_base_date,
            status="ready",
            message="using exact feature snapshot from database; on-demand pipeline skipped",
        )

    latest_snapshot = _snapshot_query(db, ticker, requested_base_date, exact=False)
    if latest_snapshot and requested_base_date is None and not force_refresh:
        return _snapshot_ready_response(
            ticker=ticker,
            snapshot=latest_snapshot,
            requested_base_date=requested_base_date,
            status="ready",
            message="using latest feature snapshot from database; on-demand pipeline skipped",
        )

    if not _env_bool("PREDICTION_ON_DEMAND_PIPELINE", True):
        if latest_snapshot:
            return _snapshot_ready_response(
                ticker=ticker,
                snapshot=latest_snapshot,
                requested_base_date=requested_base_date,
                status="ready_with_cached_fallback",
                message="on-demand pipeline disabled; using latest available snapshot",
                warning="PREDICTION_ON_DEMAND_PIPELINE is disabled",
            )

        return {
            "ticker": ticker,
            "status": "failed",
            "can_continue": False,
            "error_code": "DATA_NOT_FOUND",
            "message": f"未找到 {ticker} 的可用模型特征快照，且按需数据链路已关闭。",
        }

    modules = _csv_env(
        "PREDICTION_ON_DEMAND_MODULES",
        "market,technical,news,sentiment,features",
    )

    pipeline_result = run_data_pipeline_job(
        db=db,
        tickers=[ticker],
        modules=modules,
        start_date=None,
        end_date=requested_base_date,
        force_refresh=force_refresh,
        run_async=False,
    )

    exact_after = _snapshot_query(db, ticker, requested_base_date, exact=True) if requested_base_date else None
    if exact_after:
        return _snapshot_ready_response(
            ticker=ticker,
            snapshot=exact_after,
            requested_base_date=requested_base_date,
            status="ready",
            message="prediction inputs prepared by v1.3 on-demand data pipeline",
            pipeline_result=pipeline_result,
        )

    latest_after = _snapshot_query(db, ticker, requested_base_date, exact=False)
    if latest_after:
        allow_fallback = _env_bool("PREDICTION_ON_DEMAND_ALLOW_FALLBACK", True)
        if allow_fallback:
            return _snapshot_ready_response(
                ticker=ticker,
                snapshot=latest_after,
                requested_base_date=requested_base_date,
                status="ready_with_cached_fallback",
                message="on-demand pipeline did not produce exact target snapshot; using latest available snapshot",
                pipeline_result=pipeline_result,
                warning="exact requested base date was not prepared; fallback snapshot is used",
            )

    return {
        "ticker": ticker,
        "status": "failed",
        "can_continue": False,
        "error_code": "DATA_NOT_FOUND",
        "message": f"未能为 {ticker} 准备可用的模型输入特征。",
        "requested_base_trading_date": requested_base_date.isoformat() if requested_base_date else None,
        "on_demand_pipeline": pipeline_result,
    }
