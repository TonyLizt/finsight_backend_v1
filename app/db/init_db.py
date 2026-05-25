"""初始化数据库表与基础角色。"""

from sqlalchemy.orm import Session

from app.db.session import Base, engine, SessionLocal
from app.models import all_models  # noqa: F401  # 导入模型以注册 metadata
from app.models.all_models import Role


def init_db() -> None:
    """创建所有表，并确保基础角色存在。"""
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        for role_name, desc in [("user", "普通用户"), ("admin", "管理员")]:
            exists = db.query(Role).filter(Role.role_name == role_name).first()
            if not exists:
                db.add(Role(role_name=role_name, description=desc))
        db.commit()
    finally:
        db.close()
