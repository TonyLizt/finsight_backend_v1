#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch news sentiment count fields to use a unified 14-day window."""

from __future__ import annotations

import re
from pathlib import Path


HELPER_CODE = '\ndef sentiment_counts_for_last_two_weeks(\n    db: Session,\n    ticker: str,\n    end_time: datetime | None = None,\n    window_days: int = 14,\n) -> dict:\n    """按最近 14 个自然日统计原始新闻正/负/中性数量。\n\n    统计口径：\n    - 直接基于 news_data.publish_time；\n    - 不基于 sentiment_daily，避免滚动聚合窗口造成重复累计；\n    - 默认截止日为该 ticker 最新新闻 publish_time 所在日期；\n    - 如果传入 end_time，则以 end_time 所在日期为截止日；\n    - 窗口为最近 window_days 个自然日，包含截止日。例如截止日 2026-06-08，\n      window_days=14 时，起始日期为 2026-05-26。\n    """\n    ticker = normalize_ticker(ticker)\n    window_days = max(1, int(window_days or 14))\n\n    if end_time is not None:\n        end_date = end_time.date()\n    else:\n        latest_publish_time = (\n            db.query(func.max(NewsData.publish_time))\n            .filter(NewsData.ticker == ticker)\n            .scalar()\n        )\n        end_date = latest_publish_time.date() if latest_publish_time else datetime.now().date()\n\n    start_date = end_date - timedelta(days=window_days - 1)\n    start_dt = datetime.combine(start_date, time.min)\n    end_dt = datetime.combine(end_date, time.max)\n\n    base_q = db.query(NewsData).filter(\n        NewsData.ticker == ticker,\n        NewsData.publish_time.isnot(None),\n        NewsData.publish_time >= start_dt,\n        NewsData.publish_time <= end_dt,\n    )\n\n    total = base_q.count()\n    positive = base_q.filter(NewsData.sentiment_label == "positive").count()\n    negative = base_q.filter(NewsData.sentiment_label == "negative").count()\n    neutral = max(total - positive - negative, 0)\n\n    return {\n        "window_days": window_days,\n        "start_date": start_date.isoformat(),\n        "end_date": end_date.isoformat(),\n        "news_start_time": start_dt.isoformat(),\n        "news_end_time": end_dt.isoformat(),\n        "count_source": "news_data",\n        "positive_news_count": positive,\n        "negative_news_count": negative,\n        "neutral_news_count": neutral,\n        "total_news_count": total,\n    }\n\n'


def backup_once(path: Path, suffix: str, original: str) -> None:
    backup = path.with_suffix(path.suffix + suffix)
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")


def patch_stock_service() -> None:
    path = Path("app/services/stock_service.py")
    text = path.read_text(encoding="utf-8")
    original = text

    text = text.replace(
        "from datetime import date, datetime, timedelta",
        "from datetime import date, datetime, time, timedelta",
    )

    text = text.replace("window_days: int = 7,", "window_days: int = 14,")

    if '"sentiment_start_date"' not in text:
        text = text.replace(
            '"news_start_time": rows[0].news_start_time.isoformat() if rows[0].news_start_time else None,\n'
            '        "news_end_time": rows[-1].news_end_time.isoformat() if rows[-1].news_end_time else None,',
            '"news_start_time": rows[0].news_start_time.isoformat() if rows[0].news_start_time else None,\n'
            '        "news_end_time": rows[-1].news_end_time.isoformat() if rows[-1].news_end_time else None,\n'
            '        "sentiment_window_days": max(1, window_days),\n'
            '        "sentiment_start_date": rows[0].trading_date.isoformat(),\n'
            '        "sentiment_end_date": rows[-1].trading_date.isoformat(),',
        )

    if '"sentiment_start_date": None' not in text:
        text = text.replace(
            '"news_start_time": None,\n'
            '            "news_end_time": None,',
            '"news_start_time": None,\n'
            '            "news_end_time": None,\n'
            '            "sentiment_window_days": max(1, window_days),\n'
            '            "sentiment_start_date": None,\n'
            '            "sentiment_end_date": None,',
            1,
        )

    if "def sentiment_counts_for_last_two_weeks(" not in text:
        marker = "\ndef news_query("
        idx = text.find(marker)
        if idx < 0:
            raise RuntimeError("Could not find insertion point before news_query() in stock_service.py")
        text = text[:idx] + HELPER_CODE + text[idx:]

    backup_once(path, ".bak_two_week_sentiment_counts", original)
    path.write_text(text, encoding="utf-8")
    print("Updated app/services/stock_service.py")


