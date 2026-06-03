# Finsight Backend

**Finsight / 智融洞察：面向股票趋势预测与模拟回测的金融分析系统**

本仓库是 Finsight 项目的后端部分，采用 **FastAPI + MySQL + Docker Compose** 架构。当前版本已经从早期“API 骨架 + 占位预测”推进到：

> 可运行后端 + MySQL 数据库 + v1.2 三模型推理 + v1.3 统一数据链路 + 50 维特征快照 + 数据库优先补全 + 按需预测数据准备 + 每日自动补全调度。

当前版本适合用于前端联调、接口测试、股票查询、用户管理、单股预测、预测历史、模型版本查询、日志查看、每日数据补全任务验证等流程。

当前仍未完成的重点是：真实新闻 LLM 深度分析、完整逐日回测引擎、前端回测动画数据完全接入、更稳定的付费/高可用行情源配置。

---

## 1. 当前项目状态

### 1.1 已完成

- FastAPI 后端主框架
- MySQL 8.0 数据库连接与 ORM 表结构
- Docker Compose 一键启动后端和数据库
- JWT 登录鉴权
- 用户注册、登录、当前用户信息
- 管理员用户管理
- 股票基础库搜索
- 股票详情查询
- 股票新闻列表与新闻详情查询
- 用户自选股增删查
- v1.2 三个模型接入：
  - 主分类模型：`finsight_cls_abs_h15_v1.2`
  - 辅助强信号模型：`finsight_cls_action1p5_h10_v1.2`
  - 回归路径模型：`finsight_reg_return_path_v1.2`
- 主分类二分类输出适配为原 API 的 `prob_down / prob_neutral / prob_up`
- `model_feature_snapshots` 50 维特征快照表接入
- runtime 50 维 feature snapshot 自动生成
- 单股预测接口
- 支持 `base_trading_date` 指定预测基准日
- 预测前按需数据准备：
  - 有目标日期 snapshot：直接使用数据库缓存
  - 无目标日期 snapshot：自动触发 v1.3 Data Pipeline
  - 无法精确准备目标日期时：按配置 fallback 到最近可用 snapshot
- 预测结果保存到 `predictions`
- 预测历史卡片列表
- 预测详情接口
- 当前启用模型查询
- 股票基础库同步接口
- v1.3 统一数据链路 API：
  - `GET /api/data-pipeline/coverage`
  - `POST /api/data-pipeline/jobs`
- v1.3 数据链路模块：
  - `market`
  - `technical`
  - `news`
  - `sentiment`
  - `features`
- 数据库优先策略：
  - 有 snapshot 直接使用
  - 无 snapshot 但有行情则本地计算
  - 数据缺失时才访问外部 API
- Alpha Vantage News Sentiment 初步接入：
  - 新闻抓取
  - 新闻去重
  - `news_data` 写入
  - `sentiment_daily` 聚合
- 在线行情补全服务：
  - 默认优先 Alpha Vantage
  - 可选 Yahoo Chart 兜底
  - 可选本地 CSV fallback
  - 抓取失败时不生成假特征
  - 异常价格检测
- 技术指标重算服务
- 每日数据补全调度器：
  - 支持后台每日运行
  - 支持启动后立即运行
  - 已迁移到 v1.3 Data Pipeline
- API 自动化测试脚本
- 请求返回结构优化：
  - 顶层返回 `data_refresh_status`
  - `request_params` 不再重复嵌套 `data_refresh_status`
  - `news_summary.news_start_time / news_end_time` 自动补全

---

### 1.2 当前已验证样例

目前已在开发环境中验证：

```text
AAPL：已有 runtime snapshot，预测正常
MSFT：已有 runtime snapshot，预测正常
AMZN：已有 runtime snapshot，预测正常
GOOGL：原本没有 2026-05-29 runtime snapshot，预测时自动触发 v1.3 Data Pipeline，成功生成 snapshot 并完成预测
```

GOOGL on-demand 流程已验证：

```text
market: cached
technical: cached
news: updated
sentiment: updated
features: created_or_updated
prediction: success
```

生成结果示例：

```text
ticker = GOOGL
base_trading_date = 2026-05-29
dataset_version = runtime_v1_2_auto
feature_count = 50
news_count > 0
sentiment_score 有值
```

---

### 1.3 当前仍未完成 / 降级实现

