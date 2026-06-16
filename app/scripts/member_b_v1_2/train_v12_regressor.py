#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.multioutput import MultiOutputRegressor
from xgboost import XGBRegressor


DEFAULT_DATA_DIR = Path(
    "/data/hmt/projects/finsight/finsight_backend_v1_git/"
    "local_experiments/outputs/"
    "expanded_60_no_weak10_news48_quality_fundamental/"
    "training_dataset"
)

DEFAULT_OUTPUT_DIR = Path(
    "artifacts/models/regressor/finsight_reg_return_path_v1.2"
)

TARGET_COLS = [f"target_return_d{i}" for i in range(1, 6)]


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


def calc_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, Any]:
    err = y_pred - y_true
    abs_err = np.abs(err)

    mae = float(np.nanmean(abs_err))
    rmse = float(np.sqrt(np.nanmean(err ** 2)))
    direction_accuracy = float(np.nanmean(np.sign(y_true) == np.sign(y_pred)))
    curve_mae = float(np.nanmean(np.nanmean(abs_err, axis=1)))

    eps = 1e-4
    mask = np.abs(y_true) >= eps
    if np.any(mask):
        mape = float(np.nanmean(np.abs(err[mask] / y_true[mask])) * 100.0)
        mape_valid_ratio = float(np.mean(mask))
    else:
        mape = None
        mape_valid_ratio = 0.0

    by_horizon = {}
    for i in range(5):
        yt = y_true[:, i]
        yp = y_pred[:, i]
        e = yp - yt
        ae = np.abs(e)

        h_mask = np.abs(yt) >= eps
        if np.any(h_mask):
            h_mape = float(np.nanmean(np.abs(e[h_mask] / yt[h_mask])) * 100.0)
            h_mape_valid_ratio = float(np.mean(h_mask))
        else:
            h_mape = None
            h_mape_valid_ratio = 0.0

        by_horizon[f"d{i + 1}"] = {
            "mae": float(np.nanmean(ae)),
            "rmse": float(np.sqrt(np.nanmean(e ** 2))),
            "mape": h_mape,
            "mape_valid_ratio": h_mape_valid_ratio,
            "direction_accuracy": float(np.nanmean(np.sign(yt) == np.sign(yp))),
        }

    return {
        "mae": mae,
        "rmse": rmse,
        "mape": mape,
        "mape_valid_ratio": mape_valid_ratio,
        "direction_accuracy": direction_accuracy,
        "curve_mae": curve_mae,
        "by_horizon": by_horizon,
    }


def make_model(random_state: int, n_jobs: int) -> MultiOutputRegressor:
    base = XGBRegressor(
        objective="reg:squarederror",
        n_estimators=500,
        max_depth=3,
        learning_rate=0.03,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=3.0,
        reg_alpha=0.1,
        min_child_weight=5,
        tree_method="hist",
        eval_metric="rmse",
        random_state=random_state,
        n_jobs=n_jobs,
    )
    return MultiOutputRegressor(base, n_jobs=1)


