#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch backend market fetch to B-style Yahoo Chart source.

目的：
1. 根据 B 同学的 download_backtest_market_yahoo_chart.py，把后端 market 行情源改成 Yahoo Chart；
2. 行情链路不再调用 Alpha Vantage，避免 premium endpoint / free rate limit；
3. 保留 local_raw_csv 作为可选 fallback；
4. 新闻抓取不受影响，Alpha Vantage News Sentiment 仍可继续用于 news_data。

用法：
    python tools/patch_yahoo_chart_market_source.py
    docker compose up -d --force-recreate backend
"""

from __future__ import annotations

from pathlib import Path


YAHOO_FUNC = 'def fetch_yahoo_chart_prices(\n    ticker: str,\n    start_date: date,\n    end_date: date,\n    timeout_seconds: int | None = None,\n) -> list[dict[str, Any]]:\n    """从 Yahoo Chart 拉取日频行情。\n\n    本实现按 B 同学的 download_backtest_market_yahoo_chart.py 对齐：\n    - URL: https://query1.finance.yahoo.com/v8/finance/chart/{ticker}\n    - interval=1d\n    - events=history\n    - includeAdjustedClose=true\n    - headers 使用 B 脚本中的简洁 User-Agent / Accept\n    - 输出字段等价于 CSV: Date, Open, High, Low, Close, Adj Close, Volume\n\n    注意：Yahoo 的 period2 是右开边界，所以这里使用 end_date + 1 day。\n    """\n    timeout_seconds = timeout_seconds or _env_int("MARKET_DATA_TIMEOUT_SECONDS", 60)\n\n    period1 = int(\n        datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc).timestamp()\n    )\n    period2 = int(\n        datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()\n    )\n\n    url = (\n        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"\n        f"?period1={period1}&period2={period2}"\n        f"&interval=1d&events=history&includeAdjustedClose=true"\n    )\n\n    headers = {\n        "User-Agent": os.getenv("MARKET_DATA_USER_AGENT", "Mozilla/5.0"),\n        "Accept": "application/json,text/plain,*/*",\n    }\n\n    response = requests.get(url, headers=headers, timeout=timeout_seconds)\n    response.raise_for_status()\n\n    payload = response.json()\n    chart = payload.get("chart") or {}\n    if chart.get("error"):\n        raise RuntimeError(f"Yahoo chart error: {chart[\'error\']}")\n\n    results = chart.get("result") or []\n    if not results:\n        raise RuntimeError(f"No chart result for {ticker}: {payload}")\n\n    result = results[0]\n    timestamps = result.get("timestamp") or []\n    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]\n    adjclose_block = ((result.get("indicators") or {}).get("adjclose") or [{}])[0]\n\n    open_list = quote.get("open") or []\n    high_list = quote.get("high") or []\n    low_list = quote.get("low") or []\n    close_list = quote.get("close") or []\n    volume_list = quote.get("volume") or []\n    adjclose_list = adjclose_block.get("adjclose") or []\n\n    records: list[dict[str, Any]] = []\n\n    for idx, ts in enumerate(timestamps):\n        close = close_list[idx] if idx < len(close_list) else None\n        if close is None:\n            continue\n\n        trading_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()\n        if trading_date < start_date or trading_date > end_date:\n            continue\n\n        raw = {\n            "trading_date": trading_date,\n            "open": open_list[idx] if idx < len(open_list) else None,\n            "high": high_list[idx] if idx < len(high_list) else None,\n            "low": low_list[idx] if idx < len(low_list) else None,\n            "close": close,\n            "adj_close": adjclose_list[idx] if idx < len(adjclose_list) else close,\n            "volume": volume_list[idx] if idx < len(volume_list) else None,\n        }\n\n        record = _normalize_price_record(ticker, raw, source="yahoo_chart")\n        if record is not None:\n            records.append(record)\n\n    if not records:\n        raise RuntimeError(\n            f"Yahoo chart returned no valid OHLCV rows in requested range "\n            f"{start_date}~{end_date}"\n        )\n\n    return _add_previous_close(records)\n'
ONLINE_FUNC = 'def fetch_online_price_records(\n    ticker: str,\n    start_date: date,\n    end_date: date,\n) -> tuple[list[dict[str, Any]], str, list[str]]:\n    """按配置抓取行情：Yahoo Chart 优先，不再使用 Alpha Vantage 行情接口。\n\n    v1.3 Yahoo-only market 策略：\n    1. 默认只尝试 Yahoo Chart；\n    2. 可选 local_raw_csv fallback；\n    3. 即使 MARKET_DATA_SOURCE_PRIORITY 中仍写了 alpha_vantage，也会自动忽略；\n    4. 新闻抓取不受影响，Alpha Vantage News Sentiment 仍可继续用于 news_data。\n    """\n    raw_priority = os.getenv("MARKET_DATA_SOURCE_PRIORITY", "yahoo_chart,local_raw_csv")\n    source_priority = [\n        item.strip().lower()\n        for item in raw_priority.split(",")\n        if item.strip()\n    ]\n\n    # 行情链路不再调用 Alpha Vantage，避免 premium endpoint / free rate limit。\n    source_priority = [\n        item\n        for item in source_priority\n        if item not in {"alpha_vantage", "alphavantage", "av"}\n    ]\n\n    if not source_priority:\n        source_priority = ["yahoo_chart", "local_raw_csv"]\n\n    errors: list[str] = []\n\n    for source in source_priority:\n        try:\n            if source in {"yahoo", "yahoo_chart"}:\n                if not _env_bool("ENABLE_YAHOO_CHART_FALLBACK", True):\n                    errors.append("yahoo_chart disabled by ENABLE_YAHOO_CHART_FALLBACK=0")\n                    continue\n\n                records = fetch_yahoo_chart_prices(ticker, start_date, end_date)\n                if records:\n                    return records, "yahoo_chart", errors\n                errors.append("yahoo_chart returned no records")\n\n            elif source in {"local", "local_raw_csv"}:\n                if not _env_bool("ENABLE_LOCAL_RAW_CSV_FALLBACK", False):\n                    errors.append("local_raw_csv disabled by ENABLE_LOCAL_RAW_CSV_FALLBACK=0")\n                    continue\n\n                records, path = fetch_local_raw_csv_prices(ticker, start_date, end_date)\n                if records:\n                    return records, f"local_raw_csv:{path}", errors\n                errors.append("local_raw_csv returned no records")\n\n            else:\n                errors.append(f"unknown or disabled market data source: {source}")\n\n        except Exception as exc:\n            errors.append(f"{source} failed: {exc}")\n\n    return [], "", errors\n'


