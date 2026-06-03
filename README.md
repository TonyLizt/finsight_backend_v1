# Finsight Backend

**Finsight / 智融洞察：面向股票趋势预测与模拟回测的金融分析系统**

本仓库是 Finsight 项目的后端部分，采用 **FastAPI + MySQL + Docker Compose** 架构。当前版本已经从早期“API 骨架 + 占位预测”推进到：

> 可运行后端 + MySQL 数据库 + v1.2 真实模型初步接入 + 50 维特征快照 + 在线行情补全框架 + 每日数据补全任务 + 预测历史保存

当前版本适合用于前端联调、接口测试、股票查询、用户管理、单股预测、预测历史、模型版本查询、日志查看、每日数据补全任务验证等流程。  
真实新闻 LLM 深度分析和完整逐日回测引擎仍需继续实现。

---

## 1. 当前项目状态

### 1.1 已完成

- FastAPI 后端主框架
- MySQL 8.0 数据库连接与 ORM 表结构
- Docker Compose 一键启动后端和数据库
- JWT 登录鉴权
- 用户注册、登录、当前用户信息
- 管理员用户管理，包括：
  - 查询用户列表
  - 查看用户详情
  - 修改用户状态
  - 修改用户角色
  - 修改用户名
  - 重置用户密码
  - 软删除 / 硬删除用户
- 股票基础库搜索
- 股票详情查询
- 股票新闻列表与新闻详情查询
- 用户自选股增删查
- v1.2 模型版本接入
- v1.2 分类模型加载与推理
- v1.2 回归模型加载与未来 5 个交易日价格路径输出
- v1.2 辅助强信号模型接入
- 二分类模型输出适配为原 API 的 `prob_down / prob_neutral / prob_up`
- `model_feature_snapshots` 50 维特征快照表接入
- 单股预测接口
- 支持 `base_trading_date` 指定预测基准日
- 预测结果保存到 `predictions`
- 预测历史卡片列表
- 预测详情接口
- 当前启用模型查询
- 股票基础库同步接口
- 每日数据补全接口：
  - 手动触发
  - 查询最近任务状态
  - 写入 `crawler_logs`
- 在线行情补全服务：
  - 默认优先 Alpha Vantage
  - 可选 Yahoo Chart 兜底
  - 可选本地 CSV fallback
  - 抓取失败时停止生成新特征，避免假数据污染
  - 异常价格检测
- 技术指标重算服务
- runtime 50 维 feature snapshot 生成
- API 自动化测试脚本

---

### 1.2 当前接口测试情况

最近一次旧版接口自动化测试曾达到：

```text
Total: 37
Passed: 37
Failed: 0
```

新版后端新增了以下能力，建议使用本仓库新版 `finsight_api_auto_test.py` 重新测试：

```text
/api/crawler/daily-refresh/run
/api/crawler/daily-refresh/status
/api/predictions/run 支持 base_trading_date
/api/predictions/run 返回 data_refresh_status / strong_signal_score 等字段
```

测试报告默认输出到：

```text
api_test_results/
```

该目录不建议提交到 Git。

---

### 1.3 当前仍未完成 / 仍为降级实现

- 新闻自动抓取尚未完全接入日常预测链路
- 2025-05-21 之后新闻数据 / 情绪数据可能不完整
- 新闻 LLM 深度分析仍为模板降级版本
- 综合 LLM 报告仍为模板降级版本
- `fund_*` 财报特征当前主要采用最近真实快照 carry-forward 方式
- 回测 API 路由已存在，但完整逐日交易引擎仍未完成
- 回测动画帧、逐日持仓、逐日交易、最终持仓等仍需进一步接入真实策略
- 外部行情源存在额度、限流、403、premium endpoint 等失败场景，需要通过日志排查

---

## 2. 技术栈

| 类型             | 技术                 |
| ---------------- | -------------------- |
| Web 框架         | FastAPI              |
| 数据库           | MySQL 8.0            |
| ORM              | SQLAlchemy           |
| 数据库驱动       | PyMySQL              |
| 鉴权             | JWT                  |
| 密码哈希         | passlib + bcrypt     |
| 模型加载         | joblib               |
| 机器学习运行环境 | scikit-learn 1.8.0   |
| 数据处理         | pandas / numpy       |
| 外部请求         | requests             |
| 部署             | Docker Compose       |
| API 文档         | Swagger UI / OpenAPI |
| 语言             | Python 3.11          |

