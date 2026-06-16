#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.3 API 对齐修复脚本 v2。"""

from __future__ import annotations

from pathlib import Path


MODELS_ROUTER = '"""Model Info API：查询当前启用模型，只读。"""\n\nfrom fastapi import APIRouter, Depends\nfrom sqlalchemy import or_\nfrom sqlalchemy.orm import Session\n\nfrom app.core.deps import get_current_user\nfrom app.core.responses import ok\nfrom app.db.session import get_db\nfrom app.models.all_models import ModelVersion, User\n\nrouter = APIRouter(prefix="/api/models", tags=["Model Info API"])\n\n\ndef _model_to_dict(m: ModelVersion | None) -> dict | None:\n    if not m:\n        return None\n\n    return {\n        "version_name": m.version_name,\n        "model_type": m.model_type,\n        "algorithm": m.algorithm,\n        "horizon_days": m.horizon_days,\n        "accuracy": m.accuracy,\n        "f1_score": m.f1_score,\n        "mae": m.mae,\n        "rmse": m.rmse,\n        "feature_version": m.feature_version,\n        "model_path": m.model_path,\n        "is_active": m.is_active,\n        "created_at": m.created_at.isoformat() if m.created_at else None,\n    }\n\n\ndef _find_primary_classifier(db: Session) -> ModelVersion | None:\n    return (\n        db.query(ModelVersion)\n        .filter(\n            ModelVersion.model_type == "classifier",\n            ModelVersion.is_active.is_(True),\n            ~ModelVersion.version_name.contains("action1p5"),\n        )\n        .order_by(ModelVersion.created_at.desc())\n        .first()\n    )\n\n\ndef _find_aux_classifier(db: Session) -> ModelVersion | None:\n    """查找辅助强信号模型。"""\n    return (\n        db.query(ModelVersion)\n        .filter(\n            or_(\n                ModelVersion.model_type.in_(["aux_classifier", "auxiliary_classifier", "classifier_signal"]),\n                ModelVersion.version_name.contains("action1p5"),\n                ModelVersion.version_name.contains("strong_signal"),\n            )\n        )\n        .order_by(ModelVersion.is_active.desc(), ModelVersion.created_at.desc())\n        .first()\n    )\n\n\ndef _find_regressor(db: Session) -> ModelVersion | None:\n    return (\n        db.query(ModelVersion)\n        .filter(ModelVersion.model_type == "regressor", ModelVersion.is_active.is_(True))\n        .order_by(ModelVersion.created_at.desc())\n        .first()\n    )\n\n\n@router.get("/active")\ndef active_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):\n    classifier = _find_primary_classifier(db)\n    aux_classifier = _find_aux_classifier(db)\n    regressor = _find_regressor(db)\n\n    return ok(\n        {\n            "classifier": _model_to_dict(classifier),\n            "aux_classifier": _model_to_dict(aux_classifier),\n            "regressor": _model_to_dict(regressor),\n        }\n    )\n'


def patch_models_router() -> None:
    path = Path("app/routers/models.py")
    original = path.read_text(encoding="utf-8")

    if original == MODELS_ROUTER:
        print("No change needed: app/routers/models.py")
        return

    backup = path.with_suffix(".py.bak_v13_aux_model_v2")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    path.write_text(MODELS_ROUTER, encoding="utf-8")
    print("Updated app/routers/models.py")


def patch_stocks_router() -> None:
    path = Path("app/routers/stocks.py")
    text = path.read_text(encoding="utf-8")
    original = text

    import_line = "from app.services.news_detail_fetch_service import enrich_news_detail_if_needed\n"
    if import_line not in text:
        marker = "from app.services.stock_service import"
        idx = text.find(marker)
        if idx >= 0:
            end = text.find("\n\n", idx)
            if end >= 0:
                text = text[:end + 2] + import_line + text[end + 2:]
            else:
                text = import_line + text
        else:
            text = import_line + text

    needle = "    n = get_news_or_404(db, news_id)\n"
    insert = (
        "    n = get_news_or_404(db, news_id)\n"
        "    # v1.3：新闻详情页按需抓取原文。抓取失败不影响基础详情返回。\n"
        "    n = enrich_news_detail_if_needed(db, n, include_html=include_html)\n"
    )

    if "enrich_news_detail_if_needed(db, n, include_html=include_html)" not in text:
        if needle not in text:
            raise RuntimeError("Could not find news_detail get_news_or_404 line in app/routers/stocks.py")
        text = text.replace(needle, insert, 1)

    if text != original:
        backup = path.with_suffix(".py.bak_v13_news_detail_v2")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        print("Updated app/routers/stocks.py")
    else:
        print("No change needed: app/routers/stocks.py")


def patch_prediction_service() -> None:
    path = Path("app/services/prediction_service.py")
    text = path.read_text(encoding="utf-8")
    original = text

    if "summary = _normalize_news_summary(pred.sentiment_summary_json or {}) or {}" not in text:
        old = '    path = (pred.predicted_prices_json or {}).get("path", [])\n    return {\n'
        new = (
            '    path = (pred.predicted_prices_json or {}).get("path", [])\n'
            '    summary = _normalize_news_summary(pred.sentiment_summary_json or {}) or {}\n'
            '    return {\n'
        )
        if old in text:
            text = text.replace(old, new, 1)
        else:
            print("Warning: could not patch prediction_to_card summary insertion automatically")

    text = text.replace(
        '"news_start_time": (pred.sentiment_summary_json or {}).get("news_start_time"),',
        '"news_start_time": summary.get("news_start_time"),',
    )
    text = text.replace(
        '"news_end_time": (pred.sentiment_summary_json or {}).get("news_end_time"),',
        '"news_end_time": summary.get("news_end_time"),',
    )

    old_saved = (
        "    if saved is not None:\n"
        "        data[\"saved\"] = saved\n"
        "    return data\n"
    )
    new_saved = (
        "    # 详情接口读取的是已保存预测记录，因此 saved 默认返回 true。\n"
        "    data[\"saved\"] = True if saved is None else bool(saved)\n"
        "    return data\n"
    )
    if old_saved in text:
        text = text.replace(old_saved, new_saved, 1)
    elif 'data["saved"] = True if saved is None else bool(saved)' not in text:
        print("Warning: could not patch prediction_to_detail saved field automatically")

    if text != original:
        backup = path.with_suffix(".py.bak_v13_prediction_fields_v2")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        print("Updated app/services/prediction_service.py")
    else:
        print("No change needed: app/services/prediction_service.py")


def main() -> None:
    patch_models_router()
    patch_stocks_router()
    patch_prediction_service()
    print("Patch finished. Then run: docker compose restart backend")


if __name__ == "__main__":
    main()