- 新闻 LLM 深度分析仍为模板降级版本
- 综合 LLM 报告仍为模板降级版本
- `fund_*` 财报特征当前主要采用最近真实快照 carry-forward 或已导入快照方式
- 财报在线抓取与逐日财报特征生成仍需进一步完善
- 回测 API 路由已存在，但完整逐日交易引擎仍未完成
- 回测动画帧、逐日持仓、逐日交易、最终持仓等仍需进一步接入真实策略
- 外部行情源存在额度、限流、403、premium endpoint 等失败场景，需要通过日志排查
- 当前主分类模型本质为二分类模型，后端将其适配成 down / neutral / up API，其中 neutral 当前为 0 或降级适配值

---

## 2. 技术栈

| 类型 | 技术 |
|---|---|
| Web 框架 | FastAPI |
| 数据库 | MySQL 8.0 |
| ORM | SQLAlchemy |
| 数据库驱动 | PyMySQL |
| 鉴权 | JWT |
| 密码哈希 | passlib + bcrypt |
| 模型加载 | joblib |
| 机器学习运行环境 | scikit-learn 1.8.0 |
| 数据处理 | pandas / numpy |
| 外部请求 | requests |
| 部署 | Docker Compose |
| API 文档 | Swagger UI / OpenAPI |
| 语言 | Python 3.11 |

> 注意：B 同学交付的 v1.2 模型使用 scikit-learn 1.8.0 环境训练/保存。若运行环境低于该版本，可能出现模型反序列化 warning 或推理错误。

---

## 3. 项目结构

```text
finsight_backend_v1/
├── app/
│   ├── main.py                          # FastAPI 应用入口
│   ├── core/                            # 配置、安全、响应格式、异常处理
│   ├── db/                              # 数据库连接与初始化
│   ├── models/                          # SQLAlchemy ORM 模型
│   ├── schemas/                         # Pydantic 请求/响应模型
│   │   └── data_pipeline.py              # v1.3 Data Pipeline 请求模型
│   ├── routers/                         # API 路由
│   │   └── data_pipeline.py              # v1.3 Data Pipeline API
│   ├── services/                        # 业务逻辑
│   │   ├── model_service.py              # 模型加载与推理
│   │   ├── feature_service.py            # 特征读取
│   │   ├── feature_snapshot_service.py   # runtime 特征快照生成
│   │   ├── market_data_service.py        # 在线行情补全
│   │   ├── indicator_service.py          # 技术指标重算
│   │   ├── data_pipeline_service.py      # v1.3 统一数据链路
│   │   ├── prediction_input_service.py   # 预测前按需准备数据
│   │   ├── prediction_service.py         # 预测业务逻辑
│   │   ├── daily_refresh_service.py      # 每日数据补全调度
│   │   └── ...
│   └── scripts/                         # 初始化、导入、测试脚本
│       ├── import_member_b_real_data.py
│       ├── run_daily_data_pipeline.py
│       ├── test_online_market_fetch.py
│       └── ...
├── artifacts/
│   └── models/                          # v1.2 模型文件，可随项目提交
├── docker/
│   └── entrypoint.sh
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── run.py
├── finsight_api_auto_test.py
├── .env.example
├── .env.docker.example
├── .gitignore
└── README.md
```

---

## 4. Docker 启动方式

### 4.1 准备环境变量

首次运行时，从模板复制 Docker 环境配置：

```bash
cp .env.docker.example .env.docker
```

Docker 内部后端连接 MySQL 必须使用服务名 `db`：

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
```

不是：

```env
localhost:3306
```

---

### 4.2 推荐 `.env.docker`

```env
# =========================
# Database
# =========================
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4

# =========================
# App
# =========================
SECRET_KEY=change-this-secret-key-in-real-deployment
ACCESS_TOKEN_EXPIRE_MINUTES=1440
PROJECT_NAME=Finsight Backend
ENVIRONMENT=docker

# 真实数据阶段建议关闭 seed，避免演示数据污染真实行情、新闻和预测结果。
RUN_SEED=0

# =========================
# Daily automatic data refresh - v1.3 Data Pipeline
# =========================
ENABLE_DAILY_AUTO_REFRESH=1
DAILY_AUTO_REFRESH_HOUR=18
DAILY_AUTO_REFRESH_MINUTE=30
DAILY_AUTO_REFRESH_RUN_ON_STARTUP=0
DAILY_AUTO_REFRESH_FORCE=0
DAILY_AUTO_REFRESH_LIMIT=4
DAILY_AUTO_REFRESH_TICKERS=AAPL,MSFT,NVDA,TSLA
DAILY_AUTO_REFRESH_MODULES=market,technical,news,sentiment,features
DAILY_AUTO_REFRESH_NEWS_WINDOW_DAYS=14

