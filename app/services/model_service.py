"""模型加载与推理服务。

职责：
1. 从 model_versions 表中查找 active 模型；
2. 加载 joblib 模型；
3. 读取 feature_columns.json；
4. 缓存模型，避免每次请求重复加载；
5. 提供分类、辅助强信号、回归预测方法。

重要修复：
- 不能把 SQLAlchemy ORM 的 ModelVersion 实例直接长期缓存在 _MODEL_CACHE 中。
- 预测接口中间可能执行 data pipeline，期间 db.commit() 会导致 ORM 实例过期；
- 后续访问 loaded.version.id / loaded.version.version_name 时，会触发 DetachedInstanceError。
- 本文件改为把 ModelVersion 复制成普通 dataclass CachedModelVersion，再放入缓存。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app.models.all_models import ModelVersion


_MODEL_CACHE: dict[str, "LoadedModel"] = {}


@dataclass(frozen=True)
class CachedModelVersion:
    """脱离 SQLAlchemy Session 的模型版本快照。

    只保存业务代码常用字段。因为它不是 ORM 对象，所以不会出现
    DetachedInstanceError，也不会被 db.commit() 过期。
    """

    id: int
    version_name: str
    model_type: str
    horizon_days: int | None
    model_path: str
    is_active: bool = True
    description: str | None = None
    metrics_json: Any | None = None
    created_at: Any | None = None


@dataclass
class LoadedModel:
    version: CachedModelVersion
    model: object
    feature_columns: list[str]
    model_dir: Path


def _snapshot_version(version: ModelVersion) -> CachedModelVersion:
    """把 ORM ModelVersion 复制为普通 dataclass。"""
    return CachedModelVersion(
        id=int(version.id),
        version_name=str(version.version_name),
        model_type=str(version.model_type),
        horizon_days=version.horizon_days,
        model_path=str(version.model_path),
        is_active=bool(version.is_active),
        description=getattr(version, "description", None),
        metrics_json=getattr(version, "metrics_json", None),
        created_at=getattr(version, "created_at", None),
    )


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
    """加载 active 模型，并返回 model_match_status。

    注意：
    - 缓存中保存的是 CachedModelVersion，不是 ORM ModelVersion；
    - 避免 data pipeline 中 db.commit() 后访问模型版本字段时报 DetachedInstanceError。
    """
    version_orm, match_status = find_active_model(db, model_type, forecast_days)

    if version_orm is None:
        raise RuntimeError(f"No active model found: model_type={model_type}, forecast_days={forecast_days}")

    if not version_orm.model_path:
        raise RuntimeError(f"Model path is empty for version: {version_orm.version_name}")

    # 在当前 Session 仍有效时，把 ORM 字段全部复制出来。
    version = _snapshot_version(version_orm)
    model_path = Path(version.model_path)

    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    cache_key = f"{model_type}:{version.id}:{version.version_name}:{model_path}"

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


def _sigmoid(value: float) -> float:
    """稳定 sigmoid。"""
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def predict_classifier(loaded: LoadedModel, feature_dict: dict) -> dict:
    """主分类模型预测，返回 v5 API 需要的概率字段。

    兼容两类模型：
    - 三分类模型：classes 为 down / neutral / up 或 0 / 1 / 2；
    - 二分类模型：当前 v1.2 主分类模型实际为 down/up 二分类，neutral 置为 0。
    """
    x = make_feature_frame(feature_dict, loaded.feature_columns)

    pred_raw = loaded.model.predict(x)[0]
    pred_id = int(pred_raw)

    proba = loaded.model.predict_proba(x)[0]
    classes = list(getattr(loaded.model, "classes_", []))

    # 如果是 sklearn Pipeline，classes_ 通常在最后一步分类器上。
    if not classes and hasattr(loaded.model, "steps"):
        try:
            classes = list(loaded.model.steps[-1][1].classes_)
        except Exception:
            classes = []

    # 默认值。
    prob_down = 0.0
    prob_neutral = 0.0
    prob_up = 0.0

    if len(proba) == 3:
        # 三分类：约定 0=down, 1=neutral, 2=up。
        for cls, p in zip(classes or [0, 1, 2], proba):
            cls_text = str(cls).lower()
            if cls_text in {"0", "down", "-1"}:
                prob_down = float(p)
            elif cls_text in {"1", "neutral"}:
                prob_neutral = float(p)
            elif cls_text in {"2", "up"}:
                prob_up = float(p)

    elif len(proba) == 2:
        # v1.2 二分类：约定 0=down, 1=up，neutral=0。
        if classes:
            for cls, p in zip(classes, proba):
                cls_text = str(cls).lower()
                if cls_text in {"0", "down", "-1"}:
                    prob_down = float(p)
                elif cls_text in {"1", "up"}:
                    prob_up = float(p)
                else:
                    # 不认识的二分类标签，按顺序兜底。
                    pass

            if prob_down == 0.0 and prob_up == 0.0:
                prob_down = float(proba[0])
                prob_up = float(proba[1])
        else:
            prob_down = float(proba[0])
            prob_up = float(proba[1])

        prob_neutral = 0.0

    else:
        # 非标准输出，兜底成 hard label。
        if pred_id <= 0:
            prob_down = 1.0
        elif pred_id == 1:
            prob_up = 1.0
        else:
            prob_neutral = 1.0

    if prob_up >= prob_down and prob_up >= prob_neutral:
        label = "up"
    elif prob_down >= prob_up and prob_down >= prob_neutral:
        label = "down"
    else:
        label = "neutral"

    return {
        "predicted_label": label,
        "prob_down": float(prob_down),
        "prob_neutral": float(prob_neutral),
        "prob_up": float(prob_up),
        "predicted_growth_prob": float(prob_up),
    }


def predict_aux_classifier(loaded: LoadedModel, feature_dict: dict) -> dict:
    """辅助强信号模型预测。

    v1.2 辅助模型可能只有 decision_function，因此用 sigmoid 转为 0~1 分数。
    """
    x = make_feature_frame(feature_dict, loaded.feature_columns)

    pred = int(loaded.model.predict(x)[0])

    if hasattr(loaded.model, "decision_function"):
        decision_value = float(loaded.model.decision_function(x)[0])
        score = _sigmoid(decision_value)
        output_type = "decision_function_sigmoid"
    elif hasattr(loaded.model, "predict_proba"):
        proba = loaded.model.predict_proba(x)[0]
        decision_value = None
        score = float(proba[-1])
        output_type = "predict_proba"
    else:
        decision_value = None
        score = float(pred)
        output_type = "hard_label"

    return {
        "model_version": loaded.version.version_name,
        "strong_signal_pred": pred,
        "strong_signal_score": float(score),
        "decision_value": decision_value,
        "model_output_type": output_type,
    }


def predict_regressor(loaded: LoadedModel, feature_dict: dict) -> list[float]:
    """回归模型预测，返回未来 1~forecast_days 的收益率路径。"""
    x = make_feature_frame(feature_dict, loaded.feature_columns)
    pred = loaded.model.predict(x)[0]
    return [float(v) for v in pred]


def clear_model_cache() -> None:
    """清空模型缓存。"""
    _MODEL_CACHE.clear()
