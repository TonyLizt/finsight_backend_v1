from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


COUNT_COLS = [
    "news_count",
    "positive_news_count",
    "negative_news_count",
    "neutral_news_count",
]

SENTIMENT_COLS = [
    "sentiment_score",
    "sentiment_score_3d_avg",
    "sentiment_score_7d_avg",
]

RATIO_COLS = [
    "positive_ratio",
    "negative_ratio",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src-dir", required=True, help="Source training_dataset directory")
    parser.add_argument("--dst-dir", required=True, help="Output robust training_dataset directory")
    parser.add_argument("--count-cap-quantile", type=float, default=0.95)
    parser.add_argument("--z-clip", type=float, default=3.0)
    return parser.parse_args()


def safe_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)


def main() -> None:
    args = parse_args()

    src = Path(args.src_dir)
    dst = Path(args.dst_dir)
    dst.mkdir(parents=True, exist_ok=True)

    dataset_path = src / "dataset_h5_v1.csv"
    feature_path = src / "feature_columns_h5_v1.json"
    label_path = src / "label_config_h5_v1.json"
    summary_path = src / "dataset_summary_h5_v1.json"

    if not dataset_path.exists():
        raise FileNotFoundError(dataset_path)
    if not feature_path.exists():
        raise FileNotFoundError(feature_path)
    if not label_path.exists():
        raise FileNotFoundError(label_path)

    df = pd.read_csv(dataset_path)
    df["ticker"] = df["ticker"].astype(str).str.upper()
    original = df.copy()

    if "news_count" in df.columns:
        raw_news_count = safe_numeric(df["news_count"]).clip(lower=0.0)
    else:
        raw_news_count = pd.Series(0.0, index=df.index)

    # 1. has_news: 区分“无新闻”和“新闻情绪为 0”
    df["has_news"] = (raw_news_count > 0).astype(float)

    # 2. 新闻数量类特征：95% 分位截尾 + log1p
    cap_info = {}
    for col in COUNT_COLS:
        if col not in df.columns:
            continue

        x = safe_numeric(df[col]).clip(lower=0.0)
        positive = x[x > 0]

        if len(positive) > 0:
            cap = float(positive.quantile(args.count_cap_quantile))
            cap = max(cap, 1.0)
        else:
            cap = 1.0

        cap_info[col] = cap
        df[col] = np.log1p(x.clip(upper=cap))

    # 3. 情绪类特征：按 ticker 做 z-score，裁剪到 [-z_clip, z_clip]
    z_info = {}

    for col in SENTIMENT_COLS:
        if col not in df.columns:
            continue

        raw_col = safe_numeric(original[col])
        robust_col = pd.Series(0.0, index=df.index, dtype=float)
        ticker_stats = {}

        for ticker, group_idx in df.groupby("ticker").groups.items():
            idx = pd.Index(group_idx)
            valid_idx = idx[raw_news_count.loc[idx].values > 0]

            if len(valid_idx) < 5:
                robust_col.loc[idx] = 0.0
                ticker_stats[ticker] = {
                    "mean": 0.0,
                    "std": 0.0,
                    "valid_rows": int(len(valid_idx)),
                }
                continue

            vals = raw_col.loc[valid_idx]
            mean = float(vals.mean())
            std = float(vals.std(ddof=0))

            if std < 1e-8:
                robust_col.loc[idx] = 0.0
                ticker_stats[ticker] = {
                    "mean": mean,
                    "std": std,
                    "valid_rows": int(len(valid_idx)),
                }
                continue

            z = (raw_col.loc[idx] - mean) / std
            z = z.clip(lower=-args.z_clip, upper=args.z_clip)

            # 无新闻样本保持 0，避免把缺失当负面情绪
            z.loc[raw_news_count.loc[idx] <= 0] = 0.0

            robust_col.loc[idx] = z
            ticker_stats[ticker] = {
                "mean": mean,
                "std": std,
                "valid_rows": int(len(valid_idx)),
            }

        df[col] = robust_col
        z_info[col] = ticker_stats

    # 4. 比例特征裁剪到 [0, 1]
    for col in RATIO_COLS:
        if col in df.columns:
            df[col] = safe_numeric(df[col]).clip(lower=0.0, upper=1.0)

    # 5. 兜底清理
    for col in COUNT_COLS + SENTIMENT_COLS + RATIO_COLS + ["has_news"]:
        if col in df.columns:
            df[col] = safe_numeric(df[col])

    # 6. 写出 dataset
    out_dataset = dst / "dataset_h5_v1.csv"
    df.to_csv(out_dataset, index=False, encoding="utf-8-sig")

    # 7. 更新 feature_columns，加入 has_news
    features = json.loads(feature_path.read_text(encoding="utf-8"))
    if "has_news" not in features:
        if "news_count" in features:
            pos = features.index("news_count") + 1
            features.insert(pos, "has_news")
        else:
            features.append("has_news")

    (dst / "feature_columns_h5_v1.json").write_text(
        json.dumps(features, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    shutil.copy(label_path, dst / "label_config_h5_v1.json")

    # 原 summary 可选复制，另写 robust summary
    if summary_path.exists():
        shutil.copy(summary_path, dst / "dataset_summary_source_h5_v1.json")

    robust_summary = {
        "source_dataset": str(dataset_path),
        "output_dataset": str(out_dataset),
        "method": "news robustification",
        "count_cap_quantile": args.count_cap_quantile,
        "z_clip": args.z_clip,
        "steps": [
            "count features clipped at positive quantile and transformed by log1p",
            "sentiment features ticker-wise z-scored and clipped",
            "rows without news keep sentiment z-score as 0",
            "positive_ratio and negative_ratio clipped to [0, 1]",
            "has_news feature added",
        ],
        "shape": list(df.shape),
        "ticker_count": int(df["ticker"].nunique()),
        "date_range": [
            str(df["base_trading_date"].min()),
            str(df["base_trading_date"].max()),
        ],
        "cap_info": cap_info,
        "feature_count": len(features),
    }

    (dst / "dataset_summary_h5_v1.json").write_text(
        json.dumps(robust_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("saved dataset:", out_dataset)
    print("shape:", df.shape)
    print("ticker_count:", df["ticker"].nunique())
    print("feature_count:", len(features))
    print("date_range:", df["base_trading_date"].min(), "->", df["base_trading_date"].max())

    print("\n===== count cap info =====")
    for k, v in cap_info.items():
        print(k, "cap=", v)

    print("\n===== original vs robust news columns sample stats =====")
    check_cols = [c for c in COUNT_COLS + SENTIMENT_COLS + RATIO_COLS + ["has_news"] if c in df.columns]

    for col in check_cols:
        if col in original.columns:
            orig = safe_numeric(original[col])
        elif col == "has_news":
            orig = (raw_news_count > 0).astype(float)
        else:
            continue

        new = safe_numeric(df[col])

        print(
            col,
            "orig_mean=", round(float(orig.mean()), 6),
            "orig_max=", round(float(orig.max()), 6),
            "new_mean=", round(float(new.mean()), 6),
            "new_max=", round(float(new.max()), 6),
            "new_min=", round(float(new.min()), 6),
        )

    print("\n===== news coverage =====")
    news_summary = (
        original.groupby("ticker")
        .agg(news_sum=("news_count", "sum"))
        .reset_index()
        .sort_values(["news_sum", "ticker"], ascending=[False, True])
    )
    print("tickers_with_news:", int((news_summary["news_sum"] > 0).sum()))
    print("tickers_without_news:", int((news_summary["news_sum"] == 0).sum()))
    print(news_summary.to_string(index=False))


if __name__ == "__main__":
    main()
