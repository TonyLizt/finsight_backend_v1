from pathlib import Path
import json
import sqlite3
import pandas as pd

DB_PATH = Path("/data/hmt/projects/finsight/finsight_backend_v1_git/local_experiments/outputs/backtest_after_20250520/finsight_price_backtest_after_20250520.db")
CSV_DIR = Path("/data/hmt/datasets/finsight/market_data/backtest_market_raw_20250521_20260531")
OUT_DIR = Path("/data/hmt/projects/finsight/finsight_backend_v1_git/local_experiments/outputs/backtest_after_20250520")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TICKERS = [
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

def query_df(conn, sql):
    return pd.read_sql_query(sql, conn)

def main():
    assert DB_PATH.exists(), f"DB not found: {DB_PATH}"
    assert CSV_DIR.exists(), f"CSV dir not found: {CSV_DIR}"

    stock_csv_files = sorted([p for p in CSV_DIR.glob("*.csv") if not p.name.startswith("_download_")])

    conn = sqlite3.connect(DB_PATH)
    try:
        price = query_df(conn, """
            SELECT ticker, COUNT(*) AS rows,
                   MIN(trading_date) AS min_date,
                   MAX(trading_date) AS max_date
            FROM price_data
            WHERE trading_date > '2025-05-20'
            GROUP BY ticker
            ORDER BY ticker
        """)

        tech = query_df(conn, """
            SELECT ticker, COUNT(*) AS rows,
                   MIN(trading_date) AS min_date,
                   MAX(trading_date) AS max_date
            FROM technical_indicators
            WHERE trading_date > '2025-05-20'
            GROUP BY ticker
            ORDER BY ticker
        """)

        total_price = query_df(conn, "SELECT COUNT(*) AS n FROM price_data")
        total_tech = query_df(conn, "SELECT COUNT(*) AS n FROM technical_indicators")
    finally:
        conn.close()

    expected = set(TICKERS)
    price_set = set(price["ticker"])
    tech_set = set(tech["ticker"])

    missing_price = sorted(expected - price_set)
    missing_tech = sorted(expected - tech_set)

    price.to_csv(OUT_DIR / "backtest_price_after_20250520_summary.csv", index=False, encoding="utf-8-sig")
    tech.to_csv(OUT_DIR / "backtest_technical_after_20250520_summary.csv", index=False, encoding="utf-8-sig")

    report = {
        "db_path": str(DB_PATH),
        "csv_dir": str(CSV_DIR),
        "stock_csv_count": len(stock_csv_files),
        "expected_ticker_count": len(TICKERS),
        "price_after_cutoff_ticker_count": int(len(price)),
        "technical_after_cutoff_ticker_count": int(len(tech)),
        "missing_price_tickers": missing_price,
        "missing_technical_tickers": missing_tech,
        "price_rows_total": int(total_price.iloc[0]["n"]),
        "technical_rows_total": int(total_tech.iloc[0]["n"]),
        "price_after_cutoff_rows_min": int(price["rows"].min()) if len(price) else None,
        "price_after_cutoff_rows_max": int(price["rows"].max()) if len(price) else None,
        "technical_after_cutoff_rows_min": int(tech["rows"].min()) if len(tech) else None,
        "technical_after_cutoff_rows_max": int(tech["rows"].max()) if len(tech) else None,
        "price_after_cutoff_date_min": str(price["min_date"].min()) if len(price) else None,
        "price_after_cutoff_date_max": str(price["max_date"].max()) if len(price) else None,
        "technical_after_cutoff_date_min": str(tech["min_date"].min()) if len(tech) else None,
        "technical_after_cutoff_date_max": str(tech["max_date"].max()) if len(tech) else None,
    }

    (OUT_DIR / "backtest_market_delivery_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = []
    md.append("# 回测期行情数据交付检查报告\n")
    md.append("## 1. 数据范围\n")
    md.append("- 回测期开始日期：2025-05-21\n")
    md.append("- 当前行情覆盖结束日期：2026-05-29\n")
    md.append("- 股票池：50 个 ticker，包含 SPY/QQQ 市场基准\n")
    md.append("- 训练数据未使用 2025-05-20 之后数据；本目录数据仅供回测/样本外测试使用。\n")

    md.append("## 2. 文件与数据库\n")
    md.append(f"- Raw CSV 目录：`{CSV_DIR}`\n")
    md.append(f"- 回测数据库：`{DB_PATH}`\n")
    md.append(f"- 股票 CSV 数量：{len(stock_csv_files)}\n")

    md.append("## 3. price_data 检查\n")
    md.append(f"- ticker 数：{len(price)} / {len(TICKERS)}\n")
    md.append(f"- 每个 ticker 回测期行数范围：{report['price_after_cutoff_rows_min']} ~ {report['price_after_cutoff_rows_max']}\n")
    md.append(f"- 日期范围：{report['price_after_cutoff_date_min']} -> {report['price_after_cutoff_date_max']}\n")
    md.append(f"- 缺失 ticker：{missing_price}\n")

    md.append("## 4. technical_indicators 检查\n")
    md.append(f"- ticker 数：{len(tech)} / {len(TICKERS)}\n")
    md.append(f"- 每个 ticker 回测期行数范围：{report['technical_after_cutoff_rows_min']} ~ {report['technical_after_cutoff_rows_max']}\n")
    md.append(f"- 日期范围：{report['technical_after_cutoff_date_min']} -> {report['technical_after_cutoff_date_max']}\n")
    md.append(f"- 缺失 ticker：{missing_tech}\n")

    md.append("## 5. 结论\n")
    ok = (
        len(stock_csv_files) == 50
        and len(price) == 50
        and len(tech) == 50
        and not missing_price
        and not missing_tech
    )
    if ok:
        md.append("回测期行情 raw CSV、price_data 与 technical_indicators 均已准备完成，可交给回测模块继续使用。\n")
    else:
        md.append("仍存在缺失项，需要根据上方缺失 ticker 修复。\n")

    (OUT_DIR / "BACKTEST_MARKET_DELIVERY_CHECK.md").write_text(
        "\n".join(md),
        encoding="utf-8",
    )

    print("saved:")
    print(OUT_DIR / "backtest_price_after_20250520_summary.csv")
    print(OUT_DIR / "backtest_technical_after_20250520_summary.csv")
    print(OUT_DIR / "backtest_market_delivery_check.json")
    print(OUT_DIR / "BACKTEST_MARKET_DELIVERY_CHECK.md")

    print("\n===== REPORT =====")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
