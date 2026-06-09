"""v1.5 Twelve Data 增量自抓取脚本。

默认刷新 7 只核心股票：AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META。
每次运行会自动检测数据库最新日期/时间戳，只抓增量数据并 upsert。

示例：
PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh
PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --tickers GOOGL,NVDA --modules market,intraday,technical,features
PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --force-refresh
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.db.session import SessionLocal
from app.services.daily_refresh_service import run_daily_data_refresh
from app.services.twelvedata_market_service import core_tickers, ensure_extra_tables


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Finsight v1.5 Twelve Data incremental refresh")
    parser.add_argument("--tickers", default="", help="逗号分隔股票列表；不填则使用 7 只核心股票")
    parser.add_argument(
        "--modules",
        default="market,intraday,technical,news,news_fulltext,sentiment,features",
        help="逗号分隔模块",
    )
    parser.add_argument("--target-date", default=None, help="目标日期 YYYY-MM-DD；默认今天")
    parser.add_argument("--force-refresh", action="store_true", help="强制刷新，仍然会 upsert，不会重复插入")
    parser.add_argument("--limit", type=int, default=None, help="最大股票数")
    return parser.parse_args()


def _split_csv(value: str) -> list[str]:
    return [x.strip().upper() for x in value.split(",") if x.strip()]


def main() -> None:
    args = parse_args()
    ensure_extra_tables()

    tickers = _split_csv(args.tickers) if args.tickers.strip() else core_tickers()
    modules = [x.strip().lower() for x in args.modules.split(",") if x.strip()] if args.modules.strip() else None
    target_date = date.fromisoformat(args.target_date) if args.target_date else None

    db = SessionLocal()
    try:
        result = run_daily_data_refresh(
            db=db,
            tickers=tickers,
            modules=modules,
            target_date=target_date,
            force_refresh=args.force_refresh,
            limit=args.limit or len(tickers),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
