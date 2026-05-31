from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, RidgeClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--rolling-summary", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--cutoff-date", default="2025-05-20")
    return parser.parse_args()


def load_future_returns(db_path: Path, horizon: int) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        price = pd.read_sql_query(
            """
            SELECT ticker, trading_date, close
            FROM price_data
            ORDER BY ticker, trading_date
            """,
            conn,
        )
    finally:
        conn.close()

    price["ticker"] = price["ticker"].astype(str).str.upper()
    price["trading_date"] = pd.to_datetime(price["trading_date"], errors="coerce")
    price["close"] = pd.to_numeric(price["close"], errors="coerce")

    price = price.dropna(subset=["ticker", "trading_date", "close"]).copy()
    price = price.sort_values(["ticker", "trading_date"]).copy()

    price["future_close"] = price.groupby("ticker")["close"].shift(-horizon)
    price["target_date"] = price.groupby("ticker")["trading_date"].shift(-horizon)
    price["future_return"] = (price["future_close"] - price["close"]) / price["close"]

    out = price[["ticker", "trading_date", "target_date", "future_return"]].copy()
    out = out.rename(columns={"trading_date": "base_trading_date"})
    return out


def make_label(future_return: pd.Series, task: str) -> pd.Series:
    if task == "abs_sign":
        return (future_return > 0).astype(int)

    if task.startswith("action_"):
        # action_1p5 表示未来收益超过 1.5% 作为强正信号。
        threshold_text = task.replace("action_", "")
        threshold = float(threshold_text.replace("p", ".")) / 100.0
        return (future_return > threshold).astype(int)

    raise ValueError(f"Unsupported task: {task}")


def build_model(model_name: str):
    if model_name == "logreg":
        clf = LogisticRegression(
            max_iter=3000,
            class_weight="balanced",
            solver="lbfgs",
            random_state=42,
        )
    elif model_name == "ridge":
        clf = RidgeClassifier(
            class_weight="balanced",
            random_state=42,
        )
    else:
        raise ValueError(f"Unsupported model: {model_name}")

    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", clf),
        ]
    )


def get_score(model, X: pd.DataFrame) -> np.ndarray:
    # LogisticRegression has predict_proba; RidgeClassifier has decision_function.
    if hasattr(model.named_steps["model"], "predict_proba"):
        return model.predict_proba(X)[:, 1]

    if hasattr(model.named_steps["model"], "decision_function"):
        raw = model.decision_function(X)
        # Convert decision score to a smooth 0~1 pseudo probability for recommendation use.
        return 1.0 / (1.0 + np.exp(-raw))

    pred = model.predict(X)
    return pred.astype(float)


def evaluate(y_true, y_pred) -> dict:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
        "positive_rate_true": float(np.mean(y_true)),
        "positive_rate_pred": float(np.mean(y_pred)),
    }


def read_candidate_metrics(rolling_summary: Path, candidate: str) -> dict:
    if not rolling_summary.exists():
        return {}

    df = pd.read_csv(rolling_summary)
    row = df[df["candidate"] == candidate]
    if row.empty:
        return {}

    keep = [
        "candidate",
        "task",
        "horizon",
        "feature_set",
        "model",
        "folds",
        "mean_test_accuracy",
        "mean_test_macro_f1",
        "mean_above_baseline",
        "min_above_baseline",
        "mean_hc_test_coverage",
        "mean_hc_test_accuracy",
    ]
    keep = [c for c in keep if c in row.columns]
    return row.iloc[0][keep].to_dict()