> 注意：B 同学交付的 v1.2 模型使用 scikit-learn 1.8.0 环境训练/保存。若运行环境低于该版本，可能出现模型反序列化 warning 或推理错误。

---

## 3. 项目结构

```text
finsight_backend_v1/
├── app/
│   ├── main.py                         # FastAPI 应用入口
│   ├── core/                           # 配置、安全、响应格式、异常处理
│   ├── db/                             # 数据库连接与初始化
│   ├── models/                         # SQLAlchemy ORM 模型
│   ├── schemas/                        # Pydantic 请求/响应模型
│   ├── routers/                        # API 路由
│   ├── services/                       # 业务逻辑
│   │   ├── model_service.py             # 模型加载与推理
│   │   ├── feature_service.py           # 特征读取
│   │   ├── feature_snapshot_service.py  # runtime 特征快照生成
│   │   ├── market_data_service.py       # 在线行情补全
│   │   ├── indicator_service.py         # 技术指标重算
│   │   ├── prediction_input_service.py  # 预测前数据准备
│   │   ├── prediction_service.py        # 预测业务逻辑
│   │   ├── daily_refresh_service.py     # 每日数据补全任务
│   │   └── ...
│   └── scripts/                        # 初始化、导入、测试脚本
│       ├── import_member_b_real_data.py
│       ├── run_daily_data_refresh.py
│       ├── test_online_market_fetch.py
│       └── ...
├── artifacts/
│   └── models/                         # v1.2 模型文件，可随项目提交
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

### 4.2 推荐 `.env.docker` 基础配置

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
SECRET_KEY=change-this-secret-key-in-real-deployment
ACCESS_TOKEN_EXPIRE_MINUTES=1440
PROJECT_NAME=Finsight Backend
ENVIRONMENT=docker

# 真实数据导入后建议关闭 demo seed，避免演示行情/新闻污染真实数据。
RUN_SEED=0
```

---

### 4.3 在线行情自爬配置

当前版本默认支持后端自行抓取行情，不依赖 B 同学的本地 raw CSV。推荐配置：

```env
# Online market data crawling
ALPHA_VANTAGE_API_KEY=你的AlphaVantageKey

# 默认优先 Alpha Vantage，其次 Yahoo Chart。
MARKET_DATA_SOURCE_PRIORITY=alpha_vantage,yahoo_chart

# Yahoo Chart 是兜底源，可能出现 403。
ENABLE_YAHOO_CHART_FALLBACK=1

# 默认不使用 B 同学服务器本地 CSV。
ENABLE_LOCAL_RAW_CSV_FALLBACK=0

MARKET_DATA_MIN_HISTORY_DAYS=252
MARKET_DATA_MAX_STALE_DAYS=5
MARKET_DATA_GAP_LOOKBACK_DAYS=90
MARKET_DATA_GAP_TRIGGER_COUNT=3
MARKET_DATA_TIMEOUT_SECONDS=30

# 异常价格检测，例如周围都是 300，某天突然 190。
PRICE_SUSPICIOUS_CHANGE_THRESHOLD=0.35
PRICE_QUALITY_LOOKBACK_DAYS=120
```

如果 Alpha Vantage 免费额度用完，日志中可能出现类似：

```text
free key rate limit
premium endpoint
```

这是外部数据源限制，不是后端逻辑错误。后端会返回 `failed` 或 `cached_with_fetch_failed`，不会静默生成假特征。

---

### 4.4 每日自动补全配置

```env
# 1=FastAPI 启动后开启每日自动补全线程；0=关闭。
ENABLE_DAILY_AUTO_REFRESH=1

# 每天执行时间，使用容器本地时间。
DAILY_AUTO_REFRESH_HOUR=18
DAILY_AUTO_REFRESH_MINUTE=30

# 1=启动后立刻跑一次；默认建议 0，避免启动变慢。
DAILY_AUTO_REFRESH_RUN_ON_STARTUP=0

# 是否每天强制访问外部行情源。
DAILY_AUTO_REFRESH_FORCE=0

# 每日最多刷新多少只股票。
DAILY_AUTO_REFRESH_LIMIT=50

# 可选：指定每日刷新股票列表。
DAILY_AUTO_REFRESH_TICKERS=AAPL,MSFT,NVDA,TSLA

# 新闻窗口，目前主要用于生成 sentiment 特征。
DAILY_AUTO_REFRESH_NEWS_WINDOW_DAYS=14
```

