"""模型特征快照生成服务。

本服务负责把最新行情、技术指标、新闻情绪组合成 v1.2 模型需要的 50 维特征，
并写入 model_feature_snapshots。

字段策略：
1. 以同 ticker 最近一条真实 snapshot 为模板，保证 fund_* 财报字段完整；
2. 用最新 price_data 覆盖行情字段；
3. 用 latest technical_indicators 覆盖技术指标字段；
4. 用 sentiment_daily 窗口覆盖新闻情绪字段；
5. 生成 dataset_version=runtime_v1_2_auto 的特征快照。

失败处理：
- 如果当前价格被标记为 suspicious，不生成新 snapshot；
- 如果缺少模板 snapshot，不生成新 snapshot；
- 返回明确 status / reason，供 daily refresh 和 prediction service 写日志。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.market_data_service import validate_cached_price_quality


RUNTIME_DATASET_VERSION = "runtime_v1_2_auto"


PRICE_FEATURE_FIELDS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "previous_close",
    "change_amount",
    "daily_return",
    "change_percent",
    "amplitude",
]

INDICATOR_FEATURE_FIELDS = [
    "return_1d",
    "return_3d",
    "return_5d",
    "ma5",
    "ma20",
    "ma60",
    "ma5_gap",
    "ma20_gap",
    "ma60_gap",
    "rsi",
    "macd",
    "volatility_20d",
    "drawdown_20d",
    "volume_zscore",
]

SENTIMENT_FEATURE_FIELDS = [
    "news_count",
    "positive_news_count",
    "negative_news_count",
    "neutral_news_count",
    "sentiment_score",
    "sentiment_score_3d_avg",
    "sentiment_score_7d_avg",
    "positive_ratio",
    "negative_ratio",
]


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        s = str(value).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        s = str(value).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_json_dict(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return {}
    elif isinstance(value, dict):
        raw = value
    else:
        return {}

    if not isinstance(raw, dict):
        return {}

    return {str(k): _to_float(v) for k, v in raw.items()}


def _get_template_snapshot(db: Session, ticker: str, target_date: date | None = None) -> dict[str, Any] | None:
    """读取模板特征快照。

    优先读取 target_date 之前最近一条；如果没有 target_date，则读取该 ticker 最新一条。
    """
    if target_date:
        row = db.execute(
            text(
                """
                SELECT base_trading_date, current_price, features_json
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                  AND base_trading_date <= :target_date
                ORDER BY base_trading_date DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker, "target_date": target_date},
        ).mappings().first()
    else:
        row = db.execute(
            text(
                """
                SELECT base_trading_date, current_price, features_json
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                ORDER BY base_trading_date DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker},
        ).mappings().first()

    if not row:
        return None

    features = _parse_json_dict(row["features_json"])
    if not features:
        return None

    return {
        "base_trading_date": _to_date(row["base_trading_date"]),
        "current_price": _to_float(row["current_price"]),
        "features": features,
    }



def _get_previous_close(db: Session, ticker: str, trading_date: date) -> float | None:
    """Return previous trading day's close for robust feature derivation."""
    row = db.execute(
        text(
            """
            SELECT close
            FROM price_data
            WHERE ticker = :ticker
              AND trading_date < :trading_date
              AND close IS NOT NULL
            ORDER BY trading_date DESC
            LIMIT 1
            """
        ),
        {"ticker": ticker, "trading_date": trading_date},
    ).mappings().first()
    if not row:
        return None
    return _to_float_or_none(row.get("close"))


def _build_price_features(
    db: Session,
    ticker: str,
    base_date: date,
    price_row: dict[str, Any],
) -> dict[str, float]:
    """Build price-derived features from price_data without silently filling zeros.

    v1.5 hotfix:
    - Do not use the template snapshot's stale price fields.
    - Do not let missing DB values become 0.0 for daily_return/change/amplitude.
    - If price_data derived columns are missing, compute them from OHLC and the
      previous trading day's close.
    """
    open_price = _to_float_or_none(price_row.get("open"))
    high = _to_float_or_none(price_row.get("high"))
    low = _to_float_or_none(price_row.get("low"))
    close = _to_float_or_none(price_row.get("close"))
    volume = _to_float_or_none(price_row.get("volume"))

    previous_close = _to_float_or_none(price_row.get("previous_close"))
    if previous_close is None or previous_close <= 0:
        previous_close = _get_previous_close(db, ticker, base_date)

    change_amount = _to_float_or_none(price_row.get("change_amount"))
    if change_amount is None and close is not None and previous_close not in (None, 0):
        change_amount = close - previous_close

    daily_return = _to_float_or_none(price_row.get("daily_return"))
    if daily_return is None and change_amount is not None and previous_close not in (None, 0):
        daily_return = change_amount / previous_close

    change_percent = _to_float_or_none(price_row.get("change_percent"))
    if change_percent is None and daily_return is not None:
        # In feature snapshots we keep the model-side convention: fraction, same as daily_return.
        change_percent = daily_return

    amplitude = _to_float_or_none(price_row.get("amplitude"))
    if amplitude is None and high is not None and low is not None and previous_close not in (None, 0):
        amplitude = (high - low) / previous_close

    values = {
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "previous_close": previous_close,
        "change_amount": change_amount,
        "daily_return": daily_return,
        "change_percent": change_percent,
        "amplitude": amplitude,
    }
    return {k: float(v) for k, v in values.items() if v is not None}

def _get_price_row(db: Session, ticker: str, target_date: date | None = None) -> dict[str, Any] | None:
    if target_date:
        row = db.execute(
            text(
                """
                SELECT *
                FROM price_data
                WHERE ticker = :ticker
                  AND trading_date <= :target_date
                ORDER BY trading_date DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker, "target_date": target_date},
        ).mappings().first()
    else:
        row = db.execute(
            text(
                """
                SELECT *
                FROM price_data
                WHERE ticker = :ticker
                ORDER BY trading_date DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker},
        ).mappings().first()

    return dict(row) if row else None


def _get_indicator_row(db: Session, ticker: str, trading_date: date) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT *
            FROM technical_indicators
            WHERE ticker = :ticker
              AND trading_date = :trading_date
            LIMIT 1
            """
        ),
        {"ticker": ticker, "trading_date": trading_date},
    ).mappings().first()
    return dict(row) if row else None


def _build_sentiment_features(db: Session, ticker: str, trading_date: date, window_days: int) -> dict[str, float]:
    start_date = trading_date - timedelta(days=window_days)

    rows = db.execute(
        text(
            """
            SELECT trading_date, news_count, positive_news_count, negative_news_count,
                   neutral_news_count, sentiment_score
            FROM sentiment_daily
            WHERE ticker = :ticker
              AND trading_date >= :start_date
              AND trading_date <= :end_date
            ORDER BY trading_date ASC
            """
        ),
        {
            "ticker": ticker,
            "start_date": start_date,
            "end_date": trading_date,
        },
    ).mappings().all()

    if not rows:
        return {
            "news_count": 0.0,
            "positive_news_count": 0.0,
            "negative_news_count": 0.0,
            "neutral_news_count": 0.0,
            "sentiment_score": 0.0,
            "sentiment_score_3d_avg": 0.0,
            "sentiment_score_7d_avg": 0.0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
        }

    news_count = sum(int(r["news_count"] or 0) for r in rows)
    positive = sum(int(r["positive_news_count"] or 0) for r in rows)
    negative = sum(int(r["negative_news_count"] or 0) for r in rows)
    neutral = sum(int(r["neutral_news_count"] or 0) for r in rows)

    scores = [_to_float(r["sentiment_score"]) for r in rows]
    score = sum(scores) / len(scores) if scores else 0.0

    last3 = scores[-3:] if len(scores) >= 3 else scores
    last7 = scores[-7:] if len(scores) >= 7 else scores

    return {
        "news_count": float(news_count),
        "positive_news_count": float(positive),
        "negative_news_count": float(negative),
        "neutral_news_count": float(neutral),
        "sentiment_score": score,
        "sentiment_score_3d_avg": sum(last3) / len(last3) if last3 else 0.0,
        "sentiment_score_7d_avg": sum(last7) / len(last7) if last7 else 0.0,
        "positive_ratio": positive / news_count if news_count else 0.0,
        "negative_ratio": negative / news_count if news_count else 0.0,
    }


def _upsert_snapshot(
    db: Session,
    ticker: str,
    base_trading_date: date,
    current_price: float,
    features: dict[str, float],
) -> None:
    db.execute(
        text(
            """
            INSERT INTO model_feature_snapshots
            (dataset_version, ticker, base_trading_date, target_date_d5,
             current_price, features_json, target_json, raw_row_json)
            VALUES
            (:dataset_version, :ticker, :base_trading_date, NULL,
             :current_price, :features_json, NULL, :raw_row_json)
            ON DUPLICATE KEY UPDATE
              current_price = VALUES(current_price),
              features_json = VALUES(features_json),
              raw_row_json = VALUES(raw_row_json)
            """
        ),
        {
            "dataset_version": RUNTIME_DATASET_VERSION,
            "ticker": ticker,
            "base_trading_date": base_trading_date,
            "current_price": current_price,
            "features_json": json.dumps(features, ensure_ascii=False),
            "raw_row_json": json.dumps(
                {
                    "source": RUNTIME_DATASET_VERSION,
                    "generated_at": date.today().isoformat(),
                    "note": "generated from latest price_data, technical_indicators, sentiment_daily and carried-forward fund_* features; price-derived fields are force-synced from price_data",
                },
                ensure_ascii=False,
            ),
        },
    )
    db.commit()


def ensure_latest_feature_snapshot(
    db: Session,
    ticker: str,
    target_date: date | None = None,
    force_refresh: bool = False,
    news_window_days: int = 14,
) -> dict[str, Any]:
    """确保某 ticker 有指定日期或最新日期的 runtime feature snapshot。"""
    ticker = ticker.upper()

    quality_status, suspicious_dates = validate_cached_price_quality(db, ticker)
    if quality_status == "suspicious":
        return {
            "status": "failed",
            "can_continue": False,
            "reason": "suspicious_price_data",
            "ticker": ticker,
            "suspicious_dates": suspicious_dates,
        }

    price_row = _get_price_row(db, ticker, target_date)
    if not price_row:
        return {
            "status": "failed",
            "can_continue": False,
            "reason": "price_data_missing",
            "ticker": ticker,
        }

    base_date = _to_date(price_row["trading_date"])
    if base_date is None:
        return {
            "status": "failed",
            "can_continue": False,
            "reason": "invalid_price_trading_date",
            "ticker": ticker,
        }

    if not force_refresh:
        existing = db.execute(
            text(
                """
                SELECT id, JSON_LENGTH(features_json) AS feature_count
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                  AND base_trading_date = :base_date
                  AND dataset_version = :dataset_version
                LIMIT 1
                """
            ),
            {
                "ticker": ticker,
                "base_date": base_date,
                "dataset_version": RUNTIME_DATASET_VERSION,
            },
        ).mappings().first()

        if existing and int(existing["feature_count"] or 0) >= 50:
            return {
                "status": "cached",
                "can_continue": True,
                "ticker": ticker,
                "base_trading_date": base_date.isoformat(),
                "dataset_version": RUNTIME_DATASET_VERSION,
                "feature_count": int(existing["feature_count"]),
            }

    template = _get_template_snapshot(db, ticker, base_date)
    if not template:
        return {
            "status": "failed",
            "can_continue": False,
            "reason": "template_snapshot_missing",
            "ticker": ticker,
            "base_trading_date": base_date.isoformat(),
        }

    indicator_row = _get_indicator_row(db, ticker, base_date)
    if not indicator_row:
        return {
            "status": "failed",
            "can_continue": False,
            "reason": "technical_indicator_missing",
            "ticker": ticker,
            "base_trading_date": base_date.isoformat(),
        }

    features = dict(template["features"])

    # v1.5 hotfix: always overwrite price-derived features from price_data.
    # Do not restrict updates to keys already present in the template, because
    # older templates may miss previous_close/change_amount or carry stale zeros.
    price_features = _build_price_features(db, ticker, base_date, price_row)
    for field in PRICE_FEATURE_FIELDS:
        if field in price_features:
            features[field] = price_features[field]

    for field in INDICATOR_FEATURE_FIELDS:
        if field in features and field in indicator_row:
            features[field] = _to_float(indicator_row.get(field))

    sentiment_features = _build_sentiment_features(db, ticker, base_date, news_window_days)
    for field in SENTIMENT_FEATURE_FIELDS:
        if field in features:
            features[field] = _to_float(sentiment_features.get(field))

    current_price = _to_float(price_row.get("close"))
    if current_price <= 0:
        return {
            "status": "failed",
            "can_continue": False,
            "reason": "invalid_current_price",
            "ticker": ticker,
            "base_trading_date": base_date.isoformat(),
        }

    _upsert_snapshot(
        db=db,
        ticker=ticker,
        base_trading_date=base_date,
        current_price=current_price,
        features=features,
    )

    return {
        "status": "created_or_updated",
        "can_continue": True,
        "ticker": ticker,
        "base_trading_date": base_date.isoformat(),
        "current_price": current_price,
        "dataset_version": RUNTIME_DATASET_VERSION,
        "feature_count": len(features),
    }