# =========================
# Prediction on-demand pipeline
# =========================
PREDICTION_ON_DEMAND_PIPELINE=1
PREDICTION_ON_DEMAND_MODULES=market,technical,news,sentiment,features
PREDICTION_ON_DEMAND_ALLOW_FALLBACK=1

# =========================
# Online market data crawling
# =========================
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_api_key
MARKET_DATA_SOURCE_PRIORITY=alpha_vantage,yahoo_chart
ENABLE_YAHOO_CHART_FALLBACK=1
ENABLE_LOCAL_RAW_CSV_FALLBACK=0
MARKET_DATA_LOCAL_RAW_ROOT=/external_datasets/market_data/backtest_market_raw_20250521_20260531

MARKET_DATA_MIN_HISTORY_DAYS=252
MARKET_DATA_MAX_STALE_DAYS=5
MARKET_DATA_GAP_LOOKBACK_DAYS=90
MARKET_DATA_GAP_TRIGGER_COUNT=3
MARKET_DATA_TIMEOUT_SECONDS=30

PRICE_SUSPICIOUS_CHANGE_THRESHOLD=0.35
PRICE_QUALITY_LOOKBACK_DAYS=120

MARKET_DATA_USER_AGENT=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36

# =========================
# News crawling
# =========================
NEWS_FETCH_LOOKBACK_DAYS=30
NEWS_FETCH_LIMIT=1000
NEWS_FETCH_TIMEOUT_SECONDS=30
```

修改 `.env.docker` 后不需要重新 build：

```bash
docker compose up -d --force-recreate backend
```

---

### 4.3 启动服务

```bash
docker compose up -d
```

查看状态：

```bash
docker compose ps
```

正常应类似：

```text
finsight_backend   Up   0.0.0.0:8002->8000/tcp
finsight_mysql     Up   0.0.0.0:3308->3306/tcp
```

访问：

```text
http://127.0.0.1:8002/docs
```

测试：

```bash
curl http://127.0.0.1:8002/health
```

---

## 5. 常用 Docker 命令

### 5.1 只改 Python 代码

如果 `docker-compose.yml` 中已经挂载：

```yaml
volumes:
  - .:/app
```

则只需要：

```bash
docker compose restart backend
```

### 5.2 改了 `.env.docker`

```bash
docker compose up -d --force-recreate backend
```

### 5.3 改了 `docker-compose.yml`

```bash
docker compose down
docker compose up -d
```

### 5.4 改了 `requirements.txt`

```bash
docker compose build backend
docker compose up -d
```

### 5.5 出现奇怪依赖问题

```bash
docker compose build --no-cache backend
docker compose up -d
```

平时不要频繁使用 `--no-cache`。

---

## 6. 默认账号

如果启用 seed 或数据库中已有演示账号：

```text
管理员：admin / Admin123
普通用户：user01 / User123
```

登录示例：

```bash
curl -X POST http://127.0.0.1:8002/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin123"}'
```

---

## 7. 当前主要 API 模块

| 模块 | 前缀 | 说明 |
|---|---|---|
| Auth API | `/api/auth` | 注册、登录、当前用户 |
| Watchlist API | `/api/watchlist` | 自选股增删查 |
| Stock API | `/api/stocks` | 股票搜索、详情、新闻、情绪摘要 |
| Prediction API | `/api/predictions` | 单股预测、预测历史、预测详情 |
| Backtest API | `/api/backtest` | 回测任务、状态、帧、日志、详情、汇总、最终持仓 |
| Admin User API | `/api/admin/users` | 管理员用户管理 |
| Log API | `/api/logs` | 管理员日志查询 |
| Model Info API | `/api/models` | 当前启用模型查询 |
| Crawler API | `/api/crawler` | 股票基础库同步、每日数据补全兼容入口 |
| Data Pipeline API | `/api/data-pipeline` | v1.3 数据覆盖检查和统一数据链路任务 |

---

## 8. v1.3 Data Pipeline

### 8.1 查询数据覆盖情况

```bash
curl "http://127.0.0.1:8002/api/data-pipeline/coverage?ticker=GOOGL&end_date=2026-05-29"
```

返回会展示：

```text
price_data
technical_indicators
news_data
sentiment_daily
model_feature_snapshots
recommendation
```

状态语义：

| 状态 | 含义 |
|---|---|
| `ok` | 已覆盖目标日期 |
| `cached` | 已有缓存，可直接使用 |
| `stale` | 有数据，但没有覆盖目标日期 |
| `empty` | 表存在，但该 ticker 无数据 |
| `missing_table` | 表不存在 |
| `not_ready` | 核心数据不足，需要补全 |
| `ready` | 核心数据可用于预测 |

---

### 8.2 手动运行数据链路任务

```bash
curl -X POST http://127.0.0.1:8002/api/data-pipeline/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "tickers": ["GOOGL"],
    "end_date": "2026-05-29",
    "modules": ["market", "technical", "news", "sentiment", "features"],
    "force_refresh": false,
    "run_async": false
  }'
