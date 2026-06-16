"""删除 intraday_price_data 中不完整的盘中分钟数据。

用于清理 v1.5 初版错误写入的当天盘中残缺数据，例如 2026-06-09 只到 10:35。
默认删除指定 tickers 中行数少于阈值的日期；完整交易日通常约 390 条 1min 记录。

用法：
PYTHONPATH=/app python -m app.scripts.delete_incomplete_intraday_rows --date 2026-06-09
PYTHONPATH=/app python -m app.scripts.delete_incomplete_intraday_rows --date 2026-06-09 --min-rows 350
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from sqlalchemy import inspect, text

from app.db.session import SessionLocal


DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def _split_tickers(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_TICKERS
    items = [x.strip().upper() for x in raw.split(",") if x.strip()]
    return items or DEFAULT_TICKERS


def _table_exists(db, table_name: str) -> bool:
    try:
        return table_name in inspect(db.bind).get_table_names()
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete incomplete intraday rows")
    parser.add_argument("--date", required=True, help="要检查/删除的交易日 YYYY-MM-DD")
    parser.add_argument("--tickers", default="", help="逗号分隔 ticker；不填默认 7 只核心股票")
    parser.add_argument("--min-rows", type=int, default=350, help="少于该行数视为不完整，默认 350")
    parser.add_argument("--dry-run", action="store_true", help="只输出将删除的内容，不实际删除")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)
    tickers = _split_tickers(args.tickers)

    db = SessionLocal()
    try:
        if not _table_exists(db, "intraday_price_data"):
            print(json.dumps({"success": False, "error": "intraday_price_data table not found"}, ensure_ascii=False, indent=2))
            return

        items = []
        total_deleted = 0
        for ticker in tickers:
            stat = db.execute(
                text(
                    """
                    SELECT COUNT(*) AS rows_count,
                           MIN(market_timestamp) AS min_ts,
                           MAX(market_timestamp) AS max_ts
                    FROM intraday_price_data
                    WHERE ticker = :ticker AND trading_date = :trading_date
                    """
                ),
                {"ticker": ticker, "trading_date": target_date},
            ).mappings().first()

            rows_count = int(stat["rows_count"] or 0) if stat else 0
            should_delete = 0 < rows_count < args.min_rows
            deleted = 0

            if should_delete and not args.dry_run:
                result = db.execute(
                    text(
                        """
                        DELETE FROM intraday_price_data
                        WHERE ticker = :ticker AND trading_date = :trading_date
                        """
                    ),
                    {"ticker": ticker, "trading_date": target_date},
                )
                deleted = int(result.rowcount or 0)
                total_deleted += deleted

            items.append(
                {
                    "ticker": ticker,
                    "trading_date": target_date.isoformat(),
                    "rows_count": rows_count,
                    "min_ts": stat["min_ts"] if stat else None,
                    "max_ts": stat["max_ts"] if stat else None,
                    "should_delete": should_delete,
                    "deleted": deleted,
                }
            )

        if not args.dry_run:
            db.commit()

        print(
            json.dumps(
                {
                    "success": True,
                    "dry_run": args.dry_run,
                    "target_date": target_date.isoformat(),
                    "min_rows": args.min_rows,
                    "total_deleted": total_deleted,
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