如果修改 `.env.docker`，不需要重新 build：

```bash
docker compose up -d --force-recreate backend
```

---

### 4.5 启动服务

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

| 模块           | 前缀               | 说明                                           |
| -------------- | ------------------ | ---------------------------------------------- |
| Auth API       | `/api/auth`        | 注册、登录、当前用户                           |
| Watchlist API  | `/api/watchlist`   | 自选股增删查                                   |
| Stock API      | `/api/stocks`      | 股票搜索、详情、新闻、情绪摘要                 |
| Prediction API | `/api/predictions` | 单股预测、预测历史、预测详情                   |
| Backtest API   | `/api/backtest`    | 回测任务、状态、帧、日志、详情、汇总、最终持仓 |
| Admin User API | `/api/admin/users` | 管理员用户管理                                 |
| Log API        | `/api/logs`        | 管理员日志查询                                 |
| Model Info API | `/api/models`      | 当前启用模型查询                               |
| Crawler API    | `/api/crawler`     | 股票基础库同步、每日数据补全                   |

---

## 8. 股票行情与数据补全

### 8.1 股票详情查询

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

### 8.2 手动触发每日数据补全

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
    "force_refresh": true,
    "limit": 10
  }'
```

接口会立即返回 `running`，真实任务在后台执行。

---

### 8.3 查询每日补全状态

```bash
curl http://127.0.0.1:8002/api/crawler/daily-refresh/status \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

也可以直接查数据库日志：

```sql
SELECT task_type, ticker, start_time, end_time, status, fetched_count, LEFT(message, 300) AS msg
FROM crawler_logs
WHERE task_type LIKE 'daily_data_refresh%'
ORDER BY start_time DESC
LIMIT 20;
```

---

### 8.4 命令行测试在线行情抓取

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.test_online_market_fetch AAPL 2026-06-02"
```

成功时应类似：

```json
{
  "ticker": "AAPL",
  "status": "updated",
  "can_continue": true,
  "source": "alpha_vantage",
  "latest_price_date": "2026-06-02"
}
```

失败时也会明确说明原因，例如：

```json
{
  "status": "failed",
  "can_continue": false,
  "error": "alpha_vantage failed: ...; yahoo_chart failed: ..."
}
```

抓取失败时，系统不会继续生成新的 runtime feature snapshot。

---

## 9. 预测接口说明

### 9.1 自动使用最新可用交易日预测

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

### 9.2 指定基准日预测未来 5 个交易日

```bash
curl -X POST http://127.0.0.1:8002/api/predictions/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -d '{
    "ticker": "AAPL",
    "forecast_days": 5,
    "base_trading_date": "2026-06-02",
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

如果指定日期没有行情，系统应使用该日期或之前最近一个可用交易日；返回中应查看：

```text
base_trading_date
data_refresh_status.actual_base_trading_date
```

---

## 10. v1.2 模型说明

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

每条快照包含 50 维特征，主要包括：

```text
行情特征：open/high/low/close/volume/daily_return/change_percent/amplitude
技术指标：return_1d/3d/5d、ma5/ma20/ma60、rsi、macd、volatility_20d、drawdown_20d、volume_zscore
新闻情绪：news_count、positive/negative/neutral_news_count、sentiment_score、3d/7d 平均情绪、positive_ratio、negative_ratio
财报特征：fundamental_available、EPS、revenue、net_income、margin、YoY 等
```

---

## 11. 数据库中数据说明

### 11.1 较真实 / 当前正在使用的数据

| 表                        | 当前数据性质                                        |
| ------------------------- | --------------------------------------------------- |
| `users`                   | 真实账号 + 演示账号                                 |
| `stocks`                  | 股票基础库                                          |
| `price_data`              | 当前可由在线行情接口补全，也可能含历史导入数据      |
| `technical_indicators`    | 可由 `indicator_service.py` 从 `price_data` 重算    |
| `model_feature_snapshots` | 包含训练特征快照和 runtime_v1_2_auto 运行时特征快照 |
| `model_versions`          | 当前已注册 v1.2 模型版本                            |
| `predictions`             | 真实预测请求记录                                    |
| `watchlists`              | 用户操作产生的自选股                                |
| `operation_logs`          | 用户/系统操作日志                                   |
| `crawler_logs`            | 股票基础库同步、每日数据补全日志                    |

