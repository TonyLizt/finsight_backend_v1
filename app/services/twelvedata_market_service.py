"""Twelve Data 行情自抓取与增量入库服务（v1.5）。

设计目标：
- 行情数据统一从 twelvedata.com 获取；
- 日频行情写入 price_data；
- 1 分钟行情写入 intraday_price_data；
- 每次运行自动读取数据库最新日期/时间戳，只抓增量数据；
- 支持脚本每日重复运行，也支持某个 ticker 缺数据时现场补入库。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import Base, engine
from app.models.all_models import IntradayPriceData, PriceData, Stock


MARKET_TZ = ZoneInfo(os.getenv("TWELVEDATA_TIMEZONE", settings.twelvedata_timezone or "America/New_York"))


@dataclass
class IngestResult:
    ticker: str
    module: str
    status: str
    can_continue: bool = True
    source: str | None = None
    start: str | None = None
    end: str | None = None
    latest_before: str | None = None
    latest_after: str | None = None
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def core_tickers() -> list[str]:
    raw = os.getenv("FINSIGHT_CORE_TICKERS", settings.finsight_core_tickers)
    items = [x.strip().upper() for x in (raw or "").split(",") if x.strip()]
    if not items:
        items = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result




def previous_completed_market_date(today: date | None = None) -> date:
    """返回最近一个已经完整结束的美股交易日。

    设计目的：分钟行情默认只抓“当前日期的前一个完整交易日”，
    避免从历史最新分钟一路补到 now，导致 Twelve Data 请求过大或脚本看似卡住。

    这里先按周末规则跳过周六/周日；美股节假日如果无数据，Twelve Data 会返回空，
    后续可再接入交易日历。
    """
    current = today or datetime.now(MARKET_TZ).date()
    d = current - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        d -= timedelta(days=1)
    return d

def ensure_extra_tables() -> None:
    """创建 v1.5 新增表。

    项目当前没有 Alembic，这里复用 SQLAlchemy metadata.create_all，
    对已有表无破坏；只会创建缺失的 intraday_price_data。
    """
    # 确保模型已导入后再 create_all。
    _ = IntradayPriceData
    Base.metadata.create_all(bind=engine)


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


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(raw[:19], fmt)
                    break
                except ValueError:
                    continue
            else:
                return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(MARKET_TZ).replace(tzinfo=None)
    return dt


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        raw = str(value).strip()
        if not raw or raw.lower() in {"none", "null", "nan", "-"}:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _api_key() -> str:
    key = os.getenv("TWELVEDATA_API_KEY") or settings.twelvedata_api_key or ""
    key = key.strip()
    if not key:
        raise RuntimeError("TWELVEDATA_API_KEY is not set")
    return key


def _request_time_series(
    *,
    symbol: str,
    interval: str,
    start_date: str | None = None,
    end_date: str | None = None,
    outputsize: int | None = None,
) -> dict[str, Any]:
    url = f"{settings.twelvedata_base_url.rstrip('/')}/time_series"
    params: dict[str, Any] = {
        "symbol": symbol.upper(),
        "interval": interval,
        "apikey": _api_key(),
        "format": "JSON",
        "timezone": settings.twelvedata_timezone,
        "order": "ASC",
    }
    if outputsize:
        params["outputsize"] = int(outputsize)
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if interval in {"1min", "5min", "15min", "30min", "45min", "1h"}:
        params["prepost"] = "true" if settings.twelvedata_intraday_prepost else "false"

    timeout = int(os.getenv("TWELVEDATA_TIMEOUT_SECONDS", str(settings.twelvedata_timeout_seconds)))
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected Twelve Data payload type: {type(payload)}")

    if payload.get("status") == "error" or "code" in payload and "message" in payload:
        raise RuntimeError(f"Twelve Data error: {payload.get('message') or payload}")

    values = payload.get("values")
    if not isinstance(values, list):
        raise RuntimeError(f"Twelve Data response missing values: {payload}")

    return payload


def latest_daily_date(db: Session, ticker: str) -> date | None:
    row = db.query(PriceData.trading_date).filter(PriceData.ticker == ticker.upper()).order_by(PriceData.trading_date.desc()).first()
    return row[0] if row else None


def latest_intraday_timestamp(db: Session, ticker: str) -> datetime | None:
    ensure_extra_tables()
    row = (
        db.query(IntradayPriceData.market_timestamp)
        .filter(IntradayPriceData.ticker == ticker.upper())
        .order_by(IntradayPriceData.market_timestamp.desc())
        .first()
    )
    return row[0] if row else None


def _ensure_stock_record(db: Session, ticker: str) -> None:
    ticker = ticker.upper()
    exists = db.query(Stock).filter(Stock.ticker == ticker).first()
    if exists:
        return
    db.add(
        Stock(
            ticker=ticker,
            company_name=ticker,
            security_name=ticker,
            market="NASDAQ",
            listing_source="twelvedata_on_demand",
            etf=False,
            is_supported=True,
            is_core_pool=ticker in core_tickers(),
            data_quality_score=0.8,
            raw_json={"source": "twelvedata_on_demand"},
            first_seen_at=datetime.now(),
            last_synced_at=datetime.now(),
        )
    )
    db.commit()


def _previous_close_from_db(db: Session, ticker: str, trading_date: date) -> float | None:
    row = (
        db.query(PriceData.close)
        .filter(PriceData.ticker == ticker.upper(), PriceData.trading_date < trading_date)
        .order_by(PriceData.trading_date.desc())
        .first()
    )
    if not row or row[0] is None:
        return None
    return float(row[0])


def fetch_daily_records(ticker: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
    payload = _request_time_series(
        symbol=ticker,
        interval=settings.twelvedata_daily_interval or "1day",
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        outputsize=settings.twelvedata_daily_outputsize,
    )
    records: list[dict[str, Any]] = []
    for item in payload.get("values") or []:
        trading_date = _to_date(item.get("datetime"))
        if not trading_date or trading_date < start_date or trading_date > end_date:
            continue
        close = _to_float(item.get("close"))
        if close is None or close <= 0:
            continue
        records.append(
            {
                "ticker": ticker.upper(),
                "trading_date": trading_date,
                "open": _to_float(item.get("open")),
                "high": _to_float(item.get("high")),
                "low": _to_float(item.get("low")),
                "close": close,
                "adj_close": close,
                "volume": _to_int(item.get("volume")),
                "source": "twelvedata:time_series:1day",
            }
        )
    records.sort(key=lambda x: x["trading_date"])
    return records


def _finalize_daily_records(db: Session, ticker: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_close: float | None = None
    if records:
        previous_close = _previous_close_from_db(db, ticker, records[0]["trading_date"])

    for record in records:
        close = _to_float(record.get("close"))
        high = _to_float(record.get("high"))
        low = _to_float(record.get("low"))
        if previous_close is not None and previous_close > 0 and close is not None:
            record["previous_close"] = previous_close
            record["change_amount"] = close - previous_close
            record["daily_return"] = (close - previous_close) / previous_close
            # 当前后端 display_change_percent 会把 <=1 的比例转成百分数，故这里存比例值。
            record["change_percent"] = record["daily_return"]
            if high is not None and low is not None:
                record["amplitude"] = (high - low) / previous_close
        if close is not None and close > 0:
            previous_close = close
    return records


def upsert_daily_records(db: Session, ticker: str, records: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    ticker = ticker.upper()
    for record in _finalize_daily_records(db, ticker, records):
        row = (
            db.query(PriceData)
            .filter(PriceData.ticker == ticker, PriceData.trading_date == record["trading_date"])
            .first()
        )
        if row:
            updated += 1
        else:
            row = PriceData(ticker=ticker, trading_date=record["trading_date"])
            db.add(row)
            inserted += 1

        for field in (
            "open", "high", "low", "close", "adj_close", "previous_close",
            "change_amount", "change_percent", "daily_return", "amplitude", "volume",
        ):
            if field in record:
                setattr(row, field, record.get(field))
    db.commit()
    return inserted, updated


def ensure_daily_price_data(
    db: Session,
    ticker: str,
    *,
    end_date: date | None = None,
    force_refresh: bool = False,
    initial_backfill_days: int | None = None,
) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    _ensure_stock_record(db, ticker)

    end_date = end_date or datetime.now(MARKET_TZ).date()
    latest = latest_daily_date(db, ticker)

    if latest and not force_refresh:
        start_date = latest
        if latest >= end_date:
            return IngestResult(
                ticker=ticker,
                module="daily_market",
                status="cached",
                source="mysql_price_data",
                latest_before=latest.isoformat(),
                latest_after=latest.isoformat(),
                message="price_data already covers target date; Twelve Data fetch skipped",
            ).to_dict()
    else:
        days = initial_backfill_days or settings.twelvedata_daily_initial_backfill_days
        start_date = end_date - timedelta(days=max(days, 30))

    try:
        records = fetch_daily_records(ticker, start_date, end_date)
        inserted, updated = upsert_daily_records(db, ticker, records)
        latest_after = latest_daily_date(db, ticker)
        return IngestResult(
            ticker=ticker,
            module="daily_market",
            status="updated" if records else "empty",
            source="twelvedata:time_series:1day",
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            latest_before=latest.isoformat() if latest else None,
            latest_after=latest_after.isoformat() if latest_after else None,
            fetched_count=len(records),
            inserted_count=inserted,
            updated_count=updated,
            message="daily price_data upsert finished" if records else "Twelve Data returned no daily rows in range",
        ).to_dict()
    except Exception as exc:
        latest_after = latest_daily_date(db, ticker)
        return IngestResult(
            ticker=ticker,
            module="daily_market",
            status="failed",
            can_continue=bool(latest_after),
            source="twelvedata:time_series:1day",
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            latest_before=latest.isoformat() if latest else None,
            latest_after=latest_after.isoformat() if latest_after else None,
            message="Twelve Data daily fetch failed; existing cache may still be usable",
            error=str(exc),
        ).to_dict()


def fetch_intraday_records(
    ticker: str,
    start_dt: datetime,
    end_dt: datetime,
    *,
    interval: str | None = None,
) -> list[dict[str, Any]]:
    interval = interval or settings.twelvedata_intraday_interval or "1min"
    payload = _request_time_series(
        symbol=ticker,
        interval=interval,
        start_date=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_date=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        outputsize=settings.twelvedata_intraday_outputsize,
    )
    records: list[dict[str, Any]] = []
    for item in payload.get("values") or []:
        market_dt = _to_datetime(item.get("datetime"))
        if not market_dt or market_dt < start_dt or market_dt > end_dt:
            continue
        close = _to_float(item.get("close"))
        if close is None or close <= 0:
            continue
        records.append(
            {
                "ticker": ticker.upper(),
                "trading_date": market_dt.date(),
                "market_timestamp": market_dt.replace(second=0, microsecond=0),
                "market_time": market_dt.strftime("%H:%M"),
                "interval_type": interval,
                "open": _to_float(item.get("open")),
                "high": _to_float(item.get("high")),
                "low": _to_float(item.get("low")),
                "close": close,
                "volume": _to_int(item.get("volume")),
                "source": f"twelvedata:time_series:{interval}",
                "raw_json": item,
                "fetched_at": datetime.now(),
            }
        )
    records.sort(key=lambda x: x["market_timestamp"])
    return records


def upsert_intraday_records(db: Session, ticker: str, records: list[dict[str, Any]]) -> tuple[int, int]:
    ensure_extra_tables()
    inserted = 0
    updated = 0
    ticker = ticker.upper()

    for record in records:
        row = (
            db.query(IntradayPriceData)
            .filter(
                IntradayPriceData.ticker == ticker,
                IntradayPriceData.market_timestamp == record["market_timestamp"],
                IntradayPriceData.interval_type == record["interval_type"],
            )
            .first()
        )
        if row:
            updated += 1
        else:
            row = IntradayPriceData(
                ticker=ticker,
                market_timestamp=record["market_timestamp"],
                interval_type=record["interval_type"],
            )
            db.add(row)
            inserted += 1

        for field in (
            "trading_date", "market_time", "open", "high", "low", "close",
            "volume", "amount", "vwap", "source", "raw_json", "fetched_at",
        ):
            if field in record:
                setattr(row, field, record.get(field))
    db.commit()
    return inserted, updated


def ensure_intraday_price_data(
    db: Session,
    ticker: str,
    *,
    target_date: date | None = None,
    force_refresh: bool = False,
    initial_backfill_days: int | None = None,
) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    _ensure_stock_record(db, ticker)
    ensure_extra_tables()

    latest = latest_intraday_timestamp(db, ticker)

    # v1.5 修复：默认分钟行情目标日 = price_data 中该 ticker 的最新交易日。
    # price_data.max(trading_date) 代表后端已经确认过的最近完整日频交易日；
    # 不再使用当前自然日，避免在盘中写入几十条残缺分钟数据。
    if target_date is None:
        target_date = latest_daily_date(db, ticker) or previous_completed_market_date()

    start_dt = datetime.combine(target_date, dt_time(9, 30))
    end_dt = datetime.combine(target_date, dt_time(16, 0))
    existing_count = (
        db.query(IntradayPriceData)
        .filter(IntradayPriceData.ticker == ticker, IntradayPriceData.trading_date == target_date)
        .count()
    )
    if existing_count >= 350 and not force_refresh:
        return IngestResult(
            ticker=ticker,
            module="intraday_market",
            status="cached",
            source="mysql_intraday_price_data",
            start=start_dt.isoformat(sep=" "),
            end=end_dt.isoformat(sep=" "),
            latest_before=latest.isoformat(sep=" ") if latest else None,
            latest_after=latest.isoformat(sep=" ") if latest else None,
            fetched_count=0,
            skipped_count=existing_count,
            message="intraday data already covers target full trading date; Twelve Data fetch skipped",
        ).to_dict()

    try:
        records = fetch_intraday_records(ticker, start_dt, end_dt)
        inserted, updated = upsert_intraday_records(db, ticker, records)
        latest_after = latest_intraday_timestamp(db, ticker)
        return IngestResult(
            ticker=ticker,
            module="intraday_market",
            status="updated" if records else "empty",
            source=f"twelvedata:time_series:{settings.twelvedata_intraday_interval}",
            start=start_dt.isoformat(sep=" "),
            end=end_dt.isoformat(sep=" "),
            latest_before=latest.isoformat(sep=" ") if latest else None,
            latest_after=latest_after.isoformat(sep=" ") if latest_after else None,
            fetched_count=len(records),
            inserted_count=inserted,
            updated_count=updated,
            message="intraday_price_data upsert finished" if records else "Twelve Data returned no intraday rows in range",
        ).to_dict()
    except Exception as exc:
        latest_after = latest_intraday_timestamp(db, ticker)
        return IngestResult(
            ticker=ticker,
            module="intraday_market",
            status="failed",
            can_continue=bool(latest_after),
            source=f"twelvedata:time_series:{settings.twelvedata_intraday_interval}",
            start=start_dt.isoformat(sep=" "),
            end=end_dt.isoformat(sep=" "),
            latest_before=latest.isoformat(sep=" ") if latest else None,
            latest_after=latest_after.isoformat(sep=" ") if latest_after else None,
            message="Twelve Data intraday fetch failed; existing cache may still be usable",
            error=str(exc),
        ).to_dict()


def sleep_between_requests() -> None:
    seconds = float(os.getenv("TWELVEDATA_REQUEST_SLEEP_SECONDS", str(settings.twelvedata_request_sleep_seconds)))
    if seconds > 0:
        time.sleep(seconds)


def write_crawler_log(
    db: Session,
    *,
    ticker: str | None,
    task_type: str,
    status: str,
    message: str,
    fetched_count: int = 0,
    detail: dict[str, Any] | None = None,
) -> None:
    if not _table_exists(db, "crawler_logs"):
        return
    cols = _table_columns(db, "crawler_logs")
    now = datetime.now()
    payload: dict[str, Any] = {
        "ticker": ticker,
        "task_type": task_type,
        "start_time": now,
        "end_time": now,
        "status": status,
        "message": message if not detail else f"{message}; detail={json.dumps(detail, ensure_ascii=False, default=str)[:4000]}",
        "fetched_count": fetched_count,
    }
    usable = {k: v for k, v in payload.items() if k in cols}
    if not usable:
        return
    db.execute(
        text(
            f"INSERT INTO crawler_logs ({', '.join(f'`{k}`' for k in usable)}) "
            f"VALUES ({', '.join(f':{k}' for k in usable)})"
        ),
        usable,
    )
    db.commit()