def add_import_symbol(text: str, symbol: str) -> str:
    if symbol in text:
        return text
    marker = "    stock_data_status,\n"
    if marker in text:
        return text.replace(marker, marker + f"    {symbol},\n", 1)

    marker = "    stock_data_status\n"
    if marker in text:
        return text.replace(marker, f"    stock_data_status,\n    {symbol},\n", 1)

    raise RuntimeError(f"Could not add import symbol {symbol} to stocks.py")


def replace_in_function(text: str, func_name: str, transform) -> str:
    pattern = rf"def {func_name}\(.*?(?=\n@router\.get|\n@router\.post|\n@router\.put|\n@router\.delete|\Z)"
    m = re.search(pattern, text, flags=re.S)
    if not m:
        raise RuntimeError(f"Could not locate function {func_name}()")
    old = m.group(0)
    new = transform(old)
    return text[:m.start()] + new + text[m.end():]


def patch_stock_detail(func: str) -> str:
    if '"sentiment_counts"' in func:
        return func

    old = '"sentiment_summary": latest_sentiment_summary(db, ticker),'
    if old in func:
        return func.replace(
            old,
            '"sentiment_counts": sentiment_counts_for_last_two_weeks(db, ticker),\n'
            '            "sentiment_summary": latest_sentiment_summary(db, ticker, window_days=14),',
            1,
        )

    old = '"sentiment_summary": latest_sentiment_summary(db, ticker, window_days=14),'
    if old in func:
        return func.replace(
            old,
            '"sentiment_counts": sentiment_counts_for_last_two_weeks(db, ticker),\n'
            '            "sentiment_summary": latest_sentiment_summary(db, ticker, window_days=14),',
            1,
        )

    raise RuntimeError("Could not insert sentiment_counts into stock_detail()")


def patch_stock_news(func: str) -> str:
    if '"sentiment_counts"' in func:
        return func

    marker = '"items": ['
    idx = func.find(marker)
    if idx >= 0:
        return func[:idx] + (
            '"sentiment_counts": sentiment_counts_for_last_two_weeks(db, ticker, end_time=end_time),\n'
            '            '
        ) + func[idx:]

    marker = '"total":'
    idx = func.find(marker)
    if idx >= 0:
        return func[:idx] + (
            '"sentiment_counts": sentiment_counts_for_last_two_weeks(db, ticker, end_time=end_time),\n'
            '            '
        ) + func[idx:]

    raise RuntimeError("Could not insert sentiment_counts into stock_news()")


def patch_sentiment_summary(func: str) -> str:
    func = func.replace("window_days: int = 7,", "window_days: int = 14,")

    if '"sentiment_counts"' in func:
        return func

    old = 'return ok({"ticker": ticker, **latest_sentiment_summary(db, ticker, end_date=end_date, window_days=window_days)})'
    if old in func:
        return func.replace(
            old,
            'summary = latest_sentiment_summary(db, ticker, end_date=end_date, window_days=window_days)\n'
            '    counts = sentiment_counts_for_last_two_weeks(db, ticker, end_time=end_time)\n'
            '    return ok({"ticker": ticker, **summary, "sentiment_counts": counts})',
            1,
        )

    pattern = r"return ok\(\{\s*\"ticker\": ticker,\s*\*\*latest_sentiment_summary\(db, ticker, end_date=end_date, window_days=window_days\)\s*\}\)"
    repl = (
        'summary = latest_sentiment_summary(db, ticker, end_date=end_date, window_days=window_days)\n'
        '    counts = sentiment_counts_for_last_two_weeks(db, ticker, end_time=end_time)\n'
        '    return ok({"ticker": ticker, **summary, "sentiment_counts": counts})'
    )
    new, count = re.subn(pattern, repl, func, count=1, flags=re.S)
    if count:
        return new

    raise RuntimeError("Could not patch sentiment_summary() return block")


def patch_stocks_router() -> None:
    path = Path("app/routers/stocks.py")
    text = path.read_text(encoding="utf-8")
    original = text

    text = add_import_symbol(text, "sentiment_counts_for_last_two_weeks")

    text = replace_in_function(text, "stock_detail", patch_stock_detail)
    text = replace_in_function(text, "stock_news", patch_stock_news)
    text = replace_in_function(text, "sentiment_summary", patch_sentiment_summary)

    backup_once(path, ".bak_two_week_sentiment_counts", original)
    path.write_text(text, encoding="utf-8")
    print("Updated app/routers/stocks.py")


def main() -> None:
    patch_stock_service()
    patch_stocks_router()
    print("Patch finished.")
    print("Next:")
    print("  python -m py_compile app/services/stock_service.py app/routers/stocks.py")
    print("  docker compose restart backend")


if __name__ == "__main__":
    main()
