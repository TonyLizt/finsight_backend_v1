"""Twelve Data intraday market data service（v1.5）。

用途：
- 给 GET /api/stocks/{ticker}/detail?range=1d 返回美股 1 日日内走势；
- 默认返回小时级聚合，传 interval=1min 时返回原始分钟级曲线；
- 优先读取 intraday_price_data 数据库缓存；
- 数据库缺失时可现场调用 Twelve Data 1min 接口补入库；
- 不再依赖 AKShare / Yahoo。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.all_models import IntradayPriceData
from app.services.twelvedata_market_service import ensure_extra_tables, ensure_intraday_price_data


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _latest_intraday_date(db: Session, ticker: str) -> date | None:
    row = (
        db.query(IntradayPriceData.trading_date)
        .filter(IntradayPriceData.ticker == ticker.upper())
        .order_by(IntradayPriceData.trading_date.desc())
        .first()
    )
    return row[0] if row else None


def _load_minute_rows(db: Session, ticker: str, target_date: date) -> list[IntradayPriceData]:
    return (
        db.query(IntradayPriceData)
        .filter(
            IntradayPriceData.ticker == ticker.upper(),
            IntradayPriceData.trading_date == target_date,
            IntradayPriceData.interval_type == settings.twelvedata_intraday_interval,
        )
        .order_by(IntradayPriceData.market_timestamp.asc())
        .all()
    )


def _normalize_intraday_interval(interval: str | None) -> str:
    value = (interval or "hourly").strip().lower().replace("_", "-")
    if value in {"1min", "1-min", "1minute", "1-minute", "minute", "minutes", "min", "m1", "1m"}:
        return "1min"
    if value in {"hourly", "hour", "hours", "1h", "h1", "60min", "60-min"}:
        return "hourly"
    return "hourly"


def _minute_items(rows: list[IntradayPriceData]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: r.market_timestamp):
        if not row.market_timestamp:
            continue
        market_dt = row.market_timestamp.replace(second=0, microsecond=0)
        close = _to_float(row.close)
        items.append(
            {
                "timestamp": market_dt.isoformat(),
                "date": market_dt.date().isoformat(),
                "time": market_dt.strftime("%H:%M"),
                "open": _to_float(row.open),
                "high": _to_float(row.high),
                "low": _to_float(row.low),
                "close": close,
                "volume": _to_int(row.volume),
                "amount": None,
                "latest_price": close,
                "data_frequency": "1min",
                "source": "mysql_intraday_price_data:twelvedata_1min",
            }
        )
    return items


def _aggregate_hourly(rows: list[IntradayPriceData]) -> list[dict[str, Any]]:
    buckets: dict[str, list[IntradayPriceData]] = defaultdict(list)
    for row in rows:
        if not row.market_timestamp:
            continue
        hour_start = row.market_timestamp.replace(minute=0, second=0, microsecond=0)
        buckets[hour_start.isoformat(sep=" ")].append(row)

    items: list[dict[str, Any]] = []
    for _, bucket in sorted(buckets.items(), key=lambda x: x[0]):
        bucket = sorted(bucket, key=lambda r: r.market_timestamp)
        first = bucket[0]
        last = bucket[-1]
        highs = [_to_float(r.high) for r in bucket if r.high is not None]
        lows = [_to_float(r.low) for r in bucket if r.low is not None]
        volumes = [_to_int(r.volume) or 0 for r in bucket]
        market_dt = first.market_timestamp.replace(minute=0, second=0, microsecond=0)
        items.append(
            {
                "timestamp": market_dt.isoformat(),
                "date": market_dt.date().isoformat(),
                "time": market_dt.strftime("%H:%M"),
                "open": _to_float(first.open),
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": _to_float(last.close),
                "volume": sum(volumes),
                "amount": None,
                "latest_price": _to_float(last.close),
                "data_frequency": "hourly",
                "source": "mysql_intraday_price_data:twelvedata_1min_aggregated",
            }
        )
    return items


def get_intraday_curve(
    ticker: str,
    target_date: date | None = None,
    interval: str | None = "hourly",
) -> dict[str, Any]:
    """返回美股某日日内走势。

    interval:
    - hourly：默认值，把 1min 数据聚合成小时级 K 线，兼容旧前端；
    - 1min：直接返回 intraday_price_data 中的原始分钟级 K 线。

    target_date 为美股市场日期；不传时取 intraday_price_data 中最新交易日。
    如果数据库没有分钟行情，且 FINSIGHT_ENABLE_ON_DEMAND_INGEST=true，
    会现场调用 Twelve Data 1min 接口补入库后再读库返回。
    """
    ticker = ticker.upper().strip()
    normalized_interval = _normalize_intraday_interval(interval)
    ensure_extra_tables()

    db = SessionLocal()
    ingest_result: dict[str, Any] | None = None
    try:
        actual_date = target_date or _latest_intraday_date(db, ticker)

        if actual_date is None or not _load_minute_rows(db, ticker, actual_date):
            if settings.finsight_enable_on_demand_ingest:
                ingest_result = ensure_intraday_price_data(db, ticker, target_date=target_date)
                actual_date = target_date or _latest_intraday_date(db, ticker)
            else:
                ingest_result = {
                    "status": "skipped",
                    "message": "on-demand intraday ingest disabled",
                }

        if actual_date is None:
            return {
                "status": "empty",
                "source": "mysql_intraday_price_data",
                "data_frequency": normalized_interval,
                "ticker": ticker,
                "ak_symbol": None,
                "target_date": target_date.isoformat() if target_date else None,
                "actual_date": None,
                "items": [],
                "message": "no intraday rows in database",
                "ingest_result": ingest_result,
            }

        rows = _load_minute_rows(db, ticker, actual_date)
        if not rows:
            return {
                "status": "empty",
                "source": "mysql_intraday_price_data",
                "data_frequency": normalized_interval,
                "ticker": ticker,
                "ak_symbol": None,
                "target_date": target_date.isoformat() if target_date else None,
                "actual_date": actual_date.isoformat(),
                "items": [],
                "message": "no minute rows for target market date",
                "ingest_result": ingest_result,
            }

        if normalized_interval == "1min":
            items = _minute_items(rows)
        else:
            items = _aggregate_hourly(rows)

        return {
            "status": "success",
            "source": "mysql_intraday_price_data:twelvedata",
            "data_frequency": normalized_interval,
            "ticker": ticker,
            "ak_symbol": None,
            "target_date": target_date.isoformat() if target_date else None,
            "actual_date": actual_date.isoformat(),
            "items": items,
            "message": "ok",
            "minute_count": len(rows),
            "ingest_result": ingest_result,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "source": "mysql_intraday_price_data:twelvedata",
            "data_frequency": normalized_interval,
            "ticker": ticker,
            "target_date": target_date.isoformat() if target_date else None,
            "actual_date": None,
            "items": [],
            "message": "Twelve Data intraday read/fetch failed",
            "error": str(exc),
            "ingest_result": ingest_result,
        }
    finally:
        db.close()


def get_hourly_intraday_curve(ticker: str, target_date: date | None = None) -> dict[str, Any]:
    """Backward-compatible wrapper for existing callers."""
    return get_intraday_curve(ticker=ticker, target_date=target_date, interval="hourly")