def weighted_mean(rows: List[Dict[str, Any]], key: str) -> Any:
    vals = []
    weights = []
    for r in rows:
        v = r.get(key)
        if v is None or pd.isna(v):
            continue
        vals.append(float(v))
        weights.append(float(r["test_rows"]))
    if not vals:
        return None
    return float(np.average(vals, weights=weights))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cutoff-date", type=str, default="2025-05-20")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--n-jobs", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir
    cutoff_date = pd.Timestamp(args.cutoff_date)

    csv_path = data_dir / "dataset_h5_v1.csv"
    feature_path = data_dir / "feature_columns_h5_v1.json"

    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    if not feature_path.exists():
        raise FileNotFoundError(feature_path)

    output_dir.mkdir(parents=True, exist_ok=True)

    print("[INFO] data_dir =", data_dir)
    print("[INFO] output_dir =", output_dir)
    print("[INFO] cutoff_date =", args.cutoff_date)

    feature_cols = load_feature_columns(feature_path)
    print("[INFO] feature_count =", len(feature_cols))

    df = pd.read_csv(csv_path)
    print("[INFO] raw shape =", df.shape)

    required = ["ticker", "base_trading_date", "target_date_d5"] + feature_cols + TARGET_COLS
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df["base_trading_date"] = pd.to_datetime(df["base_trading_date"], errors="coerce")
    df["target_date_d5"] = pd.to_datetime(df["target_date_d5"], errors="coerce")

    raw_rows = len(df)
    df = df[df["target_date_d5"] <= cutoff_date].copy()
    rows_after_cutoff = len(df)
    df = df.dropna(subset=["base_trading_date", "target_date_d5"] + TARGET_COLS).copy()
    final_rows = len(df)

    print("[INFO] raw_rows =", raw_rows)
    print("[INFO] rows_after_cutoff =", rows_after_cutoff)
    print("[INFO] final_rows =", final_rows)
    print("[INFO] base_trading_date =", df["base_trading_date"].min().date(), "->", df["base_trading_date"].max().date())
    print("[INFO] target_date_d5 =", df["target_date_d5"].min().date(), "->", df["target_date_d5"].max().date())
    print("[INFO] ticker_count =", df["ticker"].nunique())

    folds = [
        ("fold_2024q3", "2024-06-30", "2024-07-01", "2024-09-30"),
        ("fold_2024q4", "2024-09-30", "2024-10-01", "2024-12-31"),
        ("fold_2025q1", "2024-12-31", "2025-01-01", "2025-03-31"),
        ("fold_2025q2_partial", "2025-03-31", "2025-04-01", "2025-05-13"),
    ]

    fold_rows = []
    base_model = make_model(args.random_state, args.n_jobs)

    print("[INFO] start rolling validation")

    for fold_name, train_end, test_start, test_end in folds:
        train_end = pd.Timestamp(train_end)
        test_start = pd.Timestamp(test_start)
        test_end = pd.Timestamp(test_end)

        train_df = df[df["base_trading_date"] <= train_end].copy()
        test_df = df[(df["base_trading_date"] >= test_start) & (df["base_trading_date"] <= test_end)].copy()

        if len(train_df) < 1000 or len(test_df) < 100:
            print(f"[SKIP] {fold_name}: train={len(train_df)}, test={len(test_df)}")
            continue

        x_train = clean_x(train_df, feature_cols)
        y_train = train_df[TARGET_COLS].astype(float).to_numpy()
        x_test = clean_x(test_df, feature_cols)
        y_test = test_df[TARGET_COLS].astype(float).to_numpy()

        model = clone(base_model)
        model.fit(x_train, y_train)
        pred = model.predict(x_test)
        m = calc_metrics(y_test, pred)

        row = {
            "fold": fold_name,
            "train_start": str(train_df["base_trading_date"].min().date()),
            "train_end": str(train_df["base_trading_date"].max().date()),
            "test_start": str(test_df["base_trading_date"].min().date()),
            "test_end": str(test_df["base_trading_date"].max().date()),
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "mae": m["mae"],
            "rmse": m["rmse"],
            "mape": m["mape"],
            "mape_valid_ratio": m["mape_valid_ratio"],
            "direction_accuracy": m["direction_accuracy"],
            "curve_mae": m["curve_mae"],
        }

        for h, hm in m["by_horizon"].items():
            for k, v in hm.items():
                row[f"{h}_{k}"] = v

        fold_rows.append(row)

        print(
            f"[FOLD] {fold_name}: train={len(train_df)}, test={len(test_df)}, "
            f"MAE={row['mae']:.6f}, RMSE={row['rmse']:.6f}, "
            f"DIR_ACC={row['direction_accuracy']:.6f}, CURVE_MAE={row['curve_mae']:.6f}"
        )

    if not fold_rows:
        raise RuntimeError("No valid rolling folds.")

    fold_df = pd.DataFrame(fold_rows)
    fold_df.to_csv(output_dir / "rolling_regression_metrics_by_fold.csv", index=False, encoding="utf-8-sig")

    overall = {
        "mae": weighted_mean(fold_rows, "mae"),
        "rmse": weighted_mean(fold_rows, "rmse"),
        "mape": weighted_mean(fold_rows, "mape"),
        "mape_valid_ratio": weighted_mean(fold_rows, "mape_valid_ratio"),
        "direction_accuracy": weighted_mean(fold_rows, "direction_accuracy"),
        "curve_mae": weighted_mean(fold_rows, "curve_mae"),
    }

    by_horizon = {}
    for h in ["d1", "d2", "d3", "d4", "d5"]:
        by_horizon[h] = {}
        for metric in ["mae", "rmse", "mape", "mape_valid_ratio", "direction_accuracy"]:
            by_horizon[h][metric] = weighted_mean(fold_rows, f"{h}_{metric}")

    print("[INFO] train final model")
    x_all = clean_x(df, feature_cols)
    y_all = df[TARGET_COLS].astype(float).to_numpy()

    final_model = make_model(args.random_state, args.n_jobs)
    final_model.fit(x_all, y_all)

    joblib.dump(final_model, output_dir / "model.joblib")

    write_json(feature_cols, output_dir / "feature_columns.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "task": "return_path_regression",
        "forecast_days": 5,
        "target_columns": TARGET_COLS,
        "target_horizon_days": [1, 2, 3, 4, 5],
        "prediction_unit": "future_return",
        "price_reconstruction": "predicted_price_i = current_price * (1 + predicted_return_i)",
        "cutoff_rule": "target_date_d5 <= cutoff_date",
        "cutoff_date": args.cutoff_date,
    }, output_dir / "target_config.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "model_type": "MultiOutputRegressor(XGBRegressor)",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "dataset_file": str(csv_path),
        "source_feature_columns_file": str(feature_path),
        "cutoff_date": args.cutoff_date,
        "filter_rule": "target_date_d5 <= cutoff_date",
        "raw_rows": int(raw_rows),
        "rows_after_cutoff": int(rows_after_cutoff),
        "final_train_rows": int(final_rows),
        "ticker_count": int(df["ticker"].nunique()),
        "base_trading_date_min": str(df["base_trading_date"].min().date()),
        "base_trading_date_max": str(df["base_trading_date"].max().date()),
        "target_date_d5_min": str(df["target_date_d5"].min().date()),
        "target_date_d5_max": str(df["target_date_d5"].max().date()),
        "feature_count": len(feature_cols),
        "target_columns": TARGET_COLS,
        "xgb_params": final_model.estimator.get_params(),
        "leakage_control_note": "训练样本使用 target_date_d5 <= 2025-05-20 过滤；财报特征应已满足 fund_available_date <= base_trading_date。",
    }, output_dir / "train_config.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "metric_scope": "rolling_validation_by_base_trading_date",
        "overall": overall,
        "by_horizon": by_horizon,
        "folds": fold_rows,
        "primary_metrics": ["mae", "rmse", "direction_accuracy", "curve_mae"],
        "secondary_metric": "mape",
        "mape_note": "MAPE 只在 |actual_return| >= 1e-4 的样本上计算。收益率接近 0 时 MAPE 不稳定，主要参考 MAE、RMSE、direction_accuracy、curve_mae。",
    }, output_dir / "metrics.json")

    sample_row = df.sort_values(["base_trading_date", "ticker"]).iloc[-1]
    sample_features = {}

    for c in feature_cols:
        v = sample_row[c]
        sample_features[c] = None if pd.isna(v) else float(v)

    current_price = None
    if "close" in df.columns and not pd.isna(sample_row["close"]):
        current_price = float(sample_row["close"])

    sample_x = pd.DataFrame([sample_features])[feature_cols]
    sample_pred = final_model.predict(sample_x)[0].tolist()

    if current_price is not None:
        sample_prices = [float(current_price * (1.0 + r)) for r in sample_pred]
    else:
        sample_prices = None

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "ticker": str(sample_row["ticker"]),
        "base_trading_date": str(sample_row["base_trading_date"].date()),
        "current_price": current_price,
        "features": sample_features,
    }, output_dir / "sample_prediction_input.json")

    write_json({
        "model_name": "finsight_reg_return_path_v1.2",
        "prediction_shape": [1, 5],
        "target_columns": TARGET_COLS,
        "predicted_return_path": {f"d{i + 1}": float(sample_pred[i]) for i in range(5)},
        "predicted_price_path": (
            {f"d{i + 1}": float(sample_prices[i]) for i in range(5)}
            if sample_prices is not None
            else None
        ),
    }, output_dir / "sample_prediction_output.json")

    readme_text = f"""# Finsight 回归价格路径模型 v1.2

本目录是 Member B v1.2 回归模型交付目录。

模型名称：finsight_reg_return_path_v1.2
模型类型：MultiOutputRegressor(XGBRegressor)
任务：预测未来 1~5 个交易日收益率路径
目标列：target_return_d1 到 target_return_d5

数据边界：
只使用 target_date_d5 <= {args.cutoff_date} 的样本。
这个规则用于避免使用 2025-05-21 之后回测区间的数据。

整体滚动验证指标：
MAE = {overall["mae"]}
RMSE = {overall["rmse"]}
MAPE = {overall["mape"]}
Direction Accuracy = {overall["direction_accuracy"]}
Curve MAE = {overall["curve_mae"]}

MAPE 说明：
收益率接近 0 时，MAPE 会不稳定。
本模型只在 |actual_return| >= 1e-4 的样本上计算 MAPE。
后续评价时主要参考 MAE、RMSE、direction_accuracy、curve_mae。

加载方式：
1. joblib.load("model.joblib")
2. 按 feature_columns.json 的顺序构造输入特征
3. model.predict(x) 的输出形状应为 (1, 5)

本目录应包含：
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
    (output_dir / "README.md").write_text(readme_text, encoding="utf-8")

    loaded = joblib.load(output_dir / "model.joblib")
    pred = loaded.predict(sample_x)

    print("[INFO] load test pred shape =", pred.shape)
    if tuple(pred.shape) != (1, 5):
        raise RuntimeError(f"Wrong prediction shape: {pred.shape}")

    print("[DONE] v1.2 回归模型训练完成")
    print("[DONE] artifact dir =", output_dir)


if __name__ == "__main__":
    main()
