# Finsight Backend

**Finsight / 智融洞察：面向股票趋势预测与模拟回测的金融分析系统**

本仓库是 Finsight 项目的后端部分，采用 **FastAPI + MySQL + Docker Compose** 架构，面向股票行情查询、新闻情绪分析、模型预测、预测历史、数据补全、日志管理和模拟回测等功能。

当前版本建议定位为：

> **Finsight Backend v1.5：FastAPI + MySQL + Docker + v1.2 三模型推理 + 百炼 LLM 报告 + Twelve Data 自爬行情 + 1min 分钟行情 + Alpha Vantage 新闻情绪 + 新闻正文抓取 + 52 维运行时特征快照 + 数据库优先补全 + 按需预测数据准备。**

---

## 1. 当前版本状态

### 1.1 已完成能力

- FastAPI 后端主框架
- MySQL 8.0 数据库连接与 ORM 表结构
- Docker Compose 一键启动后端和数据库
- JWT 登录鉴权
- 用户注册、登录、当前用户信息
- 管理员用户管理
- 股票基础库同步与搜索
- 用户自选股增删查
- 股票详情查询
- 股票新闻列表查询
- 股票新闻详情查询
- 新闻原文正文抓取与缓存
- 14 日新闻情绪统计
- 日频行情数据补全
- 1min 分钟行情数据补全
- 技术指标重算
- 新闻情绪日聚合
- 运行时模型特征快照生成
- 单股预测接口
- 预测历史列表
- 预测详情接口
- 当前启用模型查询
- 兼容式每日数据补全入口
- 统一 Data Pipeline 任务入口
- API 自动化测试脚本
- 数据库检查与修复脚本
- 百炼 / DashScope LLM 综合报告接入
- 模型预测结果保存到 `predictions`
- 多次预测不会覆盖旧记录，每次预测都会生成独立 `prediction_id`

---

### 1.2 当前核心数据源

| 数据类别       | 当前来源                           | 说明                           |
| -------------- | ---------------------------------- | ------------------------------ |
| 股票基础库     | Nasdaq Trader                      | 写入 `stocks`                  |
| 日频行情       | Twelve Data `time_series` `1day`   | 写入 `price_data`              |
| 分钟行情       | Twelve Data `time_series` `1min`   | 写入 `intraday_price_data`     |
| 新闻列表与情绪 | Alpha Vantage News Sentiment       | 写入 `news_data`               |
| 新闻正文       | 新闻原文 URL 抓取                  | 写入 `news_data.content_text`  |
| 新闻日聚合     | 后端从 `news_data` 聚合            | 写入 `sentiment_daily`         |
| 技术指标       | 后端从 `price_data` 计算           | 写入 `technical_indicators`    |
| 模型特征快照   | 后端融合行情、技术指标、新闻、财报 | 写入 `model_feature_snapshots` |
| LLM 分析报告   | 阿里云百炼 / DashScope             | 写入预测解释字段               |

---

### 1.3 当前已验证数据状态

当前开发环境中已经验证核心 7 只股票：

```text
AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META
```

已验证结果：

```text
price_data:
7 只股票全部 860 行
2023-01-03 ~ 2026-06-08

price_data 派生字段:
recent 30 rows bad count = 0
previous_close / change_amount / change_percent / daily_return / amplitude 正常

intraday_price_data:
7 只股票全部为 2026-06-08
每只股票 390 条 1min 数据
时间范围 09:30:00 ~ 15:59:00

technical_indicators:
7 只股票全部 801 行
2023-03-29 ~ 2026-06-08

news_data:
覆盖到 2026-06-09
新闻正文覆盖率约 50% ~ 70%

sentiment_daily:
覆盖到 2026-06-09

model_feature_snapshots:
7 只股票最新 base_trading_date 均为 2026-06-08
features_json 中 daily_return / change_percent / amplitude 已和 price_data 对齐

API 自动化测试:
37 / 37 passed
```

---

### 1.4 仍需继续完善

