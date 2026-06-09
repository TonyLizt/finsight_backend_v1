"""手动现场补齐某只股票数据。

用于“前端/预测访问某只股票时发现数据库没数据”，管理员可以立刻在后端容器中
调用这个脚本，尝试抓取并入库。

示例：
PYTHONPATH=/app python -m app.scripts.ensure_ticker_data_on_demand --ticker NFLX
PYTHONPATH=/app python -m app.scripts.ensure_ticker_data_on_demand --ticker NFLX --modules market,intraday,technical,features
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.db.session import SessionLocal
from app.services.daily_refresh_service import run_daily_data_refresh
from app.services.twelvedata_market_service import ensure_extra_tables


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure one ticker data on demand")
    parser.add_argument("--ticker", required=True, help="股票代码，例如 NFLX")
    parser.add_argument(
        "--modules",
        default="market,intraday,technical,news,news_fulltext,sentiment,features",
        help="逗号分隔模块；默认全链路",
    )
    parser.add_argument("--target-date", default=None, help="目标日期 YYYY-MM-DD；默认今天")
    parser.add_argument("--force-refresh", action="store_true", help="强制刷新")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_extra_tables()

    modules = [x.strip().lower() for x in args.modules.split(",") if x.strip()]
    target_date = date.fromisoformat(args.target_date) if args.target_date else None

    db = SessionLocal()
    try:
        result = run_daily_data_refresh(
            db=db,
            tickers=[args.ticker.upper()],
            modules=modules,
            target_date=target_date,
            force_refresh=args.force_refresh,
            limit=1,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
