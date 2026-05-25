"""鉴权相关依赖。"""

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.exceptions import AppException, AUTH_FAILED, PERMISSION_DENIED, USER_NOT_FOUND
from app.core.security import decode_token
from app.db.session import get_db
from app.models.all_models import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 中解析当前用户。"""
    if not credentials:
        raise AppException(AUTH_FAILED, "Missing authorization token", status.HTTP_401_UNAUTHORIZED)
    try:
        payload = decode_token(credentials.credentials)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise AppException(AUTH_FAILED, "Invalid or expired token", status.HTTP_401_UNAUTHORIZED)

    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.status == "deleted":
        raise AppException(USER_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)
    if user.status == "disabled":
        raise AppException(PERMISSION_DENIED, "User is disabled", status.HTTP_403_FORBIDDEN)
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求当前用户为管理员。"""
    if not current_user.role or current_user.role.role_name != "admin":
        raise AppException(PERMISSION_DENIED, "Admin permission required", status.HTTP_403_FORBIDDEN)
    return current_user
