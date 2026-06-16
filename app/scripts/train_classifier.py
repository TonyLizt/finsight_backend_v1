"""训练 Finsight XGBoost 分类趋势预测模型。

输入：
  data/training/dataset_h5_v1.csv
  data/training/feature_columns_h5_v1.json
  data/training/label_config_h5_v1.json

输出：
  artifacts/models/classifier/xgb_cls_h5_v1.0/
    model.joblib
    feature_columns.json
    label_config.json
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
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    classification_report,
)
from xgboost import XGBClassifier


LABEL_MAPPING = {
    "down": 0,
    "neutral": 1,
    "up": 2,
}

REVERSE_LABEL_MAPPING = {
    0: "down",
    1: "neutral",
    2: "up",
}


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


def evaluate_split(name: str, y_true, y_pred, label_names: list[str]) -> dict:
    return {
        "split": name,
        "sample_count": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_recall": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=[0, 1, 2]).tolist(),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=[0, 1, 2],
            target_names=label_names,
            zero_division=0,
            output_dict=True,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/training/dataset_h5_v1.csv")
    parser.add_argument("--features", default="data/training/feature_columns_h5_v1.json")
    parser.add_argument("--label-config", default="data/training/label_config_h5_v1.json")
    parser.add_argument("--version-name", default="xgb_cls_h5_v1.0")
    parser.add_argument("--output-root", default="artifacts/models/classifier")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    feature_path = Path(args.features)
    label_config_path = Path(args.label_config)

    out_dir = Path(args.output_root) / args.version_name
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(dataset_path)
    feature_columns = json.loads(feature_path.read_text(encoding="utf-8"))
    label_config = json.loads(label_config_path.read_text(encoding="utf-8"))

    required_cols = set(feature_columns + ["label", "base_trading_date", "ticker"])
    missing_cols = sorted(required_cols - set(df.columns))
    if missing_cols:
        raise ValueError(f"Dataset missing columns: {missing_cols}")

    df = df.dropna(subset=feature_columns + ["label"]).copy()
    df["label_id"] = df["label"].map(LABEL_MAPPING)

    if df["label_id"].isna().any():
        bad = df[df["label_id"].isna()]["label"].unique().tolist()
        raise ValueError(f"Unknown labels: {bad}")

    train_df, val_df, test_df = time_split(df)

    X_train = train_df[feature_columns]
    y_train = train_df["label_id"].astype(int)

    X_val = val_df[feature_columns]
    y_val = val_df["label_id"].astype(int)

    X_test = test_df[feature_columns]
    y_test = test_df["label_id"].astype(int)

    model = XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=1.0,
        random_state=42,
        eval_metric="mlogloss",
        tree_method="hist",
        n_jobs=4,
    )

    model.fit(X_train, y_train)

    label_names = ["down", "neutral", "up"]

    val_pred = model.predict(X_val)
    test_pred = model.predict(X_test)
    test_proba = model.predict_proba(X_test)

    metrics = {
        "version_name": args.version_name,
        "model_type": "classifier",
        "algorithm": "XGBoost",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "feature_count": len(feature_columns),
        "label_mapping": LABEL_MAPPING,
        "reverse_label_mapping": REVERSE_LABEL_MAPPING,
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
        "label_distribution": {
            "all": df["label"].value_counts().to_dict(),
            "train": train_df["label"].value_counts().to_dict(),
            "val": val_df["label"].value_counts().to_dict(),
            "test": test_df["label"].value_counts().to_dict(),
        },
        "validation": evaluate_split("validation", y_val, val_pred, label_names),
        "test": evaluate_split("test", y_test, test_pred, label_names),
        "probability_order": ["down", "neutral", "up"],
        "api_probability_mapping": {
            "prob_down": "predict_proba[:, 0]",
            "prob_neutral": "predict_proba[:, 1]",
            "prob_up": "predict_proba[:, 2]",
        },
        "sample_test_prediction": {
            "true_label": REVERSE_LABEL_MAPPING[int(y_test.iloc[0])] if len(y_test) else None,
            "predicted_label": REVERSE_LABEL_MAPPING[int(test_pred[0])] if len(test_pred) else None,
            "prob_down": float(test_proba[0][0]) if len(test_proba) else None,
            "prob_neutral": float(test_proba[0][1]) if len(test_proba) else None,
            "prob_up": float(test_proba[0][2]) if len(test_proba) else None,
        },
    }

    joblib.dump(model, out_dir / "model.joblib")
    shutil.copyfile(feature_path, out_dir / "feature_columns.json")
    shutil.copyfile(label_config_path, out_dir / "label_config.json")

    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=json_safe),
        encoding="utf-8",
    )

    train_config = {
        "version_name": args.version_name,
        "model_type": "classifier",
        "algorithm": "XGBoost",
        "forecast_days": label_config.get("forecast_days", 5),
        "feature_version": "feature_h5_v1",
        "hyperparameters": model.get_params(),
        "dataset_path": str(dataset_path),
        "feature_columns_path": str(feature_path),
        "label_config_path": str(label_config_path),
        "output_dir": str(out_dir),
        "note": "News sentiment features are placeholders in v1 and set to 0.",
    }

    (out_dir / "train_config.json").write_text(
        json.dumps(train_config, ensure_ascii=False, indent=2, default=json_safe),
        encoding="utf-8",
    )

    readme = f"""# {args.version_name}

Finsight XGBoost classifier for 5-trading-day trend prediction.

Outputs:
- prob_down
- prob_neutral
- prob_up
- predicted_label

Probability order from model.predict_proba:
[down, neutral, up]

Feature version:
feature_h5_v1

Note:
News sentiment features are placeholders in v1 and set to 0.
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    print({
        "model_path": str(out_dir / "model.joblib"),
        "metrics_path": str(out_dir / "metrics.json"),
        "test_accuracy": metrics["test"]["accuracy"],
        "test_macro_f1": metrics["test"]["macro_f1"],
        "test_weighted_f1": metrics["test"]["weighted_f1"],
    })


if __name__ == "__main__":
    main()
