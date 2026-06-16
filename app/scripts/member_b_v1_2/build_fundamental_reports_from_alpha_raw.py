from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def to_float(x):
    if x is None:
        return np.nan
    s = str(x).strip()
    if s == "" or s.lower() in {"none", "null", "nan"}:
        return np.nan
    try:
        return float(s)
    except Exception:
        return np.nan


def parse_one_ticker(raw_root: Path, ticker: str) -> pd.DataFrame:
    ticker = ticker.upper()
    ticker_dir = raw_root / ticker

    earnings_path = ticker_dir / f"{ticker}_EARNINGS.json"
    income_path = ticker_dir / f"{ticker}_INCOME_STATEMENT.json"

    if not earnings_path.exists():
        print(f"[WARN] missing earnings: {ticker}")
        return pd.DataFrame()

    earnings = json.loads(earnings_path.read_text(encoding="utf-8"))
    e_rows = earnings.get("quarterlyEarnings", []) or []

    e_df = pd.DataFrame(e_rows)
    if e_df.empty:
        print(f"[WARN] empty earnings: {ticker}")
        return pd.DataFrame()

    e_df["ticker"] = ticker
    e_df["fiscal_date"] = pd.to_datetime(e_df["fiscalDateEnding"], errors="coerce")
    e_df["reported_date"] = pd.to_datetime(e_df["reportedDate"], errors="coerce")

    rename_e = {
        "reportedEPS": "reported_eps",
        "estimatedEPS": "estimated_eps",
        "surprise": "eps_surprise",
        "surprisePercentage": "eps_surprise_pct",
        "reportTime": "report_time",
    }
    e_df = e_df.rename(columns=rename_e)

    for col in ["reported_eps", "estimated_eps", "eps_surprise", "eps_surprise_pct"]:
        if col in e_df.columns:
            e_df[col] = e_df[col].map(to_float)

    keep_e = [
        "ticker",
        "fiscal_date",
        "reported_date",
        "reported_eps",
        "estimated_eps",
        "eps_surprise",
        "eps_surprise_pct",
        "report_time",
    ]
    e_df = e_df[[c for c in keep_e if c in e_df.columns]].copy()

    if income_path.exists():
        income = json.loads(income_path.read_text(encoding="utf-8"))
        i_rows = income.get("quarterlyReports", []) or []
        i_df = pd.DataFrame(i_rows)

        if not i_df.empty:
            i_df["fiscal_date"] = pd.to_datetime(i_df["fiscalDateEnding"], errors="coerce")

            rename_i = {
                "totalRevenue": "total_revenue",
                "grossProfit": "gross_profit",
                "operatingIncome": "operating_income",
                "netIncome": "net_income",
                "ebit": "ebit",
                "ebitda": "ebitda",
                "incomeBeforeTax": "income_before_tax",
                "incomeTaxExpense": "income_tax_expense",
                "researchAndDevelopment": "research_and_development",
                "operatingExpenses": "operating_expenses",
                "costOfRevenue": "cost_of_revenue",
            }
            i_df = i_df.rename(columns=rename_i)

            income_cols = [
                "fiscal_date",
                "total_revenue",
                "gross_profit",
                "operating_income",
                "net_income",
                "ebit",
                "ebitda",
                "income_before_tax",
                "income_tax_expense",
                "research_and_development",
                "operating_expenses",
                "cost_of_revenue",
            ]

            i_df = i_df[[c for c in income_cols if c in i_df.columns]].copy()

            for col in i_df.columns:
                if col != "fiscal_date":
                    i_df[col] = i_df[col].map(to_float)

            df = e_df.merge(i_df, on="fiscal_date", how="left")
        else:
            df = e_df.copy()
    else:
        print(f"[WARN] missing income statement: {ticker}")
        df = e_df.copy()

    df = df.dropna(subset=["fiscal_date", "reported_date"]).copy()
    df = df.sort_values(["ticker", "reported_date", "fiscal_date"]).reset_index(drop=True)

    # 财务衍生特征
    if "total_revenue" in df.columns:
        revenue = df["total_revenue"].replace(0, np.nan)
    else:
        revenue = pd.Series(np.nan, index=df.index)

    for numerator, out_col in [
        ("gross_profit", "gross_margin"),
        ("operating_income", "operating_margin"),
        ("net_income", "net_margin"),
    ]:
        if numerator in df.columns:
            df[out_col] = df[numerator] / revenue
        else:
            df[out_col] = np.nan

    # 同比变化：同一 ticker 内按 fiscal_date 排序，和四个季度前比
    df = df.sort_values(["ticker", "fiscal_date"]).reset_index(drop=True)

    for col, out_col in [
        ("total_revenue", "revenue_yoy"),
        ("net_income", "net_income_yoy"),
        ("reported_eps", "eps_yoy"),
    ]:
        if col in df.columns:
            prev = df.groupby("ticker")[col].shift(4)
            denom = prev.abs().replace(0, np.nan)
            df[out_col] = (df[col] - prev) / denom
        else:
            df[out_col] = np.nan

    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--raw-root",
        default="/data/hmt/datasets/finsight/fundamentals/raw/alpha_vantage",
    )
    parser.add_argument("--tickers-file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    raw_root = Path(args.raw_root)
    tickers = [
        x.strip().upper()
        for x in Path(args.tickers_file).read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]

    frames = []
    for ticker in tickers:
        print("===== parse", ticker, "=====")
        df = parse_one_ticker(raw_root, ticker)
        print("rows:", len(df))
        if not df.empty:
            frames.append(df)

    if frames:
        out_df = pd.concat(frames, ignore_index=True)
    else:
        out_df = pd.DataFrame()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")

    print("saved:", out_path)
    print("shape:", out_df.shape)

    if not out_df.empty:
        print("ticker_count:", out_df["ticker"].nunique())
        print("reported_date range:", out_df["reported_date"].min(), "->", out_df["reported_date"].max())
        print(out_df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