def export_one(
    *,
    name: str,
    candidate: str,
    task: str,
    horizon: int,
    feature_set: str,
    model_name: str,
    dataset: pd.DataFrame,
    features: list[str],
    db_path: Path,
    rolling_summary: Path,
    output_root: Path,
    cutoff_date: str,
):
    out_dir = output_root / name
    out_dir.mkdir(parents=True, exist_ok=True)

    df = dataset.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper()
    df["base_trading_date"] = pd.to_datetime(df["base_trading_date"], errors="coerce")

    ret = load_future_returns(db_path, horizon=horizon)
    merged = df.merge(
        ret,
        on=["ticker", "base_trading_date"],
        how="left",
    )

    cutoff = pd.to_datetime(cutoff_date)
    merged = merged[
        merged["future_return"].notna()
        & merged["target_date"].notna()
        & (merged["target_date"] <= cutoff)
    ].copy()

    merged["label_final"] = make_label(merged["future_return"], task)

    missing_features = [f for f in features if f not in merged.columns]
    if missing_features:
        raise RuntimeError(f"Missing features for {candidate}: {missing_features}")

    X = merged[features].copy()
    y = merged["label_final"].astype(int).copy()

    model = build_model(model_name)
    model.fit(X, y)

    y_pred = model.predict(X)
    y_score = get_score(model, X)

    train_metrics = evaluate(y, y_pred)
    rolling_metrics = read_candidate_metrics(rolling_summary, candidate)

    joblib.dump(model, out_dir / "model.joblib")

    (out_dir / "feature_columns.json").write_text(
        json.dumps(features, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    label_config = {
        "candidate": candidate,
        "task": task,
        "horizon": horizon,
        "label_rule": (
            "abs_sign: label=1 if future_return_h > 0"
            if task == "abs_sign"
            else f"{task}: label=1 if future_return_h > {task.replace('action_', '').replace('p', '.')}%"
        ),
        "target_return_column_generated_from": "price_data.close shifted by horizon trading days",
        "cutoff_date": cutoff_date,
        "leakage_control": "Only rows whose target_date <= cutoff_date are used for final supervised training.",
    }
    (out_dir / "label_config.json").write_text(
        json.dumps(label_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    train_config = {
        "model_name": model_name,
        "candidate": candidate,
        "task": task,
        "horizon": horizon,
        "feature_set": feature_set,
        "training_dataset": str(DATASET_PATH),
        "training_db": str(db_path),
        "cutoff_date": cutoff_date,
        "train_rows": int(len(merged)),
        "ticker_count": int(merged["ticker"].nunique()),
        "base_date_range": [
            str(merged["base_trading_date"].min().date()),
            str(merged["base_trading_date"].max().date()),
        ],
        "target_date_range": [
            str(merged["target_date"].min().date()),
            str(merged["target_date"].max().date()),
        ],
        "feature_count": len(features),
        "features": features,
        "pipeline": [
            "SimpleImputer(strategy='median')",
            "StandardScaler()",
            f"{model_name}",
        ],
    }
    (out_dir / "train_config.json").write_text(
        json.dumps(train_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metrics = {
        "note": "train_metrics are fitted-sample diagnostics only; use rolling_validation_metrics for model selection.",
        "train_metrics": train_metrics,
        "rolling_validation_metrics": rolling_metrics,
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sample_row = merged.iloc[0]
    sample_input = {
        "ticker": sample_row["ticker"],
        "base_trading_date": str(sample_row["base_trading_date"].date()),
        "features": {f: float(sample_row[f]) if pd.notna(sample_row[f]) else None for f in features},
    }

    sample_X = pd.DataFrame([sample_input["features"]], columns=features)
    sample_pred = int(model.predict(sample_X)[0])
    sample_score = float(get_score(model, sample_X)[0])

    sample_output = {
        "candidate": candidate,
        "prediction_class": sample_pred,
        "prediction_score": sample_score,
        "recommendation_score": sample_score,
        "recommendation_level": (
            "strong_positive" if sample_score >= 0.65 else
            "positive" if sample_score >= 0.55 else
            "neutral" if sample_score >= 0.45 else
            "negative"
        ),
        "note": "This is a sample output for backend integration format validation.",
    }

    (out_dir / "sample_prediction_input.json").write_text(
        json.dumps(sample_input, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "sample_prediction_output.json").write_text(
        json.dumps(sample_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    readme = f"""# {candidate}

## Purpose

This directory contains the final deployable model artifact for Finsight.

## Candidate

- candidate: `{candidate}`
- task: `{task}`
- horizon: `{horizon}`
- feature_set: `{feature_set}`
- model: `{model_name}`

## Files

- `model.joblib`: sklearn pipeline, including imputer, scaler and classifier.
- `feature_columns.json`: ordered feature list required by the model.
- `label_config.json`: label definition and cutoff rule.
- `train_config.json`: final training configuration.
- `metrics.json`: rolling validation metrics and fitted-sample diagnostics.
- `sample_prediction_input.json`: example model input.
- `sample_prediction_output.json`: example model output.

## Data cutoff

Training uses only rows whose future target date is no later than `{cutoff_date}`.
Data after 2025-05-20 must be reserved for backtesting/out-of-sample use.

## Important

Large raw datasets, SQLite databases, raw news JSON, raw financial reports and API keys must not be committed to GitHub.
"""
    (out_dir / "model_readme.md").write_text(readme, encoding="utf-8")

    print("=" * 100)
    print("exported:", out_dir)
    print("train_rows:", len(merged))
    print("train_metrics:", train_metrics)
    print("rolling_metrics:", rolling_metrics)


def main():
    args = parse_args()

    global DATASET_PATH
    DATASET_PATH = Path(args.data_dir) / "dataset_h5_v1.csv"

    data_dir = Path(args.data_dir)
    db_path = Path(args.db)
    rolling_summary = Path(args.rolling_summary)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    dataset = pd.read_csv(DATASET_PATH)
    features = json.loads((data_dir / "feature_columns_h5_v1.json").read_text(encoding="utf-8"))

    # Copy original training feature/label files for traceability.
    shutil.copy(data_dir / "feature_columns_h5_v1.json", output_root / "source_feature_columns_h5_v1.json")
    shutil.copy(data_dir / "label_config_h5_v1.json", output_root / "source_label_config_h5_v1.json")

    configs = [
        {
            "name": "primary_abs_h15_market_ext_logreg",
            "candidate": "abs_h15_market_ext_logreg",
            "task": "abs_sign",
            "horizon": 15,
            "feature_set": "f0_market_ext",
            "model_name": "logreg",
        },
        {
            "name": "strong_action1p5_h10_market_ext_ridge",
            "candidate": "action1p5_h10_market_ext_ridge",
            "task": "action_1p5",
            "horizon": 10,
            "feature_set": "f0_market_ext",
            "model_name": "ridge",
        },
    ]

    for cfg in configs:
        export_one(
            dataset=dataset,
            features=features,
            db_path=db_path,
            rolling_summary=rolling_summary,
            output_root=output_root,
            cutoff_date=args.cutoff_date,
            **cfg,
        )

    manifest = {
        "final_model_delivery_root": str(output_root),
        "models": configs,
        "training_dataset": str(DATASET_PATH),
        "training_db": str(db_path),
        "rolling_summary": str(rolling_summary),
        "cutoff_date": args.cutoff_date,
    }
    (output_root / "delivery_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 100)
    print("FINAL MODEL DELIVERY ROOT:", output_root)


if __name__ == "__main__":
    main()
