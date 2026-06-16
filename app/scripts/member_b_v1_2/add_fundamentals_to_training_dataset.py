from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


FUND_FEATURES = [
    "fundamental_available",
    "fund_report_age_days",
    "fund_days_since_fiscal_end",
    "fund_reported_eps",
    "fund_estimated_eps",
    "fund_eps_surprise",
    "fund_eps_surprise_pct",
    "fund_total_revenue",
    "fund_gross_profit",
    "fund_operating_income",
    "fund_net_income",
    "fund_ebit",
    "fund_ebitda",
    "fund_gross_margin",
    "fund_operating_margin",
    "fund_net_margin",
    "fund_revenue_yoy",
    "fund_net_income_yoy",
    "fund_eps_yoy",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src-dir", required=True, help="Source training_dataset directory")
    parser.add_argument("--financial-reports", required=True, help="financial_reports_all.csv")
    parser.add_argument("--dst-dir", required=True, help="Output training_dataset directory")
    return parser.parse_args()


def safe_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)


def build_availability_date(df: pd.DataFrame) -> pd.Series:
    reported = pd.to_datetime(df["reported_date"], errors="coerce")
    report_time = df.get("report_time", pd.Series("", index=df.index)).astype(str).str.lower()

    # 保守处理：post-market 财报第二天才可用
    available = reported.copy()
    post_mask = report_time.str.contains("post", na=False)
    available.loc[post_mask] = available.loc[post_mask] + pd.Timedelta(days=1)

    return available


def add_fundamentals(base_df: pd.DataFrame, reports: pd.DataFrame) -> pd.DataFrame:
    base = base_df.copy()
    base["ticker"] = base["ticker"].astype(str).str.upper()
    base["base_trading_date_dt"] = pd.to_datetime(base["base_trading_date"], errors="coerce")

    rep = reports.copy()
    rep["ticker"] = rep["ticker"].astype(str).str.upper()
    rep["fiscal_date_dt"] = pd.to_datetime(rep["fiscal_date"], errors="coerce")
    rep["reported_date_dt"] = pd.to_datetime(rep["reported_date"], errors="coerce")
    rep["fund_available_date_dt"] = build_availability_date(rep)

    # 只保留有发布日期和可用日期的财报
    rep = rep.dropna(subset=["ticker", "fiscal_date_dt", "reported_date_dt", "fund_available_date_dt"]).copy()

    numeric_map = {
        "reported_eps": "fund_reported_eps",
        "estimated_eps": "fund_estimated_eps",
        "eps_surprise": "fund_eps_surprise",
        "eps_surprise_pct": "fund_eps_surprise_pct",
        "total_revenue": "fund_total_revenue",
        "gross_profit": "fund_gross_profit",
        "operating_income": "fund_operating_income",
        "net_income": "fund_net_income",
        "ebit": "fund_ebit",
        "ebitda": "fund_ebitda",
        "gross_margin": "fund_gross_margin",
        "operating_margin": "fund_operating_margin",
        "net_margin": "fund_net_margin",
        "revenue_yoy": "fund_revenue_yoy",
        "net_income_yoy": "fund_net_income_yoy",
        "eps_yoy": "fund_eps_yoy",
    }

    for src, dst in numeric_map.items():
        if src in rep.columns:
            rep[dst] = safe_num(rep[src])
        else:
            rep[dst] = np.nan

    rep = rep.sort_values(["ticker", "fund_available_date_dt", "fiscal_date_dt"]).copy()

    out_frames = []

    for ticker, b in base.groupby("ticker", sort=False):
        b = b.sort_values("base_trading_date_dt").copy()
        r = rep[rep["ticker"] == ticker].sort_values("fund_available_date_dt").copy()

        if r.empty:
            # 没有财报的 ticker，全部填空
            for col in FUND_FEATURES:
                b[col] = 0.0
            b["fund_fiscal_date"] = ""
            b["fund_reported_date"] = ""
            b["fund_available_date"] = ""
            out_frames.append(b)
            continue

        keep_cols = [
            "ticker",
            "fiscal_date_dt",
            "reported_date_dt",
            "fund_available_date_dt",
        ] + list(numeric_map.values())

        r = r[keep_cols].copy()

        merged = pd.merge_asof(
            b,
            r,
            left_on="base_trading_date_dt",
            right_on="fund_available_date_dt",
            by="ticker",
            direction="backward",
            allow_exact_matches=True,
        )

        # 财报是否可用
        merged["fundamental_available"] = merged["fund_available_date_dt"].notna().astype(float)

        # 日期派生特征
        merged["fund_report_age_days"] = (
            merged["base_trading_date_dt"] - merged["fund_available_date_dt"]
        ).dt.days

        merged["fund_days_since_fiscal_end"] = (
            merged["base_trading_date_dt"] - merged["fiscal_date_dt"]
        ).dt.days

        # 元数据日期列，便于检查，不放进 feature_columns
        merged["fund_fiscal_date"] = merged["fiscal_date_dt"].dt.date.astype(str)
        merged["fund_reported_date"] = merged["reported_date_dt"].dt.date.astype(str)
        merged["fund_available_date"] = merged["fund_available_date_dt"].dt.date.astype(str)

        out_frames.append(merged)

    out = pd.concat(out_frames, ignore_index=True)

    # 清理所有财报特征
    for col in FUND_FEATURES:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan)

    # 没有财报时，所有财报特征置 0
    no_fund = out["fundamental_available"] <= 0
    for col in FUND_FEATURES:
        if col != "fundamental_available":
            out.loc[no_fund, col] = 0.0

    # 有财报但部分字段缺失，也用 0 兜底。模型侧用 fundamental_available 和 age 识别是否有财报。
    for col in FUND_FEATURES:
        out[col] = out[col].fillna(0.0)

    # 删除中间 datetime 列
    drop_cols = [
        "base_trading_date_dt",
        "fiscal_date_dt",
        "reported_date_dt",
        "fund_available_date_dt",
    ]
    out = out.drop(columns=[c for c in drop_cols if c in out.columns])

    return out


