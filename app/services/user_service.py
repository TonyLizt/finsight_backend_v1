"""用户和管理员用户管理服务。"""

from datetime import datetime
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AppException,
    CANNOT_DELETE_SELF,
    CANNOT_DISABLE_SELF,
    CANNOT_REMOVE_LAST_ADMIN,
    PASSWORD_NOT_MATCH,
    USER_NOT_FOUND,
    USERNAME_EXISTS,
)
from app.core.security import get_password_hash, is_valid_password_format
from app.models.all_models import Role, User


def get_role(db: Session, role_name: str) -> Role:
    role = db.query(Role).filter(Role.role_name == role_name).first()
    if not role:
        role = Role(role_name=role_name, description=role_name)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppException(USER_NOT_FOUND, "User not found", 404)
    return user


def ensure_username_available(db: Session, username: str, exclude_user_id: int | None = None) -> None:
    q = db.query(User).filter(User.username == username)
    if exclude_user_id is not None:
        q = q.filter(User.id != exclude_user_id)
    if q.first():
        raise AppException(USERNAME_EXISTS, "该用户名已存在。", 400)


def count_active_admins(db: Session) -> int:
    return (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.role_name == "admin", User.status == "active")
        .count()
    )


def assert_not_last_admin(db: Session, user: User) -> None:
    if user.role and user.role.role_name == "admin" and user.status == "active" and count_active_admins(db) <= 1:
        raise AppException(CANNOT_REMOVE_LAST_ADMIN, "不能删除、禁用或降级最后一个管理员。", 400)


def update_user_status(db: Session, target: User, current_admin: User, new_status: str) -> User:
    if target.id == current_admin.id and new_status == "disabled":
        raise AppException(CANNOT_DISABLE_SELF, "管理员不能禁用自己。", 400)
    if new_status == "disabled":
        assert_not_last_admin(db, target)
    target.status = new_status
    db.commit()
    db.refresh(target)
    return target


def update_user_role(db: Session, target: User, new_role_name: str) -> User:
    if target.role and target.role.role_name == "admin" and new_role_name == "user":
        assert_not_last_admin(db, target)
    target.role_id = get_role(db, new_role_name).id
    db.commit()
    db.refresh(target)
    return target


def reset_password(db: Session, target: User, new_password: str, confirm_password: str) -> User:
    if new_password != confirm_password:
        raise AppException(PASSWORD_NOT_MATCH, "两次输入的密码不一致。", 400)
    if not is_valid_password_format(new_password):
        raise AppException("INVALID_PASSWORD_FORMAT", "密码格式不符合要求。", 400)
    target.password_hash = get_password_hash(new_password)
    db.commit()
    db.refresh(target)
    return target


def soft_delete_user(db: Session, target: User, current_admin: User) -> User:
    if target.id == current_admin.id:
        raise AppException(CANNOT_DELETE_SELF, "管理员不能删除自己。", 400)
    assert_not_last_admin(db, target)
    target.status = "deleted"
    target.deleted_at = datetime.utcnow()
    db.commit()
    db.refresh(target)
    return target