```

常见返回：

```text
market: cached
technical: cached
news: updated 或 cached
sentiment: updated 或 cached
features: created_or_updated 或 cached
```

---

### 8.3 命令行运行 Data Pipeline

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_daily_data_pipeline \
--tickers GOOGL \
--target-date 2026-05-29 \
--modules market,technical,news,sentiment,features"
```

---

## 9. 股票行情与数据补全

### 9.1 股票详情查询

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1m&include_news=true&include_indicators=true&auto_refresh=true" \
  -H "Authorization: Bearer $USER_TOKEN"
```

说明：

```text
auto_refresh=true 时，后端会尝试补全最新可用日频行情。
```

注意：

```text
当前系统是日频行情，不是毫秒级实时行情。
如果当天美股尚未收盘或外部 API 尚未更新，实际可用日期可能是最近一个交易日。
```

---

### 9.2 手动触发每日数据补全兼容接口

```bash
ADMIN_TOKEN=$(curl -s -X POST http://127.0.0.1:8002/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin123"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")

curl -X POST http://127.0.0.1:8002/api/crawler/daily-refresh/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "tickers": ["AAPL"],
    "target_date": "2026-06-02",
    "force_refresh": false,
    "limit": 10
  }'
```

当前该兼容入口内部已迁移到 v1.3 Data Pipeline。

---

### 9.3 查询每日补全状态

```bash
curl http://127.0.0.1:8002/api/crawler/daily-refresh/status \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

也可以直接查数据库日志：

```sql
SELECT task_type, ticker, start_time, end_time, status, fetched_count, LEFT(message, 300) AS msg
FROM crawler_logs
WHERE task_type LIKE '%daily%' OR task_type LIKE 'data_pipeline%'
ORDER BY start_time DESC
LIMIT 30;
```

---

### 9.4 命令行测试在线行情抓取

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.test_online_market_fetch AAPL 2026-06-02"
```

抓取失败时，系统不会继续生成新的 runtime feature snapshot。

---

## 10. 预测接口说明

### 10.1 自动使用最新可用交易日预测

```bash
curl -X POST http://127.0.0.1:8002/api/predictions/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -d '{
    "ticker": "AAPL",
    "forecast_days": 5,
    "analysis_mode": "full",
    "risk_profile": "balanced",
    "news_window_days": 14,
    "force_refresh": false
  }'
```

### 10.2 指定基准日预测未来 5 个交易日

```bash
curl -X POST http://127.0.0.1:8002/api/predictions/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -d '{
    "ticker": "GOOGL",
    "forecast_days": 5,
    "base_trading_date": "2026-05-29",
    "analysis_mode": "full",
    "risk_profile": "balanced",
    "news_window_days": 14,
    "force_refresh": false
  }'
```

含义：

```text
使用 base_trading_date 当天及之前的行情、技术指标、新闻情绪、财报特征作为输入，
预测之后 5 个交易日的价格路径。
```

限制：

```text
当前 v1.2 回归模型最多输出未来 5 个交易日，因此 forecast_days 限制为 1~5。
```

---

### 10.3 预测前数据准备逻辑

预测接口执行前会调用 `prediction_input_service.py`：

```text
1. 优先查 model_feature_snapshots 是否已有目标日期 50 维快照；
2. 如果有，直接使用数据库快照；
3. 如果没有，自动调用 v1.3 Data Pipeline：
   market -> technical -> news -> sentiment -> features
