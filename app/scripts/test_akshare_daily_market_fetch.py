"""单独测试 AKShare 日频行情补全。

用法：
    PYTHONPATH=/app python -m app.scripts.test_akshare_daily_market_fetch AAPL 2026-06-05

说明：
    该脚本会调用 app.services.market_data_service.ensure_price_data，
    因此会把 AKShare 抓到的日频行情 upsert 到 MySQL price_data。
"""

from __future__ import annotations

import json
import sys
from datetime import date

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.market_data_service import ensure_price_data, fetch_akshare_daily_prices


def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) >= 2 else "AAPL"
    target_date = date.fromisoformat(sys.argv[2]) if len(sys.argv) >= 3 else date.today()

    init_db()

    preview_start = date(target_date.year, target_date.month, 1)
    preview_records = fetch_akshare_daily_prices(
        ticker,
        start_date=preview_start,
        end_date=target_date,
    )

    db = SessionLocal()
    try:
        result = ensure_price_data(
            db,
            ticker=ticker,
            target_date=target_date,
            force_refresh=True,
        )
    finally:
        db.close()

    print(json.dumps(
        {
            "ticker": ticker.upper(),
            "target_date": target_date.isoformat(),
            "akshare_preview_count": len(preview_records),
            "akshare_preview_latest_date": (
                max(r["trading_date"] for r in preview_records).isoformat()
                if preview_records else None
            ),
            "ensure_price_data_result": result,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    main()
