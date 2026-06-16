#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Finsight / 智融洞察 Member B v1.2 回归模型候选实验脚本

目标：
1. 读取同一个 v1.2 最优训练集 dataset_h5_v1.csv；
2. 使用同一批 feature_columns；
3. 使用同样 rolling validation 切分；
4. 比较多种回归训练策略；
5. 不覆盖正式 artifact，只输出候选实验结果。

输出目录：
local_experiments/outputs/regressor_v1_2_candidates/
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor


DATA_DIR = Path(
    "/data/hmt/projects/finsight/finsight_backend_v1_git/"
    "local_experiments/outputs/"
    "expanded_60_no_weak10_news48_quality_fundamental/"
    "training_dataset"
)

OUT_DIR = Path("local_experiments/outputs/regressor_v1_2_candidates")

TARGET_COLS = [f"target_return_d{i}" for i in range(1, 6)]

BASELINE_V12 = {
    "mae": 0.02775220971095528,
    "rmse": 0.041393802951685266,
    "direction_accuracy": 0.5182201834862386,
    "curve_mae": 0.027752209710955274,
}

BASELINE_V11 = {
    "mae": 0.04509145079016288,
    "rmse": 0.06298616307146268,
    "direction_accuracy": 0.5245,
    "curve_mae": 0.045091450790162885,
}


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


def make_xgb(
    objective: str = "reg:squarederror",
    n_estimators: int = 400,
    max_depth: int = 3,
    learning_rate: float = 0.035,
    reg_lambda: float = 3.0,
    reg_alpha: float = 0.1,
    min_child_weight: float = 5.0,
    random_state: int = 42,
    n_jobs: int = 8,
) -> MultiOutputRegressor:
    base = XGBRegressor(
        objective=objective,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=reg_lambda,
        reg_alpha=reg_alpha,
        min_child_weight=min_child_weight,
        tree_method="hist",
        eval_metric="rmse",
        random_state=random_state,
        n_jobs=n_jobs,
    )
    return MultiOutputRegressor(base, n_jobs=1)


def make_estimator(candidate: Dict[str, Any], random_state: int, n_jobs: int):
    model_type = candidate["model_type"]

    if model_type == "xgb":
        return make_xgb(
            objective=candidate.get("objective", "reg:squarederror"),
            n_estimators=candidate.get("n_estimators", 400),
            max_depth=candidate.get("max_depth", 3),
            learning_rate=candidate.get("learning_rate", 0.035),
            reg_lambda=candidate.get("reg_lambda", 3.0),
            reg_alpha=candidate.get("reg_alpha", 0.1),
            min_child_weight=candidate.get("min_child_weight", 5.0),
            random_state=random_state,
            n_jobs=n_jobs,
        )

    if model_type == "ridge":
        return make_pipeline(
            StandardScaler(),
            Ridge(alpha=candidate.get("alpha", 10.0), random_state=random_state),
        )

    if model_type == "extra_trees":
        return ExtraTreesRegressor(
            n_estimators=candidate.get("n_estimators", 300),
            max_depth=candidate.get("max_depth", 8),
            min_samples_leaf=candidate.get("min_samples_leaf", 10),
            random_state=random_state,
            n_jobs=n_jobs,
        )

    raise ValueError(f"Unknown model_type: {model_type}")


def calc_sample_weight(df: pd.DataFrame, mode: str) -> np.ndarray | None:
    if mode == "none":
        return None

    if mode == "recent_linear":
        dates = df["base_trading_date"]
        min_d = dates.min()
        max_d = dates.max()
        span = max((max_d - min_d).days, 1)
        pos = (dates - min_d).dt.days / span
        weights = 0.7 + 0.6 * pos
        return weights.to_numpy(dtype=float)

    if mode == "recent_strong":
        dates = df["base_trading_date"]
        min_d = dates.min()
        max_d = dates.max()
        span = max((max_d - min_d).days, 1)
        pos = (dates - min_d).dt.days / span
        weights = 0.5 + 1.0 * pos
        return weights.to_numpy(dtype=float)

    raise ValueError(f"Unknown weight_mode: {mode}")


