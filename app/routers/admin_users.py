"""Admin User API：管理员用户管理。"""

from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_admin
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import User, Role, Prediction, BacktestRun, Watchlist, OperationLog
from app.schemas.admin import UpdateStatusRequest, UpdateRoleRequest, UpdateUsernameRequest, ResetPasswordRequest, DeleteUserRequest
from app.services.user_service import get_user_or_404, ensure_username_available, update_user_status, update_user_role, reset_password, soft_delete_user
from app.services.log_service import write_operation_log

router = APIRouter(prefix="/api/admin/users", tags=["Admin User API"])


def _user_counts(db: Session, user_id: int) -> dict:
    return {
        "prediction_count": db.query(Prediction).filter(Prediction.user_id == user_id).count(),
        "backtest_count": db.query(BacktestRun).filter(BacktestRun.user_id == user_id).count(),
        "watchlist_count": db.query(Watchlist).filter(Watchlist.user_id == user_id).count(),
    }


@router.get("")
def list_users(
    keyword: str | None = None,
    role: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    q = db.query(User).join(Role, User.role_id == Role.id)
    if keyword:
        q = q.filter(User.username.ilike(f"%{keyword}%"))
    if role:
        q = q.filter(Role.role_name == role)
    if status:
        q = q.filter(User.status == status)
    total = q.count()
    users = q.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for u in users:
        counts = _user_counts(db, u.id)
        items.append(
            {
                "user_id": u.id,
                "username": u.username,
                "role": u.role.role_name,
                "status": u.status,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "prediction_count": counts["prediction_count"],
                "backtest_count": counts["backtest_count"],
            }
        )
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/{user_id}")
def get_user_detail(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    u = get_user_or_404(db, user_id)
    counts = _user_counts(db, u.id)
    recent = (
        db.query(OperationLog)
        .filter(OperationLog.user_id == user_id)
        .order_by(OperationLog.created_at.desc())
        .limit(10)
        .all()
    )
    return ok(
        {
            "user_id": u.id,
            "username": u.username,
            "role": u.role.role_name,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            **counts,
            "recent_operations": [
                {
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "module": r.module,
                    "action": r.action,
                    "status": r.status,
                    "message": r.message,
                }
                for r in recent
            ],
        }
    )


@router.put("/{user_id}/status")
def update_status(user_id: int, req: UpdateStatusRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    target = get_user_or_404(db, user_id)
    update_user_status(db, target, admin, req.status)
    write_operation_log(db, admin.id, "AdminUserService", "update_status", "success", f"user_id={user_id}, status={req.status}, reason={req.reason}")
    return ok({"user_id": target.id, "username": target.username, "status": target.status, "updated_at": datetime.utcnow().isoformat()}, "user status updated")


@router.put("/{user_id}/role")
def update_role(user_id: int, req: UpdateRoleRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    target = get_user_or_404(db, user_id)
    update_user_role(db, target, req.role)
    write_operation_log(db, admin.id, "AdminUserService", "update_role", "success", f"user_id={user_id}, role={req.role}, reason={req.reason}")
    return ok({"user_id": target.id, "username": target.username, "role": target.role.role_name, "updated_at": datetime.utcnow().isoformat()}, "user role updated")


@router.put("/{user_id}/username")
def update_username(user_id: int, req: UpdateUsernameRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    target = get_user_or_404(db, user_id)
    old = target.username
    ensure_username_available(db, req.username, exclude_user_id=target.id)
    target.username = req.username
    db.commit()
    db.refresh(target)
    write_operation_log(db, admin.id, "AdminUserService", "update_username", "success", f"user_id={user_id}, old={old}, new={req.username}, reason={req.reason}")
    return ok({"user_id": target.id, "old_username": old, "new_username": target.username, "updated_at": datetime.utcnow().isoformat()}, "username updated")

@router.put("/{user_id}/password")
def reset_user_password(
    user_id: int,
    req: ResetPasswordRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    target = get_user_or_404(db, user_id)

    reset_password(
        db,
        target,
        req.new_password_sha256,
        req.confirm_password_sha256,
    )

    write_operation_log(
        db,
        admin.id,
        "AdminUserService",
        "reset_password",
        "success",
        f"user_id={user_id}, force_logout={req.force_logout}, reason={req.reason}",
    )

    return ok(
        {
            "user_id": target.id,
            "username": target.username,
            "password_updated": True,
            "force_logout": req.force_logout,
            "updated_at": datetime.utcnow().isoformat(),
        },
        "user password reset",
    )


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    hard_delete: bool = False,
    req: DeleteUserRequest | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    target = get_user_or_404(db, user_id)
    if hard_delete:
        # 课程项目不推荐物理删除；第一版保留能力但实际仍建议软删除。
        db.delete(target)
        db.commit()
    else:
        soft_delete_user(db, target, admin)
    write_operation_log(db, admin.id, "AdminUserService", "delete_user", "success", f"user_id={user_id}, hard_delete={hard_delete}, reason={req.reason if req else None}")
    return ok({"user_id": user_id, "username": target.username, "deleted": True, "hard_delete": hard_delete, "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None}, "user deleted")