- 完整逐日回测交易引擎仍需进一步完善
- 回测动画帧、逐日持仓、逐日交易、最终持仓仍需继续接入真实策略
- 财报特征仍需要更严格的逐日生成逻辑
- 外部数据源仍存在额度、限流、超时、403、付费 endpoint 等风险
- 当前主分类模型本质为二分类模型，后端将其适配为 `prob_down / prob_neutral / prob_up`
- 当前系统是日频预测系统，不是实时高频交易系统

---

## 2. 技术栈

| 类型             | 技术                         |
| ---------------- | ---------------------------- |
| Web 框架         | FastAPI                      |
| 数据库           | MySQL 8.0                    |
| ORM              | SQLAlchemy                   |
| 数据库驱动       | PyMySQL                      |
| 鉴权             | JWT                          |
| 密码哈希         | passlib + bcrypt             |
| 模型加载         | joblib                       |
| 机器学习运行环境 | scikit-learn 1.8.0           |
| 数据处理         | pandas / numpy               |
| 外部请求         | requests                     |
| 日频/分钟行情    | Twelve Data                  |
| 新闻情绪         | Alpha Vantage News Sentiment |
| LLM 报告         | 阿里云百炼 / DashScope       |
| 部署             | Docker Compose               |
| API 文档         | Swagger UI / OpenAPI         |
| 语言             | Python 3.11                  |

> 注意：B 同学交付的 v1.2 模型使用 scikit-learn 1.8.0 环境训练/保存。若运行环境低于该版本，可能出现模型反序列化 warning 或推理错误。

---

## 3. 项目结构

```text
finsight_backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── response.py
│   │   └── security.py
│   ├── db/
│   │   ├── base.py
│   │   ├── init_db.py
│   │   └── session.py
│   ├── models/
│   │   └── all_models.py
│   ├── schemas/
│   │   ├── crawler.py
│   │   ├── data_pipeline.py
│   │   ├── prediction.py
│   │   └── ...
│   ├── routers/
│   │   ├── auth.py
│   │   ├── stocks.py
│   │   ├── predictions.py
│   │   ├── backtest.py
│   │   ├── crawler.py
│   │   ├── data_pipeline.py
│   │   ├── logs.py
│   │   └── ...
│   ├── services/
│   │   ├── twelvedata_market_service.py
│   │   ├── market_data_service.py
│   │   ├── intraday_market_service.py
│   │   ├── news_service.py
│   │   ├── news_detail_fetch_service.py
│   │   ├── indicator_service.py
│   │   ├── feature_snapshot_service.py
│   │   ├── prediction_input_service.py
│   │   ├── prediction_service.py
│   │   ├── model_service.py
│   │   ├── llm_service.py
│   │   ├── data_pipeline_service.py
│   │   ├── daily_refresh_service.py
│   │   └── ...
│   └── scripts/
│       ├── create_intraday_price_data_table.py
│       ├── run_twelvedata_incremental_refresh.py
│       ├── ensure_ticker_data_on_demand.py
│       ├── repair_price_derived_fields.py
│       ├── delete_incomplete_intraday_rows.py
│       ├── repair_feature_snapshots_from_price_data.py
│       └── ...
├── artifacts/
│   └── models/
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

## 4. 环境变量配置

首次运行时复制环境变量模板：

```bash
cp .env.docker.example .env.docker
```

Docker 内部后端连接 MySQL 必须使用服务名 `db`：

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
```

不要写成：

```env
localhost:3306
```

---

### 4.1 推荐 `.env.docker`

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
RUN_SEED=0

# =========================
# Core tickers
# =========================
FINSIGHT_CORE_TICKERS=AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META
FINSIGHT_ENABLE_ON_DEMAND_INGEST=true

# =========================
# Twelve Data - market data
# =========================
TWELVEDATA_API_KEY=your_twelvedata_api_key
TWELVEDATA_BASE_URL=https://api.twelvedata.com
TWELVEDATA_TIMEZONE=America/New_York
TWELVEDATA_DAILY_INTERVAL=1day
TWELVEDATA_INTRADAY_INTERVAL=1min
TWELVEDATA_DAILY_OUTPUTSIZE=5000
TWELVEDATA_INTRADAY_OUTPUTSIZE=500
TWELVEDATA_INTRADAY_PREPOST=false
TWELVEDATA_TIMEOUT_SECONDS=30
TWELVEDATA_REQUEST_SLEEP_SECONDS=1