def transform_y_train(
    y_train: np.ndarray,
    train_df: pd.DataFrame,
    mode: str,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    info: Dict[str, Any] = {"target_transform": mode}

    if mode == "raw":
        return y_train, info

    if mode == "clip_q01_q99":
        lo = np.nanquantile(y_train, 0.01, axis=0)
        hi = np.nanquantile(y_train, 0.99, axis=0)
        y2 = np.clip(y_train, lo, hi)
        info["clip_low"] = lo.tolist()
        info["clip_high"] = hi.tolist()
        return y2, info

    if mode == "clip_fixed_12pct":
        lo = -0.12
        hi = 0.12
        y2 = np.clip(y_train, lo, hi)
        info["clip_low"] = lo
        info["clip_high"] = hi
        return y2, info

    if mode == "vol_norm":
        if "volatility_20d" not in train_df.columns:
            raise ValueError("vol_norm requires volatility_20d feature.")
        vol = pd.to_numeric(train_df["volatility_20d"], errors="coerce").to_numpy(dtype=float)
        med = float(np.nanmedian(vol))
        vol = np.where(np.isfinite(vol), vol, med)
        vol = np.clip(vol, 0.005, 0.20)
        y2 = y_train / vol.reshape(-1, 1)
        info["vol_clip_low"] = 0.005
        info["vol_clip_high"] = 0.20
        return y2, info

    raise ValueError(f"Unknown target_transform: {mode}")


def inverse_pred(
    pred: np.ndarray,
    test_df: pd.DataFrame,
    transform_info: Dict[str, Any],
) -> np.ndarray:
    mode = transform_info.get("target_transform", "raw")

    if mode in ["raw", "clip_q01_q99", "clip_fixed_12pct"]:
        return pred

    if mode == "vol_norm":
        vol = pd.to_numeric(test_df["volatility_20d"], errors="coerce").to_numpy(dtype=float)
        med = float(np.nanmedian(vol))
        vol = np.where(np.isfinite(vol), vol, med)
        vol = np.clip(vol, 0.005, 0.20)
        return pred * vol.reshape(-1, 1)

    raise ValueError(f"Unknown target_transform: {mode}")


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


def get_candidates(quick: bool) -> List[Dict[str, Any]]:
    candidates = [
        {
            "name": "xgb_square_base",
            "model_type": "xgb",
            "objective": "reg:squarederror",
            "target_transform": "raw",
            "weight_mode": "none",
            "n_estimators": 500,
            "max_depth": 3,
            "learning_rate": 0.03,
            "reg_lambda": 3.0,
            "reg_alpha": 0.1,
            "min_child_weight": 5.0,
        },
        {
            "name": "xgb_huber_raw",
            "model_type": "xgb",
            "objective": "reg:pseudohubererror",
            "target_transform": "raw",
            "weight_mode": "none",
            "n_estimators": 500,
            "max_depth": 3,
            "learning_rate": 0.03,
            "reg_lambda": 3.0,
            "reg_alpha": 0.1,
            "min_child_weight": 5.0,
        },
        {
            "name": "xgb_abs_raw",
            "model_type": "xgb",
            "objective": "reg:absoluteerror",
            "target_transform": "raw",
            "weight_mode": "none",
            "n_estimators": 450,
            "max_depth": 3,
            "learning_rate": 0.035,
            "reg_lambda": 3.0,
            "reg_alpha": 0.1,
            "min_child_weight": 5.0,
        },
        {
            "name": "xgb_square_clip_q01_q99",
            "model_type": "xgb",
            "objective": "reg:squarederror",
            "target_transform": "clip_q01_q99",
            "weight_mode": "none",
            "n_estimators": 500,
            "max_depth": 3,
            "learning_rate": 0.03,
            "reg_lambda": 3.0,
            "reg_alpha": 0.1,
            "min_child_weight": 5.0,
        },
        {
            "name": "xgb_square_recent_weight",
            "model_type": "xgb",
            "objective": "reg:squarederror",
            "target_transform": "raw",
            "weight_mode": "recent_linear",
            "n_estimators": 500,
            "max_depth": 3,
            "learning_rate": 0.03,
            "reg_lambda": 3.0,
            "reg_alpha": 0.1,
            "min_child_weight": 5.0,
        },
        {
            "name": "xgb_huber_recent_weight",
            "model_type": "xgb",
            "objective": "reg:pseudohubererror",
            "target_transform": "raw",
            "weight_mode": "recent_linear",
            "n_estimators": 500,
            "max_depth": 3,
            "learning_rate": 0.03,
            "reg_lambda": 3.0,
            "reg_alpha": 0.1,
            "min_child_weight": 5.0,
        },
        {
            "name": "xgb_square_vol_norm",
            "model_type": "xgb",
            "objective": "reg:squarederror",
            "target_transform": "vol_norm",
            "weight_mode": "none",
            "n_estimators": 500,
            "max_depth": 3,
            "learning_rate": 0.03,
            "reg_lambda": 3.0,
            "reg_alpha": 0.1,
            "min_child_weight": 5.0,
        },
        {
            "name": "xgb_square_more_regularized",
            "model_type": "xgb",
            "objective": "reg:squarederror",
            "target_transform": "raw",
            "weight_mode": "none",
            "n_estimators": 350,
            "max_depth": 2,
            "learning_rate": 0.04,
            "reg_lambda": 8.0,
            "reg_alpha": 0.5,
            "min_child_weight": 10.0,
        },
        {
            "name": "ridge_scaled",
            "model_type": "ridge",
            "target_transform": "raw",
            "weight_mode": "none",
            "alpha": 20.0,
        },
        {
            "name": "extra_trees_shallow",
            "model_type": "extra_trees",
            "target_transform": "raw",
            "weight_mode": "none",
            "n_estimators": 300,
            "max_depth": 8,
            "min_samples_leaf": 10,
        },
    ]

    if quick:
        return candidates[:5]

    return candidates


def fit_model(estimator, x_train, y_train, sample_weight):
    if sample_weight is None:
        estimator.fit(x_train, y_train)
    else:
        try:
            estimator.fit(x_train, y_train, sample_weight=sample_weight)
        except TypeError:
            print("[WARN] current estimator does not accept sample_weight; fitting without weights.")
            estimator.fit(x_train, y_train)
    return estimator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--cutoff-date", type=str, default="2025-05-20")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--n-jobs", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = args.data_dir / "dataset_h5_v1.csv"
    feature_path = args.data_dir / "feature_columns_h5_v1.json"

    print("[INFO] data_dir =", args.data_dir)
    print("[INFO] csv_path =", csv_path)
    print("[INFO] feature_path =", feature_path)
    print("[INFO] output_dir =", out_dir)

    feature_cols = load_feature_columns(feature_path)

    df = pd.read_csv(csv_path)
    df["base_trading_date"] = pd.to_datetime(df["base_trading_date"], errors="coerce")
    df["target_date_d5"] = pd.to_datetime(df["target_date_d5"], errors="coerce")

    missing = [c for c in ["ticker", "base_trading_date", "target_date_d5"] + feature_cols + TARGET_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    raw_rows = len(df)
    df = df[df["target_date_d5"] <= pd.Timestamp(args.cutoff_date)].copy()
    df = df.dropna(subset=["base_trading_date", "target_date_d5"] + TARGET_COLS).copy()

    print("[INFO] raw_rows =", raw_rows)
    print("[INFO] filtered_rows =", len(df))
    print("[INFO] feature_count =", len(feature_cols))
    print("[INFO] base_trading_date =", df["base_trading_date"].min().date(), "->", df["base_trading_date"].max().date())
    print("[INFO] target_date_d5 =", df["target_date_d5"].min().date(), "->", df["target_date_d5"].max().date())
    print("[INFO] ticker_count =", df["ticker"].nunique())

    folds = [
        ("fold_2024q3", "2024-06-30", "2024-07-01", "2024-09-30"),
        ("fold_2024q4", "2024-09-30", "2024-10-01", "2024-12-31"),
        ("fold_2025q1", "2024-12-31", "2025-01-01", "2025-03-31"),
        ("fold_2025q2_partial", "2025-03-31", "2025-04-01", "2025-05-13"),
    ]

    candidates = get_candidates(args.quick)
    print("[INFO] candidate_count =", len(candidates))

    all_fold_rows: List[Dict[str, Any]] = []
    summary_rows: List[Dict[str, Any]] = []

    for idx, cand in enumerate(candidates, 1):
        cname = cand["name"]
        print("\n" + "=" * 80)
        print(f"[CANDIDATE {idx}/{len(candidates)}] {cname}")
        print("=" * 80)

        fold_rows: List[Dict[str, Any]] = []

        for fold_name, train_end, test_start, test_end in folds:
            train_end = pd.Timestamp(train_end)
            test_start = pd.Timestamp(test_start)
            test_end = pd.Timestamp(test_end)

            train_df = df[df["base_trading_date"] <= train_end].copy()
            test_df = df[(df["base_trading_date"] >= test_start) & (df["base_trading_date"] <= test_end)].copy()

            if len(train_df) < 1000 or len(test_df) < 100:
                print(f"[SKIP] {cname} {fold_name}: train={len(train_df)}, test={len(test_df)}")
                continue

            x_train = clean_x(train_df, feature_cols)
            y_train_raw = train_df[TARGET_COLS].astype(float).to_numpy()

            x_test = clean_x(test_df, feature_cols)
            y_test = test_df[TARGET_COLS].astype(float).to_numpy()

            y_train, transform_info = transform_y_train(
                y_train_raw,
                train_df,
                cand.get("target_transform", "raw"),
            )

            sample_weight = calc_sample_weight(train_df, cand.get("weight_mode", "none"))

            estimator = make_estimator(cand, random_state=args.random_state, n_jobs=args.n_jobs)
            estimator = fit_model(estimator, x_train, y_train, sample_weight)

            pred_trans = estimator.predict(x_test)
            pred = inverse_pred(pred_trans, test_df, transform_info)

            m = calc_metrics(y_test, pred)

            row = {
                "candidate": cname,
                "fold": fold_name,
                "train_rows": int(len(train_df)),
                "test_rows": int(len(test_df)),
                "train_start": str(train_df["base_trading_date"].min().date()),
                "train_end": str(train_df["base_trading_date"].max().date()),
                "test_start": str(test_df["base_trading_date"].min().date()),
                "test_end": str(test_df["base_trading_date"].max().date()),
                "model_type": cand.get("model_type"),
                "objective": cand.get("objective"),
                "target_transform": cand.get("target_transform", "raw"),
                "weight_mode": cand.get("weight_mode", "none"),
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
            all_fold_rows.append(row)

            print(
                f"[FOLD] {fold_name}: "
                f"MAE={row['mae']:.6f}, RMSE={row['rmse']:.6f}, "
                f"DIR_ACC={row['direction_accuracy']:.6f}, CURVE_MAE={row['curve_mae']:.6f}"
            )

        if not fold_rows:
            continue

        summary = {
            "candidate": cname,
            "model_type": cand.get("model_type"),
            "objective": cand.get("objective"),
            "target_transform": cand.get("target_transform", "raw"),
            "weight_mode": cand.get("weight_mode", "none"),
            "fold_count": len(fold_rows),
            "mae": weighted_mean(fold_rows, "mae"),
            "rmse": weighted_mean(fold_rows, "rmse"),
            "mape": weighted_mean(fold_rows, "mape"),
            "mape_valid_ratio": weighted_mean(fold_rows, "mape_valid_ratio"),
            "direction_accuracy": weighted_mean(fold_rows, "direction_accuracy"),
            "curve_mae": weighted_mean(fold_rows, "curve_mae"),
        }

        summary["delta_mae_vs_v12"] = summary["mae"] - BASELINE_V12["mae"]
        summary["delta_rmse_vs_v12"] = summary["rmse"] - BASELINE_V12["rmse"]
        summary["delta_dir_vs_v12"] = summary["direction_accuracy"] - BASELINE_V12["direction_accuracy"]
        summary["delta_curve_mae_vs_v12"] = summary["curve_mae"] - BASELINE_V12["curve_mae"]

        summary["delta_mae_vs_v11"] = summary["mae"] - BASELINE_V11["mae"]
        summary["delta_rmse_vs_v11"] = summary["rmse"] - BASELINE_V11["rmse"]
        summary["delta_dir_vs_v11"] = summary["direction_accuracy"] - BASELINE_V11["direction_accuracy"]

        # 综合分只是辅助排序，不作为唯一选择依据。
        # MAE/RMSE 越小越好，方向准确率越大越好。
        summary["composite_score"] = (
            summary["mae"]
            + 0.40 * summary["rmse"]
            - 0.02 * (summary["direction_accuracy"] - 0.5)
        )

        summary_rows.append(summary)

        print(
            f"[SUMMARY] {cname}: "
            f"MAE={summary['mae']:.6f} ({summary['delta_mae_vs_v12']:+.6f} vs v1.2), "
            f"RMSE={summary['rmse']:.6f} ({summary['delta_rmse_vs_v12']:+.6f} vs v1.2), "
            f"DIR_ACC={summary['direction_accuracy']:.6f} ({summary['delta_dir_vs_v12']:+.6f} vs v1.2), "
            f"SCORE={summary['composite_score']:.6f}"
        )

    if not summary_rows:
        raise RuntimeError("No candidate produced summary rows.")

    fold_df = pd.DataFrame(all_fold_rows)
    summary_df = pd.DataFrame(summary_rows)

    fold_path = out_dir / "candidate_fold_metrics.csv"
    summary_path = out_dir / "candidate_summary.csv"
    json_path = out_dir / "candidate_summary.json"

    fold_df.to_csv(fold_path, index=False, encoding="utf-8-sig")
    summary_df.sort_values("composite_score").to_csv(summary_path, index=False, encoding="utf-8-sig")

    write_json({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data_dir": str(args.data_dir),
        "dataset": str(csv_path),
        "feature_columns": str(feature_path),
        "cutoff_date": args.cutoff_date,
        "baseline_v12": BASELINE_V12,
        "baseline_v11": BASELINE_V11,
        "rows": summary_df.sort_values("composite_score").to_dict(orient="records"),
    }, json_path)

    print("\n" + "=" * 80)
    print("[RESULT] saved files:")
    print(" -", fold_path)
    print(" -", summary_path)
    print(" -", json_path)

    print("\n[RESULT] Top by composite_score:")
    cols = [
        "candidate", "mae", "rmse", "direction_accuracy", "curve_mae",
        "delta_mae_vs_v12", "delta_rmse_vs_v12", "delta_dir_vs_v12",
        "composite_score",
    ]
    print(summary_df.sort_values("composite_score")[cols].head(10).to_string(index=False))

    print("\n[RESULT] Best by MAE:")
    print(summary_df.sort_values("mae")[cols].head(5).to_string(index=False))

    print("\n[RESULT] Best by direction_accuracy:")
    print(summary_df.sort_values("direction_accuracy", ascending=False)[cols].head(5).to_string(index=False))


if __name__ == "__main__":
    main()
