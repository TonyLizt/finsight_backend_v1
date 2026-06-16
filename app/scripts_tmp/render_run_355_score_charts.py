from __future__ import annotations

import csv
import html
import json
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from app.db.session import SessionLocal
from app.models.all_models import BacktestRun, BacktestDailyPosition


RUN_ID = 414


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def setup_axis(title: str) -> None:
    ax = plt.gca()
    ax.set_title(title)
    ax.set_xlabel("Date")
    ax.set_ylabel("Score")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()


def build_series(ticker_rows: list[dict[str, Any]], score_field: str) -> tuple[list[datetime], list[float]]:
    dates: list[datetime] = []
    values: list[float] = []
    for row in ticker_rows:
        date_text = str(row["snapshot_date"])
        score = parse_float(row.get(score_field))
        if not date_text or score is None:
            continue
        dates.append(parse_date(date_text))
        values.append(score)
    return dates, values


def save_single_score_chart(
    *,
    ticker: str,
    ticker_rows: list[dict[str, Any]],
    score_field: str,
    label: str,
    output_path: Path,
) -> bool:
    dates, values = build_series(ticker_rows, score_field)
    if not dates:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(12, 5))
    plt.plot(dates, values, linewidth=1.8, marker="o", markersize=2.5, label=label)
    setup_axis(f"{ticker} {label} Curve")
    plt.legend()
    plt.savefig(output_path, dpi=160)
    plt.close()
    return True


def save_combined_score_chart(
    *,
    ticker: str,
    ticker_rows: list[dict[str, Any]],
    output_path: Path,
) -> bool:
    stock_dates, stock_values = build_series(ticker_rows, "stock_score")
    situation_dates, situation_values = build_series(ticker_rows, "situation_score")

    if not stock_dates and not situation_dates:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(12, 5))

    if stock_dates:
        plt.plot(
            stock_dates,
            stock_values,
            linewidth=1.8,
            marker="o",
            markersize=2.5,
            label="Stock Score",
        )

    if situation_dates:
        plt.plot(
            situation_dates,
            situation_values,
            linewidth=1.8,
            marker="o",
            markersize=2.5,
            label="Situation Score",
        )

    setup_axis(f"{ticker} Stock Score / Situation Score Curves")
    plt.legend()
    plt.savefig(output_path, dpi=160)
    plt.close()
    return True


