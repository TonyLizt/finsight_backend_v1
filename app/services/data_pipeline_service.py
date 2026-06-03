"""统一数据链路服务 v1.3 增强版。

本版本在 v1.3 第一版基础上继续完善：

1. 数据库优先：
   - 有 feature snapshot：直接用；
   - 没有 snapshot 但有 price_data：本地计算指标和特征；
   - price_data 也没有，或 force_refresh=True：才访问外部行情 API。

2. technical 模块避免重复计算：
   - technical_indicators 已经覆盖到 price_data 最新日期时，返回 cached；
   - 缺失或 force_refresh=True 时才重算。

3. sentiment 模块开始接入：
   - 从已有 news_data 聚合 sentiment_daily；
   - 支持常见字段：sentiment_score / overall_sentiment_score / sentiment_label / overall_sentiment_label；
   - 自动适配 sentiment_daily 中存在的字段，如 news_count / total_news_count。

4. coverage 查询更严格：
   - 不再简单判断“有数据就是 ok”；
   - 如果最新日期早于 end_date 或落后于上游数据，返回 stale / partial；
   - recommendation 会给出建议执行模块。

后续 v1.3 继续接入：
- news 模块：B 同学新闻抓取脚本；
- fundamentals 模块：B 同学财报抓取脚本；
- features 模块：记录更完整的数据来源与质量。
"""

from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import requests

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.services.feature_snapshot_service import ensure_latest_feature_snapshot
from app.services.indicator_service import rebuild_technical_indicators_for_ticker
from app.services.market_data_service import ensure_price_data


DEFAULT_MODULES = ["market", "technical", "features"]


def _to_date(value: Any) -> date | None:
    """把 date / datetime / YYYY-MM-DD 字符串转为 date。"""
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


