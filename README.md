# Finsight Backend

**Finsight / 智融洞察：面向股票趋势预测与模拟回测的金融分析系统**

本仓库是 Finsight 项目的后端部分，采用 **FastAPI + MySQL + Docker Compose** 架构。当前版本定位为：

> 后端可运行 API 骨架 + MySQL 数据库基础实现 + 演示数据 + 占位预测/回测逻辑

当前版本适合用于前端联调、接口测试、用户管理、股票查询、预测历史、日志查询等基础流程。真实行情爬虫、真实新闻爬虫、真实 XGBoost 模型、LLM 报告和完整回测引擎仍需后续接入。

---

## 1. 当前项目状态

### 1.1 已完成

- FastAPI 后端主框架
- MySQL 数据库连接与 ORM 表结构
- Docker Compose 一键启动后端和数据库
- JWT 登录鉴权
- 用户注册、登录、当前用户信息
- 管理员用户管理
- 股票基础库搜索
- 股票详情查询
- 新闻列表与新闻详情查询
- 自选股增删查
- 单股预测接口
- 预测历史卡片列表
- 预测详情接口
- 模型版本信息查询
- 日志查询
- 爬虫状态查询
- 股票基础库同步接口
- 回测相关 API 路由壳
- API 自动化测试脚本

### 1.2 当前接口测试情况

最近一次接口自动化测试结果：

```text
Total: 37
Passed: 37
Failed: 0
```

说明当前后端接口连通性和基础响应结构已经基本可用。

### 1.3 仍未完成 / 当前为占位实现

- 真实行情爬虫尚未接入
- 真实新闻爬虫尚未接入
- 新闻情绪分析目前主要是演示数据
- XGBoost 分类模型尚未真实加载
- XGBoost 回归模型尚未真实加载
- LLM 新闻分析和综合报告目前为模板/占位文本
- 回测接口已存在，但真实逐日回测引擎尚未完成
- `portfolio_snapshots`、`backtest_daily_positions`、`backtest_trades`、`user_simulated_positions` 等回测结果表目前主要为空或无真实业务数据

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
| 部署 | Docker Compose |
| API 文档 | Swagger UI / OpenAPI |
| 语言 | Python 3.10 |

---

## 3. 项目结构

```text
finsight_backend_v1/
├── app/
│   ├── main.py                      # FastAPI 应用入口
│   ├── core/                        # 配置、安全、响应格式、异常处理
│   ├── db/                          # 数据库连接与初始化
│   ├── models/                      # SQLAlchemy ORM 模型
│   ├── schemas/                     # Pydantic 请求/响应模型
│   ├── routers/                     # API 路由
│   ├── services/                    # 业务逻辑
│   └── scripts/                     # 初始化数据、同步脚本
├── docker/
│   └── entrypoint.sh                # Docker 后端启动脚本
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── run.py
├── finsight_api_auto_test.py        # API 自动化测试脚本
├── .env.example                     # 普通环境变量模板
├── .env.docker.example              # Docker 环境变量模板
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

检查 `.env.docker` 中的数据库连接，Docker 内部应使用服务名 `db`：

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
```

注意：

```text
后端容器连接 MySQL 容器时使用 db:3306
不是 localhost:3306
```

### 4.2 启动服务

```bash
docker compose up -d
```

查看容器状态：

```bash
docker compose ps
```

正常状态应类似：

```text
finsight_backend   Up   0.0.0.0:8002->8000/tcp
finsight_mysql     Up   0.0.0.0:3308->3306/tcp
```

### 4.3 访问 API 文档

浏览器打开：

```text
http://127.0.0.1:8002/docs
```

终端测试：

```bash
curl http://127.0.0.1:8002/docs
curl http://127.0.0.1:8002/health
```

### 4.4 查看日志

```bash
docker compose logs -f backend
```

如果启动成功，日志中应能看到：

```text
Database is ready.
Running demo seed script...
Seed demo data inserted.
INFO:     Application startup complete.
```

---

## 5. 当前端口说明

| 服务 | 容器内部端口 | 宿主机端口 | 说明 |
|---|---:|---:|---|
| FastAPI Backend | 8000 | 8002 | 访问 API 文档和接口 |
| MySQL | 3306 | 3308 | 宿主机连接数据库使用 |

后端访问地址：

```text
http://127.0.0.1:8002
```

MySQL 宿主机连接方式：

```bash
mysql -h 127.0.0.1 -P 3308 -u finsight_user -p finsight
```

密码：

```text
finsight_password
```