4. 如果目标日期无法准备，但存在更早可用快照，则根据 PREDICTION_ON_DEMAND_ALLOW_FALLBACK 决定是否降级；
5. 返回 data_refresh_status 给前端。
```

前端应展示：

```text
requested_base_trading_date
actual_base_trading_date
base_trading_date_source
warnings
```

如果外部行情源拉取失败，可能出现：

```text
base_trading_date_source = fallback_to_latest_available
```

这表示系统使用最近可用交易日作为实际模型输入基准日。

---

### 10.4 预测返回关键字段

```json
{
  "prediction_id": 13,
  "ticker": "GOOGL",
  "base_trading_date": "2026-05-29",
  "forecast_start_date": "2026-06-01",
  "forecast_end_date": "2026-06-05",
  "request_params": {
    "ticker": "GOOGL",
    "forecast_days": 5,
    "base_trading_date": "2026-05-29"
  },
  "data_refresh_status": {
    "status": "ready",
    "base_trading_date_source": "requested_exact_match"
  },
  "classification": {
    "predicted_label": "down",
    "prob_up": 0.0243211,
    "prob_neutral": 0.0,
    "prob_down": 0.975679,
    "aux_model": {
      "strong_signal_score": 0.3124206
    }
  },
  "regression": {
    "price_path": []
  },
  "news_summary": {
    "news_start_time": "2026-05-16",
    "news_end_time": "2026-05-29",
    "sentiment_curve": []
  }
}
```

---

## 11. v1.2 模型说明

当前已接入 B 同学交付的 v1.2 模型：

| 模型 | 类型 | 说明 |
|---|---|---|
| `finsight_cls_abs_h15_v1.2` | classifier | 主分类模型，二分类输出，经后端适配为 down / neutral / up |
| `finsight_cls_action1p5_h10_v1.2` | aux_classifier | 辅助强信号模型，用于推荐分数微调 |
| `finsight_reg_return_path_v1.2` | regressor | 回归路径模型，输出未来 5 个交易日收益率路径 |

模型输入来自：

```text
model_feature_snapshots.features_json
```

每条快照包含 50 维特征，主要包括：

```text
行情特征：open/high/low/close/volume/daily_return/change_percent/amplitude
技术指标：return_1d/3d/5d、ma5/ma20/ma60、rsi、macd、volatility_20d、drawdown_20d、volume_zscore
新闻情绪：news_count、positive/negative/neutral_news_count、sentiment_score、3d/7d 平均情绪、positive_ratio、negative_ratio
财报特征：fundamental_available、EPS、revenue、net_income、margin、YoY 等
```

---

## 12. 数据库中数据说明

### 12.1 较真实 / 当前正在使用的数据

| 表 | 当前数据性质 |
|---|---|
| `users` | 真实账号 + 演示账号 |
| `stocks` | 股票基础库 |
| `price_data` | 当前可由在线行情接口补全，也可能含历史导入数据 |
| `technical_indicators` | 可由 `indicator_service.py` 从 `price_data` 重算 |
| `news_data` | 当前可由 Alpha Vantage News Sentiment 抓取写入 |
| `sentiment_daily` | 当前可由 `news_data` 聚合 |
| `model_feature_snapshots` | 包含训练特征快照和 `runtime_v1_2_auto` 运行时特征快照 |
| `model_versions` | 当前已注册 v1.2 模型版本 |
| `predictions` | 真实预测请求记录 |
| `watchlists` | 用户操作产生的自选股 |
| `operation_logs` | 用户/系统操作日志 |
| `crawler_logs` | 股票基础库同步、每日数据补全、data pipeline 日志 |

### 12.2 部分降级 / 仍需完善的数据

| 表 | 当前数据性质 |
|---|---|
| `financial_reports` / `fund_*` 来源数据 | 财报特征仍需进一步严格化 |
| `backtest_runs` | 能创建任务，但真实逐日回测仍待完善 |
| `backtest_event_logs` | 部分为任务创建或占位日志 |
| `portfolio_snapshots` | 完整回测未接入前可能为空 |
| `backtest_daily_positions` | 完整回测未接入前可能为空 |
| `backtest_trades` | 完整回测未接入前可能为空 |
| `user_simulated_positions` | 完整回测未接入前可能为空 |

---

## 13. API 自动化测试

运行完整测试：

```bash
python finsight_api_auto_test.py --base-url http://127.0.0.1:8002
```

指定预测基准日：

```bash
python finsight_api_auto_test.py \
  --base-url http://127.0.0.1:8002 \
  --prediction-base-date 2026-05-29
```

开启 Data Pipeline 测试：

```bash
python finsight_api_auto_test.py \
  --base-url http://127.0.0.1:8002 \
  --run-data-pipeline \
  --pipeline-ticker GOOGL \
  --pipeline-target-date 2026-05-29
```

开启每日补全兼容接口测试：

```bash
python finsight_api_auto_test.py \
  --base-url http://127.0.0.1:8002 \
  --run-daily-refresh \
  --daily-refresh-target-date 2026-05-29
