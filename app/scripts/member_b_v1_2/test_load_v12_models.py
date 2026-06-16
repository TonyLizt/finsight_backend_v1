#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Finsight / 智融洞察 Member B v1.2 模型加载测试脚本

作用：
1. 加载主分类模型 finsight_cls_abs_h15_v1.2；
2. 加载辅助强信号模型 finsight_cls_action1p5_h10_v1.2；
3. 加载回归价格路径模型 finsight_reg_return_path_v1.2；
4. 使用各模型目录下的 sample_prediction_input.json 做一次本地预测；
5. 验证回归模型输出 shape 是否为 (1, 5)。

运行方式：
PYTHONPATH=. python app/scripts/member_b_v1_2/test_load_v12_models.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]

PRIMARY_CLS_DIR = ROOT / "artifacts/models/classifier/finsight_cls_abs_h15_v1.2"
AUX_CLS_DIR = ROOT / "artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2"
REG_DIR = ROOT / "artifacts/models/regressor/finsight_reg_return_path_v1.2"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_sample_x(model_dir: Path) -> pd.DataFrame:
    feature_columns: List[str] = load_json(model_dir / "feature_columns.json")
    sample: Dict[str, Any] = load_json(model_dir / "sample_prediction_input.json")

    if "features" not in sample:
        raise KeyError(f"{model_dir}/sample_prediction_input.json 缺少 features 字段")

    x = pd.DataFrame([sample["features"]])[feature_columns]
    return x


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def test_primary_classifier() -> None:
    print("\n===== 主分类模型：finsight_cls_abs_h15_v1.2 =====")
    model_dir = PRIMARY_CLS_DIR
    model = joblib.load(model_dir / "model.joblib")
    x = build_sample_x(model_dir)

    pred = model.predict(x)
    print("predict:", pred.tolist())

    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(x)
        print("predict_proba shape:", proba.shape)
        print("predict_proba:", proba.tolist())
    elif hasattr(model, "decision_function"):
        raw = model.decision_function(x)
        print("decision_function:", np.asarray(raw).tolist())
        print("sigmoid pseudo-score:", sigmoid(raw).tolist())
    else:
        print("score: 当前模型无 predict_proba / decision_function")


def test_aux_classifier() -> None:
    print("\n===== 辅助强信号模型：finsight_cls_action1p5_h10_v1.2 =====")
    model_dir = AUX_CLS_DIR
    model = joblib.load(model_dir / "model.joblib")
    x = build_sample_x(model_dir)

    pred = model.predict(x)
    print("predict:", pred.tolist())

    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(x)
        print("predict_proba shape:", proba.shape)
        print("predict_proba:", proba.tolist())
    elif hasattr(model, "decision_function"):
        raw = model.decision_function(x)
        print("decision_function:", np.asarray(raw).tolist())
        print("sigmoid pseudo-score:", sigmoid(raw).tolist())
    else:
        print("score: 当前模型无 predict_proba / decision_function")


def test_regressor() -> None:
    print("\n===== 回归价格路径模型：finsight_reg_return_path_v1.2 =====")
    model_dir = REG_DIR
    model = joblib.load(model_dir / "model.joblib")
    x = build_sample_x(model_dir)

    pred = model.predict(x)
    print("pred shape:", pred.shape)
    print("pred return path:", pred.tolist())

    if tuple(pred.shape) != (1, 5):
        raise RuntimeError(f"回归模型输出 shape 异常，期望 (1, 5)，实际 {pred.shape}")

    sample = load_json(model_dir / "sample_prediction_input.json")
    current_price = sample.get("current_price")
    if current_price is not None:
        price_path = [float(current_price * (1.0 + r)) for r in pred[0]]
        print("current_price:", current_price)
        print("pred price path:", price_path)


def main() -> None:
    print("ROOT:", ROOT)

    for d in [PRIMARY_CLS_DIR, AUX_CLS_DIR, REG_DIR]:
        if not d.exists():
            raise FileNotFoundError(f"模型目录不存在：{d}")
        if not (d / "model.joblib").exists():
            raise FileNotFoundError(f"缺少模型文件：{d / 'model.joblib'}")

    test_primary_classifier()
    test_aux_classifier()
    test_regressor()

    print("\n[DONE] 三套 v1.2 模型均已成功加载并完成 sample 预测。")


if __name__ == "__main__":
    main()
