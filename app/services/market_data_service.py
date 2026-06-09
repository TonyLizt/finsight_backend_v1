"""行情数据补全服务：线上自爬版。

本服务用于为股票详情、预测、回测准备最新可用的日频行情数据。

当前版本的核心策略：
1. 默认不依赖 B 同学的本地 raw CSV；
2. 优先使用 Alpha Vantage 官方 API 自行拉取日频行情；
3. Alpha Vantage 不可用时，尝试 Yahoo Chart 作为兜底；
4. 如果线上抓取失败，必须明确返回 failed 或 cached_with_fetch_failed；
5. 如果数据库缓存存在明显异常价格，不允许继续生成新的模型特征快照；
6. 所有写入字段尽量与 B 同学已有 price_data / import_price_csv.py 的字段保持一致。

需要的环境变量：
- ALPHA_VANTAGE_API_KEY=你的 Alpha Vantage API Key
- MARKET_DATA_PRIMARY_SOURCE=alpha_vantage
- ENABLE_YAHOO_CHART_FALLBACK=1
- ENABLE_LOCAL_RAW_CSV_FALLBACK=0
"""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import requests
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_LOCAL_RAW_ROOT = "/external_datasets/market_data/backtest_market_raw_20250521_20260531"


@dataclass
class MarketDataRefreshResult:
    """行情补全结果。

    status:
    - cached：数据库已有足够新且质量正常的数据；
    - updated：本次成功从线上或本地源写入 / 更新行情；
    - cached_with_fetch_failed：线上抓取失败，但已有缓存质量正常，非强制刷新时允许继续；
    - failed：抓取失败，且缓存缺失或疑似异常，后续不能继续生成模型特征；
    - skipped：配置关闭或参数缺失。

    can_continue:
    - True：后续可以继续重算 technical_indicators / 生成 feature snapshot；
    - False：后续应停止，避免基于坏数据预测。
    """

    ticker: str
    status: str
    can_continue: bool
    source: str | None = None
    latest_price_date: str | None = None
    inserted_count: int = 0
    updated_count: int = 0
    fetched_count: int = 0
    message: str = ""
    error: str | None = None
    price_quality_status: str = "unknown"
    suspicious_dates: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["suspicious_dates"] is None:
            data["suspicious_dates"] = []
        return data


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _today_utc_date() -> date:
    return datetime.now(timezone.utc).date()


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value[:10]).date()
    except ValueError:
        return None


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        text_value = str(value).strip()
        if not text_value or text_value.lower() in {"nan", "none", "null"}:
            return default
        return float(text_value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _table_columns(db: Session, table_name: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(db.bind).get_columns(table_name)}
    except Exception:
        return set()


def get_latest_price_date(db: Session, ticker: str) -> date | None:
    """读取 price_data 中某股票最新交易日。"""
    row = db.execute(
        text("SELECT MAX(trading_date) AS latest_date FROM price_data WHERE ticker = :ticker"),
        {"ticker": ticker.upper()},
    ).mappings().first()
    return _to_date(row["latest_date"]) if row and row["latest_date"] else None


def get_recent_price_rows(db: Session, ticker: str, days: int = 120) -> list[dict[str, Any]]:
    """读取最近若干日行情，用于质量检测。"""
    rows = db.execute(
        text(
            """
            SELECT ticker, trading_date, open, high, low, close, adj_close, volume
            FROM price_data
            WHERE ticker = :ticker
            ORDER BY trading_date DESC
            LIMIT :limit
            """
        ),
        {"ticker": ticker.upper(), "limit": days},
    ).mappings().all()
    return [dict(row) for row in reversed(rows)]


def detect_suspicious_price_rows(
    rows: list[dict[str, Any]],
    threshold: float | None = None,
) -> list[str]:
    """检测明显异常价格点。

    规则：
    - 对每个交易日，取前后最多 10 个有效 close 的中位数；
    - 如果当前 close 相比邻域中位数偏离超过 threshold，则标记异常；
    - 默认 threshold=0.35，即偏离 35% 以上视为疑似坏数据。

    这个规则用于拦截“周围都是 300，某天突然 190”这种 seed/demo 残留。
    """
    if threshold is None:
        threshold = _env_float("PRICE_SUSPICIOUS_CHANGE_THRESHOLD", 0.35)

    valid: list[tuple[date, float]] = []
    for row in rows:
        trading_date = _to_date(row.get("trading_date"))
        close = _to_float(row.get("close"))
        if trading_date is not None and close is not None and close > 0:
            valid.append((trading_date, close))

    if len(valid) < 8:
        return []

    closes = [x[1] for x in valid]
    suspicious: list[str] = []

    for idx, (trading_date, close) in enumerate(valid):
        left = max(0, idx - 10)
        right = min(len(valid), idx + 11)
        neighbor = [closes[j] for j in range(left, right) if j != idx]

        if len(neighbor) < 5:
            continue

        neighbor_median = median(neighbor)
        if neighbor_median <= 0:
            continue

        deviation = abs(close - neighbor_median) / neighbor_median
        if deviation >= threshold:
            suspicious.append(trading_date.isoformat())

    return suspicious


def validate_cached_price_quality(db: Session, ticker: str) -> tuple[str, list[str]]:
    """检查缓存行情质量。

    返回：
    - ok：未发现明显异常；
    - suspicious：发现明显异常价格；
    - empty：没有行情。
    """
    rows = get_recent_price_rows(db, ticker, days=_env_int("PRICE_QUALITY_LOOKBACK_DAYS", 120))
    if not rows:
        return "empty", []

    suspicious_dates = detect_suspicious_price_rows(rows)
    if suspicious_dates:
        return "suspicious", suspicious_dates

    return "ok", []


def _expected_recent_weekdays(start_date: date, end_date: date) -> set[date]:
    """粗略生成工作日集合，不精确处理美股节假日。"""
    result: set[date] = set()
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            result.add(current)
        current += timedelta(days=1)
    return result


def has_recent_price_gap(db: Session, ticker: str, lookback_days: int) -> bool:
    """检测最近一段时间内是否有明显工作日缺口。"""
    end_date = _today_utc_date()
    start_date = end_date - timedelta(days=lookback_days)

    rows = db.execute(
        text(
            """
            SELECT trading_date
            FROM price_data
            WHERE ticker = :ticker
              AND trading_date >= :start_date
              AND trading_date <= :end_date
            """
        ),
        {
            "ticker": ticker.upper(),
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings().all()

    actual = {_to_date(row["trading_date"]) for row in rows}
    actual.discard(None)

    expected = _expected_recent_weekdays(start_date, end_date)
    missing_count = len(expected - actual)

    # 允许节假日造成少量缺口；缺口过多才认为需要刷新。
    return missing_count >= _env_int("MARKET_DATA_GAP_TRIGGER_COUNT", 3)


def _normalize_price_record(ticker: str, row: dict[str, Any], source: str) -> dict[str, Any] | None:
    """把不同来源行情字段统一为 price_data 表字段。"""
    trading_date = _to_date(
        row.get("trading_date")
        or row.get("date")
        or row.get("Date")
        or row.get("timestamp")
        or row.get("datetime")
    )
    if trading_date is None:
        return None

    open_price = _to_float(row.get("open") or row.get("Open"))
    high_price = _to_float(row.get("high") or row.get("High"))
    low_price = _to_float(row.get("low") or row.get("Low"))
    close_price = _to_float(row.get("close") or row.get("Close"))
    adj_close = _to_float(
        row.get("adj_close")
        or row.get("Adj Close")
        or row.get("adjusted_close")
        or row.get("adjclose")
        or close_price
    )
    volume = _to_int(row.get("volume") or row.get("Volume"), default=0)

    if close_price is None or close_price <= 0:
        return None

    previous_close = _to_float(row.get("previous_close"))
    change_amount = None
    change_percent = None
    daily_return = None
    amplitude = None

    if previous_close is not None and previous_close > 0:
        change_amount = close_price - previous_close
        daily_return = change_amount / previous_close
        change_percent = daily_return * 100

        if high_price is not None and low_price is not None:
            amplitude = (high_price - low_price) / previous_close * 100

    return {
        "ticker": ticker.upper(),
        "trading_date": trading_date,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "adj_close": adj_close,
        "previous_close": previous_close,
        "change_amount": change_amount,
        "change_percent": change_percent,
        "daily_return": daily_return,
        "amplitude": amplitude,
        "volume": volume,
        "source": source,
    }


def _add_previous_close(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按交易日排序后补 previous_close / daily_return / change_percent / amplitude。"""
    records = sorted(records, key=lambda item: item["trading_date"])
    previous_close: float | None = None

    for record in records:
        close = _to_float(record.get("close"))
        high = _to_float(record.get("high"))
        low = _to_float(record.get("low"))

        if previous_close is not None and previous_close > 0 and close is not None:
            record["previous_close"] = previous_close
            record["change_amount"] = close - previous_close
            record["daily_return"] = (close - previous_close) / previous_close
            record["change_percent"] = record["daily_return"] * 100

            if high is not None and low is not None:
                record["amplitude"] = (high - low) / previous_close * 100

        if close is not None and close > 0:
            previous_close = close

    return records


def _alpha_vantage_request(params: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    """请求 Alpha Vantage 并处理常见限流 / 错误响应。"""
    url = "https://www.alphavantage.co/query"
    response = requests.get(url, params=params, timeout=timeout_seconds)
    response.raise_for_status()

    payload = response.json()

    # Alpha Vantage 限流或 key 错误通常通过 JSON 字段返回，而不是 HTTP 4xx。
    for key in ("Error Message", "Information", "Note"):
        if key in payload:
            raise RuntimeError(f"Alpha Vantage {key}: {payload[key]}")

    return payload


def fetch_alpha_vantage_daily_prices(
    ticker: str,
    start_date: date,
    end_date: date,
    timeout_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """从 Alpha Vantage 官方 API 拉取日频行情。

    优先尝试 TIME_SERIES_DAILY_ADJUSTED；如果当前 key 不支持或接口返回异常，
    再尝试 TIME_SERIES_DAILY。两者返回均整理为 price_data 字段。
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY is not set")

    timeout_seconds = timeout_seconds or _env_int("MARKET_DATA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)

    # 如果起始日期较早，用 full；否则 compact 可减少数据量。
    outputsize = "full" if start_date < (_today_utc_date() - timedelta(days=90)) else "compact"

    functions = [
        x.strip()
        for x in os.getenv(
            "ALPHA_VANTAGE_DAILY_FUNCTIONS",
            "TIME_SERIES_DAILY_ADJUSTED,TIME_SERIES_DAILY",
        ).split(",")
        if x.strip()
    ]

    errors: list[str] = []

    for function in functions:
        try:
            payload = _alpha_vantage_request(
                {
                    "function": function,
                    "symbol": ticker.upper(),
                    "outputsize": outputsize,
                    "datatype": "json",
                    "apikey": api_key,
                },
                timeout_seconds=timeout_seconds,
            )

            series = payload.get("Time Series (Daily)")
            if not isinstance(series, dict) or not series:
                raise RuntimeError(f"Alpha Vantage response missing Time Series (Daily), keys={list(payload.keys())}")

            records: list[dict[str, Any]] = []

            for date_text, values in series.items():
                trading_date = _to_date(date_text)
                if trading_date is None:
                    continue
                if trading_date < start_date or trading_date > end_date:
                    continue

                # TIME_SERIES_DAILY_ADJUSTED:
                # 1 open, 2 high, 3 low, 4 close, 5 adjusted close, 6 volume
                # TIME_SERIES_DAILY:
                # 1 open, 2 high, 3 low, 4 close, 5 volume
                raw = {
                    "trading_date": trading_date,
                    "open": values.get("1. open"),
                    "high": values.get("2. high"),
                    "low": values.get("3. low"),
                    "close": values.get("4. close"),
                    "adj_close": values.get("5. adjusted close") or values.get("4. close"),
                    "volume": values.get("6. volume") or values.get("5. volume"),
                }

                record = _normalize_price_record(ticker, raw, source=f"alpha_vantage:{function}")
                if record is not None:
                    records.append(record)

            if records:
                return _add_previous_close(records)

            raise RuntimeError(f"Alpha Vantage returned no records in requested range {start_date}~{end_date}")

        except Exception as exc:
            errors.append(f"{function} failed: {exc}")

    raise RuntimeError("; ".join(errors))


def fetch_yahoo_chart_prices(
    ticker: str,
    start_date: date,
    end_date: date,
    timeout_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """从 Yahoo Chart 拉取日频行情。

    本实现按 B 同学的 download_backtest_market_yahoo_chart.py 对齐：
    - URL: https://query1.finance.yahoo.com/v8/finance/chart/{ticker}
    - interval=1d
    - events=history
    - includeAdjustedClose=true
    - headers 使用简洁 User-Agent / Accept
    - 输出字段等价于 CSV: Date, Open, High, Low, Close, Adj Close, Volume

    注意：Yahoo 的 period2 是右开边界，所以这里使用 end_date + 1 day。
    """
    timeout_seconds = timeout_seconds or _env_int("MARKET_DATA_TIMEOUT_SECONDS", 60)

    period1 = int(
        datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    )
    period2 = int(
        datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()
    )

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"
        f"?period1={period1}&period2={period2}"
        f"&interval=1d&events=history&includeAdjustedClose=true"
    )

    headers = {
        "User-Agent": os.getenv("MARKET_DATA_USER_AGENT", "Mozilla/5.0"),
        "Accept": "application/json,text/plain,*/*",
    }

    response = requests.get(url, headers=headers, timeout=timeout_seconds)
    response.raise_for_status()

    payload = response.json()
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart error: {chart['error']}")

    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No chart result for {ticker}: {payload}")

    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adjclose_block = (indicators.get("adjclose") or [{}])[0]

    open_list = quote.get("open") or []
    high_list = quote.get("high") or []
    low_list = quote.get("low") or []
    close_list = quote.get("close") or []
    volume_list = quote.get("volume") or []
    adjclose_list = adjclose_block.get("adjclose") or []

    records: list[dict[str, Any]] = []

    for idx, ts in enumerate(timestamps):
        close = close_list[idx] if idx < len(close_list) else None
        if close is None:
            continue

        trading_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if trading_date < start_date or trading_date > end_date:
            continue

        raw = {
            "trading_date": trading_date,
            "open": open_list[idx] if idx < len(open_list) else None,
            "high": high_list[idx] if idx < len(high_list) else None,
            "low": low_list[idx] if idx < len(low_list) else None,
            "close": close,
            "adj_close": adjclose_list[idx] if idx < len(adjclose_list) else close,
            "volume": volume_list[idx] if idx < len(volume_list) else None,
        }

        record = _normalize_price_record(ticker, raw, source="yahoo_chart")
        if record is not None:
            records.append(record)

    if not records:
        raise RuntimeError(
            f"Yahoo chart returned no valid OHLCV rows in requested range "
            f"{start_date}~{end_date}"
        )

    return _add_previous_close(records)


def _find_local_raw_csv(ticker: str, local_raw_root: str | None = None) -> list[Path]:
    """可选本地 CSV fallback。默认关闭，仅用于应急或离线演示。"""
    root = Path(local_raw_root or os.getenv("MARKET_DATA_LOCAL_RAW_ROOT", DEFAULT_LOCAL_RAW_ROOT))
    if not root.exists():
        return []

    ticker_lower = ticker.lower()
    candidates: list[Path] = []
    for path in root.rglob("*.csv"):
        name = path.name.lower()
        stem = path.stem.lower()
        if stem == ticker_lower or stem.startswith(f"{ticker_lower}_") or ticker_lower in name:
            candidates.append(path)

    return sorted(candidates)


def fetch_local_raw_csv_prices(
    ticker: str,
    start_date: date | None = None,
    end_date: date | None = None,
    local_raw_root: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """从本地 CSV 读取行情。默认不启用，除非 ENABLE_LOCAL_RAW_CSV_FALLBACK=1。"""
    files = _find_local_raw_csv(ticker, local_raw_root)
    if not files:
        return [], None

    for path in files:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
        except Exception:
            continue

        records: list[dict[str, Any]] = []
        for row in rows:
            record = _normalize_price_record(ticker, row, source="local_raw_csv")
            if record is None:
                continue

            trading_date = record["trading_date"]
            if start_date and trading_date < start_date:
                continue
            if end_date and trading_date > end_date:
                continue

            records.append(record)

        if records:
            return _add_previous_close(records), str(path)

    return [], None


def upsert_price_records(db: Session, records: list[dict[str, Any]]) -> tuple[int, int]:
    """将标准化行情 upsert 到 price_data。"""
    if not records:
        return 0, 0

    cols = _table_columns(db, "price_data")
    if not cols:
        raise RuntimeError("price_data table not found")

    usable = [
        "ticker",
        "trading_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "previous_close",
        "change_amount",
        "change_percent",
        "daily_return",
        "amplitude",
        "volume",
    ]
    usable = [col for col in usable if col in cols]

    select_sql = text(
        "SELECT id FROM price_data WHERE ticker = :ticker AND trading_date = :trading_date LIMIT 1"
    )

    insert_sql = text(
        f"""
        INSERT INTO price_data ({", ".join(f"`{col}`" for col in usable)})
        VALUES ({", ".join(f":{col}" for col in usable)})
        """
    )

    update_cols = [col for col in usable if col not in {"ticker", "trading_date"}]
    update_sql = text(
        f"""
        UPDATE price_data
        SET {", ".join(f"`{col}` = :{col}" for col in update_cols)}
        WHERE ticker = :ticker AND trading_date = :trading_date
        """
    )

    inserted = 0
    updated = 0

    for record in records:
        row = {col: record.get(col) for col in usable}

        exists = db.execute(
            select_sql,
            {
                "ticker": row["ticker"],
                "trading_date": row["trading_date"],
            },
        ).first()

        if exists:
            db.execute(update_sql, row)
            updated += 1
        else:
            db.execute(insert_sql, row)
            inserted += 1

    db.commit()
    return inserted, updated


def fetch_online_price_records(
    ticker: str,
    start_date: date,
    end_date: date,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """按配置抓取行情：Yahoo Chart 优先，不再使用 Alpha Vantage 行情接口。

    v1.3 Yahoo market 策略：
    1. 默认只尝试 Yahoo Chart；
    2. 可选 local_raw_csv fallback；
    3. 即使 MARKET_DATA_SOURCE_PRIORITY 中仍写 alpha_vantage，也会自动忽略；
    4. 新闻抓取不受影响，Alpha Vantage News Sentiment 仍可继续用于 news_data。
    """
    raw_priority = os.getenv("MARKET_DATA_SOURCE_PRIORITY", "yahoo_chart,local_raw_csv")
    source_priority = [item.strip().lower() for item in raw_priority.split(",") if item.strip()]

    # 行情链路不再调用 Alpha Vantage，避免 premium endpoint / free rate limit。
    source_priority = [
        item
        for item in source_priority
        if item not in {"alpha_vantage", "alphavantage", "av"}
    ]

    if not source_priority:
        source_priority = ["yahoo_chart", "local_raw_csv"]

    errors: list[str] = []

    for source in source_priority:
        try:
            if source in {"yahoo", "yahoo_chart"}:
                if not _env_bool("ENABLE_YAHOO_CHART_FALLBACK", True):
                    errors.append("yahoo_chart disabled by ENABLE_YAHOO_CHART_FALLBACK=0")
                    continue

                records = fetch_yahoo_chart_prices(ticker, start_date, end_date)
                if records:
                    return records, "yahoo_chart", errors
                errors.append("yahoo_chart returned no records")

            elif source in {"local", "local_raw_csv"}:
                if not _env_bool("ENABLE_LOCAL_RAW_CSV_FALLBACK", False):
                    errors.append("local_raw_csv disabled by ENABLE_LOCAL_RAW_CSV_FALLBACK=0")
                    continue

                records, path = fetch_local_raw_csv_prices(ticker, start_date, end_date)
                if records:
                    return records, f"local_raw_csv:{path}", errors
                errors.append("local_raw_csv returned no records")

            else:
                errors.append(f"unknown or disabled market data source: {source}")

        except Exception as exc:
            errors.append(f"{source} failed: {exc}")

    return [], "", errors


def ensure_price_data(
    db: Session,
    ticker: str,
    min_history_days: int | None = None,
    max_stale_days: int | None = None,
    force_refresh: bool = False,
    target_date: date | None = None,
) -> dict[str, Any]:
    """确保 price_data 中有某 ticker 的最新可用日频行情。

    线上自爬规则：
    1. 非强制刷新时，如果缓存新鲜且质量正常，直接返回 cached；
    2. 需要刷新时，按 MARKET_DATA_SOURCE_PRIORITY 自行爬取；
    3. 默认使用与 B 同学脚本一致的 Yahoo Chart，不依赖 B 同学本地 CSV；
    4. 抓取失败且 force_refresh=True 时，返回 failed，不继续预测；
    5. 抓取失败但缓存质量正常且 force_refresh=False 时，允许使用缓存；
    6. 发现异常价格时返回 failed，阻止生成新 feature snapshot。
    """
    ticker = ticker.upper()
    target_date = target_date or _today_utc_date()

    min_history_days = min_history_days or _env_int("MARKET_DATA_MIN_HISTORY_DAYS", 252)
    max_stale_days = max_stale_days or _env_int("MARKET_DATA_MAX_STALE_DAYS", 5)

    latest_date = get_latest_price_date(db, ticker)
    quality_status, suspicious_dates = validate_cached_price_quality(db, ticker)

    stale = latest_date is None or latest_date < (target_date - timedelta(days=max_stale_days))
    has_gap = has_recent_price_gap(
        db,
        ticker,
        lookback_days=_env_int("MARKET_DATA_GAP_LOOKBACK_DAYS", 90),
    )

    if not force_refresh and latest_date and not stale and not has_gap and quality_status == "ok":
        return MarketDataRefreshResult(
            ticker=ticker,
            status="cached",
            can_continue=True,
            source="mysql_price_data",
            latest_price_date=latest_date.isoformat(),
            message="cached price_data is fresh enough",
            price_quality_status=quality_status,
            suspicious_dates=suspicious_dates,
        ).to_dict()

    start_date = target_date - timedelta(days=min_history_days + 30)

    # 非强制刷新时，已有数据可减少爬取窗口；强制刷新保留较长窗口以覆盖坏数据。
    if latest_date and not force_refresh:
        start_date = max(start_date, latest_date - timedelta(days=10))

    records, source, errors = fetch_online_price_records(ticker, start_date, target_date)

    if not records:
        latest_date = get_latest_price_date(db, ticker)
        quality_status, suspicious_dates = validate_cached_price_quality(db, ticker)

        if latest_date and quality_status == "ok" and not force_refresh:
            return MarketDataRefreshResult(
                ticker=ticker,
                status="cached_with_fetch_failed",
                can_continue=True,
                source="mysql_price_data",
                latest_price_date=latest_date.isoformat(),
                message="online fetch failed; using existing valid cached price_data",
                error="; ".join(errors) if errors else "no records fetched",
                price_quality_status=quality_status,
                suspicious_dates=suspicious_dates,
            ).to_dict()

        return MarketDataRefreshResult(
            ticker=ticker,
            status="failed",
            can_continue=False,
            source=None,
            latest_price_date=latest_date.isoformat() if latest_date else None,
            message="online fetch failed and cached price_data is missing or suspicious",
            error="; ".join(errors) if errors else "no records fetched",
            price_quality_status=quality_status,
            suspicious_dates=suspicious_dates,
        ).to_dict()

    inserted, updated = upsert_price_records(db, records)

    latest_date = get_latest_price_date(db, ticker)
    quality_status, suspicious_dates = validate_cached_price_quality(db, ticker)

    if quality_status == "suspicious":
        return MarketDataRefreshResult(
            ticker=ticker,
            status="failed",
            can_continue=False,
            source=source,
            latest_price_date=latest_date.isoformat() if latest_date else None,
            inserted_count=inserted,
            updated_count=updated,
            fetched_count=len(records),
            message="price_data refreshed but suspicious prices were detected; feature snapshot generation should be skipped",
            error="suspicious price rows detected",
            price_quality_status=quality_status,
            suspicious_dates=suspicious_dates,
        ).to_dict()

    return MarketDataRefreshResult(
        ticker=ticker,
        status="updated",
        can_continue=True,
        source=source,
        latest_price_date=latest_date.isoformat() if latest_date else None,
        inserted_count=inserted,
        updated_count=updated,
        fetched_count=len(records),
        message="price_data refreshed successfully from online source",
        price_quality_status=quality_status,
        suspicious_dates=suspicious_dates,
    ).to_dict()