### 11.2 部分降级 / 仍需完善的数据

| 表                         | 当前数据性质                       |
| -------------------------- | ---------------------------------- |
| `news_data`                | 新闻数据仍需进一步完善自动抓取     |
| `sentiment_daily`          | 2025-05-21 之后可能不完整          |
| `backtest_runs`            | 能创建任务，但真实逐日回测仍待完善 |
| `backtest_event_logs`      | 部分为任务创建或占位日志           |
| `portfolio_snapshots`      | 完整回测未接入前可能为空           |
| `backtest_daily_positions` | 完整回测未接入前可能为空           |
| `backtest_trades`          | 完整回测未接入前可能为空           |
| `user_simulated_positions` | 完整回测未接入前可能为空           |

---

## 12. API 自动化测试

运行：

```bash
python finsight_api_auto_test.py --base-url http://127.0.0.1:8002
```

指定预测基准日：

```bash
python finsight_api_auto_test.py \
  --base-url http://127.0.0.1:8002 \
  --prediction-base-date 2026-06-02
```

开启每日补全接口测试：

```bash
python finsight_api_auto_test.py \
  --base-url http://127.0.0.1:8002 \
  --run-daily-refresh \
  --daily-refresh-target-date 2026-06-02
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
每日补全接口是否能启动
```

---

## 13. 数据备份与恢复

### 13.1 备份

```bash
mkdir -p backups

docker compose exec db sh -c \
'mysqldump --no-tablespaces --single-transaction --quick --routines --triggers -ufinsight_user -pfinsight_password finsight' \
> backups/finsight_backup_$(date +%Y%m%d_%H%M%S).sql
```

### 13.2 恢复

```bash
docker compose exec -T db mysql -ufinsight_user -pfinsight_password finsight < backups/你的备份文件.sql
```

---

## 14. Git 协作建议

### 14.1 不建议提交

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

### 14.2 可以提交

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

## 15. 常见问题

### 15.1 后端一直重启

```bash
docker compose logs --tail=120 backend
```

### 15.2 8002 端口被占用

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

### 15.3 修改 Python 代码没生效

```bash
docker compose restart backend
```

如果没有 volume 挂载，才需要重新 build。

### 15.4 `.env.docker` 修改没生效

```bash
docker compose up -d --force-recreate backend
```

### 15.5 行情抓取失败

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

此时系统应返回 `failed`，不会继续生成新 feature snapshot。

### 15.6 预测日期为什么不是今天

模型是日频模型，输入需要某个交易日收盘后的完整特征。如果今天还没有收盘价，系统只能使用最近一个可用交易日作为 `actual_base_trading_date`。

---

## 16. 当前待开发重点

建议按优先级继续开发：

1. **新闻数据自动补全**
   - Alpha Vantage News Sentiment
   - 新闻去重
   - `news_data` 写入
   - `sentiment_daily` 聚合
2. **财报特征更严格生成**
   - 从 `financial_reports_all.csv` 或在线财报接口生成逐日 `fund_*`
   - 替代当前 carry-forward 简化逻辑
3. **真实 LLM 报告**
   - 新闻级摘要
   - 综合分析报告
   - 超时降级
4. **完整回测引擎**
   - 逐日交易循环
   - 买入/卖出/持有决策
   - 持仓与现金更新
   - 动画帧
   - 交易日志
   - 最终持仓
5. **数据质量面板**
   - 每只股票最新行情日期
   - 最新 feature snapshot 日期
   - 新闻覆盖日期
   - 抓取失败原因统计

---

## 17. 项目定位总结

当前版本是：

```text
Finsight Backend v1.2 Integrated：
FastAPI + MySQL + Docker + v1.2 模型推理 + 50 维特征快照 + 在线行情自爬框架 + 每日数据补全任务。
```

已经适合：

```text
前端联调
用户系统
股票搜索与详情
预测页面
预测历史
模型版本展示
每日数据补全任务测试
日志页面
```

仍需继续完善：

```text
新闻自动抓取
LLM 深度报告
真实回测动画
更严格的实时数据质量控制
```