TWELVEDATA_DAILY_INITIAL_BACKFILL_DAYS=1260
TWELVEDATA_INTRADAY_INITIAL_BACKFILL_DAYS=1

# =========================
# Alpha Vantage - news and fundamentals
# =========================
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_api_key
NEWS_FETCH_LOOKBACK_DAYS=30
NEWS_FETCH_LIMIT=1000
NEWS_FETCH_TIMEOUT_SECONDS=30

# =========================
# LLM - Bailian / DashScope
# =========================
DASHSCOPE_API_KEY=your_dashscope_api_key
BAILIAN_MODEL=qwen-plus
BAILIAN_TIMEOUT_SECONDS=60

# =========================
# Daily automatic data refresh
# =========================
ENABLE_DAILY_AUTO_REFRESH=1
DAILY_AUTO_REFRESH_HOUR=18
DAILY_AUTO_REFRESH_MINUTE=30
DAILY_AUTO_REFRESH_RUN_ON_STARTUP=0
DAILY_AUTO_REFRESH_FORCE=0
DAILY_AUTO_REFRESH_LIMIT=7
DAILY_AUTO_REFRESH_TICKERS=AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META
DAILY_AUTO_REFRESH_MODULES=market,intraday,technical,news,news_fulltext,sentiment,features
DAILY_AUTO_REFRESH_NEWS_WINDOW_DAYS=14

# =========================
# Prediction on-demand pipeline
# =========================
PREDICTION_ON_DEMAND_PIPELINE=1
PREDICTION_ON_DEMAND_MODULES=market,technical,news,news_fulltext,sentiment,features
PREDICTION_ON_DEMAND_ALLOW_FALLBACK=1
```

修改 `.env.docker` 后重建容器配置：

```bash
docker compose up -d --force-recreate backend
```

---

## 5. Docker 启动方式

### 5.1 启动服务

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

访问 API 文档：

```text
http://127.0.0.1:8002/docs
```

健康检查：

```bash
curl http://127.0.0.1:8002/health
```

---

### 5.2 常用 Docker 命令

只改 Python 代码：

```bash
docker compose restart backend
```

改了 `.env.docker`：

```bash
docker compose up -d --force-recreate backend
```

改了 `docker-compose.yml`：

```bash
docker compose down
docker compose up -d
```

改了 `requirements.txt`：

```bash
docker compose build backend
docker compose up -d
```

依赖环境异常时：

```bash
docker compose build --no-cache backend
docker compose up -d
```

查看后端日志：

```bash
docker compose logs --tail=120 backend
```

---

## 6. 数据表初始化与补全脚本

### 6.1 创建分钟行情表

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.create_intraday_price_data_table"
```

### 6.2 运行 v1.5 增量数据补全

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh"
```

### 6.3 只刷新 features

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules features --force-refresh"
```

### 6.4 只刷新最近完整交易日的分钟行情

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules intraday"
```

系统默认使用 `price_data` 中每只股票的最新 `trading_date` 作为分钟行情目标日，避免抓取当天盘中残缺数据。

### 6.5 指定日期刷新分钟行情

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules intraday --target-date 2026-06-08"
```

### 6.6 修复日频派生字段

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.repair_price_derived_fields"
```

### 6.7 删除残缺分钟行情

先 dry-run：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.delete_incomplete_intraday_rows --date 2026-06-09 --dry-run"
```

确认后执行：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.delete_incomplete_intraday_rows --date 2026-06-09"
```

### 6.8 修复特征快照中的行情派生字段

只修最新 snapshot：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data --latest-only"
```

