"""Repair model_feature_snapshots.features_json price-derived fields from price_data.

Usage:
    PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data
    PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data --latest-only
    PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data --tickers AAPL,MSFT --dry-run
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal

DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        raw = str(value).strip()
        if not raw or raw.lower() in {"none", "null", "nan"}:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_features(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return {}
    elif isinstance(value, dict):
        raw = value
    else:
        return {}
    return raw if isinstance(raw, dict) else {}


def _get_previous_close(db, ticker: str, trading_date) -> float | None:
    row = db.execute(
        text(
            """
            SELECT close
            FROM price_data
            WHERE ticker = :ticker
              AND trading_date < :trading_date
              AND close IS NOT NULL
            ORDER BY trading_date DESC
            LIMIT 1
            """
        ),
        {"ticker": ticker, "trading_date": trading_date},
    ).mappings().first()
    return _to_float(row["close"]) if row else None


def _build_price_values(db, row: dict[str, Any]) -> dict[str, float]:
    ticker = row["ticker"]
    base_date = row["base_trading_date"]

    open_price = _to_float(row.get("open"))
    high = _to_float(row.get("high"))
    low = _to_float(row.get("low"))
    close = _to_float(row.get("close"))
    volume = _to_float(row.get("volume"))

    previous_close = _to_float(row.get("previous_close"))
    if previous_close is None or previous_close <= 0:
        previous_close = _get_previous_close(db, ticker, base_date)

    change_amount = _to_float(row.get("change_amount"))
    if change_amount is None and close is not None and previous_close not in (None, 0):
        change_amount = close - previous_close

    daily_return = _to_float(row.get("daily_return"))
    if daily_return is None and change_amount is not None and previous_close not in (None, 0):
        daily_return = change_amount / previous_close

    change_percent = _to_float(row.get("change_percent"))
    if change_percent is None and daily_return is not None:
        change_percent = daily_return

    amplitude = _to_float(row.get("amplitude"))
    if amplitude is None and high is not None and low is not None and previous_close not in (None, 0):
        amplitude = (high - low) / previous_close

    values = {
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "previous_close": previous_close,
        "change_amount": change_amount,
        "daily_return": daily_return,
        "change_percent": change_percent,
        "amplitude": amplitude,
    }
    return {k: float(v) for k, v in values.items() if v is not None}


def _load_snapshot_rows(db, ticker: str, latest_only: bool):
    if latest_only:
        sql = """
            SELECT
                s.id,
                s.ticker,
                s.base_trading_date,
                s.current_price,
                s.features_json,
                p.open,
                p.high,
                p.low,
                p.close,
                p.volume,
                p.previous_close,
                p.change_amount,
                p.change_percent,
                p.daily_return,
                p.amplitude
            FROM model_feature_snapshots s
            JOIN price_data p
              ON p.ticker = s.ticker
             AND p.trading_date = s.base_trading_date
            WHERE s.ticker = :ticker
            ORDER BY s.base_trading_date DESC, s.id DESC
            LIMIT 1
        """
    else:
        sql = """
            SELECT
                s.id,
                s.ticker,
                s.base_trading_date,
                s.current_price,
                s.features_json,
                p.open,
                p.high,
                p.low,
                p.close,
                p.volume,
                p.previous_close,
                p.change_amount,
                p.change_percent,
                p.daily_return,
                p.amplitude
            FROM model_feature_snapshots s
            JOIN price_data p
              ON p.ticker = s.ticker
             AND p.trading_date = s.base_trading_date
            WHERE s.ticker = :ticker
            ORDER BY s.base_trading_date ASC, s.id ASC
        """
    return db.execute(text(sql), {"ticker": ticker}).mappings().all()


def repair(tickers: list[str], latest_only: bool, dry_run: bool) -> int:
    db = SessionLocal()
    total_updated = 0
    total_scanned = 0

    try:
        for ticker in tickers:
            rows = _load_snapshot_rows(db, ticker, latest_only)
            updated = 0
            scanned = 0
            for row in rows:
                scanned += 1
                features = _parse_features(row["features_json"])
                price_values = _build_price_values(db, dict(row))

                changed = False
                for key, value in price_values.items():
                    old = _to_float(features.get(key))
                    if old != value:
                        features[key] = value
                        changed = True

                close = price_values.get("close")
                current_price = _to_float(row.get("current_price"))
                if close is not None and current_price != close:
                    changed = True

                if changed:
                    updated += 1
                    if not dry_run:
                        db.execute(
                            text(
                                """
                                UPDATE model_feature_snapshots
                                SET features_json = :features_json,
                                    current_price = :current_price
                                WHERE id = :id
                                """
                            ),
                            {
                                "id": row["id"],
                                "features_json": json.dumps(features, ensure_ascii=False),
                                "current_price": close,
                            },
                        )

            total_updated += updated
            total_scanned += scanned
            print(f"{ticker}: scanned={scanned}, updated={updated}")

        if dry_run:
            db.rollback()
            print("dry_run=true, no database changes committed")
        else:
            db.commit()
            print("committed=true")
    finally:
        db.close()

    print(f"total_scanned={total_scanned}, total_updated={total_updated}")
    return total_updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS), help="Comma-separated tickers")
    parser.add_argument("--latest-only", action="store_true", help="Repair latest snapshot only for each ticker")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without committing changes")
    args = parser.parse_args()

    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    repair(tickers=tickers, latest_only=args.latest_only, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
