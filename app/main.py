"""Finsight FastAPI 后端主入口。

当前版本：v1.3 Data Pipeline Integration

本文件负责：
1. 创建 FastAPI 应用实例；
2. 注册 CORS 中间件；
3. 初始化数据库表结构；
4. 启动可选的每日数据自动补全调度器；
5. 注册各业务模块 Router；
6. 统一处理业务异常和未知异常。

当前后端已覆盖：
- 用户注册 / 登录 / JWT 鉴权；
- 管理员用户管理；
- 股票搜索、详情、新闻、情绪摘要；
- 自选股管理；
- v1.2 三个模型的预测链路；
- 预测历史与预测详情；
- 股票基础库同步；
- 每日数据补全任务；
- v1.3 统一数据链路 Data Pipeline API；
- 回测 API 路由壳。

说明：
- 真实新闻 LLM 深度分析、完整逐日回测引擎、前端回测动画数据接入等内容放到后续 v1.4 完善。
- 当前数据库初始化仍使用 create_all 快速建表，正式生产环境建议改为 Alembic 迁移。
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import AppException, INTERNAL_ERROR
from app.core.responses import fail
from app.db.init_db import init_db
from app.routers import (
    auth,
    admin_users,
    watchlist,
    stocks,
    predictions,
    backtest,
    logs,
    models,
    crawler,
)
from app.routers.data_pipeline import router as data_pipeline_router
from app.services.daily_refresh_service import start_daily_refresh_scheduler


app = FastAPI(
    title=settings.project_name,
    version="1.3.0",
)


# GUI 客户端一般是本地应用。
# 当前课程项目阶段先放开 CORS，方便 PyQt / Web 前端联调。
# 如果后续部署到公网，应按实际前端域名收紧 allow_origins。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """应用启动初始化逻辑。

    1. 初始化数据库表结构；
    2. 按环境变量决定是否启动每日数据自动补全调度器。

    每日自动补全调度器由以下环境变量控制：
    - ENABLE_DAILY_AUTO_REFRESH=1 时启动；
    - ENABLE_DAILY_AUTO_REFRESH=0 或未配置时不启动。

    这样可以避免开发环境或接口测试时频繁访问外部行情 API。
    """
    init_db()
    start_daily_refresh_scheduler()


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """统一处理项目内部主动抛出的业务异常。"""
    detail = exc.detail if isinstance(exc.detail, dict) else {
        "error_code": INTERNAL_ERROR,
        "message": str(exc.detail),
    }

    return JSONResponse(
        status_code=exc.status_code,
        content=fail(
            detail.get("error_code", INTERNAL_ERROR),
            detail.get("message", "error"),
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """统一处理未捕获异常。

    注意：
    - 不把 Python 堆栈直接返回给前端，避免泄露服务端内部信息；
    - 开发调试时如需查看具体错误，应使用：
      docker compose logs --tail=200 backend
    """
    return JSONResponse(
        status_code=500,
        content=fail(INTERNAL_ERROR, "服务端内部错误。"),
    )


@app.get("/health")
def health_check():
    """健康检查接口。"""
    return {
        "success": True,
        "data": {
            "status": "ok",
        },
        "message": "ok",
    }


# =========================
# Router registration
# =========================
# 基础认证与用户侧功能
app.include_router(auth.router)
app.include_router(watchlist.router)

# 股票、预测、回测相关功能
app.include_router(stocks.router)
app.include_router(predictions.router)
app.include_router(backtest.router)

# 管理端功能
app.include_router(admin_users.router)
app.include_router(logs.router)
app.include_router(models.router)
app.include_router(crawler.router)

# v1.3 统一数据链路 API
# 包括：
# - GET  /api/data-pipeline/coverage
# - POST /api/data-pipeline/jobs
app.include_router(data_pipeline_router)