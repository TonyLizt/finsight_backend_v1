from __future__ import annotations

import csv
import json
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models.all_models import BacktestRun, BacktestDailyPosition


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    return value


def score_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def get_signal_field(signal_json: Any, key: str) -> Any:
    if isinstance(signal_json, dict):
        return signal_json.get(key)
    return None


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    db = SessionLocal()

    try:
        # 最近一次「用戶端 / 前端」建立的回測，排除 [PARAM_SCAN] 掃參。
        run = (
            db.query(BacktestRun)
            .filter(
                or_(
                    BacktestRun.run_name.is_(None),
                    ~BacktestRun.run_name.like("[PARAM_SCAN]%"),
                )
            )
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .first()
        )

        if not run:
            raise SystemExit("No client-created backtest run found.")

        run_id = run.id
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("outputs/backtest_debug") / f"latest_client_run_{run_id}_score_curves_{timestamp}"
        out_dir.mkdir(parents=True, exist_ok=True)

        rows = (
            db.query(BacktestDailyPosition)
            .filter(BacktestDailyPosition.run_id == run_id)
            .order_by(
                BacktestDailyPosition.snapshot_date.asc(),
                BacktestDailyPosition.ticker.asc(),
                BacktestDailyPosition.id.asc(),
            )
            .all()
        )

        if not rows:
            raise SystemExit(f"No daily position rows found for run_id={run_id}.")

        tickers = list(run.tickers_json or [])
        if not tickers:
            tickers = sorted({r.ticker for r in rows})

        dates = sorted({plain(r.snapshot_date) for r in rows})

        # date + ticker -> row
        row_map: dict[tuple[str, str], BacktestDailyPosition] = {}

        for r in rows:
            d = plain(r.snapshot_date)
            key = (d, r.ticker)
            row_map[key] = r

        # 1) long table: 每日每股票一行
        long_rows: list[dict[str, Any]] = []

        for d in dates:
            for ticker in tickers:
                r = row_map.get((d, ticker))

                if r is None:
                    long_rows.append(
                        {
                            "snapshot_date": d,
                            "ticker": ticker,
                            "stock_score": "",
                            "situation_score": "",
                            "quantity": "",
                            "current_price": "",
                            "stock_value": "",
                            "position_ratio": "",
                            "holding_status": "",
                            "is_active_position": "",
                            "latest_buy_date": "",
                            "latest_sell_date": "",
                        }
                    )
                    continue

                signal_json = r.signal_json if isinstance(r.signal_json, dict) else {}

                long_rows.append(
                    {
                        "snapshot_date": d,
                        "ticker": ticker,
                        "stock_score": score_value(r.stock_score),
                        "situation_score": score_value(r.situation_score),
                        "quantity": plain(r.quantity),
                        "current_price": plain(r.current_price),
                        "stock_value": plain(r.stock_value),
                        "position_ratio": plain(r.position_ratio),
                        "holding_status": get_signal_field(signal_json, "holding_status"),
                        "is_active_position": get_signal_field(signal_json, "is_active_position"),
                        "latest_buy_date": get_signal_field(signal_json, "latest_buy_date"),
                        "latest_sell_date": get_signal_field(signal_json, "latest_sell_date"),
                    }
                )

        write_csv(
            out_dir / "score_curves_long.csv",
            [
                "snapshot_date",
                "ticker",
                "stock_score",
                "situation_score",
                "quantity",
                "current_price",
                "stock_value",
                "position_ratio",
                "holding_status",
                "is_active_position",
                "latest_buy_date",
                "latest_sell_date",
            ],
            long_rows,
        )

        # 2) stock_score wide table: 日期 x ticker
        stock_wide_rows: list[dict[str, Any]] = []

        for d in dates:
            row: dict[str, Any] = {"snapshot_date": d}

            for ticker in tickers:
                r = row_map.get((d, ticker))
                row[ticker] = score_value(r.stock_score) if r else ""

            stock_wide_rows.append(row)

        write_csv(
            out_dir / "stock_score_curve_by_ticker.csv",
            ["snapshot_date", *tickers],
            stock_wide_rows,
        )

        # 3) situation_score wide table: 日期 x ticker
        situation_wide_rows: list[dict[str, Any]] = []

        for d in dates:
            row = {"snapshot_date": d}

            for ticker in tickers:
                r = row_map.get((d, ticker))
                row[ticker] = score_value(r.situation_score) if r else ""

            situation_wide_rows.append(row)

        write_csv(
            out_dir / "situation_score_curve_by_ticker.csv",
            ["snapshot_date", *tickers],
            situation_wide_rows,
        )

        # 4) JSON: 每 ticker 一組曲線
        curve_json: dict[str, list[dict[str, Any]]] = {}

        for ticker in tickers:
            curve_json[ticker] = []

            for d in dates:
                r = row_map.get((d, ticker))

                if r is None:
                    curve_json[ticker].append(
                        {
                            "date": d,
                            "stock_score": None,
                            "situation_score": None,
                            "quantity": None,
                            "current_price": None,
                            "stock_value": None,
                            "position_ratio": None,
                            "holding_status": None,
                            "is_active_position": None,
                        }
                    )
                    continue

                signal_json = r.signal_json if isinstance(r.signal_json, dict) else {}

                curve_json[ticker].append(
                    {
                        "date": d,
                        "stock_score": r.stock_score,
                        "situation_score": r.situation_score,
                        "quantity": plain(r.quantity),
                        "current_price": plain(r.current_price),
                        "stock_value": plain(r.stock_value),
                        "position_ratio": plain(r.position_ratio),
                        "holding_status": get_signal_field(signal_json, "holding_status"),
                        "is_active_position": get_signal_field(signal_json, "is_active_position"),
                    }
                )

        with (out_dir / "score_curves_by_ticker.json").open("w", encoding="utf-8") as f:
            json.dump(curve_json, f, ensure_ascii=False, indent=2, default=plain)

        # 5) 人可讀 report.log
        report_path = out_dir / "score_curve_report.log"

        with report_path.open("w", encoding="utf-8") as f:
            f.write("============================================================\n")
            f.write("Latest client backtest score curve report\n")
            f.write("============================================================\n")
            f.write(f"generated_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"run_id: {run.id}\n")
            f.write(f"user_id: {run.user_id}\n")
            f.write(f"run_name: {run.run_name}\n")
            f.write(f"status: {run.status}\n")
            f.write(f"date_range: {plain(run.start_date)} to {plain(run.end_date)}\n")
            f.write(f"tickers: {json.dumps(tickers, ensure_ascii=False)}\n")
            f.write(f"daily_position_rows: {len(rows)}\n")
            f.write(f"dates_count: {len(dates)}\n")
            f.write(f"tickers_count: {len(tickers)}\n")
            f.write("\n")

            f.write("============================================================\n")
            f.write("Ticker summary\n")
            f.write("============================================================\n")

            for ticker in tickers:
                ticker_rows = [row_map.get((d, ticker)) for d in dates]
                ticker_rows = [r for r in ticker_rows if r is not None]

                stock_scores = [r.stock_score for r in ticker_rows if r.stock_score is not None]
                situation_scores = [r.situation_score for r in ticker_rows if r.situation_score is not None]

                f.write("\n")
                f.write(f"[{ticker}]\n")
                f.write(f"points={len(ticker_rows)}\n")

                if stock_scores:
                    f.write(
                        "stock_score "
                        f"min={min(stock_scores):.4f} "
                        f"max={max(stock_scores):.4f} "
                        f"first={stock_scores[0]:.4f} "
                        f"last={stock_scores[-1]:.4f}\n"
                    )
                else:
                    f.write("stock_score empty\n")

                if situation_scores:
                    f.write(
                        "situation_score "
                        f"min={min(situation_scores):.4f} "
                        f"max={max(situation_scores):.4f} "
                        f"first={situation_scores[0]:.4f} "
                        f"last={situation_scores[-1]:.4f}\n"
                    )
                else:
                    f.write("situation_score empty\n")

                f.write("first_10_points:\n")

                for r in ticker_rows[:10]:
                    signal_json = r.signal_json if isinstance(r.signal_json, dict) else {}

                    f.write(
                        "  "
                        f"date={plain(r.snapshot_date)} "
                        f"stock_score={r.stock_score} "
                        f"situation_score={r.situation_score} "
                        f"qty={r.quantity} "
                        f"current_price={plain(r.current_price)} "
                        f"holding_status={get_signal_field(signal_json, 'holding_status')} "
                        f"is_active_position={get_signal_field(signal_json, 'is_active_position')}\n"
                    )

        print("Export finished.")
        print(f"run_id={run.id}")
        print(f"run_name={run.run_name}")
        print(f"status={run.status}")
        print(f"out_dir={out_dir}")
        print(f"long_csv={out_dir / 'score_curves_long.csv'}")
        print(f"stock_curve_csv={out_dir / 'stock_score_curve_by_ticker.csv'}")
        print(f"situation_curve_csv={out_dir / 'situation_score_curve_by_ticker.csv'}")
        print(f"json={out_dir / 'score_curves_by_ticker.json'}")
        print(f"report={report_path}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
