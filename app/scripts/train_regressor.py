"""训练 Finsight XGBoost 回归价格路径模型。

输入：
  data/training/dataset_h5_v1.csv
  data/training/feature_columns_h5_v1.json

输出：
  artifacts/models/regressor/xgb_reg_h5_v1.0/
    model.joblib
    feature_columns.json
    target_config.json
    metrics.json
    train_config.json
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, mean_absolute_percentage_error
from sklearn.multioutput import MultiOutputRegressor
from xgboost import XGBRegressor


TARGET_COLUMNS = [
    "target_return_d1",
    "target_return_d2",
    "target_return_d3",
    "target_return_d4",
    "target_return_d5",
]


def json_safe(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def time_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """按 base_trading_date 时间顺序切分，不 shuffle。"""
    df = df.copy()
    df["base_trading_date"] = pd.to_datetime(df["base_trading_date"])
    unique_dates = sorted(df["base_trading_date"].unique())

    n = len(unique_dates)
    train_end_idx = int(n * 0.70)
    val_end_idx = int(n * 0.85)

    train_dates = set(unique_dates[:train_end_idx])
    val_dates = set(unique_dates[train_end_idx:val_end_idx])
    test_dates = set(unique_dates[val_end_idx:])

    train_df = df[df["base_trading_date"].isin(train_dates)].copy()
    val_df = df[df["base_trading_date"].isin(val_dates)].copy()
    test_df = df[df["base_trading_date"].isin(test_dates)].copy()

    return train_df, val_df, test_df


def evaluate_regression(name: str, y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mape = mean_absolute_percentage_error(y_true, y_pred)

    direction_accuracy = float((np.sign(y_true) == np.sign(y_pred)).mean())

    per_day = {}
    for i, col in enumerate(TARGET_COLUMNS):
        per_day[col] = {
            "mae": float(mean_absolute_error(y_true[:, i], y_pred[:, i])),
            "rmse": float(np.sqrt(mean_squared_error(y_true[:, i], y_pred[:, i]))),
            "direction_accuracy": float((np.sign(y_true[:, i]) == np.sign(y_pred[:, i])).mean()),
        }

    curve_mae = float(np.mean(np.abs(y_true - y_pred)))

    return {
        "split": name,
        "sample_count": int(len(y_true)),
        "mae": float(mae),
        "rmse": float(rmse),
        "mape": float(mape),
        "direction_accuracy": direction_accuracy,
        "curve_mae": curve_mae,
        "per_day": per_day,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/training/dataset_h5_v1.csv")
    parser.add_argument("--features", default="data/training/feature_columns_h5_v1.json")
    parser.add_argument("--version-name", default="xgb_reg_h5_v1.0")
    parser.add_argument("--output-root", default="artifacts/models/regressor")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    feature_path = Path(args.features)

    out_dir = Path(args.output_root) / args.version_name
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(dataset_path)
    feature_columns = json.loads(feature_path.read_text(encoding="utf-8"))

    required_cols = set(feature_columns + TARGET_COLUMNS + ["base_trading_date", "ticker", "close"])
    missing_cols = sorted(required_cols - set(df.columns))
    if missing_cols:
        raise ValueError(f"Dataset missing columns: {missing_cols}")

    df = df.dropna(subset=feature_columns + TARGET_COLUMNS).copy()

    train_df, val_df, test_df = time_split(df)

    X_train = train_df[feature_columns]
    y_train = train_df[TARGET_COLUMNS].values

    X_val = val_df[feature_columns]
    y_val = val_df[TARGET_COLUMNS].values

    X_test = test_df[feature_columns]
    y_test = test_df[TARGET_COLUMNS].values

    base_model = XGBRegressor(
        objective="reg:squarederror",
        n_estimators=300,
        max_depth=4,
        learning_rate=0.04,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=1.0,
        random_state=42,
        tree_method="hist",
        n_jobs=4,
    )

    model = MultiOutputRegressor(base_model)
    model.fit(X_train, y_train)

    val_pred = model.predict(X_val)
    test_pred = model.predict(X_test)

    metrics = {
        "version_name": args.version_name,
        "model_type": "regressor",
        "algorithm": "MultiOutputRegressor(XGBoost)",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "feature_count": len(feature_columns),
        "target_columns": TARGET_COLUMNS,
        "dataset": str(dataset_path),
        "split": {
            "method": "time_order_70_15_15_no_shuffle",
            "train_sample_count": int(len(train_df)),
            "val_sample_count": int(len(val_df)),
            "test_sample_count": int(len(test_df)),
            "train_date_min": str(train_df["base_trading_date"].min().date()),
            "train_date_max": str(train_df["base_trading_date"].max().date()),
            "val_date_min": str(val_df["base_trading_date"].min().date()),
            "val_date_max": str(val_df["base_trading_date"].max().date()),
            "test_date_min": str(test_df["base_trading_date"].min().date()),
            "test_date_max": str(test_df["base_trading_date"].max().date()),
        },
        "validation": evaluate_regression("validation", y_val, val_pred),
        "test": evaluate_regression("test", y_test, test_pred),
        "sample_test_prediction": {
            "ticker": str(test_df.iloc[0]["ticker"]) if len(test_df) else None,
            "base_trading_date": str(test_df.iloc[0]["base_trading_date"].date()) if len(test_df) else None,
            "current_price": float(test_df.iloc[0]["close"]) if len(test_df) else None,
            "true_returns": y_test[0].tolist() if len(y_test) else None,
            "predicted_returns": test_pred[0].tolist() if len(test_pred) else None,
        },
    }

    joblib.dump(model, out_dir / "model.joblib")
    shutil.copyfile(feature_path, out_dir / "feature_columns.json")

    target_config = {
        "forecast_days": 5,
        "target_type": "future_return_path",
        "target_columns": TARGET_COLUMNS,
        "formula": "target_return_di = (close[t+i] - close[t]) / close[t]",
        "price_restore_formula": "predicted_price_i = current_price * (1 + predicted_return_i)",
        "output_format": {
            "day_index": "1..5",
            "target_date": "generated by trading calendar",
            "predicted_return": "model output",
            "predicted_price": "current_price * (1 + predicted_return)",
            "lower_bound": "v1 can use volatility approximation",
            "upper_bound": "v1 can use volatility approximation"
        },
    }

    (out_dir / "target_config.json").write_text(
        json.dumps(target_config, ensure_ascii=False, indent=2, default=json_safe),
        encoding="utf-8",
    )

    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=json_safe),
        encoding="utf-8",
    )

    train_config = {
        "version_name": args.version_name,
        "model_type": "regressor",
        "algorithm": "MultiOutputRegressor(XGBoost)",
        "forecast_days": 5,
        "feature_version": "feature_h5_v1",
        "base_hyperparameters": base_model.get_params(),
        "dataset_path": str(dataset_path),
        "feature_columns_path": str(feature_path),
        "target_columns": TARGET_COLUMNS,
        "output_dir": str(out_dir),
        "note": "News sentiment features are placeholders in v1 and set to 0.",
    }

    (out_dir / "train_config.json").write_text(
        json.dumps(train_config, ensure_ascii=False, indent=2, default=json_safe),
        encoding="utf-8",
    )

    readme = f"""# {args.version_name}

Finsight XGBoost regressor for 5-trading-day price path prediction.

Model output:
- target_return_d1
- target_return_d2
- target_return_d3
- target_return_d4
- target_return_d5

Restore price path:
predicted_price_i = current_price * (1 + predicted_return_i)

Feature version:
feature_h5_v1

Note:
News sentiment features are placeholders in v1 and set to 0.
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    print({
        "model_path": str(out_dir / "model.joblib"),
        "metrics_path": str(out_dir / "metrics.json"),
        "test_mae": metrics["test"]["mae"],
        "test_rmse": metrics["test"]["rmse"],
        "test_direction_accuracy": metrics["test"]["direction_accuracy"],
        "test_curve_mae": metrics["test"]["curve_mae"],
    })


if __name__ == "__main__":
    main()