def _to_datetime(value: Any) -> datetime | None:
    """把常见时间值转为 datetime。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    raw = str(value).strip()
    if not raw:
        return None

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y%m%dT%H%M%S",
    ):
        try:
            return datetime.strptime(raw[:19] if "%S" in fmt and "-" in fmt else raw, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        raw = str(value).strip()
        if not raw or raw.lower() in {"none", "null", "nan"}:
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        return table_name in inspect(db.bind).get_table_names()
    except Exception:
        return False


def _table_columns(db: Session, table_name: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(db.bind).get_columns(table_name)}
    except Exception:
        return set()


def _scalar(db: Session, sql: str, params: dict[str, Any]) -> Any:
    row = db.execute(text(sql), params).first()
    if not row:
        return None
    return row[0]


def _date_status(latest: date | None, target: date | None) -> str:
    """判断 latest 是否覆盖 target。"""
    if latest is None:
        return "empty"
    if target is None:
        return "ok"
    return "ok" if latest >= target else "stale"


def _insert_crawler_log(
    db: Session,
    *,
    task_type: str,
    ticker: str | None,
    status: str,
    message: str,
    detail: dict[str, Any] | None = None,
    fetched_count: int = 0,
) -> None:
    """复用 crawler_logs 记录数据链路日志，避免第一版新增表带来迁移风险。"""
    if not _table_exists(db, "crawler_logs"):
        return

    cols = _table_columns(db, "crawler_logs")
    now = datetime.now()
    detail_text = json.dumps(detail or {}, ensure_ascii=False, default=str)

    payload: dict[str, Any] = {
        "task_type": task_type,
        "ticker": ticker,
        "status": status,
        "message": message,
        "fetched_count": fetched_count,
        "start_time": now,
        "end_time": now,
    }

    if "detail_json" in cols:
        payload["detail_json"] = detail_text
    if "detail" in cols:
        payload["detail"] = detail_text
    if "error_message" in cols and status in {"failed", "partial_success"}:
        payload["error_message"] = message

    usable = {k: v for k, v in payload.items() if k in cols}
    if not usable:
        return

    db.execute(
        text(
            f"""
            INSERT INTO crawler_logs ({", ".join(f"`{k}`" for k in usable)})
            VALUES ({", ".join(f":{k}" for k in usable)})
            """
        ),
        usable,
    )
    db.commit()


def _latest_price_date(db: Session, ticker: str, end_date: date | None) -> date | None:
    if not _table_exists(db, "price_data"):
        return None

    if end_date:
        value = _scalar(
            db,
            """
            SELECT MAX(trading_date)
            FROM price_data
            WHERE ticker = :ticker AND trading_date <= :end_date
            """,
            {"ticker": ticker, "end_date": end_date},
        )
    else:
        value = _scalar(
            db,
            "SELECT MAX(trading_date) FROM price_data WHERE ticker = :ticker",
            {"ticker": ticker},
        )
    return _to_date(value)


def _latest_indicator_date(db: Session, ticker: str, end_date: date | None) -> date | None:
    if not _table_exists(db, "technical_indicators"):
        return None

    if end_date:
        value = _scalar(
            db,
            """
            SELECT MAX(trading_date)
            FROM technical_indicators
            WHERE ticker = :ticker AND trading_date <= :end_date
            """,
            {"ticker": ticker, "end_date": end_date},
        )
    else:
        value = _scalar(
            db,
            "SELECT MAX(trading_date) FROM technical_indicators WHERE ticker = :ticker",
            {"ticker": ticker},
        )
    return _to_date(value)


def _latest_sentiment_date(db: Session, ticker: str, end_date: date | None) -> date | None:
    if not _table_exists(db, "sentiment_daily"):
        return None

    if end_date:
        value = _scalar(
            db,
            """
            SELECT MAX(trading_date)
            FROM sentiment_daily
            WHERE ticker = :ticker AND trading_date <= :end_date
            """,
            {"ticker": ticker, "end_date": end_date},
        )
    else:
        value = _scalar(
            db,
            "SELECT MAX(trading_date) FROM sentiment_daily WHERE ticker = :ticker",
            {"ticker": ticker},
        )
    return _to_date(value)


def _latest_news_publish_date(db: Session, ticker: str, end_date: date | None) -> date | None:
    if not _table_exists(db, "news_data"):
        return None

    cols = _table_columns(db, "news_data")
    time_col = None
    for candidate in ("publish_time", "published_at", "time_published", "created_at"):
        if candidate in cols:
            time_col = candidate
            break

    if not time_col:
        return None

    if end_date:
        value = _scalar(
            db,
            f"""
            SELECT MAX({time_col})
            FROM news_data
            WHERE ticker = :ticker
              AND DATE({time_col}) <= :end_date
            """,
            {"ticker": ticker, "end_date": end_date},
        )
    else:
        value = _scalar(
            db,
            f"SELECT MAX({time_col}) FROM news_data WHERE ticker = :ticker",
            {"ticker": ticker},
        )

    dt = _to_datetime(value)
    return dt.date() if dt else None


def _latest_snapshot(db: Session, ticker: str, end_date: date | None) -> dict[str, Any] | None:
    if not _table_exists(db, "model_feature_snapshots"):
        return None

    if end_date:
        row = db.execute(
            text(
                """
                SELECT ticker, base_trading_date, dataset_version, current_price,
                       JSON_LENGTH(features_json) AS feature_count
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                  AND base_trading_date <= :end_date
                  AND JSON_LENGTH(features_json) >= 50
                ORDER BY base_trading_date DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker, "end_date": end_date},
        ).mappings().first()
    else:
        row = db.execute(
            text(
                """
                SELECT ticker, base_trading_date, dataset_version, current_price,
                       JSON_LENGTH(features_json) AS feature_count
                FROM model_feature_snapshots
                WHERE ticker = :ticker
                  AND JSON_LENGTH(features_json) >= 50
                ORDER BY base_trading_date DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker},
        ).mappings().first()

    return dict(row) if row else None


def run_market_module(
    db: Session,
    ticker: str,
    end_date: date | None,
    force_refresh: bool,
) -> dict[str, Any]:
    """行情模块：数据库优先，目标日期缺口时才尝试线上补齐。

    v1.3 on-demand 优化：
    - 如果数据库行情已经覆盖 end_date，直接 cached；
    - 如果数据库只有 end_date 之前的旧行情，则尝试线上补齐到 end_date；
    - 如果线上补齐失败，但旧行情质量可用，则返回 cached_stale_fetch_failed，
      允许后续使用最近可用交易日降级预测，并在返回中写明 warning；
    - force_refresh=True 时仍按强制刷新处理。
    """
    latest = _latest_price_date(db, ticker, end_date)

    if latest and not force_refresh:
        if end_date is None or latest >= end_date:
            return {
                "module": "market",
                "ticker": ticker,
                "status": "cached",
                "can_continue": True,
                "latest_price_date": latest.isoformat(),
                "message": "price_data already exists in database; online fetch skipped",
            }

        refresh_result = ensure_price_data(
            db=db,
            ticker=ticker,
            force_refresh=True,
            target_date=end_date,
        )
        refresh_result["module"] = "market"

        if refresh_result.get("can_continue"):
            latest_after = _latest_price_date(db, ticker, end_date)
            refresh_result["latest_price_date"] = latest_after.isoformat() if latest_after else refresh_result.get("latest_price_date")
            refresh_result["message"] = "price_data refreshed because requested end_date was not covered"
            return refresh_result

        return {
            "module": "market",
            "ticker": ticker,
            "status": "cached_stale_fetch_failed",
            "can_continue": True,
            "latest_price_date": latest.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "message": (
                "requested end_date is newer than cached price_data; online refresh failed; "
                "using latest cached price_data as fallback"
            ),
            "error": refresh_result.get("error") or refresh_result.get("message"),
            "refresh_result": refresh_result,
        }

    result = ensure_price_data(
        db=db,
        ticker=ticker,
        force_refresh=force_refresh,
        target_date=end_date,
    )
    result["module"] = "market"
    return result

def run_technical_module(
    db: Session,
    ticker: str,
    end_date: date | None,
    force_refresh: bool,
) -> dict[str, Any]:
    """技术指标模块：已覆盖则 cached，否则重算。"""
    latest_price = _latest_price_date(db, ticker, end_date)
    latest_indicator = _latest_indicator_date(db, ticker, end_date)

    if latest_price and latest_indicator and latest_indicator >= latest_price and not force_refresh:
        return {
            "module": "technical",
            "ticker": ticker,
            "status": "cached",
            "can_continue": True,
            "latest_price_date": latest_price.isoformat(),
            "latest_indicator_date": latest_indicator.isoformat(),
            "message": "technical_indicators already cover latest price_data; recalculation skipped",
        }

    result = rebuild_technical_indicators_for_ticker(db=db, ticker=ticker)
    result["module"] = "technical"
    return result


def run_features_module(
    db: Session,
    ticker: str,
    end_date: date | None,
    force_refresh: bool,
    news_window_days: int = 14,
    upstream_changed: bool = False,
) -> dict[str, Any]:
    """特征模块：优先使用已有 50 维 snapshot；上游数据变化时自动重建。

    重要优化：
    - 如果 market / technical / news / sentiment 在同一个 pipeline job 中发生了更新，
      即使 feature snapshot 已存在，也应该重新生成一次，避免模型继续使用旧情绪/旧指标。
    - 这不是全局 force_refresh，不会导致 market 模块强制调用外部行情 API。
    """
    snapshot = _latest_snapshot(db, ticker, end_date)

    if snapshot and not force_refresh and not upstream_changed:
        snap_date = _to_date(snapshot.get("base_trading_date"))
        if end_date is None or snap_date == end_date:
            return {
                "module": "features",
                "ticker": ticker,
                "status": "cached",
                "can_continue": True,
                "base_trading_date": snap_date.isoformat() if snap_date else None,
                "dataset_version": snapshot.get("dataset_version"),
                "feature_count": int(snapshot.get("feature_count") or 0),
                "message": "valid feature snapshot already exists; generation skipped",
                "feature_refresh_reason": "snapshot_exists_and_upstream_unchanged",
            }

    effective_force_refresh = force_refresh or upstream_changed

    result = ensure_latest_feature_snapshot(
        db=db,
        ticker=ticker,
        target_date=end_date,
        force_refresh=effective_force_refresh,
        news_window_days=news_window_days,
    )
    result["module"] = "features"
    result["feature_refresh_reason"] = (
        "global_force_refresh" if force_refresh
        else "upstream_data_changed" if upstream_changed
        else "snapshot_missing_or_stale"
    )
    return result


def _extract_news_date(row: dict[str, Any], cols: set[str]) -> date | None:
    """从 news_data 行中提取归属日期。"""
    if "trading_date" in cols:
        d = _to_date(row.get("trading_date"))
        if d:
            return d

    for col in ("publish_time", "published_at", "time_published", "created_at"):
        if col in cols:
            dt = _to_datetime(row.get(col))
            if dt:
                return dt.date()

    return None


def _extract_sentiment_score(row: dict[str, Any]) -> float:
    """从新闻行中提取情绪分数。没有数值时根据 label 降级映射。"""
    for col in ("sentiment_score", "overall_sentiment_score", "score"):
        if col in row and row.get(col) is not None:
            return _to_float(row.get(col), 0.0)

    label = ""
    for col in ("sentiment_label", "overall_sentiment_label", "label"):
        if col in row and row.get(col):
            label = str(row.get(col)).lower()
            break

    if "bullish" in label or "positive" in label:
        return 0.35
    if "bearish" in label or "negative" in label:
        return -0.35
    return 0.0


def _score_to_label(score: float) -> str:
    if score > 0.05:
        return "positive"
    if score < -0.05:
        return "negative"
    return "neutral"


def _upsert_sentiment_daily(
    db: Session,
    ticker: str,
    rows_by_date: dict[date, dict[str, Any]],
) -> dict[str, int]:
    """将聚合后的每日情绪写入 sentiment_daily。"""
    if not _table_exists(db, "sentiment_daily"):
        return {
            "inserted": 0,
            "updated": 0,
            "skipped": len(rows_by_date),
        }

    cols = _table_columns(db, "sentiment_daily")
    inserted = 0
    updated = 0
    skipped = 0

    for trading_date, item in rows_by_date.items():
        payload: dict[str, Any] = {
            "ticker": ticker,
            "trading_date": trading_date,
        }

        # 兼容不同版本字段。
        if "news_count" in cols:
            payload["news_count"] = item["total"]
        if "total_news_count" in cols:
            payload["total_news_count"] = item["total"]
        if "positive_news_count" in cols:
            payload["positive_news_count"] = item["positive"]
        if "negative_news_count" in cols:
            payload["negative_news_count"] = item["negative"]
        if "neutral_news_count" in cols:
            payload["neutral_news_count"] = item["neutral"]
        if "sentiment_score" in cols:
            payload["sentiment_score"] = item["sentiment_score"]
        if "avg_sentiment_score" in cols:
            payload["avg_sentiment_score"] = item["sentiment_score"]
        if "sentiment_label" in cols:
            payload["sentiment_label"] = item["sentiment_label"]

        usable = {k: v for k, v in payload.items() if k in cols}
        if "ticker" not in usable or "trading_date" not in usable:
            skipped += 1
            continue

        exists = db.execute(
            text(
                """
                SELECT 1
                FROM sentiment_daily
                WHERE ticker = :ticker
                  AND trading_date = :trading_date
                LIMIT 1
                """
            ),
            {
                "ticker": ticker,
                "trading_date": trading_date,
            },
        ).first()

        if exists:
            update_cols = [k for k in usable if k not in {"ticker", "trading_date"}]
            if not update_cols:
                skipped += 1
                continue

            db.execute(
                text(
                    f"""
                    UPDATE sentiment_daily
                    SET {", ".join(f"`{k}` = :{k}" for k in update_cols)}
                    WHERE ticker = :ticker
                      AND trading_date = :trading_date
                    """
                ),
                usable,
            )
            updated += 1
        else:
            db.execute(
                text(
                    f"""
                    INSERT INTO sentiment_daily ({", ".join(f"`{k}`" for k in usable)})
                    VALUES ({", ".join(f":{k}" for k in usable)})
                    """
                ),
                usable,
            )
            inserted += 1

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }


def _alpha_vantage_datetime(dt: date | datetime | None, end_of_day: bool = False) -> str | None:
    """转换为 Alpha Vantage NEWS_SENTIMENT 需要的 YYYYMMDDTHHMM 格式。"""
    if dt is None:
        return None

    if isinstance(dt, datetime):
        value = dt
    else:
        value = datetime.combine(dt, datetime.max.time() if end_of_day else datetime.min.time())

    return value.strftime("%Y%m%dT%H%M")


def _alpha_news_parse_time(value: Any) -> datetime | None:
    """解析 Alpha Vantage time_published，例如 20260602T142729。"""
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:15] if fmt == "%Y%m%dT%H%M%S" else raw[:13] if fmt == "%Y%m%dT%H%M" else raw[:19], fmt)
        except ValueError:
            continue
    return _to_datetime(raw)


def _alpha_label_to_standard(label: Any) -> str:
    """把 Alpha Vantage 的标签统一为 positive / negative / neutral。"""
    text = str(label or "").lower()
    if "bullish" in text or "positive" in text:
        return "positive"
    if "bearish" in text or "negative" in text:
        return "negative"
    return "neutral"


def fetch_alpha_vantage_news(
    ticker: str,
    start_date: date | None,
    end_date: date | None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """抓取 Alpha Vantage NEWS_SENTIMENT。

    说明：
    - 只负责拉取和标准化，不直接写库；
    - 如果 key 不存在、额度用完、premium 限制或网络异常，会抛出 RuntimeError；
    - v1.3 暂不做 LLM，只使用 Alpha Vantage 自带的 overall_sentiment_score / label。
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY is not set")

    params: dict[str, Any] = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker.upper(),
        "limit": limit,
        "apikey": api_key,
    }

    time_from = _alpha_vantage_datetime(start_date)
    time_to = _alpha_vantage_datetime(end_date, end_of_day=True)

    if time_from:
        params["time_from"] = time_from
    if time_to:
        params["time_to"] = time_to

    timeout = int(os.getenv("NEWS_FETCH_TIMEOUT_SECONDS", "30"))
    url = "https://www.alphavantage.co/query"

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()

    payload = response.json()

    for key in ("Error Message", "Information", "Note"):
        if key in payload:
            raise RuntimeError(f"Alpha Vantage {key}: {payload[key]}")

    feed = payload.get("feed")
    if not isinstance(feed, list):
        raise RuntimeError(f"Alpha Vantage NEWS_SENTIMENT response missing feed, keys={list(payload.keys())}")

    items: list[dict[str, Any]] = []

    for item in feed:
        if not isinstance(item, dict):
            continue

        # ticker_sentiment 中可能有多只股票，优先取当前 ticker 的局部情绪。
        ticker_score = None
        ticker_label = None
        for ts in item.get("ticker_sentiment") or []:
            if str(ts.get("ticker", "")).upper() == ticker.upper():
                ticker_score = _to_float(ts.get("ticker_sentiment_score"), 0.0)
                ticker_label = _alpha_label_to_standard(ts.get("ticker_sentiment_label"))
                break

        overall_score = _to_float(item.get("overall_sentiment_score"), 0.0)
        overall_label = _alpha_label_to_standard(item.get("overall_sentiment_label"))

        publish_dt = _alpha_news_parse_time(item.get("time_published"))

        items.append(
            {
                "ticker": ticker.upper(),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "url": item.get("url"),
                "source": item.get("source"),
                "source_domain": item.get("source_domain"),
                "banner_image": item.get("banner_image"),
                "category_within_source": item.get("category_within_source"),
                "publish_time": publish_dt,
                "time_published": item.get("time_published"),
                "sentiment_score": ticker_score if ticker_score is not None else overall_score,
                "sentiment_label": ticker_label or overall_label,
                "overall_sentiment_score": overall_score,
                "overall_sentiment_label": overall_label,
                "raw_json": item,
            }
        )

    return items


def _map_news_item_to_table_columns(item: dict[str, Any], cols: set[str]) -> dict[str, Any]:
    """根据当前 news_data 表真实字段动态映射新闻记录。"""
    mapping_candidates: dict[str, Any] = {
        "ticker": item.get("ticker"),
        "title": item.get("title"),
        "summary": item.get("summary"),
        "description": item.get("summary"),
        "content": item.get("summary"),
        "url": item.get("url"),
        "news_url": item.get("url"),
        "source": item.get("source"),
        "source_domain": item.get("source_domain"),
        "banner_image": item.get("banner_image"),
        "image_url": item.get("banner_image"),
        "category": item.get("category_within_source"),
        "category_within_source": item.get("category_within_source"),
        "publish_time": item.get("publish_time"),
        "published_at": item.get("publish_time"),
        "time_published": item.get("time_published"),
        "trading_date": item.get("publish_time").date() if item.get("publish_time") else None,
        "sentiment_score": item.get("sentiment_score"),
        "overall_sentiment_score": item.get("overall_sentiment_score"),
        "sentiment_label": item.get("sentiment_label"),
        "overall_sentiment_label": item.get("overall_sentiment_label"),
        "raw_json": json.dumps(item.get("raw_json") or item, ensure_ascii=False, default=str),
        "raw_payload": json.dumps(item.get("raw_json") or item, ensure_ascii=False, default=str),
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    return {k: v for k, v in mapping_candidates.items() if k in cols}


def _news_exists(db: Session, cols: set[str], row: dict[str, Any]) -> bool:
    """新闻去重：优先按 url，其次按 ticker + title + publish_time。"""
    if "url" in cols and row.get("url"):
        exists = db.execute(
            text("SELECT 1 FROM news_data WHERE url = :url LIMIT 1"),
            {"url": row["url"]},
        ).first()
        return exists is not None

    if "news_url" in cols and row.get("news_url"):
        exists = db.execute(
            text("SELECT 1 FROM news_data WHERE news_url = :url LIMIT 1"),
            {"url": row["news_url"]},
        ).first()
        return exists is not None

    if {"ticker", "title", "publish_time"}.issubset(cols) and row.get("title") and row.get("publish_time"):
        exists = db.execute(
            text(
                """
                SELECT 1
                FROM news_data
                WHERE ticker = :ticker
                  AND title = :title
                  AND publish_time = :publish_time
                LIMIT 1
                """
            ),
            {
                "ticker": row.get("ticker"),
                "title": row.get("title"),
                "publish_time": row.get("publish_time"),
            },
        ).first()
        return exists is not None

    return False


def upsert_news_data(db: Session, items: list[dict[str, Any]]) -> dict[str, int]:
    """把标准化后的新闻写入 news_data。"""
    if not _table_exists(db, "news_data"):
        raise RuntimeError("news_data table does not exist")

    cols = _table_columns(db, "news_data")

    inserted = 0
    skipped_duplicate = 0
    skipped_unmapped = 0

    for item in items:
        row = _map_news_item_to_table_columns(item, cols)

        if "ticker" not in row:
            skipped_unmapped += 1
            continue

        if _news_exists(db, cols, row):
            skipped_duplicate += 1
            continue

        usable = {k: v for k, v in row.items() if k in cols}

        if not usable:
            skipped_unmapped += 1
            continue

        db.execute(
            text(
                f"""
                INSERT INTO news_data ({", ".join(f"`{k}`" for k in usable)})
                VALUES ({", ".join(f":{k}" for k in usable)})
                """
            ),
            usable,
        )
        inserted += 1

    db.commit()

    return {
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_unmapped": skipped_unmapped,
    }


def run_news_module(
    db: Session,
    ticker: str,
    start_date: date | None,
    end_date: date | None,
    force_refresh: bool,
) -> dict[str, Any]:
    """新闻抓取模块：Alpha Vantage NEWS_SENTIMENT → news_data。

    数据库优先：
    - 如果 news_data 已经覆盖 end_date，且 force_refresh=false，则直接 cached；
    - 否则调用 Alpha Vantage；
    - 抓取失败但已有旧新闻时返回 cached_stale_fetch_failed，不阻塞核心预测。
    """
    if not _table_exists(db, "news_data"):
        return {
            "module": "news",
            "ticker": ticker,
            "status": "failed",
            "can_continue": False,
            "message": "news_data table does not exist",
        }

    latest_news = _latest_news_publish_date(db, ticker, end_date)

    if latest_news and end_date and latest_news >= end_date and not force_refresh:
        return {
            "module": "news",
            "ticker": ticker,
            "status": "cached",
            "can_continue": True,
            "latest_news_date": latest_news.isoformat(),
            "message": "news_data already covers target date; online fetch skipped",
        }

    # 如果没有指定 start_date，优先从最新新闻后一天开始补；没有新闻则补最近 30 天。
    fetch_start = start_date
    if fetch_start is None:
        if latest_news:
            fetch_start = latest_news + timedelta(days=1)
        elif end_date:
            fetch_start = end_date - timedelta(days=int(os.getenv("NEWS_FETCH_LOOKBACK_DAYS", "30")))

    try:
        items = fetch_alpha_vantage_news(
            ticker=ticker,
            start_date=fetch_start,
            end_date=end_date,
            limit=int(os.getenv("NEWS_FETCH_LIMIT", "1000")),
        )
        result = upsert_news_data(db, items)

        latest_after = _latest_news_publish_date(db, ticker, end_date)

        return {
            "module": "news",
            "ticker": ticker,
            "status": "updated",
            "can_continue": True,
            "source": "alpha_vantage_news_sentiment",
            "fetched_count": len(items),
            "inserted": result["inserted"],
            "skipped_duplicate": result["skipped_duplicate"],
            "skipped_unmapped": result["skipped_unmapped"],
            "latest_news_date": latest_after.isoformat() if latest_after else None,
            "message": "news_data fetched and stored",
        }

    except Exception as exc:
        latest_existing = _latest_news_publish_date(db, ticker, None)
        return {
            "module": "news",
            "ticker": ticker,
            "status": "cached_stale_fetch_failed" if latest_existing else "failed",
            "can_continue": latest_existing is not None,
            "latest_news_date": latest_existing.isoformat() if latest_existing else None,
            "message": "news fetch failed; using existing cached news_data if available",
            "error": str(exc),
        }




def run_news_module_placeholder(ticker: str) -> dict[str, Any]:
    """新闻抓取模块占位。

    v1.3 后续会接入 B 同学 Alpha Vantage NEWS_SENTIMENT 脚本：
    - 抓 raw JSON；
    - 清洗去重；
    - 写入 news_data。
    """
    return {
        "module": "news",
        "ticker": ticker,
        "status": "skipped_not_implemented",
        "can_continue": True,
        "message": "news crawling will be integrated in v1.3 next step",
    }


def run_sentiment_module(
    db: Session,
    ticker: str,
    start_date: date | None,
    end_date: date | None,
    force_refresh: bool,
) -> dict[str, Any]:
    """情绪聚合模块：从 news_data 聚合到 sentiment_daily。

    这是 v1.3 的初步实现，不调用 LLM，只使用已有新闻情绪字段。
    """
    if not _table_exists(db, "news_data"):
        return {
            "module": "sentiment",
            "ticker": ticker,
            "status": "failed",
            "can_continue": False,
            "message": "news_data table does not exist",
        }

    if not _table_exists(db, "sentiment_daily"):
        return {
            "module": "sentiment",
            "ticker": ticker,
            "status": "failed",
            "can_continue": False,
            "message": "sentiment_daily table does not exist",
        }

    latest_news = _latest_news_publish_date(db, ticker, end_date)
    latest_sentiment = _latest_sentiment_date(db, ticker, end_date)

    if (
        latest_news
        and latest_sentiment
        and latest_sentiment >= latest_news
        and not force_refresh
    ):
        return {
            "module": "sentiment",
            "ticker": ticker,
            "status": "cached",
            "can_continue": True,
            "latest_news_date": latest_news.isoformat(),
            "latest_sentiment_date": latest_sentiment.isoformat(),
            "message": "sentiment_daily already covers latest news_data; aggregation skipped",
        }

    news_cols = _table_columns(db, "news_data")

    rows = db.execute(
        text("SELECT * FROM news_data WHERE ticker = :ticker"),
        {"ticker": ticker},
    ).mappings().all()

    grouped: dict[date, list[float]] = defaultdict(list)

    for raw_row in rows:
        row = dict(raw_row)
        news_date = _extract_news_date(row, news_cols)
        if not news_date:
            continue

        if start_date and news_date < start_date:
            continue
        if end_date and news_date > end_date:
            continue

        grouped[news_date].append(_extract_sentiment_score(row))

    if not grouped:
        return {
            "module": "sentiment",
            "ticker": ticker,
            "status": "empty",
            "can_continue": True,
            "message": "no news rows available for sentiment aggregation",
        }

    rows_by_date: dict[date, dict[str, Any]] = {}

    for trading_date, scores in grouped.items():
        total = len(scores)
        positive = sum(1 for s in scores if s > 0.05)
        negative = sum(1 for s in scores if s < -0.05)
        neutral = total - positive - negative
        avg_score = sum(scores) / total if total else 0.0

        rows_by_date[trading_date] = {
            "total": total,
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "sentiment_score": avg_score,
            "sentiment_label": _score_to_label(avg_score),
        }

    result = _upsert_sentiment_daily(db, ticker, rows_by_date)

    return {
        "module": "sentiment",
        "ticker": ticker,
        "status": "updated",
        "can_continue": True,
        "aggregated_days": len(rows_by_date),
        "inserted": result["inserted"],
        "updated": result["updated"],
        "skipped": result["skipped"],
        "latest_aggregated_date": max(rows_by_date).isoformat(),
        "message": "sentiment_daily aggregated from news_data",
    }


def run_fundamentals_module_placeholder(ticker: str) -> dict[str, Any]:
    """财报模块占位。"""
    return {
        "module": "fundamentals",
        "ticker": ticker,
        "status": "skipped_not_implemented",
        "can_continue": True,
        "message": "fundamentals pipeline will be integrated in v1.3 next step",
    }



def _pipeline_step_changed(result: dict[str, Any]) -> bool:
    """判断某个 pipeline step 是否实际改变了数据库中的上游数据。

    用途：
    - 如果 news / sentiment / technical / market 发生变化，features 应该在同一 job 内自动重建；
    - 如果只是 cached / skipped / duplicate，则不触发 features 重建。
    """
    status = str(result.get("status") or "").lower()

    if status in {"created", "updated", "created_or_updated"}:
        # 对 news 来说 fetched 1000 但全部 duplicate 时，不算真正变化。
        inserted = int(result.get("inserted") or result.get("inserted_count") or 0)
        updated = int(result.get("updated") or result.get("updated_count") or 0)

        module = result.get("module")
        if module in {"news", "sentiment", "technical", "market"}:
            return (inserted + updated) > 0 or module == "technical"

        return True

    # 部分 service 返回 updated_count / inserted_count，但 status 不是标准 updated。
    numeric_changed = (
        int(result.get("inserted") or 0)
        + int(result.get("updated") or 0)
        + int(result.get("inserted_count") or 0)
        + int(result.get("updated_count") or 0)
    )
    return numeric_changed > 0



def run_data_pipeline_job(
    db: Session,
    *,
    tickers: list[str],
    modules: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    force_refresh: bool = False,
    run_async: bool = False,
) -> dict[str, Any]:
    """执行数据链路任务。

    当前版本同步执行。run_async 参数保留，后续可改成 BackgroundTasks/Celery。
    """
    job_id = f"data_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    modules = modules or DEFAULT_MODULES
    normalized_tickers = [t.strip().upper() for t in tickers if t and t.strip()]

    items: list[dict[str, Any]] = []
    success_steps = 0
    failed_steps = 0
    skipped_steps = 0

    _insert_crawler_log(
        db,
        task_type="data_pipeline_job",
        ticker=None,
        status="running",
        message=f"data pipeline job started: {job_id}",
        detail={
            "job_id": job_id,
            "tickers": normalized_tickers,
            "modules": modules,
            "start_date": start_date,
            "end_date": end_date,
            "force_refresh": force_refresh,
            "run_async": run_async,
        },
    )

    for ticker in normalized_tickers:
        # 标记当前 ticker 在本次 job 中是否发生了上游数据变化。
        # 如果变化，则后续 features 模块自动重建 snapshot。
        upstream_changed = False

        for module in modules:
            try:
                if module == "market":
                    result = run_market_module(db, ticker, end_date, force_refresh)
                elif module == "technical":
                    result = run_technical_module(db, ticker, end_date, force_refresh)
                elif module == "features":
                    result = run_features_module(
                        db,
                        ticker,
                        end_date,
                        force_refresh,
                        upstream_changed=upstream_changed,
                    )
                    if result.get("can_continue"):
                        upstream_changed = False
                elif module == "news":
                    result = run_news_module(db, ticker, start_date, end_date, force_refresh)
                elif module == "sentiment":
                    result = run_sentiment_module(db, ticker, start_date, end_date, force_refresh)
                elif module == "fundamentals":
                    result = run_fundamentals_module_placeholder(ticker)
                else:
                    result = {
                        "module": module,
                        "ticker": ticker,
                        "status": "failed",
                        "can_continue": False,
                        "message": f"unknown pipeline module: {module}",
                    }

                status = result.get("status", "unknown")
                if status in {"failed", "error"} or result.get("can_continue") is False:
                    failed_steps += 1
                    log_status = "failed"
                elif str(status).startswith("skipped"):
                    skipped_steps += 1
                    log_status = "skipped"
                else:
                    success_steps += 1
                    log_status = "success"

                if module != "features" and _pipeline_step_changed(result):
                    upstream_changed = True
                    result["upstream_changed_for_features"] = True

                items.append(result)

                _insert_crawler_log(
                    db,
                    task_type="data_pipeline_step",
                    ticker=ticker,
                    status=log_status,
                    message=json.dumps(result, ensure_ascii=False, default=str)[:1000],
                    detail={"job_id": job_id, "result": result},
                    fetched_count=int(result.get("fetched_count") or result.get("updated") or 0),
                )

            except Exception as exc:
                failed_steps += 1
                result = {
                    "module": module,
                    "ticker": ticker,
                    "status": "failed",
                    "can_continue": False,
                    "message": str(exc),
                }
                items.append(result)
                _insert_crawler_log(
                    db,
                    task_type="data_pipeline_step",
                    ticker=ticker,
                    status="failed",
                    message=str(exc),
                    detail={"job_id": job_id, "module": module, "ticker": ticker},
                )

    total_steps = len(items)
    if failed_steps == 0:
        status = "success"
    elif success_steps > 0 or skipped_steps > 0:
        status = "partial_success"
    else:
        status = "failed"

    summary = {
        "job_id": job_id,
        "status": status,
        "tickers": normalized_tickers,
        "modules": modules,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "force_refresh": force_refresh,
        "run_async": run_async,
        "total_steps": total_steps,
        "success_steps": success_steps,
        "failed_steps": failed_steps,
        "skipped_steps": skipped_steps,
        "items": items,
    }

    _insert_crawler_log(
        db,
        task_type="data_pipeline_job",
        ticker=None,
        status=status,
        message=f"data pipeline job finished: {job_id}, status={status}",
        detail=summary,
        fetched_count=success_steps,
    )

    return summary


def get_data_coverage(db: Session, ticker: str, end_date: date | None = None) -> dict[str, Any]:
    """查询某股票各类数据覆盖情况。"""
    ticker = ticker.upper()

    price_latest = _latest_price_date(db, ticker, end_date)
    indicator_latest = _latest_indicator_date(db, ticker, end_date)
    news_latest = _latest_news_publish_date(db, ticker, end_date)
    sentiment_latest = _latest_sentiment_date(db, ticker, end_date)

    price = _coverage_count_date(
        db,
        table="price_data",
        ticker=ticker,
        date_col="trading_date",
        latest_date=price_latest,
        target_date=end_date,
    )

    technical = _coverage_count_date(
        db,
        table="technical_indicators",
        ticker=ticker,
        date_col="trading_date",
        latest_date=indicator_latest,
        target_date=price_latest or end_date,
    )

    sentiment_target = news_latest or end_date
    sentiment = _coverage_count_date(
        db,
        table="sentiment_daily",
        ticker=ticker,
        date_col="trading_date",
        latest_date=sentiment_latest,
        target_date=sentiment_target,
    )

    news = _coverage_news(db, ticker, end_date, news_latest)
    snapshot = _coverage_snapshot(db, ticker, end_date, price_latest)

    return {
        "ticker": ticker,
        "end_date": end_date.isoformat() if end_date else None,
        "price_data": price,
        "technical_indicators": technical,
        "news_data": news,
        "sentiment_daily": sentiment,
        "model_feature_snapshots": snapshot,
        "recommendation": _coverage_recommendation(price, technical, news, sentiment, snapshot),
    }


def _coverage_count_date(
    db: Session,
    *,
    table: str,
    ticker: str,
    date_col: str,
    latest_date: date | None,
    target_date: date | None,
) -> dict[str, Any]:
    if not _table_exists(db, table):
        return {
            "exists": False,
            "row_count": 0,
            "latest_date": None,
            "target_date": target_date.isoformat() if target_date else None,
            "status": "missing_table",
        }

    sql = f"SELECT COUNT(*) FROM {table} WHERE ticker = :ticker"
    params: dict[str, Any] = {"ticker": ticker}
    if target_date:
        sql += f" AND {date_col} <= :target_date"
        params["target_date"] = target_date

    row_count = int(_scalar(db, sql, params) or 0)

    status = _date_status(latest_date, target_date)
    if row_count == 0:
        status = "empty"

    return {
        "exists": True,
        "row_count": row_count,
        "latest_date": latest_date.isoformat() if latest_date else None,
        "target_date": target_date.isoformat() if target_date else None,
        "status": status,
    }


def _coverage_news(
    db: Session,
    ticker: str,
    end_date: date | None,
    latest_news_date: date | None,
) -> dict[str, Any]:
    if not _table_exists(db, "news_data"):
        return {
            "exists": False,
            "row_count": 0,
            "latest_publish_date": None,
            "status": "missing_table",
        }

    row_count = int(_scalar(db, "SELECT COUNT(*) FROM news_data WHERE ticker = :ticker", {"ticker": ticker}) or 0)

    status = "ok" if row_count > 0 else "empty"
    if end_date and latest_news_date and latest_news_date < end_date:
        status = "stale"

    return {
        "exists": True,
        "row_count": row_count,
        "latest_publish_date": latest_news_date.isoformat() if latest_news_date else None,
        "target_date": end_date.isoformat() if end_date else None,
        "status": status,
    }


def _coverage_snapshot(
    db: Session,
    ticker: str,
    end_date: date | None,
    price_latest: date | None,
) -> dict[str, Any]:
    if not _table_exists(db, "model_feature_snapshots"):
        return {
            "exists": False,
            "row_count": 0,
            "latest_base_trading_date": None,
            "feature_count": None,
            "dataset_version": None,
            "status": "missing_table",
        }

    target = end_date or price_latest
    snapshot = _latest_snapshot(db, ticker, target)

    row_count = int(
        _scalar(
            db,
            "SELECT COUNT(*) FROM model_feature_snapshots WHERE ticker = :ticker",
            {"ticker": ticker},
        )
        or 0
    )

    if not snapshot:
        return {
            "exists": True,
            "row_count": row_count,
            "latest_base_trading_date": None,
            "feature_count": None,
            "dataset_version": None,
            "target_date": target.isoformat() if target else None,
            "status": "empty",
        }

    latest = _to_date(snapshot.get("base_trading_date"))
    feature_count = int(snapshot.get("feature_count") or 0)
    status = "ok" if feature_count >= 50 else "partial"
    if target and latest and latest < target:
        status = "stale"

    return {
        "exists": True,
        "row_count": row_count,
        "latest_base_trading_date": latest.isoformat() if latest else None,
        "feature_count": feature_count,
        "dataset_version": snapshot.get("dataset_version"),
        "target_date": target.isoformat() if target else None,
        "status": status,
    }


def _coverage_recommendation(
    price: dict[str, Any],
    technical: dict[str, Any],
    news: dict[str, Any],
    sentiment: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """根据覆盖情况给出数据准备建议。"""
    suggested: list[str] = []
    problems: list[str] = []

    if price.get("status") not in {"ok"}:
        problems.append("price_data")
        suggested.extend(["market", "technical", "features"])

    if technical.get("status") not in {"ok"}:
        problems.append("technical_indicators")
        suggested.extend(["technical", "features"])

    # news 没有实现自动抓取，所以只提示，不强制影响 core readiness。
    if news.get("status") in {"empty", "stale", "missing_table"}:
        problems.append("news_data")
        suggested.append("news")

    if sentiment.get("status") not in {"ok"}:
        problems.append("sentiment_daily")
        suggested.extend(["sentiment", "features"])

    if snapshot.get("status") not in {"ok"}:
        problems.append("model_feature_snapshots")
        suggested.append("features")

    deduped: list[str] = []
    for item in suggested:
        if item not in deduped:
            deduped.append(item)

    if not problems:
        return {
            "status": "ready",
            "suggested_modules": [],
            "message": "core data is ready for prediction",
        }

    # 行情/指标/特征是核心问题；news/sentiment 是增强问题。
    core_problem = any(p in problems for p in ["price_data", "technical_indicators", "model_feature_snapshots"])

    return {
        "status": "partial" if not core_problem else "not_ready",
        "suggested_modules": deduped,
        "message": f"missing/stale modules: {problems}",
    }
