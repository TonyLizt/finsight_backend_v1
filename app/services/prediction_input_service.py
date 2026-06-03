"""预测输入补全服务。

本服务是预测接口调用模型前的统一入口，负责保证模型输入数据可靠。

流程：
1. ensure_price_data：补齐最新可用日频行情；
2. rebuild_technical_indicators_for_ticker：重算技术指标；
3. ensure_latest_feature_snapshot：生成 / 读取 50 维 runtime feature snapshot；
4. 如果行情抓取失败且不能安全使用缓存，则停止后续步骤，避免基于假数据预测。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.feature_snapshot_service import ensure_latest_feature_snapshot
from app.services.indicator_service import rebuild_technical_indicators_for_ticker
from app.services.market_data_service import ensure_price_data


def ensure_prediction_inputs(
    db: Session,
    ticker: str,
    forecast_days: int = 5,
    news_window_days: int = 14,
    force_refresh: bool = False,
    base_trading_date: date | None = None,
) -> dict[str, Any]:
    """确保预测所需输入已准备好。

    参数：
    - ticker：股票代码；
    - forecast_days：预测天数，目前 v1.2 回归模型最多支持 5；
    - news_window_days：新闻情绪窗口；
    - force_refresh：是否强制访问外部行情源；
    - base_trading_date：用户指定预测基准日。为空时使用最新可用日期。

    失败处理：
    - 如果行情抓取失败且缓存也不可靠，直接返回 status=failed；
    - 上层 PredictionService 应将该失败转换为 API 错误或降级提示；
    - 不继续生成 feature snapshot，避免坏数据污染模型输入。
    """
    ticker = ticker.upper()

    if forecast_days < 1 or forecast_days > 5:
        return {
            "ticker": ticker,
            "status": "failed",
            "can_continue": False,
            "error_code": "UNSUPPORTED_FORECAST_DAYS",
            "message": "当前 v1.2 回归模型最多支持 1~5 个交易日预测。",
            "forecast_days": forecast_days,
        }

    price_status = ensure_price_data(
        db=db,
        ticker=ticker,
        force_refresh=force_refresh,
        target_date=base_trading_date,
    )

    if not price_status.get("can_continue", False):
        return {
            "ticker": ticker,
            "status": "failed",
            "can_continue": False,
            "error_code": "MARKET_DATA_REFRESH_FAILED",
            "message": "行情补全失败，且缓存行情缺失或存在疑似异常，已停止生成预测特征。",
            "price_data": price_status,
            "technical_indicators": {
                "status": "skipped",
                "reason": "price_data_not_safe",
            },
            "feature_snapshot": {
                "status": "skipped",
                "reason": "price_data_not_safe",
            },
        }

    indicator_status = rebuild_technical_indicators_for_ticker(
        db=db,
        ticker=ticker,
    )

    # 如果用户指定了 base_trading_date，则 feature snapshot 使用该日期或该日期之前最近交易日。
    snapshot_status = ensure_latest_feature_snapshot(
        db=db,
        ticker=ticker,
        target_date=base_trading_date,
        force_refresh=force_refresh,
        news_window_days=news_window_days,
    )

    can_continue = snapshot_status.get("status") in {
        "created",
        "updated",
        "cached",
        "created_or_updated",
    }

    return {
        "ticker": ticker,
        "status": "ready" if can_continue else "failed",
        "can_continue": can_continue,
        "forecast_days": forecast_days,
        "requested_base_trading_date": base_trading_date.isoformat() if base_trading_date else None,
        "actual_base_trading_date": snapshot_status.get("base_trading_date"),
        "price_data": price_status,
        "technical_indicators": indicator_status,
        "feature_snapshot": snapshot_status,
    }