def main() -> None:
    args = parse_args()

    src = Path(args.src_dir)
    dst = Path(args.dst_dir)
    dst.mkdir(parents=True, exist_ok=True)

    dataset_path = src / "dataset_h5_v1.csv"
    feature_path = src / "feature_columns_h5_v1.json"
    label_path = src / "label_config_h5_v1.json"
    summary_path = src / "dataset_summary_h5_v1.json"

    reports_path = Path(args.financial_reports)

    if not dataset_path.exists():
        raise FileNotFoundError(dataset_path)
    if not feature_path.exists():
        raise FileNotFoundError(feature_path)
    if not label_path.exists():
        raise FileNotFoundError(label_path)
    if not reports_path.exists():
        raise FileNotFoundError(reports_path)

    base_df = pd.read_csv(dataset_path)
    reports = pd.read_csv(reports_path)

    out_df = add_fundamentals(base_df, reports)

    out_dataset = dst / "dataset_h5_v1.csv"
    out_df.to_csv(out_dataset, index=False, encoding="utf-8-sig")

    features = json.loads(feature_path.read_text(encoding="utf-8"))

    for f in FUND_FEATURES:
        if f not in features:
            features.append(f)

    (dst / "feature_columns_h5_v1.json").write_text(
        json.dumps(features, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    shutil.copy(label_path, dst / "label_config_h5_v1.json")

    if summary_path.exists():
        shutil.copy(summary_path, dst / "dataset_summary_source_h5_v1.json")

    coverage = (
        out_df.groupby("ticker")
        .agg(
            rows=("ticker", "size"),
            fundamental_rows=("fundamental_available", "sum"),
            avg_report_age_days=("fund_report_age_days", "mean"),
            min_reported_date=("fund_reported_date", "min"),
            max_reported_date=("fund_reported_date", "max"),
        )
        .reset_index()
    )
    coverage.to_csv(dst / "fundamental_coverage_by_ticker.csv", index=False, encoding="utf-8-sig")

    summary = {
        "source_training_dataset": str(dataset_path),
        "financial_reports": str(reports_path),
        "output_dataset": str(out_dataset),
        "method": "latest financial report as base, joined by reported_date/available_date <= base_trading_date",
        "leakage_control": "post-market reports use reported_date + 1 day as available date; merge_asof backward only",
        "shape": list(out_df.shape),
        "ticker_count": int(out_df["ticker"].nunique()),
        "date_range": [
            str(out_df["base_trading_date"].min()),
            str(out_df["base_trading_date"].max()),
        ],
        "feature_count": len(features),
        "fundamental_feature_count": len(FUND_FEATURES),
        "fundamental_features": FUND_FEATURES,
        "fundamental_available_rate": float(out_df["fundamental_available"].mean()),
    }

    (dst / "dataset_summary_h5_v1.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("saved dataset:", out_dataset)
    print("shape:", out_df.shape)
    print("ticker_count:", out_df["ticker"].nunique())
    print("date_range:", out_df["base_trading_date"].min(), "->", out_df["base_trading_date"].max())
    print("feature_count:", len(features))
    print("fundamental_features:", len(FUND_FEATURES))
    print("fundamental_available_rate:", out_df["fundamental_available"].mean())

    print("\n===== fundamental coverage by ticker =====")
    print(coverage.to_string(index=False))

    print("\n===== sample rows =====")
    cols = [
        "ticker",
        "base_trading_date",
        "fund_fiscal_date",
        "fund_reported_date",
        "fund_available_date",
        "fundamental_available",
        "fund_report_age_days",
        "fund_reported_eps",
        "fund_eps_surprise_pct",
        "fund_total_revenue",
        "fund_revenue_yoy",
        "news_count",
        "sentiment_score",
    ]
    cols = [c for c in cols if c in out_df.columns]
    print(out_df[cols].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
