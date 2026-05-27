"""从本地 CSV 导入日频行情到 price_data。

CSV 字段要求：
Date,Open,High,Low,Close,Adj Close,Volume
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from decimal import Decimal, InvalidOperation

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.all_models import PriceData


def to_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    value = str(value).strip()
    if value == "" or value.lower() in {"nan", "none", "null"}:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def to_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = str(value).strip()
    if value == "" or value.lower() in {"nan", "none", "null"}:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def import_one_csv(csv_path: Path, ticker: str) -> dict:
    db = SessionLocal()
    inserted = 0
    updated = 0
    skipped = 0

    try:
        rows = []

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                date_str = row.get("Date")
                close = to_decimal(row.get("Close"))

                if not date_str or close is None:
                    skipped += 1
                    continue

                rows.append({
                    "trading_date": datetime.strptime(date_str, "%Y-%m-%d").date(),
                    "open": to_decimal(row.get("Open")),
                    "high": to_decimal(row.get("High")),
                    "low": to_decimal(row.get("Low")),
                    "close": close,
                    "adj_close": to_decimal(row.get("Adj Close")) or close,
                    "volume": to_int(row.get("Volume")),
                })

        rows.sort(key=lambda x: x["trading_date"])

        prev_close = None

        for r in rows:
            close = r["close"]
            high = r["high"]
            low = r["low"]

            if prev_close is not None and prev_close != 0:
                change_amount = close - prev_close
                daily_return = float(change_amount / prev_close)
                change_percent = daily_return
                amplitude = float((high - low) / prev_close) if high is not None and low is not None else None
            else:
                change_amount = None
                daily_return = None
                change_percent = None
                amplitude = None

            existing = (
                db.query(PriceData)
                .filter(
                    PriceData.ticker == ticker,
                    PriceData.trading_date == r["trading_date"],
                )
                .first()
            )

            values = {
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": close,
                "adj_close": r["adj_close"],
                "previous_close": prev_close,
                "change_amount": change_amount,
                "change_percent": change_percent,
                "daily_return": daily_return,
                "amplitude": amplitude,
                "volume": r["volume"],
            }

            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
                updated += 1
            else:
                db.add(
                    PriceData(
                        ticker=ticker,
                        trading_date=r["trading_date"],
                        **values,
                    )
                )
                inserted += 1

            prev_close = close

        db.commit()

        return {
            "ticker": ticker,
            "csv": str(csv_path),
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "valid_rows": len(rows),
        }

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-dir", required=True)
    parser.add_argument("--tickers", nargs="+", required=True)
    args = parser.parse_args()

    init_db()

    csv_dir = Path(args.csv_dir)

    for ticker in args.tickers:
        ticker = ticker.upper()
        matches = sorted(csv_dir.glob(f"{ticker}_*.csv"))

        if not matches:
            print({"ticker": ticker, "status": "missing_csv"})
            continue

        result = import_one_csv(matches[0], ticker)
        print(result)

    print({"done": True})


if __name__ == "__main__":
    main()