全量修复：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data"
```

---

## 7. 默认账号

如果数据库已有 seed 或演示账号：

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

获取 token：

```bash
ADMIN_TOKEN=$(curl -s -X POST http://127.0.0.1:8002/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin123"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
```

---

## 8. 当前主要 API 模块

| 模块              | 前缀                 | 说明                                           |
| ----------------- | -------------------- | ---------------------------------------------- |
| Auth API          | `/api/auth`          | 注册、登录、当前用户                           |
| Watchlist API     | `/api/watchlist`     | 自选股增删查                                   |
| Stock API         | `/api/stocks`        | 股票搜索、详情、新闻、情绪摘要                 |
| Prediction API    | `/api/predictions`   | 单股预测、预测历史、预测详情                   |
| Backtest API      | `/api/backtest`      | 回测任务、状态、帧、日志、详情、汇总、最终持仓 |
| Admin User API    | `/api/admin/users`   | 管理员用户管理                                 |
| Log API           | `/api/logs`          | 管理员日志查询                                 |
| Model Info API    | `/api/models`        | 当前启用模型查询                               |
| Crawler API       | `/api/crawler`       | 股票基础库同步、每日数据补全兼容入口           |
| Data Pipeline API | `/api/data-pipeline` | 数据覆盖检查和统一数据链路任务                 |

---

## 9. 股票接口

### 9.1 股票搜索

```bash
curl "http://127.0.0.1:8002/api/stocks/search?keyword=GOOGL&only_supported=false&include_etf=true&limit=10" \
  -H "Authorization: Bearer $USER_TOKEN"
```

### 9.2 股票详情

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1m&include_news=true&include_indicators=true&auto_refresh=false" \
  -H "Authorization: Bearer $USER_TOKEN"
```

返回重点字段：

```text
current_quote.current_price
current_quote.previous_close
current_quote.change
current_quote.change_percent
current_quote.daily_return
current_quote.amplitude
price_curve
indicator_curve
latest_news
sentiment_counts
sentiment_summary
```

### 9.3 新闻列表

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/news?limit=20" \
  -H "Authorization: Bearer $USER_TOKEN"
```

支持滚动加载时，前端可传入时间范围、limit、cursor 等参数，具体以当前 `stocks.py` 路由实现为准。

### 9.4 新闻详情

```bash
curl "http://127.0.0.1:8002/api/stocks/news/{news_id}" \
  -H "Authorization: Bearer $USER_TOKEN"
```

新闻详情优先返回数据库中的正文缓存。若正文未抓取成功，可能返回摘要或抓取状态。

### 9.5 14 日情绪摘要

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/sentiment-summary?window_days=14" \
  -H "Authorization: Bearer $USER_TOKEN"
```

---

## 10. Data Pipeline

### 10.1 查询数据覆盖情况

```bash
curl "http://127.0.0.1:8002/api/data-pipeline/coverage?ticker=GOOGL&end_date=2026-06-08"
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

常见状态：

| 状态            | 含义                       |
| --------------- | -------------------------- |
| `ok`            | 已覆盖目标日期             |
| `cached`        | 已有缓存，可直接使用       |
| `stale`         | 有数据，但没有覆盖目标日期 |
| `empty`         | 表存在，但该 ticker 无数据 |
| `missing_table` | 表不存在                   |
| `not_ready`     | 核心数据不足，需要补全     |
| `ready`         | 核心数据可用于预测         |

---

### 10.2 创建同步数据任务

```bash
curl -X POST http://127.0.0.1:8002/api/data-pipeline/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "tickers": ["GOOGL"],
    "end_date": "2026-06-08",
    "modules": ["market", "intraday", "technical", "news", "news_fulltext", "sentiment", "features"],
    "force_refresh": false,
    "run_async": false
  }'
```

---

### 10.3 命令行运行数据链路

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh \
--modules market,intraday,technical,news,news_fulltext,sentiment,features"
```

只跑新闻正文：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules news_fulltext"
```

---

## 11. 预测接口

### 11.1 自动使用最新可用交易日预测

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

### 11.2 指定基准日预测

```bash
curl -X POST http://127.0.0.1:8002/api/predictions/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -d '{
    "ticker": "GOOGL",
    "forecast_days": 5,
    "base_trading_date": "2026-06-08",
    "analysis_mode": "full",
    "risk_profile": "balanced",
    "news_window_days": 14,
    "force_refresh": false
  }'