def write_index_html(path: Path, summaries: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("<!doctype html>")
    lines.append("<html>")
    lines.append("<head>")
    lines.append('<meta charset="utf-8">')
    lines.append("<title>Run 355 Score Curves</title>")
    lines.append("""
<style>
body { font-family: Arial, sans-serif; margin: 24px; background: #f7f7f7; }
h1 { margin-bottom: 8px; }
.card { background: white; padding: 16px; margin: 20px 0; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
img { max-width: 100%; border: 1px solid #ddd; border-radius: 8px; background: white; }
.meta { color: #666; font-size: 14px; margin-bottom: 12px; }
.grid { display: grid; grid-template-columns: 1fr; gap: 16px; }
</style>
""")
    lines.append("</head>")
    lines.append("<body>")
    lines.append("<h1>Backtest Run 355 Score Curves</h1>")
    lines.append(f"<p class='meta'>Generated at {html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</p>")

    for item in summaries:
        ticker = item["ticker"]
        combined = f"charts/combined/{ticker}_score_curves.png"
        stock = f"charts/stock_score/{ticker}_stock_score_curve.png"
        situation = f"charts/situation_score/{ticker}_situation_score_curve.png"

        lines.append("<div class='card'>")
        lines.append(f"<h2>{html.escape(ticker)}</h2>")
        lines.append(
            "<p class='meta'>"
            f"rows={item['rows']} | "
            f"stock_points={item['stock_points']} | "
            f"situation_points={item['situation_points']} | "
            f"stock_min={item['stock_min']} | stock_max={item['stock_max']} | "
            f"situation_min={item['situation_min']} | situation_max={item['situation_max']}"
            "</p>"
        )
        lines.append("<div class='grid'>")
        lines.append(f"<h3>Combined</h3><img src='{html.escape(combined)}'>")
        lines.append(f"<h3>Stock Score</h3><img src='{html.escape(stock)}'>")
        lines.append(f"<h3>Situation Score</h3><img src='{html.escape(situation)}'>")
        lines.append("</div>")
        lines.append("</div>")

    lines.append("</body>")
    lines.append("</html>")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    db = SessionLocal()
    try:
        run = db.query(BacktestRun).filter(BacktestRun.id == RUN_ID).first()
        if not run:
            raise SystemExit(f"BacktestRun id={RUN_ID} not found.")

        rows = (
            db.query(BacktestDailyPosition)
            .filter(BacktestDailyPosition.run_id == RUN_ID)
            .order_by(
                BacktestDailyPosition.snapshot_date.asc(),
                BacktestDailyPosition.ticker.asc(),
                BacktestDailyPosition.id.asc(),
            )
            .all()
        )

        if not rows:
            raise SystemExit(f"No BacktestDailyPosition rows found for run_id={RUN_ID}.")

        tickers = list(run.tickers_json or [])
        if not tickers:
            tickers = sorted({r.ticker for r in rows})

        out_dir = Path("outputs/backtest_debug") / f"run_{RUN_ID}_score_curves_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # long rows
        long_rows: list[dict[str, Any]] = []
        grouped: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in tickers}

        for r in rows:
            row = {
                "snapshot_date": plain(r.snapshot_date),
                "ticker": r.ticker,
                "stock_score": plain(r.stock_score),
                "situation_score": plain(r.situation_score),
                "quantity": plain(r.quantity),
                "current_price": plain(r.current_price),
                "stock_value": plain(r.stock_value),
                "position_ratio": plain(r.position_ratio),
            }
            long_rows.append(row)
            grouped.setdefault(r.ticker, []).append(row)

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
            ],
            long_rows,
        )

        chart_root = out_dir / "charts"
        stock_dir = chart_root / "stock_score"
        situation_dir = chart_root / "situation_score"
        combined_dir = chart_root / "combined"

        summaries: list[dict[str, Any]] = []
        generated_png_files: list[str] = []

        for ticker in tickers:
            ticker_rows = grouped.get(ticker, [])
            stock_scores = [parse_float(r["stock_score"]) for r in ticker_rows if parse_float(r["stock_score"]) is not None]
            situation_scores = [parse_float(r["situation_score"]) for r in ticker_rows if parse_float(r["situation_score"]) is not None]

            stock_path = stock_dir / f"{ticker}_stock_score_curve.png"
            situation_path = situation_dir / f"{ticker}_situation_score_curve.png"
            combined_path = combined_dir / f"{ticker}_score_curves.png"

            if save_single_score_chart(
                ticker=ticker,
                ticker_rows=ticker_rows,
                score_field="stock_score",
                label="Stock Score",
                output_path=stock_path,
            ):
                generated_png_files.append(str(stock_path))

            if save_single_score_chart(
                ticker=ticker,
                ticker_rows=ticker_rows,
                score_field="situation_score",
                label="Situation Score",
                output_path=situation_path,
            ):
                generated_png_files.append(str(situation_path))

            if save_combined_score_chart(
                ticker=ticker,
                ticker_rows=ticker_rows,
                output_path=combined_path,
            ):
                generated_png_files.append(str(combined_path))

            summaries.append(
                {
                    "ticker": ticker,
                    "rows": len(ticker_rows),
                    "stock_points": len(stock_scores),
                    "situation_points": len(situation_scores),
                    "stock_min": min(stock_scores) if stock_scores else None,
                    "stock_max": max(stock_scores) if stock_scores else None,
                    "situation_min": min(situation_scores) if situation_scores else None,
                    "situation_max": max(situation_scores) if situation_scores else None,
                }
            )

        write_csv(
            chart_root / "chart_summary.csv",
            [
                "ticker",
                "rows",
                "stock_points",
                "situation_points",
                "stock_min",
                "stock_max",
                "situation_min",
                "situation_max",
            ],
            summaries,
        )

        write_index_html(out_dir / "index.html", summaries)

        print("Chart generation finished.")
        print(f"run_id={RUN_ID}")
        print(f"run_name={run.run_name}")
        print(f"status={run.status}")
        print(f"out_dir={out_dir}")
        print(f"index_html={out_dir / 'index.html'}")
        print(f"summary_csv={chart_root / 'chart_summary.csv'}")
        print(f"generated_png_files={len(generated_png_files)}")

        for file in generated_png_files[:30]:
            print(file)

        if len(generated_png_files) > 30:
            print(f'... and {len(generated_png_files) - 30} more')

    finally:
        db.close()


if __name__ == "__main__":
    main()
