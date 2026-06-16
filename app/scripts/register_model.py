"""注册模型版本到 model_versions 表。

说明：
- 业务接口字段使用 forecast_days；
- model_versions 表字段当前叫 horizon_days；
- 这里将 forecast_days 写入 horizon_days。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.all_models import ModelVersion


def register_model(
    version_name: str,
    model_type: str,
    algorithm: str,
    forecast_days: int,
    model_path: str,
    feature_version: str,
    accuracy: float | None = None,
    f1_score: float | None = None,
    mae: float | None = None,
    rmse: float | None = None,
    is_active: bool = True,
) -> dict:
    db = SessionLocal()
    try:
        if is_active:
            old_active = (
                db.query(ModelVersion)
                .filter(
                    ModelVersion.model_type == model_type,
                    ModelVersion.horizon_days == forecast_days,
                    ModelVersion.is_active.is_(True),
                )
                .all()
            )
            for old in old_active:
                old.is_active = False

        existing = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.version_name == version_name,
                ModelVersion.model_type == model_type,
            )
            .first()
        )

        if existing:
            existing.algorithm = algorithm
            existing.horizon_days = forecast_days
            existing.model_path = model_path
            existing.feature_version = feature_version
            existing.accuracy = accuracy
            existing.f1_score = f1_score
            existing.mae = mae
            existing.rmse = rmse
            existing.is_active = is_active
            action = "updated"
            model_id = existing.id
        else:
            model = ModelVersion(
                version_name=version_name,
                model_type=model_type,
                algorithm=algorithm,
                horizon_days=forecast_days,
                model_path=model_path,
                feature_version=feature_version,
                accuracy=accuracy,
                f1_score=f1_score,
                mae=mae,
                rmse=rmse,
                is_active=is_active,
                created_at=datetime.utcnow(),
            )
            db.add(model)
            db.flush()
            action = "inserted"
            model_id = model.id

        db.commit()

        return {
            "action": action,
            "id": model_id,
            "version_name": version_name,
            "model_type": model_type,
            "forecast_days": forecast_days,
            "model_path": model_path,
            "is_active": is_active,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--model-type", required=True, choices=["classifier", "regressor"])
    parser.add_argument("--algorithm", required=True)
    parser.add_argument("--forecast-days", type=int, default=5)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--feature-version", default="feature_h5_v1")
    parser.add_argument("--accuracy", type=float, default=None)
    parser.add_argument("--f1-score", type=float, default=None)
    parser.add_argument("--mae", type=float, default=None)
    parser.add_argument("--rmse", type=float, default=None)
    parser.add_argument("--inactive", action="store_true")

    args = parser.parse_args()

    if not Path(args.model_path).exists():
        raise FileNotFoundError(f"model_path does not exist: {args.model_path}")

    init_db()

    result = register_model(
        version_name=args.version_name,
        model_type=args.model_type,
        algorithm=args.algorithm,
        forecast_days=args.forecast_days,
        model_path=args.model_path,
        feature_version=args.feature_version,
        accuracy=args.accuracy,
        f1_score=args.f1_score,
        mae=args.mae,
        rmse=args.rmse,
        is_active=not args.inactive,
    )

    print(result)


if __name__ == "__main__":
    main()