```

含义：

```text
使用 base_trading_date 当天及之前的行情、技术指标、新闻情绪、财报特征作为输入，
预测之后若干交易日的价格路径。
```

限制：

```text
当前 v1.2 回归模型最多输出未来 5 个交易日，因此 forecast_days 建议限制为 1~5。
```

---

### 11.3 预测前数据准备逻辑

预测接口执行前会调用 `prediction_input_service.py`：

```text
1. 优先查 model_feature_snapshots 是否已有目标日期运行时快照；
2. 如果有，直接使用数据库快照；
3. 如果没有，自动调用 Data Pipeline：
   market -> technical -> news -> news_fulltext -> sentiment -> features
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

---

### 11.4 预测返回关键字段

```json
{
  "prediction_id": 37,
  "ticker": "GOOGL",
  "base_trading_date": "2026-06-08",
  "forecast_days": 5,
  "classification": {
    "predicted_label": "down",
    "prob_up": 0.0243211,
    "prob_neutral": 0.0,
    "prob_down": 0.975679
  },
  "regression": {
    "price_path": []
  },
  "news_summary": {
    "news_start_time": "2026-05-26",
    "news_end_time": "2026-06-08",
    "sentiment_curve": []
  },
  "llm_report": "..."
}
```

实际字段以当前 `schemas/prediction.py` 和路由返回为准。

---

## 12. 模型说明

当前已接入 B 同学交付的 v1.2 模型：

| 模型                              | 类型           | 说明                                                     |
| --------------------------------- | -------------- | -------------------------------------------------------- |
| `finsight_cls_abs_h15_v1.2`       | classifier     | 主分类模型，二分类输出，经后端适配为 down / neutral / up |
| `finsight_cls_action1p5_h10_v1.2` | aux_classifier | 辅助强信号模型，用于推荐分数微调                         |
| `finsight_reg_return_path_v1.2`   | regressor      | 回归路径模型，输出未来 5 个交易日收益率路径              |

模型输入来自：

```text
model_feature_snapshots.features_json
```

当前运行时快照包含约 52 个特征，主要包括：

```text
行情特征：
open / high / low / close / volume
previous_close / change_amount / change_percent / daily_return / amplitude

技术指标：
return_1d / return_3d / return_5d
ma5 / ma20 / ma60
ma5_gap / ma20_gap / ma60_gap
rsi / macd
volatility_20d / drawdown_20d / volume_zscore

新闻情绪：
news_count
positive_news_count / negative_news_count / neutral_news_count
positive_ratio / negative_ratio
sentiment_score
sentiment_score_3d_avg / sentiment_score_7d_avg

财报特征：
fundamental_available
fund_reported_eps / fund_estimated_eps / fund_eps_surprise / fund_eps_surprise_pct
fund_total_revenue / fund_revenue_yoy
fund_net_income / fund_net_income_yoy
fund_gross_profit / fund_gross_margin
fund_operating_income / fund_operating_margin
fund_net_margin
fund_report_age_days / fund_days_since_fiscal_end
```

---

## 13. 数据库表说明

### 13.1 当前核心表

| 表                        | 说明                            |
| ------------------------- | ------------------------------- |
| `users`                   | 用户账号                        |
| `stocks`                  | 股票基础库                      |
| `watchlists`              | 用户自选股                      |
| `price_data`              | 日频 OHLCV 与涨跌幅派生字段     |
| `intraday_price_data`     | 1min 分钟行情                   |
| `technical_indicators`    | MA、RSI、MACD、波动率等技术指标 |
| `news_data`               | 新闻标题、摘要、情绪、原文正文  |
| `sentiment_daily`         | 按 ticker/date 聚合的新闻情绪   |
| `model_feature_snapshots` | 模型运行时特征快照              |
| `model_versions`          | 模型版本信息                    |
| `predictions`             | 预测记录                        |
| `crawler_logs`            | 爬虫与数据链路日志              |
| `operation_logs`          | 用户与系统操作日志              |

### 13.2 回测相关表

| 表                         | 说明         |
| -------------------------- | ------------ |
| `backtest_runs`            | 回测任务     |
| `backtest_event_logs`      | 回测事件日志 |
| `portfolio_snapshots`      | 组合净值快照 |
| `backtest_daily_positions` | 每日持仓     |
| `backtest_trades`          | 回测交易记录 |
| `user_simulated_positions` | 用户模拟持仓 |

