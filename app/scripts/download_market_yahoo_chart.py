"""Yahoo Chart 行情下载 / 入库脚本。

这个脚本和后端 market_data_service 使用同一套 Yahoo Chart 抓取函数，方便单独运行。

示例 1：只下载 CSV，不写库
    PYTHONPATH=/app python -m app.scripts.download_market_yahoo_chart \
      --tickers AAPL,MSFT \
      --start-date 2025-05-21 \
      --end-date 2026-06-05 \
      --out-dir /app/local_experiments/yahoo_market_raw

示例 2：下载并写入 MySQL price_data
    PYTHONPATH=/app python -m app.scripts.download_market_yahoo_chart \
      --tickers AAPL \
      --start-date 2025-05-21 \
      --end-date 2026-06-05 \
      --write-db
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.market_data_service import fetch_yahoo_chart_prices, upsert_price_records

CORE_TICKERS = [
    "AAPL", "AMAT", "AMD", "AMZN", "AVGO",
    "BA", "BAC", "CAT", "COP", "COST",
    "CRM", "CVX", "DIS", "GE", "GOOGL",
    "GS", "HD", "HON", "IBM", "INTC",
    "JPM", "KO", "LMT", "LOW", "MA",
    "MCD", "META", "MSFT", "MU", "NFLX",
    "NKE", "NOW", "NVDA", "ORCL", "PG",
    "PYPL", "QCOM", "QQQ", "SBUX", "SPY",
    "T", "TGT", "TSLA", "UBER", "UNH",
    "V", "VZ", "WFC", "WMT", "XOM",
]


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_tickers(value: str | None, use_core: bool) -> list[str]:
    if use_core or not value:
        return CORE_TICKERS
    return [x.strip().upper() for x in value.split(",") if x.strip()]


def json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rec in records:
            writer.writerow(
                {
                    "Date": rec["trading_date"].isoformat(),
                    "Open": rec.get("open"),
                    "High": rec.get("high"),
                    "Low": rec.get("low"),
                    "Close": rec.get("close"),
                    "Adj Close": rec.get("adj_close"),
                    "Volume": rec.get("volume"),
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Yahoo Chart daily OHLCV market data.")
    parser.add_argument("--tickers", default=None, help="逗号分隔股票列表，例如 AAPL,MSFT。默认使用 50 只核心股票。")
    parser.add_argument("--core", action="store_true", help="使用 B 同学 no_weak10 50 只核心股票池。")
    parser.add_argument("--start-date", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期 YYYY-MM-DD，脚本内部会按 Yahoo 右开边界处理")
    parser.add_argument("--out-dir", default="yahoo_market_raw", help="CSV 输出目录")
    parser.add_argument("--write-db", action="store_true", help="是否写入 MySQL price_data")
    parser.add_argument("--no-csv", action="store_true", help="不输出 CSV，只测试或只写库")
    parser.add_argument("--sleep-seconds", type=float, default=2.0, help="每只股票之间的间隔，避免被限流")
    args = parser.parse_args()

    tickers = parse_tickers(args.tickers, args.core)
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    out_dir = Path(args.out_dir)

    if args.write_db:
        init_db()
        db = SessionLocal()
    else:
        db = None

    ok: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []

    try:
        for index, ticker in enumerate(tickers, start=1):
            print("=" * 80)
            print(f"[{index}/{len(tickers)}] downloading {ticker} {start_date}~{end_date}")

            try:
                records = fetch_yahoo_chart_prices(ticker, start_date, end_date)

                inserted = updated = 0
                if db is not None:
                    inserted, updated = upsert_price_records(db, records)

                if not args.no_csv:
                    csv_path = out_dir / f"{ticker}_{start_date:%Y%m%d}_{end_date:%Y%m%d}.csv"
                    write_csv(csv_path, records)
                else:
                    csv_path = None

                item = {
                    "ticker": ticker,
                    "status": "success",
                    "rows": len(records),
                    "first_date": records[0]["trading_date"].isoformat() if records else None,
                    "latest_date": records[-1]["trading_date"].isoformat() if records else None,
                    "inserted": inserted,
                    "updated": updated,
                    "csv_path": str(csv_path) if csv_path else None,
                }
                ok.append(item)
                print(json.dumps(item, ensure_ascii=False, indent=2, default=json_safe))

            except Exception as exc:
                item = {"ticker": ticker, "status": "failed", "error": repr(exc)}
                bad.append(item)
                print(json.dumps(item, ensure_ascii=False, indent=2))

            if index < len(tickers) and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    finally:
        if db is not None:
            db.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_download_ok.json").write_text(json.dumps(ok, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "_download_bad.json").write_text(json.dumps(bad, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 80)
    print(json.dumps({"ok_count": len(ok), "bad_count": len(bad)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
