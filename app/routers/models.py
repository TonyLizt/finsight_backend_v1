"""Model Info API：查询当前启用模型，只读。"""

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import ModelVersion, User

router = APIRouter(prefix="/api/models", tags=["Model Info API"])


def _model_to_dict(m: ModelVersion | None) -> dict | None:
    if not m:
        return None

    return {
        "version_name": m.version_name,
        "model_type": m.model_type,
        "algorithm": m.algorithm,
        "horizon_days": m.horizon_days,
        "accuracy": m.accuracy,
        "f1_score": m.f1_score,
        "mae": m.mae,
        "rmse": m.rmse,
        "feature_version": m.feature_version,
        "model_path": m.model_path,
        "is_active": m.is_active,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _find_primary_classifier(db: Session) -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .filter(
            ModelVersion.model_type == "classifier",
            ModelVersion.is_active.is_(True),
            ~ModelVersion.version_name.contains("action1p5"),
        )
        .order_by(ModelVersion.created_at.desc())
        .first()
    )


def _find_aux_classifier(db: Session) -> ModelVersion | None:
    """查找辅助强信号模型。"""
    return (
        db.query(ModelVersion)
        .filter(
            or_(
                ModelVersion.model_type.in_(["aux_classifier", "auxiliary_classifier", "classifier_signal"]),
                ModelVersion.version_name.contains("action1p5"),
                ModelVersion.version_name.contains("strong_signal"),
            )
        )
        .order_by(ModelVersion.is_active.desc(), ModelVersion.created_at.desc())
        .first()
    )


def _find_regressor(db: Session) -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .filter(ModelVersion.model_type == "regressor", ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.created_at.desc())
        .first()
    )


@router.get("/active")
def active_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    classifier = _find_primary_classifier(db)
    aux_classifier = _find_aux_classifier(db)
    regressor = _find_regressor(db)

    return ok(
        {
            "classifier": _model_to_dict(classifier),
            "aux_classifier": _model_to_dict(aux_classifier),
            "regressor": _model_to_dict(regressor),
        }
    )