def replace_function(text: str, function_name: str, replacement: str) -> str:
    marker = f"def {function_name}("
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"function not found: {function_name}")

    # 找下一个顶格 def，作为函数结束位置。
    next_def = text.find("
def ", start + len(marker))
    if next_def < 0:
        next_def = len(text)
    else:
        next_def += 1

    return text[:start] + replacement.rstrip() + "


" + text[next_def:]


def main() -> None:
    path = Path("app/services/market_data_service.py")
    if not path.exists():
        raise FileNotFoundError(path)

    original = path.read_text(encoding="utf-8")
    text = original

    backup = path.with_suffix(".py.bak_yahoo_chart_market_source")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    text = replace_function(text, "fetch_yahoo_chart_prices", YAHOO_FUNC)
    text = replace_function(text, "fetch_online_price_records", ONLINE_FUNC)

    text = text.replace(
        "默认优先 Alpha Vantage，再 Yahoo，不依赖 B 同学本地 CSV；",
        "默认使用与 B 同学脚本一致的 Yahoo Chart，不再调用 Alpha Vantage 行情接口；",
    )
    text = text.replace(
        '"MARKET_DATA_SOURCE_PRIORITY",
            "alpha_vantage,yahoo_chart",',
        '"MARKET_DATA_SOURCE_PRIORITY",
            "yahoo_chart,local_raw_csv",',
    )
    text = text.replace(
        "优先 Alpha Vantage；",
        "优先 Yahoo Chart；",
    )

    if text == original:
        print("No changes made.")
        return

    path.write_text(text, encoding="utf-8")
    print(f"Updated {path}")
    print(f"Backup saved to {backup}")
    print("Next: docker compose up -d --force-recreate backend")


if __name__ == "__main__":
    main()
