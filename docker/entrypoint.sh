#!/usr/bin/env bash
set -e

# 等待 MySQL 真正可连接。depends_on 只能保证容器健康检查通过，
# 这里再用 SQLAlchemy 连接一次，避免初始化数据库时偶发失败。
python - <<'PY'
import os
import time
from sqlalchemy import create_engine, text

url = os.getenv("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL is not set")

last_error = None
for i in range(60):
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Database is ready.")
        break
    except Exception as exc:
        last_error = exc
        print(f"Waiting for database... {i + 1}/60: {exc}")
        time.sleep(2)
else:
    raise SystemExit(f"Database is not ready: {last_error}")
PY

# 第一版项目没有 Alembic，app 启动时会 create_all。
# 这里先运行 seed_demo，用于创建表和写入演示数据；脚本本身做了基本幂等处理。
if [ "${RUN_SEED:-1}" = "1" ]; then
    echo "Running demo seed script..."
    python -m app.scripts.seed_demo
fi

# 启动 FastAPI。生产环境可以把 --reload 去掉；这里默认不用 reload，适合 Docker。
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
