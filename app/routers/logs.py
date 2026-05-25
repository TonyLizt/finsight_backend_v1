"""Log API：管理员查询操作日志。"""

from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_admin
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import OperationLog, User

router = APIRouter(prefix="/api/logs", tags=["Log API"])


@router.get("")
def list_logs(
    module: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    q = db.query(OperationLog)
    if module:
        q = q.filter(OperationLog.module == module)
    if status:
        q = q.filter(OperationLog.status == status)
    if user_id:
        q = q.filter(OperationLog.user_id == user_id)
    if start_time:
        q = q.filter(OperationLog.created_at >= start_time)
    if end_time:
        q = q.filter(OperationLog.created_at <= end_time)
    total = q.count()
    rows = q.order_by(OperationLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for r in rows:
        username = None
        if r.user_id:
            u = db.query(User).filter(User.id == r.user_id).first()
            username = u.username if u else None
        items.append(
            {
                "log_id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "user_id": r.user_id,
                "username": username,
                "module": r.module,
                "action": r.action,
                "status": r.status,
                "message": r.message,
            }
        )
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})
