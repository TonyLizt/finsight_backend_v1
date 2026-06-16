#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor


DATA_DIR = Path(
    "/data/hmt/projects/finsight/finsight_backend_v1_git/"
    "local_experiments/outputs/"
    "expanded_60_no_weak10_news48_quality_fundamental/"
    "training_dataset"
)

CANDIDATE_DIR = Path("local_experiments/outputs/regressor_v1_2_candidates")

OUTPUT_DIR = Path("artifacts/models/regressor/finsight_reg_return_path_v1.2")

TARGET_COLS = [f"target_return_d{i}" for i in range(1, 6)]

CANDIDATE_NAME = "extra_trees_shallow"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_feature_columns(path: Path) -> List[str]:
    obj = read_json(path)
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ["feature_columns", "features", "columns"]:
            if isinstance(obj.get(key), list):
                return obj[key]
    raise ValueError(f"Cannot parse feature columns from {path}")


def clean_x(df: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
    x = df[feature_cols].copy()
    for c in feature_cols:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.replace([np.inf, -np.inf], np.nan)
    return x


def weighted_mean(df: pd.DataFrame, col: str) -> Any:
    vals = pd.to_numeric(df[col], errors="coerce")
    weights = pd.to_numeric(df["test_rows"], errors="coerce")
    mask = vals.notna() & weights.notna()
    if not mask.any():
        return None
    return float(np.average(vals[mask], weights=weights[mask]))


def main() -> None:
    csv_path = DATA_DIR / "dataset_h5_v1.csv"
    feature_path = DATA_DIR / "feature_columns_h5_v1.json"
    fold_metrics_path = CANDIDATE_DIR / "candidate_fold_metrics.csv"
    summary_path = CANDIDATE_DIR / "candidate_summary.csv"

    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    if not feature_path.exists():
        raise FileNotFoundError(feature_path)
    if not fold_metrics_path.exists():
        raise FileNotFoundError(fold_metrics_path)
    if not summary_path.exists():
        raise FileNotFoundError(summary_path)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    feature_cols = load_feature_columns(feature_path)

    df = pd.read_csv(csv_path)
    df["base_trading_date"] = pd.to_datetime(df["base_trading_date"], errors="coerce")
    df["target_date_d5"] = pd.to_datetime(df["target_date_d5"], errors="coerce")

    raw_rows = len(df)
    df = df[df["target_date_d5"] <= pd.Timestamp("2025-05-20")].copy()
    rows_after_cutoff = len(df)
    df = df.dropna(subset=["base_trading_date", "target_date_d5"] + TARGET_COLS).copy()

    missing = [c for c in ["ticker", "base_trading_date", "target_date_d5"] + feature_cols + TARGET_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    print("[INFO] training final ExtraTrees model")
    print("[INFO] dataset =", csv_path)
    print("[INFO] rows =", len(df))
    print("[INFO] feature_count =", len(feature_cols))
    print("[INFO] base_trading_date =", df["base_trading_date"].min().date(), "->", df["base_trading_date"].max().date())
    print("[INFO] target_date_d5 =", df["target_date_d5"].min().date(), "->", df["target_date_d5"].max().date())

    x_all = clean_x(df, feature_cols)
    y_all = df[TARGET_COLS].astype(float).to_numpy()

    model = ExtraTreesRegressor(
        n_estimators=300,
        max_depth=8,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=max(1, min(8, os.cpu_count() or 1)),
    )

    model.fit(x_all, y_all)
    joblib.dump(model, OUTPUT_DIR / "model.joblib")

    # 读取候选实验中的 ExtraTrees rolling 指标
    fold_df_all = pd.read_csv(fold_metrics_path)
    fold_df = fold_df_all[fold_df_all["candidate"] == CANDIDATE_NAME].copy()

    if len(fold_df) == 0:
        raise RuntimeError(f"No fold metrics found for {CANDIDATE_NAME}")

    overall = {
        "mae": weighted_mean(fold_df, "mae"),
        "rmse": weighted_mean(fold_df, "rmse"),
        "mape": weighted_mean(fold_df, "mape"),
        "mape_valid_ratio": weighted_mean(fold_df, "mape_valid_ratio"),
        "direction_accuracy": weighted_mean(fold_df, "direction_accuracy"),
        "curve_mae": weighted_mean(fold_df, "curve_mae"),
    }

    by_horizon: Dict[str, Dict[str, Any]] = {}
    for h in ["d1", "d2", "d3", "d4", "d5"]:
        by_horizon[h] = {}
        for metric in ["mae", "rmse", "mape", "mape_valid_ratio", "direction_accuracy"]:
            col = f"{h}_{metric}"
            if col in fold_df.columns:
                by_horizon[h][metric] = weighted_mean(fold_df, col)
            else:
                by_horizon[h][metric] = None

    fold_rows = fold_df.to_dict(orient="records")

    write_json(feature_cols, OUTPUT_DIR / "feature_columns.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "task": "return_path_regression",
        "forecast_days": 5,
        "target_columns": TARGET_COLS,
        "target_horizon_days": [1, 2, 3, 4, 5],
        "prediction_unit": "future_return",
        "price_reconstruction": "predicted_price_i = current_price * (1 + predicted_return_i)",
        "cutoff_rule": "target_date_d5 <= cutoff_date",
        "cutoff_date": "2025-05-20",
    }, OUTPUT_DIR / "target_config.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "model_type": "ExtraTreesRegressor",
        "candidate_name": CANDIDATE_NAME,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data_dir": str(DATA_DIR),
        "dataset_file": str(csv_path),
        "source_feature_columns_file": str(feature_path),
        "source_candidate_fold_metrics_file": str(fold_metrics_path),
        "source_candidate_summary_file": str(summary_path),
        "cutoff_date": "2025-05-20",
        "filter_rule": "target_date_d5 <= cutoff_date",
        "raw_rows": int(raw_rows),
        "rows_after_cutoff": int(rows_after_cutoff),
        "final_train_rows": int(len(df)),
        "ticker_count": int(df["ticker"].nunique()),
        "base_trading_date_min": str(df["base_trading_date"].min().date()),
        "base_trading_date_max": str(df["base_trading_date"].max().date()),
        "target_date_d5_min": str(df["target_date_d5"].min().date()),
        "target_date_d5_max": str(df["target_date_d5"].max().date()),
        "feature_count": len(feature_cols),
        "target_columns": TARGET_COLS,
        "model_params": model.get_params(),
        "leakage_control_note": "训练样本使用 target_date_d5 <= 2025-05-20 过滤；财报特征应已满足 fund_available_date <= base_trading_date。",
    }, OUTPUT_DIR / "train_config.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "model_type": "ExtraTreesRegressor",
        "candidate_name": CANDIDATE_NAME,
        "metric_scope": "rolling_validation_by_base_trading_date",
        "overall": overall,
        "by_horizon": by_horizon,
        "folds": fold_rows,
        "primary_metrics": ["mae", "rmse", "direction_accuracy", "curve_mae"],
        "secondary_metric": "mape",
        "mape_note": "MAPE 只在 |actual_return| >= 1e-4 的样本上计算。收益率接近 0 时 MAPE 不稳定，主要参考 MAE、RMSE、direction_accuracy、curve_mae。",
        "comparison_note": "该 ExtraTrees 版本在候选实验中相较 xgb_square_base 同时降低 MAE/RMSE/curve_mae，并提升 direction_accuracy。",
    }, OUTPUT_DIR / "metrics.json")

    sample_row = df.sort_values(["base_trading_date", "ticker"]).iloc[-1]

    sample_features = {}
    for c in feature_cols:
        v = sample_row[c]
        sample_features[c] = None if pd.isna(v) else float(v)

    current_price = None
    if "close" in df.columns and not pd.isna(sample_row["close"]):
        current_price = float(sample_row["close"])

    sample_x = pd.DataFrame([sample_features])[feature_cols]
    sample_pred = model.predict(sample_x)[0].tolist()

    if current_price is not None:
        sample_prices = [float(current_price * (1.0 + r)) for r in sample_pred]
    else:
        sample_prices = None

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "model_type": "ExtraTreesRegressor",
        "ticker": str(sample_row["ticker"]),
        "base_trading_date": str(sample_row["base_trading_date"].date()),
        "current_price": current_price,
        "features": sample_features,
    }, OUTPUT_DIR / "sample_prediction_input.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "model_type": "ExtraTreesRegressor",
        "prediction_shape": [1, 5],
        "target_columns": TARGET_COLS,
        "predicted_return_path": {f"d{i + 1}": float(sample_pred[i]) for i in range(5)},
        "predicted_price_path": (
            {f"d{i + 1}": float(sample_prices[i]) for i in range(5)}
            if sample_prices is not None
            else None
        ),
    }, OUTPUT_DIR / "sample_prediction_output.json")

    readme = f"""# Finsight 回归价格路径模型 v1.2

本目录是 Finsight / 智融洞察项目 Member B v1.2 回归模型交付目录。

## 1. 模型基本信息

- 模型名称：finsight_reg_return_path_v1.2
- 模型类型：ExtraTreesRegressor
- 候选实验名称：extra_trees_shallow
- 任务：预测未来 1~5 个交易日收益率路径
- 目标列：target_return_d1 到 target_return_d5

## 2. 数据边界

训练样本过滤规则：

target_date_d5 <= 2025-05-20

训练数据来自：

{csv_path}

训练样本 base_trading_date 范围：

{df["base_trading_date"].min().date()} -> {df["base_trading_date"].max().date()}

目标标签 target_date_d5 范围：

{df["target_date_d5"].min().date()} -> {df["target_date_d5"].max().date()}

## 3. 滚动验证指标

整体 rolling validation 指标：

MAE = {overall["mae"]}
RMSE = {overall["rmse"]}
MAPE = {overall["mape"]}
Direction Accuracy = {overall["direction_accuracy"]}
Curve MAE = {overall["curve_mae"]}

相比原 XGB baseline，本版本在 MAE、RMSE、Curve MAE 和 Direction Accuracy 上均有提升。

## 4. 指标解释

MAE 和 Curve MAE 越低，说明未来 1~5 日收益率路径预测误差越小。

RMSE 越低，说明模型较少出现较大的离谱误差。

Direction Accuracy 越高，说明模型对未来收益率正负方向判断越准。

MAPE 在收益率接近 0 时容易失真，因此只作为补充参考。

## 5. 加载方式

加载时必须按 feature_columns.json 中的顺序构造输入特征。

示例：

import json
import joblib
import pandas as pd

model = joblib.load("model.joblib")
feature_columns = json.load(open("feature_columns.json", "r", encoding="utf-8"))
sample = json.load(open("sample_prediction_input.json", "r", encoding="utf-8"))

x = pd.DataFrame([sample["features"]])[feature_columns]
pred = model.predict(x)

print(pred.shape)  # 应为 (1, 5)

## 6. 文件清单

model.joblib
feature_columns.json
target_config.json
metrics.json
train_config.json
README.md
sample_prediction_input.json
sample_prediction_output.json
rolling_regression_metrics_by_fold.csv
"""

    (OUTPUT_DIR / "README.md").write_text(readme, encoding="utf-8")

    # 保存 ExtraTrees 的 fold 明细到正式 artifact
    fold_df.to_csv(OUTPUT_DIR / "rolling_regression_metrics_by_fold.csv", index=False, encoding="utf-8-sig")

    # 加载测试
    loaded = joblib.load(OUTPUT_DIR / "model.joblib")
    pred = loaded.predict(sample_x)

    print("[INFO] load test pred shape =", pred.shape)
    print("[INFO] overall =", overall)

    if tuple(pred.shape) != (1, 5):
        raise RuntimeError(f"Unexpected prediction shape: {pred.shape}")

    print("[DONE] ExtraTrees final artifact exported.")
    print("[DONE] output_dir =", OUTPUT_DIR)


if __name__ == "__main__":
    main()