---

## 6. 默认演示账号

初始化脚本会创建默认账号：

```text
管理员：admin / Admin123
普通用户：user01 / User123
```

登录接口示例：

```bash
curl -X POST http://127.0.0.1:8002/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin123"}'
```

---

## 7. 环境变量文件说明

### 7.1 `.env.docker`

`.env.docker` 是 **Docker 容器运行时使用的环境变量配置文件**。

`docker-compose.yml` 中通过：

```yaml
env_file:
  - .env.docker
```

将它注入后端容器。

通常包含：

```env
APP_NAME=Finsight Backend
ENV=development
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
SECRET_KEY=please-change-this-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

修改 `.env.docker` 后，通常 **不需要重新 build**，只需要重新创建后端容器：

```bash
docker compose up -d --force-recreate backend
```

### 7.2 `.env.example`

`.env.example` 是普通本地运行配置模板，不会被程序自动读取。它用于告诉组员本项目需要哪些环境变量。

组员可复制：

```bash
cp .env.example .env
```

再根据自己的本地环境修改。

### 7.3 `.env.docker.example`

`.env.docker.example` 是 Docker 配置模板，建议提交到 Git。  
真实 `.env.docker` 不建议提交到 Git，因为里面可能有数据库密码和密钥。

---

## 8. 常用 API 模块

当前后端提供以下主要 API：

| 模块 | 前缀 | 说明 |
|---|---|---|
| Auth API | `/api/auth` | 注册、登录、当前用户 |
| Watchlist API | `/api/watchlist` | 自选股增删查 |
| Stock API | `/api/stocks` | 股票搜索、详情、新闻、情绪摘要 |
| Prediction API | `/api/predictions` | 单股预测、预测历史、预测详情 |
| Backtest API | `/api/backtest` | 回测任务、状态、帧、日志、最终持仓接口壳 |
| Admin User API | `/api/admin/users` | 管理员用户管理 |
| Log API | `/api/logs` | 管理员日志查询 |
| Model Info API | `/api/models` | 当前启用模型查询 |
| Crawler API | `/api/crawler` | 爬虫状态和股票基础库同步 |

---

## 9. API 自动化测试

运行 API 自动化测试：

```bash
python finsight_api_auto_test.py --base-url http://127.0.0.1:8002
```

测试完成后会生成报告：

```text
api_test_results/
├── finsight_api_test_report_时间戳.json
└── finsight_api_test_report_时间戳.md
```

报告会记录：

- 请求方法
- 请求 URL
- 请求参数
- 请求体
- 返回状态码
- 返回 JSON
- 耗时
- 是否通过

注意：`api_test_results/` 不建议提交到 Git。

---

## 10. 日常开发推荐命令

### 10.1 只改了 Python 代码

如果 `docker-compose.yml` 中已经挂载：

```yaml
volumes:
  - .:/app
