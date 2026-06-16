"""修复 price_data 中的派生行情字段。

用于 v1.5 Twelve Data 初版把 previous_close / change_amount /
change_percent / daily_return / amplitude 覆盖成 NULL 后的一次性修复，
也可以后续重复运行，安全地按 ticker + trading_date 顺序重算。

用法：
PYTHONPATH=/app python -m app.scripts.repair_price_derived_fields
PYTHONPATH=/app python -m app.scripts.repair_price_derived_fields --tickers AAPL,GOOGL,META
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import inspect, text

from app.db.session import SessionLocal


DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def _split_tickers(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_TICKERS
    items = [x.strip().upper() for x in raw.split(",") if x.strip()]
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result or DEFAULT_TICKERS


def _table_columns(db, table_name: str) -> set[str]:
    return {c["name"] for c in inspect(db.bind).get_columns(table_name)}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def repair_ticker(db, ticker: str, dry_run: bool = False) -> dict[str, Any]:
    cols = _table_columns(db, "price_data")
    required = {"id", "ticker", "trading_date", "high", "low", "close"}
    missing = required - cols
    if missing:
        return {
            "ticker": ticker,
            "status": "failed",
            "error": f"price_data missing columns: {sorted(missing)}",
        }

    rows = db.execute(
        text(
            """
            SELECT id, trading_date, high, low, close
            FROM price_data
            WHERE ticker = :ticker
            ORDER BY trading_date ASC
            """
        ),
        {"ticker": ticker.upper()},
    ).mappings().all()

    if not rows:
        return {"ticker": ticker, "status": "empty", "rows": 0, "updated": 0}

    writable = [
        c for c in (
            "previous_close",
            "change_amount",
            "change_percent",
            "daily_return",
            "amplitude",
        )
        if c in cols
    ]
    if not writable:
        return {"ticker": ticker, "status": "skipped", "rows": len(rows), "updated": 0, "message": "no derived columns"}

    previous_close: float | None = None
    updated = 0
    null_first = 0

    for row in rows:
        close = _to_float(row["close"])
        high = _to_float(row["high"])
        low = _to_float(row["low"])

        payload: dict[str, Any] = {"id": row["id"]}

        if previous_close is None or previous_close <= 0 or close is None:
            # 每个 ticker 第一条有效记录没有 previous_close，这是正常的。
            payload.update(
                {
                    "previous_close": None,
                    "change_amount": None,
                    "change_percent": None,
                    "daily_return": None,
                    "amplitude": None,
                }
            )
            null_first += 1
        else:
            change_amount = close - previous_close
            daily_return = change_amount / previous_close
            payload.update(
                {
                    "previous_close": previous_close,
                    "change_amount": change_amount,
                    # DB 保存比例值；API 展示层 display_change_percent 会转为百分数。
                    "change_percent": daily_return,
                    "daily_return": daily_return,
                    "amplitude": ((high - low) / previous_close) if high is not None and low is not None else None,
                }
            )

        if not dry_run:
            update_cols = [c for c in writable if c in payload]
            db.execute(
                text(
                    f"""
                    UPDATE price_data
                    SET {", ".join(f"`{c}` = :{c}" for c in update_cols)}
                    WHERE id = :id
                    """
                ),
                {k: payload.get(k) for k in ["id", *update_cols]},
            )
        updated += 1

        if close is not None and close > 0:
            previous_close = close

    if not dry_run:
        db.commit()

    return {
        "ticker": ticker.upper(),
        "status": "updated" if not dry_run else "dry_run",
        "rows": len(rows),
        "updated": updated,
        "first_rows_without_previous_close": null_first,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair price_data derived fields")
    parser.add_argument("--tickers", default="", help="逗号分隔 ticker；不填默认 7 只核心股票")
    parser.add_argument("--dry-run", action="store_true", help="只计算不写库")
    args = parser.parse_args()

    tickers = _split_tickers(args.tickers)
    db = SessionLocal()
    try:
        results = [repair_ticker(db, ticker, dry_run=args.dry_run) for ticker in tickers]
        print(json.dumps({"success": True, "tickers": tickers, "items": results}, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
