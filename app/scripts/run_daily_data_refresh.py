#!/usr/bin/env python3
"""命令行每日数据补全脚本。

用途：
- 可在服务器 crontab 中每天执行一次；
- 也可手动执行，和 /api/crawler/daily-refresh/run 的逻辑一致。

示例：
PYTHONPATH=/app python -m app.scripts.run_daily_data_refresh --tickers AAPL MSFT --force-refresh

如果不传 tickers，脚本会按 daily_refresh_service.select_daily_refresh_tickers 的规则自动选择。
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from app.db.session import SessionLocal
from app.services.daily_refresh_service import run_daily_data_refresh


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Finsight daily data refresh once.")
    parser.add_argument("--tickers", nargs="*", default=None, help="Optional tickers, e.g. AAPL MSFT NVDA.")
    parser.add_argument("--target-date", default=None, help="Target date in YYYY-MM-DD. Default: today.")
    parser.add_argument("--force-refresh", action="store_true", help="Force fetching recent market data.")
    parser.add_argument("--limit", type=int, default=None, help="Max ticker count when tickers are not specified.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = run_daily_data_refresh(
            db=db,
            tickers=args.tickers,
            target_date=parse_date(args.target_date),
            force_refresh=args.force_refresh,
            limit=args.limit,
        )
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
