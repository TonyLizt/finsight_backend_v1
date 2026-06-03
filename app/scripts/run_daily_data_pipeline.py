"""手动运行 v1.3 每日数据链路任务。

示例：
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_daily_data_pipeline --tickers AAPL,MSFT --target-date 2026-05-29"
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.db.session import SessionLocal
from app.services.daily_refresh_service import run_daily_data_refresh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Finsight v1.3 daily data pipeline")
    parser.add_argument("--tickers", default="", help="逗号分隔股票列表，例如 AAPL,MSFT")
    parser.add_argument("--modules", default="", help="逗号分隔模块，例如 market,technical,news,sentiment,features")
    parser.add_argument("--target-date", default=None, help="目标日期，例如 2026-05-29")
    parser.add_argument("--force-refresh", action="store_true", help="是否强制刷新")
    parser.add_argument("--limit", type=int, default=None, help="最大股票数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()] or None
    modules = [x.strip() for x in args.modules.split(",") if x.strip()] or None
    target_date = date.fromisoformat(args.target_date) if args.target_date else None

    db = SessionLocal()
    try:
        result = run_daily_data_refresh(
            db=db,
            tickers=tickers,
            modules=modules,
            target_date=target_date,
            force_refresh=args.force_refresh,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
