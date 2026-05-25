"""Prediction API：单股预测、历史卡片、详情。"""

from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import User, Prediction
from app.schemas.prediction import PredictionRunRequest
from app.services.prediction_service import run_prediction, prediction_to_card, prediction_to_detail, get_prediction_for_user
from app.services.log_service import write_operation_log

router = APIRouter(prefix="/api/predictions", tags=["Prediction API"])


@router.post("/run")
def run(req: PredictionRunRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = run_prediction(db, user.id, req)
    write_operation_log(db, user.id, "PredictionService", "run_prediction", "success", f"prediction generated for {req.ticker}, prediction_id={data['prediction_id']}")
    return ok(data, "prediction generated")


@router.get("/history")
def history(
    ticker: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Prediction).filter(Prediction.user_id == user.id)
    if ticker:
        q = q.filter(Prediction.ticker == ticker.upper())
    if start_time:
        q = q.filter(Prediction.prediction_time >= start_time)
    if end_time:
        q = q.filter(Prediction.prediction_time <= end_time)
    total = q.count()
    rows = q.order_by(Prediction.prediction_time.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"items": [prediction_to_card(db, p) for p in rows], "total": total, "page": page, "page_size": page_size})


@router.get("/{prediction_id}")
def detail(prediction_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    is_admin = user.role and user.role.role_name == "admin"
    pred = get_prediction_for_user(db, prediction_id, user.id, is_admin)
    return ok(prediction_to_detail(db, pred))