```

那么只改 Python 代码时，不需要重新 build：

```bash
docker compose restart backend
```

### 10.2 改了 `.env.docker`

环境变量是运行时配置，不需要重新 build：

```bash
docker compose up -d --force-recreate backend
```

### 10.3 改了 `docker-compose.yml`

例如改端口、volume、服务名等：

```bash
docker compose down
docker compose up -d
```

### 10.4 改了 `requirements.txt`

依赖变化需要重新构建后端镜像：

```bash
docker compose build backend
docker compose up -d
```

### 10.5 出现奇怪依赖问题才用

只有依赖缓存损坏、构建层异常、依赖版本冲突时才使用无缓存构建：

```bash
docker compose build --no-cache backend
docker compose up -d
```

平时不要频繁使用 `--no-cache`，因为它会重新执行 apt 和 pip 安装，速度较慢。

---

## 11. 当前数据库中数据说明

### 11.1 真实基础数据

| 表 | 当前数据性质 |
|---|---|
| `roles` | 系统角色基础数据 |
| `users` | 用户账号数据，包含演示账号和测试账号 |
| `stocks` | 股票基础库数据，部分来自同步，ticker / security_name 等字段较接近真实 |
| `watchlists` | 用户真实操作产生的自选股记录 |
| `operation_logs` | 测试和操作过程中真实写入的日志 |
| `stock_universe_sync_logs` | 股票基础库同步日志 |

### 11.2 演示或占位数据

| 表 | 当前数据性质 |
|---|---|
| `price_data` | 主要是 seed_demo 插入的演示行情 |
| `news_data` | 主要是演示新闻或空数据 |
| `technical_indicators` | 当前多为空或演示数据 |
| `sentiment_daily` | 当前多为演示情绪聚合 |
| `model_versions` | 演示模型元数据，未真实加载模型 |
| `predictions` | 真实请求记录 + 占位预测结果 |
| `backtest_runs` | 真实创建的任务记录，但回测计算未完成 |
| `backtest_event_logs` | 回测任务创建日志，目前多为占位日志 |
| `portfolio_snapshots` | 真实回测未接入，当前基本为空 |
| `backtest_daily_positions` | 真实回测未接入，当前基本为空 |
| `backtest_trades` | 真实回测未接入，当前基本为空 |
| `user_simulated_positions` | 真实回测未接入，当前基本为空 |

---

## 12. 当前仍需后续开发的重点

### 12.1 真实行情数据

需要接入：

- Yahoo Finance / yfinance / 其他行情源
- OHLCV 历史行情
- 缺失交易日补齐
- `daily_return`
- `amplitude`
- `fifty_two_week_high`
- `fifty_two_week_low`

### 12.2 技术指标计算

需要实现：

- MA5
- MA20
- MA60
- RSI
- MACD
- 20 日波动率
- 20 日回撤
- 成交量 z-score

### 12.3 新闻爬虫与新闻情绪

需要实现：

- 新闻列表抓取
- 新闻正文抓取
- HTML 清洗
- 新闻去重
- 新闻归属交易日映射
- 情绪分数计算
- 每日情绪聚合

### 12.4 真实模型推理

当前预测服务仍为占位逻辑。后续需要接入：

- XGBoost 分类模型
- XGBoost 回归模型
- `model_versions.model_path`
- 特征构造
- 模型加载缓存
- 推荐分数计算
- 结构化解释

### 12.5 LLM 报告

当前报告主要为模板文本。后续可接入：

- 新闻级 LLM 分析
- 预测综合 LLM 报告
- Prompt 模板
- 调用超时与失败降级

### 12.6 真实异步回测引擎

当前回测 API 只实现接口壳。后续需要实现：

- 创建 run 后进入 running
- 生成交易日序列
- 每日生成预测信号
- 买入 / 卖出 / 持有决策
- 更新现金和持仓
- 写入 `portfolio_snapshots`
- 写入 `backtest_daily_positions`
- 写入 `backtest_trades`
- 写入 `backtest_event_logs`
- 完成后写入 `user_simulated_positions`

---

## 13. Git 协作建议

### 13.1 不建议提交的内容

以下内容应在 `.gitignore` 中忽略：

```text
__pycache__/
*.pyc
.env
.env.docker
api_test_results/
mysql_data/
logs/
.venv/
venv/
*.db
*.sqlite
models_artifacts/
artifacts/
```

### 13.2 建议提交的内容

```text
app/
docker/
Dockerfile
docker-compose.yml
requirements.txt
run.py
README.md
README_DOCKER.md
.env.example
.env.docker.example
.gitignore
finsight_api_auto_test.py
```

### 13.3 分支建议

```text
main
feature/stock-crawler
feature/news-crawler
feature/prediction-model
feature/backtest-engine
feature/frontend-api-client
fix/api-response-fields
fix/docker-config
```

---

## 14. 常见问题

### 14.1 端口被占用

如果 8002 被占用，修改 `docker-compose.yml`：

```yaml
ports:
  - "8003:8000"
```

然后：

```bash
docker compose down
docker compose up -d
```

### 14.2 后端一直重启

查看日志：

```bash
docker compose logs --tail=100 backend
```

### 14.3 MySQL 容器正常但后端连不上数据库

检查 `.env.docker`：

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
```

Docker 容器内部必须使用 `db:3306`。

### 14.4 修改 Python 代码后没有生效

如果已经挂载 volume：

```yaml
volumes:
  - .:/app
```

执行：

```bash
docker compose restart backend
```

如果没有挂载 volume，需要重新 build：

```bash
docker compose build backend
docker compose up -d
```

---

## 15. 项目定位总结

当前版本是：

```text
Finsight 后端 v1.x：可运行 API 骨架 + MySQL 数据库 + Docker 部署 + 演示数据 + 占位预测/回测逻辑
```

已经适合：

```text
前端联调
接口测试
管理员页面开发
股票搜索与详情页面开发
预测结果页面开发
预测历史页面开发
日志页面开发
```

仍需后续完成：

```text
真实数据
真实模型
真实新闻分析
真实回测动画
最终模拟持仓业务闭环
```
