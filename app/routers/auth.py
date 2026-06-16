"""Auth API：注册、登录、当前用户。"""

from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.exceptions import AppException, AUTH_FAILED, PASSWORD_NOT_MATCH
from app.core.responses import ok
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.session import get_db
from app.models.all_models import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.log_service import write_operation_log
from app.services.user_service import ensure_username_available, get_role

router = APIRouter(prefix="/api/auth", tags=["Auth API"])


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if req.password_sha256.lower() != req.confirm_password_sha256.lower():
        raise AppException(PASSWORD_NOT_MATCH, "两次输入的密码不一致。", 400)

    ensure_username_available(db, req.username)

    role = get_role(db, "user")

    user = User(
        username=req.username,
        password_hash=get_password_hash(req.password_sha256),
        role_id=role.id,
        status="active",
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    write_operation_log(
        db,
        user.id,
        "AuthService",
        "register",
        "success",
        f"user registered: {user.username}",
    )

    return ok(
        {
            "user_id": user.id,
            "username": user.username,
            "role": user.role.role_name,
            "status": user.status,
            "created_at": user.created_at.isoformat(),
        },
        "register success",
    )


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .filter(User.username == req.username, User.status != "deleted")
        .first()
    )

    if not user or not verify_password(req.password_sha256, user.password_hash):
        raise AppException(
            AUTH_FAILED,
            "用户名或密码错误。",
            status.HTTP_401_UNAUTHORIZED,
        )

    if user.status == "disabled":
        raise AppException(
            AUTH_FAILED,
            "用户已被禁用。",
            status.HTTP_403_FORBIDDEN,
        )

    user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_access_token(
        user.id,
        {
            "role": user.role.role_name,
        },
    )

    write_operation_log(
        db,
        user.id,
        "AuthService",
        "login",
        "success",
        "login success",
    )

    return ok(
        {
            "token": token,
            "user_id": user.id,
            "username": user.username,
            "role": user.role.role_name,
            "status": user.status,
        },
        "login success",
    )


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return ok(
        {
            "user_id": current_user.id,
            "username": current_user.username,
            "role": current_user.role.role_name,
            "status": current_user.status,
            "last_login_at": current_user.last_login_at.isoformat()
            if current_user.last_login_at
            else None,
        }
    )