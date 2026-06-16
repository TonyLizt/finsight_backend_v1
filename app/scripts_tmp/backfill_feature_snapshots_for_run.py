from __future__ import annotations

import csv
import os
from collections import Counter
from datetime import datetime, date
from pathlib import Path

from app.db.session import SessionLocal
from app.models.all_models import BacktestRun, PriceData
from app.services.feature_snapshot_service import ensure_latest_feature_snapshot


RUN_ID = int(os.environ.get("RUN_ID", "413"))
START_DATE_TEXT = os.environ.get("START_DATE", "2025-05-14")
END_DATE_TEXT = os.environ.get("END_DATE", "2026-06-12")
FORCE_REFRESH = os.environ.get("FORCE_REFRESH", "1") == "1"


def parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> None:
    start_date = parse_date(START_DATE_TEXT)
    end_date = parse_date(END_DATE_TEXT)

    db = SessionLocal()

    try:
        run = db.query(BacktestRun).filter(BacktestRun.id == RUN_ID).first()

        if not run:
            raise SystemExit(f"BacktestRun id={RUN_ID} not found.")

        tickers = list(run.tickers_json or [])

        if not tickers:
            raise SystemExit(f"Run id={RUN_ID} has no tickers_json.")

        out_dir = Path("outputs/backtest_debug") / f"run_{RUN_ID}_feature_snapshot_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)

        log_path = out_dir / "backfill_result.csv"

        print("============================================================")
        print("Backfill model_feature_snapshots")
        print("============================================================")
        print(f"RUN_ID={RUN_ID}")
        print(f"run_name={run.run_name}")
        print(f"status={run.status}")
        print(f"tickers={tickers}")
        print(f"START_DATE={start_date}")
        print(f"END_DATE={end_date}")
        print(f"FORCE_REFRESH={FORCE_REFRESH}")
        print(f"out_dir={out_dir}")
        print("")

        results: list[dict[str, object]] = []
        counter: Counter[str] = Counter()

        for ticker in tickers:
            trading_days = (
                db.query(PriceData.trading_date)
                .filter(
                    PriceData.ticker == ticker,
                    PriceData.trading_date >= start_date,
                    PriceData.trading_date <= end_date,
                    PriceData.close.isnot(None),
                )
                .order_by(PriceData.trading_date.asc())
                .all()
            )

            print(f"[{ticker}] trading_days={len(trading_days)}")

            for item in trading_days:
                target_date = item[0]

                try:
                    result = ensure_latest_feature_snapshot(
                        db=db,
                        ticker=ticker,
                        target_date=target_date,
                        force_refresh=FORCE_REFRESH,
                        news_window_days=14,
                    )
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "can_continue": False,
                        "ticker": ticker,
                        "base_trading_date": target_date.isoformat(),
                        "reason": repr(exc),
                    }

                status = str(result.get("status"))
                reason = str(result.get("reason") or "")
                base_date = str(result.get("base_trading_date") or target_date)

                counter[status] += 1

                if status not in {"cached", "created_or_updated"}:
                    print(
                        "[WARN]",
                        ticker,
                        target_date,
                        "status=",
                        status,
                        "reason=",
                        reason,
                    )

                results.append(
                    {
                        "ticker": ticker,
                        "target_date": target_date.isoformat(),
                        "status": status,
                        "can_continue": result.get("can_continue"),
                        "base_trading_date": base_date,
                        "reason": reason,
                        "feature_count": result.get("feature_count"),
                        "current_price": result.get("current_price"),
                        "dataset_version": result.get("dataset_version"),
                    }
                )

        with log_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "ticker",
                    "target_date",
                    "status",
                    "can_continue",
                    "base_trading_date",
                    "reason",
                    "feature_count",
                    "current_price",
                    "dataset_version",
                ],
            )
            writer.writeheader()
            writer.writerows(results)

        print("")
        print("============================================================")
        print("Summary")
        print("============================================================")
        for status, count in sorted(counter.items()):
            print(f"{status}: {count}")

        print(f"log_path={log_path}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
