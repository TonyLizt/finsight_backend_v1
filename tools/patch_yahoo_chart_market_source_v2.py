#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch backend market fetch to member-B-style Yahoo Chart source.

目标：
1. 后端行情 market 模块不再调用 Alpha Vantage 日频行情接口；
2. 使用与 B 同学 download_backtest_market_yahoo_chart.py 一致的 Yahoo Chart API；
3. 保留 local_raw_csv 作为可选 fallback；
4. 新闻抓取不受影响，Alpha Vantage News Sentiment 仍可继续用于 news_data；
5. 同时新增可单独运行的 app.scripts.download_market_yahoo_chart。

用法：
    python tools/patch_yahoo_chart_market_source_v2.py
    docker compose up -d --force-recreate backend
"""

from __future__ import annotations

import re
from pathlib import Path


YAHOO_FUNC = '''def fetch_yahoo_chart_prices(
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
'''


ONLINE_FUNC = '''def fetch_online_price_records(
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
'''


def _find_top_level_function(text: str, function_name: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"^def\s+{re.escape(function_name)}\s*\(", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None

    start = match.start()
    next_match = re.search(r"^(def|class)\s+\w+\s*[(:]", text[match.end():], re.MULTILINE)
    if next_match:
        end = match.end() + next_match.start()
    else:
        end = len(text)
    return start, end


def replace_function(text: str, function_name: str, replacement: str, *, required: bool = True) -> str:
    bounds = _find_top_level_function(text, function_name)
    if not bounds:
        if required:
            raise RuntimeError(f"function not found: {function_name}")
        return text

    start, end = bounds
    return text[:start] + replacement.rstrip() + "\n\n\n" + text[end:].lstrip("\n")


def insert_function_after(text: str, anchor_function: str, function_code: str) -> str:
    if f"def {function_code.split('def ', 1)[1].split('(', 1)[0]}(" in text:
        return text

    bounds = _find_top_level_function(text, anchor_function)
    if not bounds:
        return text + "\n\n" + function_code.rstrip() + "\n"

    _, end = bounds
    return text[:end].rstrip() + "\n\n\n" + function_code.rstrip() + "\n\n" + text[end:].lstrip("\n")


def main() -> None:
    path = Path("app/services/market_data_service.py")
    if not path.exists():
        raise FileNotFoundError(path)

    original = path.read_text(encoding="utf-8")
    text = original

    backup = path.with_suffix(".py.bak_yahoo_chart_market_source_v2")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    text = replace_function(text, "fetch_yahoo_chart_prices", YAHOO_FUNC, required=True)

    if _find_top_level_function(text, "fetch_online_price_records"):
        text = replace_function(text, "fetch_online_price_records", ONLINE_FUNC, required=True)
    else:
        # 有些版本没有这个函数，插入它，供后续 service / scripts 复用。
        text = insert_function_after(text, "fetch_yahoo_chart_prices", ONLINE_FUNC)

    # 把默认配置和说明改成 Yahoo Chart 优先。保留 Alpha Vantage 给新闻，不用于行情。
    replacements = {
        "alpha_vantage,yahoo_chart": "yahoo_chart,local_raw_csv",
        "alpha_vantage, yahoo_chart": "yahoo_chart, local_raw_csv",
        "默认优先 Alpha Vantage，再 Yahoo": "默认使用与 B 同学脚本一致的 Yahoo Chart",
        "优先 Alpha Vantage": "优先 Yahoo Chart",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    if text == original:
        print("No changes made.")
        return

    path.write_text(text, encoding="utf-8")
    print(f"Updated {path}")
    print(f"Backup saved to {backup}")
    print("Next: docker compose up -d --force-recreate backend")


if __name__ == "__main__":
    main()
