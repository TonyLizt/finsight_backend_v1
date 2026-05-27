"""模型加载与推理服务。

职责：
1. 从 model_versions 表中查找 active 模型；
2. 加载 joblib 模型；
3. 读取 feature_columns.json；
4. 缓存模型，避免每次请求重复加载；
5. 提供分类与回归预测方法。

注意：
- 业务接口使用 forecast_days；
- model_versions 表字段叫 horizon_days；
- 二者在这里含义等价。
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app.models.all_models import ModelVersion


_MODEL_CACHE: dict[str, "LoadedModel"] = {}


@dataclass
class LoadedModel:
    version: ModelVersion
    model: object
    feature_columns: list[str]
    model_dir: Path


def find_active_model(
    db: Session,
    model_type: str,
    forecast_days: int,
) -> tuple[ModelVersion | None, str]:
    """查找 active 模型。

    优先精确匹配 horizon_days = forecast_days；
    如果没有精确匹配，则找同类型 active 模型中 horizon_days 最接近的模型。
    """
    exact = (
        db.query(ModelVersion)
        .filter(
            ModelVersion.model_type == model_type,
            ModelVersion.horizon_days == forecast_days,
            ModelVersion.is_active.is_(True),
        )
        .first()
    )

    if exact:
        return exact, "exact_match"

    active_models = (
        db.query(ModelVersion)
        .filter(
            ModelVersion.model_type == model_type,
            ModelVersion.is_active.is_(True),
        )
        .all()
    )

    if not active_models:
        return None, "model_not_found"

    nearest = min(
        active_models,
        key=lambda m: abs((m.horizon_days or forecast_days) - forecast_days),
    )
    return nearest, "nearest_active_model"


def load_active_model(
    db: Session,
    model_type: str,
    forecast_days: int,
) -> tuple[LoadedModel, str]:
    """加载 active 模型，并返回 model_match_status。"""
    version, match_status = find_active_model(db, model_type, forecast_days)

    if version is None:
        raise RuntimeError(f"No active model found: model_type={model_type}, forecast_days={forecast_days}")

    if not version.model_path:
        raise RuntimeError(f"Model path is empty for version: {version.version_name}")

    model_path = Path(version.model_path)

    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    cache_key = f"{model_type}:{version.version_name}:{model_path}"

    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key], match_status

    model_dir = model_path.parent
    feature_path = model_dir / "feature_columns.json"

    if not feature_path.exists():
        raise FileNotFoundError(f"feature_columns.json not found: {feature_path}")

    feature_columns = json.loads(feature_path.read_text(encoding="utf-8"))
    model = joblib.load(model_path)

    loaded = LoadedModel(
        version=version,
        model=model,
        feature_columns=feature_columns,
        model_dir=model_dir,
    )

    _MODEL_CACHE[cache_key] = loaded
    return loaded, match_status


def make_feature_frame(feature_dict: dict, feature_columns: list[str]) -> pd.DataFrame:
    """按 feature_columns 顺序构造单样本 DataFrame。"""
    missing = [c for c in feature_columns if c not in feature_dict]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    return pd.DataFrame(
        [[feature_dict[c] for c in feature_columns]],
        columns=feature_columns,
    )


def predict_classifier(loaded: LoadedModel, feature_dict: dict) -> dict:
    """分类模型预测，返回 v5 API 需要的概率字段。"""
    x = make_feature_frame(feature_dict, loaded.feature_columns)

    pred_id = int(loaded.model.predict(x)[0])
    proba = loaded.model.predict_proba(x)[0]

    id_to_label = {
        0: "down",
        1: "neutral",
        2: "up",
    }

    return {
        "predicted_label": id_to_label[pred_id],
        "prob_down": float(proba[0]),
        "prob_neutral": float(proba[1]),
        "prob_up": float(proba[2]),
        "predicted_growth_prob": float(proba[2]),
    }


def predict_regressor(loaded: LoadedModel, feature_dict: dict) -> list[float]:
    """回归模型预测，返回未来 1~forecast_days 的收益率路径。"""
    x = make_feature_frame(feature_dict, loaded.feature_columns)
    pred = loaded.model.predict(x)[0]
    return [float(v) for v in pred]


def clear_model_cache() -> None:
    _MODEL_CACHE.clear()
