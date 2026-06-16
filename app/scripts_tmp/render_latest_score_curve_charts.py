
from __future__ import annotations



import csv

import html

import json

from datetime import datetime

from pathlib import Path

from typing import Any



import matplotlib



matplotlib.use("Agg")



import matplotlib.pyplot as plt

import matplotlib.dates as mdates





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





def find_latest_score_curve_dir() -> Path:

    base = Path("outputs/backtest_debug")



    dirs = sorted(

        base.glob("latest_client_run_*_score_curves_*"),

        key=lambda p: p.stat().st_mtime,

        reverse=True,

    )



    if not dirs:

        raise SystemExit("No score curve export directory found under outputs/backtest_debug.")



    return dirs[0]





def load_long_rows(csv_path: Path) -> list[dict[str, str]]:

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:

        return list(csv.DictReader(f))





def group_by_ticker(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:

    grouped: dict[str, list[dict[str, str]]] = {}



    for row in rows:

        ticker = row.get("ticker", "").strip()



        if not ticker:

            continue



        grouped.setdefault(ticker, []).append(row)



    for ticker in grouped:

        grouped[ticker].sort(key=lambda r: r.get("snapshot_date", ""))



    return grouped





def build_series(

    ticker_rows: list[dict[str, str]],

    score_field: str,

) -> tuple[list[datetime], list[float]]:

    dates: list[datetime] = []

    values: list[float] = []



    for row in ticker_rows:

        date_text = row.get("snapshot_date", "").strip()

        score = parse_float(row.get(score_field))



        if not date_text or score is None:

            continue



        dates.append(parse_date(date_text))

        values.append(score)



    return dates, values





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





def save_single_score_chart(

    *,

    ticker: str,

    ticker_rows: list[dict[str, str]],

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

    ticker_rows: list[dict[str, str]],

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





def summarize_ticker(ticker: str, ticker_rows: list[dict[str, str]]) -> dict[str, Any]:

    stock_scores = [

        parse_float(row.get("stock_score"))

        for row in ticker_rows

        if parse_float(row.get("stock_score")) is not None

    ]



    situation_scores = [

        parse_float(row.get("situation_score"))

        for row in ticker_rows

        if parse_float(row.get("situation_score")) is not None

    ]



    active_days = 0



    for row in ticker_rows:

        is_active = str(row.get("is_active_position", "")).lower()



        if is_active in {"true", "1", "yes"}:

            active_days += 1



    return {

        "ticker": ticker,

        "rows": len(ticker_rows),

        "stock_points": len(stock_scores),

        "situation_points": len(situation_scores),

        "active_days": active_days,

        "stock_min": min(stock_scores) if stock_scores else None,

        "stock_max": max(stock_scores) if stock_scores else None,

        "stock_first": stock_scores[0] if stock_scores else None,

        "stock_last": stock_scores[-1] if stock_scores else None,

        "situation_min": min(situation_scores) if situation_scores else None,

        "situation_max": max(situation_scores) if situation_scores else None,

        "situation_first": situation_scores[0] if situation_scores else None,

        "situation_last": situation_scores[-1] if situation_scores else None,

    }





def write_summary_csv(path: Path, summaries: list[dict[str, Any]]) -> None:

    fields = [

        "ticker",

        "rows",

        "stock_points",

        "situation_points",

        "active_days",

        "stock_min",

        "stock_max",

        "stock_first",

        "stock_last",

        "situation_min",

        "situation_max",

        "situation_first",

        "situation_last",

    ]



    with path.open("w", encoding="utf-8-sig", newline="") as f:

        writer = csv.DictWriter(f, fieldnames=fields)

        writer.writeheader()

        writer.writerows(summaries)





def write_index_html(

    *,

    path: Path,

    score_dir: Path,

    summaries: list[dict[str, Any]],

) -> None:

    lines: list[str] = []



    lines.append("<!doctype html>")

    lines.append("<html>")

    lines.append("<head>")

    lines.append('<meta charset="utf-8">')

    lines.append("<title>Finsight Backtest Score Curves</title>")

    lines.append(

        """

<style>

body { font-family: Arial, sans-serif; margin: 24px; background: #f7f7f7; }

h1 { margin-bottom: 8px; }

.card { background: white; padding: 16px; margin: 20px 0; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }

img { max-width: 100%; border: 1px solid #ddd; border-radius: 8px; background: white; }

.meta { color: #666; font-size: 14px; margin-bottom: 12px; }

.grid { display: grid; grid-template-columns: 1fr; gap: 16px; }

</style>

"""

    )

    lines.append("</head>")

    lines.append("<body>")

    lines.append("<h1>Finsight Backtest Score Curves</h1>")

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

            f"active_days={item['active_days']} | "

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

    score_dir = find_latest_score_curve_dir()

    long_csv = score_dir / "score_curves_long.csv"



    if not long_csv.exists():

        raise SystemExit(f"score_curves_long.csv not found: {long_csv}")



    rows = load_long_rows(long_csv)

    grouped = group_by_ticker(rows)



    if not grouped:

        raise SystemExit("No ticker rows found in score_curves_long.csv.")



    chart_root = score_dir / "charts"

    stock_dir = chart_root / "stock_score"

    situation_dir = chart_root / "situation_score"

    combined_dir = chart_root / "combined"



    summaries: list[dict[str, Any]] = []

    generated_files: list[str] = []



    for ticker, ticker_rows in grouped.items():

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

            generated_files.append(str(stock_path))



        if save_single_score_chart(

            ticker=ticker,

            ticker_rows=ticker_rows,

            score_field="situation_score",

            label="Situation Score",

            output_path=situation_path,

        ):

            generated_files.append(str(situation_path))



        if save_combined_score_chart(

            ticker=ticker,

            ticker_rows=ticker_rows,

            output_path=combined_path,

        ):

            generated_files.append(str(combined_path))



        summaries.append(summarize_ticker(ticker, ticker_rows))



    write_summary_csv(chart_root / "chart_summary.csv", summaries)

    write_index_html(path=score_dir / "index.html", score_dir=score_dir, summaries=summaries)



    print("Chart generation finished.")

    print(f"score_dir={score_dir}")

    print(f"chart_root={chart_root}")

    print(f"index_html={score_dir / 'index.html'}")

    print(f"summary_csv={chart_root / 'chart_summary.csv'}")

    print(f"tickers={len(grouped)}")

    print(f"generated_png_files={len(generated_files)}")



    for file in generated_files[:20]:

        print(file)



    if len(generated_files) > 20:

        print(f"... and {len(generated_files) - 20} more")





if __name__ == "__main__":

    main()