```

测试报告输出：

```text
api_test_results/
├── finsight_api_test_report_时间戳.json
└── finsight_api_test_report_时间戳.md
```

新版测试脚本会额外检查：

```text
预测概率字段是否存在
概率和是否接近 1
price_path 长度是否符合 forecast_days
是否返回 model_version / reg_model_version
是否返回 data_refresh_status
request_params 中是否不再重复嵌套 data_refresh_status
news_summary 是否有 news_start_time / news_end_time
Data Pipeline coverage / job 是否可用
每日补全兼容接口是否可用
```

---

## 14. 数据备份与恢复

### 14.1 备份

```bash
mkdir -p backups

docker compose exec db sh -c \
'mysqldump --no-tablespaces --single-transaction --quick --routines --triggers -ufinsight_user -pfinsight_password finsight' \
> backups/finsight_backup_$(date +%Y%m%d_%H%M%S).sql
```

### 14.2 恢复

```bash
docker compose exec -T db mysql -ufinsight_user -pfinsight_password finsight < backups/你的备份文件.sql
```

---

## 15. Git 协作建议

### 15.1 不建议提交

```text
__pycache__/
*.pyc
.env
.env.docker
api_test_results/
backups/
mysql_data/
logs/
.venv/
venv/
*.db
*.sqlite
local_experiments/
external_data/
import_data/
```

### 15.2 可以提交

```text
app/
artifacts/models/
docker/
Dockerfile
docker-compose.yml
requirements.txt
run.py
README.md
.env.example
.env.docker.example
.gitignore
finsight_api_auto_test.py
```

当前模型文件不大时，可以提交：

```text
artifacts/models/
```

不要提交：

```text
训练集 CSV
raw JSON
SQLite 中间库
数据库备份 SQL
真实 .env.docker
```

---

## 16. 常见问题

### 16.1 后端一直重启

```bash
docker compose logs --tail=120 backend
```

### 16.2 8002 端口被占用

修改 `docker-compose.yml`：

```yaml
ports:
  - "8003:8000"
```

然后：

```bash
docker compose down
docker compose up -d
```

### 16.3 修改 Python 代码没生效

```bash
docker compose restart backend
```

如果没有 volume 挂载，才需要重新 build。

### 16.4 `.env.docker` 修改没生效

```bash
docker compose up -d --force-recreate backend
```

### 16.5 行情抓取失败

先单独测试：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.test_online_market_fetch AAPL 2026-06-02"
```

常见原因：

```text
Alpha Vantage 免费额度用完
Alpha Vantage endpoint 对当前 key 不可用
Yahoo Chart 返回 403
网络无法访问外部数据源
```

此时系统应返回 `failed` 或使用已有缓存 fallback，不能静默生成假数据。

### 16.6 预测日期为什么不是今天

模型是日频模型，输入需要某个交易日收盘后的完整特征。如果今天还没有收盘价，系统只能使用最近一个可用交易日作为 `actual_base_trading_date`。

---

## 17. 当前待开发重点

建议按优先级继续开发：

1. **真实 LLM 新闻分析**
   - 新闻级摘要
   - 多新闻窗口综合分析
   - 超时降级
2. **财报特征更严格生成**
   - 从 `financial_reports_all.csv` 或在线财报接口生成逐日 `fund_*`
   - 替代当前 carry-forward 简化逻辑
3. **完整逐日回测引擎**
   - 逐日交易循环
   - 买入/卖出/持有决策
   - 持仓与现金更新
   - 动画帧
   - 交易日志
   - 最终持仓
4. **前端回测动画数据完全接入**
5. **更稳定的行情源或付费数据源配置**
6. **数据质量面板**
   - 每只股票最新行情日期
   - 最新 feature snapshot 日期
   - 新闻覆盖日期
   - 抓取失败原因统计

---

## 18. 项目定位总结

当前版本是：

```text
Finsight Backend v1.3：
FastAPI + MySQL + Docker + v1.2 三模型推理 + 50 维特征快照 + v1.3 Data Pipeline + 数据库优先 + 按需预测数据准备 + 每日自动补全调度。
```

已经适合：

```text
前端联调
用户系统
股票搜索与详情
预测页面
预测历史
模型版本展示
Data Pipeline 验证
每日数据补全任务测试
日志页面
```

仍需继续完善：

```text
真实新闻 LLM 深度报告
真实逐日回测动画
更严格的财报特征
更稳定的数据源
```
