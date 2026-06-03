"""Finsight FastAPI 后端第一版。

覆盖成员 C 负责的后端主框架、数据库模型、用户管理、预测记录、日志、模型信息、回测接口壳等部分。
大模型报告与真实回测引擎当前为占位实现，后续可在 services 中替换。
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import AppException, INTERNAL_ERROR
from app.core.responses import fail
from app.db.init_db import init_db
from app.routers import auth, admin_users, watchlist, stocks, predictions, backtest, logs, models, crawler
from app.services.daily_refresh_service import start_daily_refresh_scheduler

app = FastAPI(title=settings.project_name, version="1.0.0")

# GUI 客户端一般是本地应用；第一版先放开 CORS，部署时可按域名收紧。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    # 第一版使用 create_all 快速初始化。正式项目建议改用 Alembic 迁移。
    init_db()
    # 可选启动每日数据自动补全调度器。
    # 通过 ENABLE_DAILY_AUTO_REFRESH=1 控制，默认不开启，避免开发环境意外频繁访问外部行情源。
    start_daily_refresh_scheduler()


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"error_code": INTERNAL_ERROR, "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=fail(detail.get("error_code", INTERNAL_ERROR), detail.get("message", "error")))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # 避免将内部堆栈暴露给前端。开发期可临时打印日志。
    return JSONResponse(status_code=500, content=fail(INTERNAL_ERROR, "服务端内部错误。"))


@app.get("/health")
def health_check():
    return {"success": True, "data": {"status": "ok"}, "message": "ok"}


app.include_router(auth.router)
app.include_router(watchlist.router)
app.include_router(stocks.router)
app.include_router(predictions.router)
app.include_router(backtest.router)
app.include_router(admin_users.router)
app.include_router(logs.router)
app.include_router(models.router)
app.include_router(crawler.router)
