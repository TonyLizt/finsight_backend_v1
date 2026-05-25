"""Model Info API：查询当前启用模型，只读。"""

from fastapi import APIRouter, Depends
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
        "algorithm": m.algorithm,
        "horizon_days": m.horizon_days,
        "accuracy": m.accuracy,
        "f1_score": m.f1_score,
        "mae": m.mae,
        "rmse": m.rmse,
        "feature_version": m.feature_version,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/active")
def active_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    classifier = db.query(ModelVersion).filter(ModelVersion.model_type == "classifier", ModelVersion.is_active.is_(True)).order_by(ModelVersion.created_at.desc()).first()
    regressor = db.query(ModelVersion).filter(ModelVersion.model_type == "regressor", ModelVersion.is_active.is_(True)).order_by(ModelVersion.created_at.desc()).first()
    return ok({"classifier": _model_to_dict(classifier), "regressor": _model_to_dict(regressor)})
