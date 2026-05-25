"""日志服务。"""

from sqlalchemy.orm import Session

from app.models.all_models import OperationLog


def write_operation_log(
    db: Session,
    user_id: int | None,
    module: str,
    action: str,
    status: str,
    message: str,
) -> None:
    """写入操作日志。日志写入失败不应影响主业务。"""
    try:
        db.add(OperationLog(user_id=user_id, module=module, action=action, status=status, message=message))
        db.commit()
    except Exception:
        db.rollback()
