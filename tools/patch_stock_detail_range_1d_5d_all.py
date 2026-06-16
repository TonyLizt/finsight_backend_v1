#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''Patch stock detail API ranges: 1d / 5d / all.

作用：
1. 扩展 GET /api/stocks/{ticker}/detail 的 range 参数；
2. 支持 range=1d、range=5d、range=all；
3. 保留原来的 range=1m/3m/6m/1y；
4. price_curve 与 indicator_curve 使用同一 range；
5. 对 range=all 不再 limit，返回数据库中该股票全部日频行情。
'''

from __future__ import annotations

import re
from pathlib import Path


STOCK_SERVICE_PRICE_CURVE = """def price_curve(db: Session, ticker: str, days: int | None = 90) -> list[PriceData]:
    \"\"\"返回股票日频价格曲线。

    days:
        - int：返回最近 N 个交易日；
        - None：返回该 ticker 在 price_data 表中的全部历史日频数据。

    返回顺序始终为时间升序，便于前端直接画线图。
    \"\"\"
    q = (
        db.query(PriceData)
        .filter(PriceData.ticker == normalize_ticker(ticker))
        .order_by(PriceData.trading_date.desc())
    )
    if days is not None:
        q = q.limit(max(1, int(days)))
    return q.all()[::-1]


"""

STOCK_DETAIL_RANGE_BLOCK = """    requested_range = (range or \"3m\").strip().lower()
    days_map = {
        \"1d\": 1,
        \"5d\": 5,
        \"1m\": 22,
        \"3m\": 66,
        \"6m\": 132,
        \"1y\": 252,
    }
    if requested_range == \"all\":
        days = None
    else:
        days = days_map.get(requested_range, 66)
        if requested_range not in days_map:
            requested_range = \"3m\"
    curve = price_curve(db, ticker, days)
"""

INDICATOR_QUERY_BLOCK = """        indicator_q = (
            db.query(TechnicalIndicator)
            .filter(TechnicalIndicator.ticker == ticker)
            .order_by(TechnicalIndicator.trading_date.desc())
        )
        if days is not None:
            indicator_q = indicator_q.limit(days)
        indicators = indicator_q.all()[::-1]
"""


def backup_once(path: Path, suffix: str, original: str) -> None:
    backup = path.with_suffix(path.suffix + suffix)
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")


def patch_stock_service() -> None:
    path = Path("app/services/stock_service.py")
    text = path.read_text(encoding="utf-8")
    original = text

    if "def price_curve(db: Session, ticker: str, days: int | None = 90)" in text:
        print("No change needed: app/services/stock_service.py already supports all range")
        return

    pattern = r"def price_curve\(db: Session, ticker: str, days: int = 90\) -> list\[PriceData\]:\n.*?\n\ndef calc_52_week_high_low"
    replacement = STOCK_SERVICE_PRICE_CURVE + "def calc_52_week_high_low"
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError("Could not patch price_curve() in app/services/stock_service.py")

    backup_once(path, ".bak_range_1d_5d_all", original)
    path.write_text(text, encoding="utf-8")
    print("Updated app/services/stock_service.py")


def patch_stocks_router() -> None:
    path = Path("app/routers/stocks.py")
    text = path.read_text(encoding="utf-8")
    original = text

    if '"1d": 1' not in text or 'requested_range = (range or "3m").strip().lower()' not in text:
        pattern = (
            r"    days_map = \{[^\n]*\}\n"
            r"    days = days_map\.get\(range, 66\)\n"
            r"    curve = price_curve\(db, ticker, days\)\n"
        )
        text, count = re.subn(pattern, STOCK_DETAIL_RANGE_BLOCK, text, count=1)
        if count != 1:
            pattern = r"    days_map = \{.*?\}\n    days = days_map\.get\(range, 66\)\n    curve = price_curve\(db, ticker, days\)\n"
            text, count = re.subn(pattern, STOCK_DETAIL_RANGE_BLOCK, text, count=1, flags=re.S)
        if count != 1:
            raise RuntimeError("Could not patch stock detail days_map block in app/routers/stocks.py")

    old_indicator_line = (
        "        indicators = db.query(TechnicalIndicator).filter(TechnicalIndicator.ticker == ticker)."
        "order_by(TechnicalIndicator.trading_date.desc()).limit(days).all()[::-1]\n"
    )
    if old_indicator_line in text:
        text = text.replace(old_indicator_line, INDICATOR_QUERY_BLOCK, 1)
    elif "indicator_q = (" not in text:
        pattern = (
            r"        indicators = db\.query\(TechnicalIndicator\).*?"
            r"\.limit\(days\)\.all\(\)\[::-1\]\n"
        )
        text, count = re.subn(pattern, INDICATOR_QUERY_BLOCK, text, count=1, flags=re.S)
        if count != 1:
            raise RuntimeError("Could not patch indicator query in app/routers/stocks.py")

    metadata_line = '            "data_refresh_status": refresh_status,\n'
    metadata_insert = (
        '            "data_refresh_status": refresh_status,\n'
        '            "price_range": requested_range,\n'
        '            "price_curve_count": len(curve),\n'
        '            "price_curve_start_date": curve[0].trading_date.isoformat() if curve else None,\n'
        '            "price_curve_end_date": curve[-1].trading_date.isoformat() if curve else None,\n'
        '            "data_frequency": "daily",\n'
    )
    if '"price_range": requested_range' not in text and metadata_line in text:
        text = text.replace(metadata_line, metadata_insert, 1)

    if text == original:
        print("No change needed: app/routers/stocks.py")
        return

    backup_once(path, ".bak_range_1d_5d_all", original)
    path.write_text(text, encoding="utf-8")
    print("Updated app/routers/stocks.py")


def main() -> None:
    patch_stock_service()
    patch_stocks_router()
    print("Patch finished. Next run:")
    print("  python -m py_compile app/services/stock_service.py app/routers/stocks.py")
    print("  docker compose restart backend")


if __name__ == "__main__":
    main()