当前回测表结构和接口已存在，但完整逐日策略回测仍是后续重点。

---

## 14. 数据库状态检查

### 14.1 快速检查核心表覆盖

```bash
docker compose exec backend bash -lc 'PYTHONPATH=/app python - <<PY
from sqlalchemy import text
from app.db.session import SessionLocal

TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
db = SessionLocal()

for table, date_col in [
    ("price_data", "trading_date"),
    ("technical_indicators", "trading_date"),
    ("sentiment_daily", "trading_date"),
    ("model_feature_snapshots", "base_trading_date"),
]:
    print("\\n==", table, "==")
    for ticker in TICKERS:
        row = db.execute(text(f"""
            SELECT COUNT(*) AS cnt, MIN({date_col}) AS min_date, MAX({date_col}) AS max_date
            FROM {table}
            WHERE ticker = :ticker
        """), {"ticker": ticker}).mappings().first()
        print(ticker, dict(row))

print("\\n== intraday_price_data ==")
for ticker in TICKERS:
    row = db.execute(text("""
        SELECT trading_date, COUNT(*) AS cnt, MIN(market_timestamp) AS min_ts, MAX(market_timestamp) AS max_ts
        FROM intraday_price_data
        WHERE ticker = :ticker
        GROUP BY trading_date
        ORDER BY trading_date DESC
        LIMIT 1
    """), {"ticker": ticker}).mappings().first()
    print(ticker, dict(row) if row else None)

db.close()
PY'
```

### 14.2 检查最新 feature snapshot 派生字段

```bash
docker compose exec backend bash -lc 'PYTHONPATH=/app python - <<PY
import json
from sqlalchemy import text
from app.db.session import SessionLocal

TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
db = SessionLocal()

for ticker in TICKERS:
    row = db.execute(text("""
        SELECT id, ticker, base_trading_date, current_price, features_json
        FROM model_feature_snapshots
        WHERE ticker = :ticker
        ORDER BY base_trading_date DESC, id DESC
        LIMIT 1
    """), {"ticker": ticker}).mappings().first()

    features = row["features_json"]
    if isinstance(features, str):
        features = json.loads(features)

    print({
        "ticker": ticker,
        "base_trading_date": str(row["base_trading_date"]),
        "current_price": float(row["current_price"]),
        "feature_close": features.get("close"),
        "feature_daily_return": features.get("daily_return"),
        "feature_change_percent": features.get("change_percent"),
        "feature_amplitude": features.get("amplitude"),
    })

db.close()
PY'
```

---

## 15. API 自动化测试

运行完整测试：

```bash
python finsight_api_auto_test.py --base-url http://127.0.0.1:8002
```

指定预测基准日：

```bash
python finsight_api_auto_test.py \
  --base-url http://127.0.0.1:8002 \
  --prediction-base-date 2026-06-08
```

开启 Data Pipeline 测试：

```bash
python finsight_api_auto_test.py \
  --base-url http://127.0.0.1:8002 \
  --run-data-pipeline \
  --pipeline-ticker GOOGL \
  --pipeline-target-date 2026-06-08
```

测试报告输出：

```text
api_test_results/
├── finsight_api_test_report_时间戳.json
└── finsight_api_test_report_时间戳.md
```

---

## 16. 数据备份与恢复

### 16.1 备份

```bash
mkdir -p backups

docker compose exec db sh -c \
'mysqldump --no-tablespaces --single-transaction --quick --routines --triggers -ufinsight_user -pfinsight_password finsight' \
> backups/finsight_backup_$(date +%Y%m%d_%H%M%S).sql
```

### 16.2 恢复

```bash
docker compose exec -T db mysql -ufinsight_user -pfinsight_password finsight < backups/你的备份文件.sql
```

---

## 17. Git 协作建议

### 17.1 切换到 v1.5 分支

```bash
git fetch origin
git switch v1.5 || git switch -c v1.5 origin/v1.5 || git switch -c v1.5
```

### 17.2 提交当前 v1.5 hotfix

