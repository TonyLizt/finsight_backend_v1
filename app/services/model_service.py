"""模型加载与推理服务。

职责：
1. 从 model_versions 表中查找 active 模型；
2. 加载 joblib 模型；
3. 读取 feature_columns.json；
4. 缓存模型，避免每次请求重复加载；
5. 提供分类与回归预测方法。

重要说明：
- 业务接口使用 forecast_days；
- model_versions 表字段叫 horizon_days；
- 二者在这里含义等价。
- 原 04 文档设计是 down / neutral / up 三分类；
- B 同学 v1.2 主分类模型是二分类，predict_proba 输出通常是 [prob_down, prob_up]；
- 因此前端/API 仍然返回 prob_down / prob_neutral / prob_up，但对二分类模型会做兼容映射。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app.models.all_models import ModelVersion


# 进程内模型缓存。
# key: "{model_type}:{version_name}:{model_path}"
# value: LoadedModel
_MODEL_CACHE: dict[str, "LoadedModel"] = {}


@dataclass
class LoadedModel:
    """已加载模型对象。"""

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

    查找规则：
    1. 优先查找 model_type 一致、horizon_days = forecast_days、is_active=true 的模型；
    2. 如果没有精确匹配，则在同类型 active 模型中选择 horizon_days 最接近的模型；
    3. 返回模型记录和匹配状态。

    返回 match_status：
    - exact_match：周期完全匹配；
    - nearest_active_model：使用最近周期模型；
    - model_not_found：没有可用模型。
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


def _resolve_model_path(model_path_text: str) -> Path:
    """解析模型文件路径。

    数据库中的 model_path 可能是：
    - artifacts/models/.../model.joblib
    - /app/artifacts/models/.../model.joblib

    Docker 中工作目录通常是 /app，但为了稳妥，这里同时尝试：
    1. 原始路径；
    2. 当前工作目录 + 相对路径；
    3. /app + 相对路径。
    """
    raw_path = Path(model_path_text)

    candidates: list[Path] = []

    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(raw_path)
        candidates.append(Path.cwd() / raw_path)
        candidates.append(Path("/app") / raw_path)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # 保留原始路径用于错误提示。
    return raw_path


def load_active_model(
    db: Session,
    model_type: str,
    forecast_days: int,
) -> tuple[LoadedModel, str]:
    """加载 active 模型，并返回 model_match_status。

    返回：
    - LoadedModel：模型对象、特征列、模型版本信息；
    - match_status：exact_match / nearest_active_model。
    """
    version, match_status = find_active_model(db, model_type, forecast_days)

    if version is None:
        raise RuntimeError(
            f"No active model found: model_type={model_type}, forecast_days={forecast_days}"
        )

    if not version.model_path:
        raise RuntimeError(f"Model path is empty for version: {version.version_name}")

    model_path = _resolve_model_path(version.model_path)

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

    if not isinstance(feature_columns, list) or not feature_columns:
        raise RuntimeError(f"Invalid feature_columns.json: {feature_path}")

    model = joblib.load(model_path)

    loaded = LoadedModel(
        version=version,
        model=model,
        feature_columns=[str(c) for c in feature_columns],
        model_dir=model_dir,
    )

    _MODEL_CACHE[cache_key] = loaded
    return loaded, match_status


def _to_float(value: Any, default: float = 0.0) -> float:
    """将特征值安全转换为 float。"""
    if value is None:
        return default

    try:
        text_value = str(value).strip()
        if text_value == "" or text_value.lower() in {"none", "null", "nan"}:
            return default
        return float(text_value)
    except (TypeError, ValueError):
        return default


def make_feature_frame(feature_dict: dict, feature_columns: list[str]) -> pd.DataFrame:
    """按 feature_columns 顺序构造单样本 DataFrame。

    注意：
    - 模型训练和推理必须使用完全一致的特征顺序；
    - 如果缺少模型要求的字段，直接抛出明确错误；
    - 值统一转 float，避免 MySQL JSON / Decimal / 字符串等类型导致 sklearn 报错。
    """
    missing = [c for c in feature_columns if c not in feature_dict]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    row = [_to_float(feature_dict.get(c)) for c in feature_columns]

    return pd.DataFrame(
        [row],
        columns=feature_columns,
    )


def _get_model_classes(model: object) -> list[Any] | None:
    """读取 sklearn 模型类别标签。

    对 Pipeline：
    - 新版 sklearn 通常可以直接从 pipeline.classes_ 读取；
    - 如果没有，则尝试读取最后一个 estimator 的 classes_。
    """
    classes = getattr(model, "classes_", None)

    if classes is not None:
        return list(classes)

    steps = getattr(model, "steps", None)
    if steps:
        last_estimator = steps[-1][1]
        classes = getattr(last_estimator, "classes_", None)
        if classes is not None:
            return list(classes)

    return None


def _normalize_three_probs(prob_down: float, prob_neutral: float, prob_up: float) -> tuple[float, float, float]:
    """归一化 down / neutral / up 三个概率，确保总和为 1。"""
    prob_down = max(0.0, float(prob_down))
    prob_neutral = max(0.0, float(prob_neutral))
    prob_up = max(0.0, float(prob_up))

    total = prob_down + prob_neutral + prob_up

    if total <= 0:
        return 1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0

    return prob_down / total, prob_neutral / total, prob_up / total


def _adapt_binary_proba_to_three_class(
    model: object,
    proba: list[float],
) -> tuple[float, float, float]:
    """将 v1.2 二分类概率适配为 API 需要的三分类概率。

    B 同学 v1.2 主分类模型是二分类：
    - 通常 classes_ = [0, 1]
    - 0 表示 down / not-up
    - 1 表示 up

    原 API 仍需要：
    - prob_down
    - prob_neutral
    - prob_up

    这里的 neutral 是根据 up/down 概率差距估计出来的：
    - up/down 越接近，模型越不确定，neutral 越高；
    - up/down 差距越大，neutral 越低。
    """
    if len(proba) != 2:
        raise RuntimeError(f"Binary adapter expects proba size 2, got {len(proba)}")

    classes = _get_model_classes(model)

    if classes is not None and len(classes) == 2:
        class_to_prob: dict[int, float] = {}
        for cls, prob in zip(classes, proba):
            try:
                class_to_prob[int(cls)] = float(prob)
            except (TypeError, ValueError):
                pass

        # 默认约定：0 = down / not-up，1 = up。
        prob_down = class_to_prob.get(0, float(proba[0]))
        prob_up = class_to_prob.get(1, float(proba[1]))
    else:
        prob_down = float(proba[0])
        prob_up = float(proba[1])

    confidence_gap = abs(prob_up - prob_down)

    # gap=0 时 neutral 最大；gap>=0.5 时 neutral 接近 0。
    prob_neutral = max(0.0, 1.0 - confidence_gap * 2.0)

    return _normalize_three_probs(prob_down, prob_neutral, prob_up)


def predict_classifier(loaded: LoadedModel, feature_dict: dict) -> dict:
    """执行分类模型预测，返回 v5 API 需要的概率字段。

    兼容两类模型：
    1. 三分类模型：predict_proba 输出 [prob_down, prob_neutral, prob_up]；
    2. B 同学 v1.2 二分类模型：predict_proba 输出 [prob_down, prob_up]。

    返回字段保持与原 Prediction API 兼容：
    - predicted_label
    - prob_down
    - prob_neutral
    - prob_up
    - predicted_growth_prob
    - model_output_type
    """
    x = make_feature_frame(feature_dict, loaded.feature_columns)

    if not hasattr(loaded.model, "predict"):
        raise RuntimeError(f"Classifier model does not support predict: {loaded.version.version_name}")

    if not hasattr(loaded.model, "predict_proba"):
        raise RuntimeError(
            f"Classifier model does not support predict_proba: {loaded.version.version_name}"
        )

    pred_raw = loaded.model.predict(x)[0]
    proba_raw = loaded.model.predict_proba(x)[0]
    proba = [float(v) for v in proba_raw]

    if len(proba) == 2:
        prob_down, prob_neutral, prob_up = _adapt_binary_proba_to_three_class(loaded.model, proba)
        model_output_type = "binary_adapted"

    elif len(proba) == 3:
        prob_down, prob_neutral, prob_up = _normalize_three_probs(
            float(proba[0]),
            float(proba[1]),
            float(proba[2]),
        )
        model_output_type = "three_class"

    else:
        raise RuntimeError(
            f"Unsupported classifier predict_proba output size: {len(proba)} "
            f"for model {loaded.version.version_name}"
        )

    probs = {
        "down": prob_down,
        "neutral": prob_neutral,
        "up": prob_up,
    }

    # 原始模型预测如果是三分类，可参考 pred_raw；但为了和二分类适配后的 neutral 保持一致，
    # 最终 predicted_label 统一由三类概率最大值决定。
    predicted_label = max(probs, key=probs.get)

    return {
        "predicted_label": predicted_label,
        "prob_down": prob_down,
        "prob_neutral": prob_neutral,
        "prob_up": prob_up,
        "predicted_growth_prob": prob_up,
        "model_output_type": model_output_type,
        "raw_pred": str(pred_raw),
    }



def _sigmoid(value: float) -> float:
    """数值稳定的 sigmoid，用于 RidgeClassifier decision_function 伪概率。"""
    import math

    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def predict_aux_classifier(loaded: LoadedModel, feature_dict: dict) -> dict:
    """辅助强信号模型预测。

    B 同学 v1.2 的 ``finsight_cls_action1p5_h10_v1.2`` 是 RidgeClassifier，
    它没有 predict_proba，但提供 decision_function。这里按交付说明使用
    ``decision_function + sigmoid`` 生成 pseudo-score。

    返回：
    - strong_signal_pred：模型原始类别预测；
    - strong_signal_score：sigmoid(decision_function)，不是严格概率；
    - model_output_type：decision_function_sigmoid。
    """
    x = make_feature_frame(feature_dict, loaded.feature_columns)

    if not hasattr(loaded.model, "predict"):
        raise RuntimeError(f"Aux classifier does not support predict: {loaded.version.version_name}")

    pred = int(loaded.model.predict(x)[0])

    if hasattr(loaded.model, "decision_function"):
        raw_score = loaded.model.decision_function(x)
        if hasattr(raw_score, "tolist"):
            raw_score = raw_score.tolist()
        if isinstance(raw_score, list):
            decision_value = raw_score[0]
            if isinstance(decision_value, list):
                decision_value = decision_value[0]
        else:
            decision_value = raw_score
        decision_value = float(decision_value)
        strong_signal_score = _sigmoid(decision_value)
    elif hasattr(loaded.model, "predict_proba"):
        proba = loaded.model.predict_proba(x)[0]
        strong_signal_score = float(proba[-1])
        decision_value = None
    else:
        decision_value = None
        strong_signal_score = float(pred)

    return {
        "strong_signal_pred": pred,
        "strong_signal_score": strong_signal_score,
        "decision_value": decision_value,
        "model_output_type": "decision_function_sigmoid",
        "model_version": loaded.version.version_name,
    }

def predict_regressor(loaded: LoadedModel, feature_dict: dict) -> list[float]:
    """执行回归模型预测，返回未来 1~forecast_days 的收益率路径。

    B 同学 v1.2 回归模型输出形状通常是：
    - (1, 5)，表示未来 1~5 日收益率；
    - 或一维数组，表示同样含义。

    本函数统一返回 ``list[float]``。
    """
    x = make_feature_frame(feature_dict, loaded.feature_columns)
    pred = loaded.model.predict(x)

    # sklearn 多输出回归通常返回 ndarray shape=(1, n_outputs)
    if hasattr(pred, "tolist"):
        pred = pred.tolist()

    if isinstance(pred, list) and pred and isinstance(pred[0], list):
        values = pred[0]
    else:
        values = pred

    return [float(v) for v in values]


def clear_model_cache() -> None:
    """清空进程内模型缓存。"""
    _MODEL_CACHE.clear()
