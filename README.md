# Finsight Backend v1

本工程是根据 02、03、04 三份 v5.0 设计文档生成的 **成员 C 后端第一版代码**。

已实现重点：

- FastAPI 后端主框架
- SQLAlchemy / MySQL 数据模型
- Auth API：注册、登录、当前用户
- Admin User API：查询用户、修改状态、修改角色、修改用户名、重置密码、软删除
- Watchlist API：自选股增删查
- Stock API：股票搜索、股票详情、新闻列表、新闻详情、情绪摘要
- Prediction API：单股预测、预测历史卡片、预测详情
- Log API：管理员日志查询
- Model Info API：当前启用模型查询
- Crawler API：爬虫状态、股票基础库同步状态、手动同步股票基础库
- Backtest API：接口壳、状态、日志、帧、单日详情、最终持仓查询

暂未真正接入：

- 真实 XGBoost 模型文件加载
- 真实 LLM 调用
- 真实逐日回测引擎
- 真实行情/新闻自动爬虫

这些部分已经预留在 `app/services/` 里，后续可替换服务实现，不需要大改 API 层。

---

以后日常开发推荐命令
只改了 Python 代码
docker compose restart backend
改了 .env.docker
docker compose up -d --force-recreate backend
改了 docker-compose.yml
docker compose down
docker compose up -d
改了 requirements.txt
docker compose build backend
docker compose up -d
出现奇怪依赖问题才用
docker compose build --no-cache backend
docker compose up -d

## 1. 环境安装

```bash
cd finsight_backend_v1
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 2. 配置数据库

复制配置文件：

```bash
copy .env.example .env
# 或 macOS/Linux
cp .env.example .env
```

生产/服务器建议使用 MySQL：

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@127.0.0.1:3306/finsight?charset=utf8mb4
```

本地快速调试可以临时使用 SQLite：

```env
DATABASE_URL=sqlite:///./finsight_dev.db
```

> 注意：正式项目仍应使用 MySQL，SQLite 只是为了本地快速跑通接口。

---

## 3. 初始化演示数据

```bash
python -m app.scripts.seed_demo
```

默认账号：

```text
管理员：admin / Admin123
普通用户：user01 / User123
```

---

## 4. 启动服务

```bash
python run.py
```

或：

```bash
uvicorn app.main:app --reload
```

访问：

```text
http://127.0.0.1:8000/docs
```

---

## 5. 主要接口

### Auth

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
```

### Stock

```text
GET /api/stocks/search
GET /api/stocks/{ticker}/detail
GET /api/stocks/{ticker}/news
GET /api/stocks/news/{news_id}
GET /api/stocks/{ticker}/sentiment-summary
```

### Prediction

```text
POST /api/predictions/run
GET  /api/predictions/history
GET  /api/predictions/{prediction_id}
```

### Backtest

```text
POST /api/backtest/run
GET  /api/backtest/{run_id}/status
GET  /api/backtest/{run_id}/frames
GET  /api/backtest/{run_id}/logs
GET  /api/backtest/{run_id}/days/{date}
GET  /api/backtest/{run_id}/summary
GET  /api/backtest/{run_id}/final-positions
GET  /api/backtest/latest/final-positions
```

### Admin

```text
GET    /api/admin/users
GET    /api/admin/users/{user_id}
PUT    /api/admin/users/{user_id}/status
PUT    /api/admin/users/{user_id}/role
PUT    /api/admin/users/{user_id}/username
PUT    /api/admin/users/{user_id}/password
DELETE /api/admin/users/{user_id}
```

---

## 6. 测试流程示例

1. 登录：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"user01","password":"User123"}'
```

2. 拿到 token 后查询 AAPL 详情：

```bash
curl http://127.0.0.1:8000/api/stocks/AAPL/detail \
  -H "Authorization: Bearer <token>"
```

3. 执行预测：

```bash
curl -X POST http://127.0.0.1:8000/api/predictions/run \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","forecast_days":5}'
```

---

## 7. 后续接入点

### 接入真实模型

替换：

```text
app/services/prediction_service.py
```

当前该文件中的 `run_prediction()` 是占位推理逻辑，后续可以加载 XGBoost 模型并替换分类/回归输出。

### 接入 LLM

替换：

```text
news_llm_report
report_text
```

建议新增：

```text
app/services/report_service.py
```

### 接入真实回测引擎

替换或扩展：

```text
app/services/backtest_service.py
```

目前 `POST /api/backtest/run` 只创建任务与日志，不做逐日计算。后续需要将回测循环写入：

```text
backtest_trades
portfolio_snapshots
backtest_daily_positions
backtest_event_logs
user_simulated_positions
```

---

## 8. 目录结构

```text
finsight_backend_v1/
├── app/
│   ├── core/          # 配置、鉴权、响应、异常
│   ├── db/            # 数据库连接与初始化
│   ├── models/        # SQLAlchemy 数据模型
│   ├── schemas/       # Pydantic 请求体
│   ├── services/      # 业务服务层
│   ├── routers/       # FastAPI 路由
│   └── scripts/       # 初始化/同步脚本
├── requirements.txt
├── .env.example
├── run.py
└── README.md
```