```bash
git status

git add app/core/config.py
git add app/models/all_models.py
git add app/services/twelvedata_market_service.py
git add app/services/market_data_service.py
git add app/services/intraday_market_service.py
git add app/services/data_pipeline_service.py
git add app/services/daily_refresh_service.py
git add app/services/feature_snapshot_service.py
git add app/scripts/create_intraday_price_data_table.py
git add app/scripts/run_twelvedata_incremental_refresh.py
git add app/scripts/ensure_ticker_data_on_demand.py
git add app/scripts/repair_price_derived_fields.py
git add app/scripts/delete_incomplete_intraday_rows.py
git add app/scripts/repair_feature_snapshots_from_price_data.py
git add README.md

git commit -m "fix(data): stabilize v1.5 Twelve Data pipeline and feature snapshots"
git push -u origin v1.5
```

### 17.3 不建议提交

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

### 17.4 可以提交

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

---

## 18. 常见问题

### 18.1 后端一直重启

```bash
docker compose logs --tail=120 backend
```

### 18.2 8002 端口被占用

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

### 18.3 修改 Python 代码没生效

```bash
docker compose restart backend
```

如果没有 volume 挂载，才需要重新 build。

### 18.4 `.env.docker` 修改没生效

```bash
docker compose up -d --force-recreate backend
```

### 18.5 日频涨跌幅变成 0 或 NULL

先修复 `price_data`：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.repair_price_derived_fields"
```

再修复 feature snapshot：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data --latest-only"
```

### 18.6 分钟行情只有几十条

说明抓到了当天盘中残缺数据。删除残缺交易日后，重新抓最近完整交易日：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.delete_incomplete_intraday_rows --date 2026-06-09"

docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules intraday"
```

### 18.7 预测日期为什么不是今天

模型是日频模型，输入需要完整交易日收盘后的特征。如果今天没有完整收盘行情，系统会使用最近一个完整交易日作为 `actual_base_trading_date`。

### 18.8 LLM 报告失败

检查：

```text
DASHSCOPE_API_KEY 是否配置
BAILIAN_MODEL 是否可用
网络是否能访问 DashScope
后端日志是否有 timeout 或 unauthorized
```

LLM 失败时，预测主流程不应中断，应返回模板降级报告或错误说明。

---

## 19. 当前待开发重点

建议后续按优先级推进：

1. **完整逐日回测引擎**
   - 逐日交易循环
   - 买入 / 卖出 / 持有决策
   - 持仓与现金更新
   - 回测动画帧
   - 交易日志
   - 最终持仓

2. **前端回测动画数据完全接入**
   - 组合净值曲线
   - 每日新闻
   - 每日指标
   - 每日交易动作
   - 最终持仓展示

3. **财报特征更严格生成**
   - 从财报表或在线财报接口生成逐日 `fund_*`
   - 替代简单 carry-forward
   - 明确财报发布日期与可见性，避免未来函数

4. **数据质量面板**
   - 每只股票最新行情日期
   - 最新分钟行情日期
   - 最新 feature snapshot 日期
   - 新闻覆盖日期
   - 新闻正文覆盖率
   - 抓取失败原因统计

5. **数据源稳定性**
   - Twelve Data 限流处理
   - Alpha Vantage 限流处理
   - 失败重试与退避
   - 付费数据源替换预留

---

## 20. 项目定位总结

当前版本是：

```text
Finsight Backend v1.5：
FastAPI + MySQL + Docker + v1.2 三模型推理
+ 百炼 LLM 报告
+ Twelve Data 日频/分钟行情
+ Alpha Vantage 新闻情绪
+ 新闻正文抓取
+ 52 维运行时特征快照
+ 数据库优先补全
+ 按需预测数据准备
+ 每日自动补全调度
```

已经适合：

```text
前端联调
用户系统
股票搜索与详情
新闻列表与新闻详情
预测页面
预测历史
LLM 综合解释报告
模型版本展示
Data Pipeline 验证
每日数据补全任务测试
日志页面
基础回测接口联调
```

仍需继续完善：

```text
真实逐日回测交易引擎
更严格的财报特征
更稳定的数据源治理
数据质量可视化面板
```