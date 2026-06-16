# Finsight Backend v1.5

> **智融洞察：面向股票趋势预测与模拟回测的金融分析系统**  
> 一个集成股票行情、新闻情绪、机器学习预测、LLM 解释报告与模拟回测的 AI for Finance 项目后端。

Finsight Backend 是 Finsight 项目的后端服务，基于 **FastAPI + MySQL + Docker Compose** 构建，向前端客户端提供统一的 HTTP/JSON API。系统支持用户登录、自选股管理、股票行情查询、新闻情绪分析、模型预测、中文解释报告、模拟回测、日志查询和管理员用户管理。

本项目面向课程实践、金融数据分析原型和 AI for Finance 演示场景。系统中的预测结果、推荐分数、新闻情绪和回测收益仅用于学习、实验和展示，**不构成任何真实投资建议**。

---

## 目录

- [项目简介](#项目简介)
- [项目特色](#项目特色)
- [系统能做什么](#系统能做什么)
- [使用流程](#使用流程)
- [技术架构](#技术架构)
- [当前版本](#当前版本)
- [环境要求](#环境要求)
- [快速启动](#快速启动)
- [环境变量说明](#环境变量说明)
- [数据库初始化](#数据库初始化)
- [主要 API 模块](#主要-api-模块)
- [模型预测说明](#模型预测说明)
- [模拟回测说明](#模拟回测说明)
- [数据流水线与爬虫](#数据流水线与爬虫)
- [自动化测试](#自动化测试)
- [项目目录结构](#项目目录结构)
- [常见问题](#常见问题)
- [安全与免责声明](#安全与免责声明)

---

## 项目简介

普通用户在分析股票时，往往需要同时查看多个维度的信息：

- 股票当前价格与历史走势；
- 技术指标，例如均线、RSI、MACD；
- 公司相关新闻；
- 新闻情绪倾向；
- 模型对未来趋势的判断；
- 如果按某套策略交易，历史上收益如何。

Finsight 的目标是把这些信息整合到同一个系统中。用户可以在前端选择股票、查看行情新闻、发起预测，并通过模拟回测观察模型信号在历史区间内的表现。后端负责数据存储、数据补齐、模型推理、LLM 报告生成和回测计算。

整体架构如下：

```text
PySide6 桌面客户端
        ↓ HTTP / JSON / JWT
FastAPI 后端服务
        ↓ SQLAlchemy / PyMySQL
MySQL 数据库
        ↓
行情数据源 / 新闻数据源 / 模型文件 / LLM 服务
```

---

## 项目特色

### 1. 行情、新闻、情绪与预测一体化

系统不是只输出一个简单的“涨/跌”结果，而是同时展示股票行情、技术指标、新闻列表、新闻情绪、模型概率、推荐分数和解释报告，让用户能够从多个角度理解预测结果。

### 2. 新闻增强的股票预测

系统将新闻情绪与量价数据结合，使用特征快照、技术指标和新闻聚合结果构造模型输入。相比只看价格的简单预测，本项目更强调“新闻 + 量价”的融合分析。

### 3. LLM 中文解释报告

模型本身负责数值预测，LLM 负责把结构化结果整理成更适合用户阅读的中文说明。即使 LLM 服务不可用，后端也会返回模板降级报告，保证前端流程不中断。

### 4. 动态模拟回测

回测不是一次性只返回最终收益，而是支持按交易日生成资金曲线、持仓快照、交易日志和事件日志。前端可以边拉取边播放，展示“每天发生了什么”。

### 5. 用户端与管理员端分离

普通用户关注股票、预测和回测；管理员负责用户管理、日志查看和数据任务状态检查。权限通过 JWT 和角色控制。

### 6. Docker 化部署

后端和 MySQL 可以通过 Docker Compose 一键启动，方便课程演示、服务器部署和团队协作。

---

## 系统能做什么

### 普通用户功能

| 功能       | 说明                                                  |
| ---------- | ----------------------------------------------------- |
| 注册与登录 | 使用账号登录系统，后端使用 JWT 鉴权                   |
| 自选股管理 | 添加、删除、查看关注股票                              |
| 股票搜索   | 根据股票代码或名称搜索证券基础库                      |
| 股票详情   | 查看日频行情、1min 分钟行情、当前价格、涨跌幅和成交量 |
| 技术指标   | 查看 MA、RSI、MACD 等指标曲线                         |
| 新闻列表   | 查看股票相关新闻、来源、发布时间和摘要                |
| 新闻详情   | 查看单条新闻详情、正文缓存和情绪结果                  |
| 新闻情绪   | 查看 14 日新闻情绪统计和情绪曲线                      |
| 单股预测   | 获取上涨/下跌概率、推荐分、预测价格路径和解释报告     |
| 历史预测   | 查看自己的预测记录和预测详情                          |
| 模拟回测   | 选择股票池和日期区间，运行历史模拟交易                |
| 最终持仓   | 查看最近一次回测后的模拟持仓与盈亏                    |

### 管理员功能

| 功能         | 说明                                   |
| ------------ | -------------------------------------- |
| 用户列表     | 查看系统用户列表                       |
| 用户详情     | 查看单个用户状态、角色和登录信息       |
| 用户状态管理 | 启用、禁用或软删除用户                 |
| 角色管理     | 调整用户角色                           |
| 重置密码     | 管理员为用户重置密码                   |
| 日志查询     | 查询登录、预测、回测、爬虫、异常等日志 |
| 爬虫状态     | 查看每日刷新和股票基础库同步状态       |
| 模型状态     | 查看当前 active 模型版本               |

---

## 使用流程

### 面向普通用户

1. 打开 Finsight 前端客户端。
2. 注册或使用已有账号登录。
3. 在自选股区域搜索并添加股票，例如 `AAPL`、`MSFT`、`NVDA`。
4. 点击股票卡片，查看行情走势、技术指标和相关新闻。
5. 点击预测按钮，系统会返回：
   - 预测方向；
   - 上涨/下跌概率；
   - 推荐分数；
   - 未来价格路径；
   - 新闻情绪摘要；
   - 中文解释报告。
6. 进入回测页面，选择股票池、开始日期、结束日期和初始资金。
7. 启动回测后，前端会动态展示每日资金曲线、买卖记录、持仓变化和最终收益。

### 面向管理员

1. 使用管理员账号登录。
2. 进入管理员页面。
3. 查看用户列表和用户详情。
4. 根据需要禁用、启用、重置密码或修改角色。
5. 查看操作日志、预测日志、回测日志和爬虫日志。
6. 检查数据任务、模型状态和系统运行情况。

---

## 技术架构

| 类别       | 技术                            |
| ---------- | ------------------------------- |
| 后端语言   | Python 3.11                     |
| Web 框架   | FastAPI                         |
| ASGI 服务  | Uvicorn                         |
| 数据库     | MySQL 8.0                       |
| ORM        | SQLAlchemy                      |
| 数据库驱动 | PyMySQL                         |
| 参数校验   | Pydantic v2                     |
| 认证鉴权   | JWT + passlib + bcrypt          |
| 数据处理   | pandas / numpy                  |
| 机器学习   | scikit-learn / xgboost / joblib |
| 行情数据   | Twelve Data / 其他备用数据服务  |
| 新闻数据   | Alpha Vantage News Sentiment    |
| LLM 报告   | 阿里云百炼 / DashScope          |
| 部署方式   | Docker / Docker Compose         |
| 客户端     | PySide6 桌面应用                |

---

## 当前版本

当前后端版本：**v1.5**

v1.5 主要能力如下：

| 模块           | 状态   | 说明                                                        |
| -------------- | ------ | ----------------------------------------------------------- |
| 用户认证       | 已完成 | 注册、登录、JWT、当前用户信息                               |
| 密码安全       | 已完成 | 前端 SHA-256 摘要，后端 bcrypt 存储                         |
| 管理员用户管理 | 已完成 | 用户查询、禁用、启用、改名、改角色、重置密码、软删除        |
| 股票基础库     | 已完成 | 支持 Nasdaq listed / other listed 股票基础库                |
| 自选股         | 已完成 | 添加、删除、查询，支持 mini curve                           |
| 股票详情       | 已完成 | 日频行情、1min 分钟行情、技术指标、新闻、情绪统计           |
| 新闻数据       | 已完成 | 新闻列表、新闻详情、情绪分数和正文缓存                      |
| 数据流水线     | 已完成 | market / technical / news / sentiment / features 模块化补齐 |
| 模型预测       | 已完成 | 主分类、辅助强信号、回归路径三模型体系                      |
| LLM 报告       | 已完成 | 百炼 / DashScope；失败时模板降级                            |
| 模拟回测       | 已完成 | 异步逐日回测、frames、logs、summary、final positions        |
| 日志系统       | 已完成 | 操作日志、预测日志、回测日志、爬虫日志查询                  |
| 自动化测试     | 已完成 | 覆盖核心 API，跳过长耗时或高风险接口                        |

---

## 环境要求

推荐使用 Docker 方式运行后端。

### Docker 部署要求

- Docker
- Docker Compose
- 可访问外部行情/新闻/LLM 服务的网络环境

### 本地 Python 运行要求

- Python 3.11
- MySQL 8.0
- pip / virtualenv

---

## 快速启动

### 1. 克隆项目

```bash
git clone <your-repository-url>
cd <your-backend-repository>
```

### 2. 准备环境变量

```bash
cp .env.docker.example .env.docker
```

然后编辑 `.env.docker`，至少确认以下配置：

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
SECRET_KEY=please-change-this-secret
RUN_SEED=1
```

如需启用真实新闻、行情或 LLM 报告，请配置对应服务的 API Key。不要把真实 `.env` 文件提交到 GitHub。

### 3. 启动服务

```bash
docker compose up -d --build
```

### 4. 查看容器状态

```bash
docker compose ps
```

### 5. 查看后端日志

```bash
docker compose logs -f backend
```

### 6. 测试健康检查

```bash
curl http://localhost:8002/health
```

预期返回：

```json
{
  "success": true,
  "data": {
    "status": "ok"
  },
  "message": "ok"
}
```

### 7. 访问接口文档

启动后可在浏览器打开：

```text
http://localhost:8002/docs
```

---

## 环境变量说明

常用环境变量如下：

| 变量                            | 是否必填 | 说明                          |
| ------------------------------- | -------- | ----------------------------- |
| `DATABASE_URL`                  | 是       | 后端连接 MySQL 的地址         |
| `SECRET_KEY`                    | 是       | JWT 签名密钥，部署时必须替换  |
| `ACCESS_TOKEN_EXPIRE_MINUTES`   | 否       | Token 有效期                  |
| `RUN_SEED`                      | 否       | 容器启动时是否导入演示数据    |
| `ENABLE_DAILY_AUTO_REFRESH`     | 否       | 是否开启每日自动刷新          |
| `DAILY_AUTO_REFRESH_TICKERS`    | 否       | 每日刷新股票池                |
| `DAILY_AUTO_REFRESH_MODULES`    | 否       | 每日刷新模块                  |
| `PREDICTION_ON_DEMAND_PIPELINE` | 否       | 预测前是否自动补齐数据        |
| `PREDICTION_ON_DEMAND_MODULES`  | 否       | 预测前补齐模块                |
| `ALPHA_VANTAGE_API_KEY`         | 可选     | 新闻和部分基础数据服务 Key    |
| `TWELVE_DATA_API_KEY`           | 可选     | 分钟级行情服务 Key            |
| `DASHSCOPE_API_KEY`             | 可选     | 百炼 / DashScope LLM 服务 Key |

---

## 数据库初始化

### 自动初始化

Docker 启动时，entrypoint 会等待 MySQL 就绪，然后启动 FastAPI。若设置：

```env
RUN_SEED=1
```

则会执行演示数据初始化脚本。

### 手动进入数据库

```bash
docker compose exec db mysql -ufinsight_user -pfinsight_password finsight
```

### 常见核心表

| 表名                       | 说明                           |
| -------------------------- | ------------------------------ |
| `roles`                    | 用户角色                       |
| `users`                    | 用户账号、密码哈希、状态和角色 |
| `stocks`                   | 股票基础库                     |
| `watchlists`               | 用户自选股                     |
| `price_data`               | 日频行情                       |
| `intraday_price_data`      | 分钟级行情                     |
| `news_data`                | 新闻、摘要、正文缓存和情绪结果 |
| `technical_indicators`     | 技术指标                       |
| `sentiment_daily`          | 每日新闻情绪聚合               |
| `model_feature_snapshots`  | 模型特征快照                   |
| `model_versions`           | 模型版本                       |
| `predictions`              | 用户预测记录                   |
| `backtest_runs`            | 回测任务                       |
| `portfolio_snapshots`      | 回测资金曲线                   |
| `backtest_daily_positions` | 回测每日持仓                   |
| `backtest_event_logs`      | 回测事件日志                   |
| `user_simulated_positions` | 用户最近回测最终模拟持仓       |
| `operation_logs`           | 用户操作和系统日志             |
| `crawler_logs`             | 爬虫日志                       |

---

## 主要 API 模块

所有需要登录的接口都使用：

```http
Authorization: Bearer <token>
```

### 认证模块

| 方法   | 路径                 | 说明             |
| ------ | -------------------- | ---------------- |
| `POST` | `/api/auth/register` | 用户注册         |
| `POST` | `/api/auth/login`    | 用户登录         |
| `GET`  | `/api/auth/me`       | 获取当前登录用户 |

登录接口不接收明文密码。前端需要先计算：

```text
password_sha256 = sha256("FINSIGHT_CLIENT_PASSWORD_V1:" + raw_password)
```

### 自选股模块

| 方法     | 路径                      | 说明           |
| -------- | ------------------------- | -------------- |
| `GET`    | `/api/watchlist`          | 获取自选股列表 |
| `POST`   | `/api/watchlist`          | 添加自选股     |
| `DELETE` | `/api/watchlist/{ticker}` | 删除自选股     |

### 股票与新闻模块

| 方法  | 路径                                     | 说明                           |
| ----- | ---------------------------------------- | ------------------------------ |
| `GET` | `/api/stocks/search`                     | 搜索股票                       |
| `GET` | `/api/stocks/{ticker}/detail`            | 股票详情、行情、指标、新闻摘要 |
| `GET` | `/api/stocks/{ticker}/news`              | 股票新闻列表                   |
| `GET` | `/api/stocks/news/{news_id}`             | 新闻详情                       |
| `GET` | `/api/stocks/{ticker}/sentiment-summary` | 新闻情绪摘要                   |

### 预测模块

| 方法   | 路径                               | 说明         |
| ------ | ---------------------------------- | ------------ |
| `POST` | `/api/predictions/run`             | 发起单股预测 |
| `GET`  | `/api/predictions/history`         | 查询预测历史 |
| `GET`  | `/api/predictions/{prediction_id}` | 查询预测详情 |

### 回测模块

| 方法   | 路径                                     | 说明                             |
| ------ | ---------------------------------------- | -------------------------------- |
| `POST` | `/api/backtest/run`                      | 创建回测任务                     |
| `GET`  | `/api/backtest/{run_id}/status`          | 查询回测状态                     |
| `GET`  | `/api/backtest/{run_id}/frames`          | 增量拉取回测帧                   |
| `GET`  | `/api/backtest/{run_id}/logs`            | 查询回测日志                     |
| `GET`  | `/api/backtest/{run_id}/summary`         | 查询回测汇总                     |
| `GET`  | `/api/backtest/{run_id}/final-positions` | 查询某次回测最终持仓             |
| `GET`  | `/api/backtest/latest/final-positions`   | 查询当前用户最近一次回测最终持仓 |

### 管理员与运维模块

| 方法     | 路径                                  | 说明                   |
| -------- | ------------------------------------- | ---------------------- |
| `GET`    | `/api/admin/users`                    | 用户列表               |
| `GET`    | `/api/admin/users/{user_id}`          | 用户详情               |
| `PUT`    | `/api/admin/users/{user_id}/status`   | 修改用户状态           |
| `PUT`    | `/api/admin/users/{user_id}/role`     | 修改用户角色           |
| `PUT`    | `/api/admin/users/{user_id}/username` | 修改用户名             |
| `PUT`    | `/api/admin/users/{user_id}/password` | 重置密码               |
| `DELETE` | `/api/admin/users/{user_id}`          | 软删除用户             |
| `GET`    | `/api/logs`                           | 查询日志               |
| `GET`    | `/api/models/active`                  | 查询当前 active 模型   |
| `GET`    | `/api/crawler/status`                 | 查询爬虫状态           |
| `GET`    | `/api/crawler/stock-universe/status`  | 查询股票基础库同步状态 |
| `POST`   | `/api/crawler/stock-universe/sync`    | 同步股票基础库         |
| `GET`    | `/api/crawler/daily-refresh/status`   | 查询每日刷新状态       |
| `POST`   | `/api/crawler/daily-refresh/run`      | 执行每日刷新           |
| `GET`    | `/api/data-pipeline/coverage`         | 查询数据覆盖情况       |
| `POST`   | `/api/data-pipeline/jobs`             | 创建数据流水线任务     |

---

## 模型预测说明

当前模型推理在后端完成，客户端不会直接加载模型。

后端预测流程：

```text
用户点击预测
→ 后端检查行情、新闻、技术指标和特征快照
→ 若数据缺失，按配置触发数据流水线补齐
→ 读取 active 模型版本
→ 构造 50 维特征向量
→ 主分类模型输出上涨/下跌倾向
→ 辅助模型判断强信号
→ 回归模型生成未来价格路径
→ 计算 recommendation_score
→ 生成新闻情绪摘要和中文解释报告
→ 保存 predictions 记录
→ 返回前端展示
```

当前 active 模型体系：

| 模型角色       | version_name                      | algorithm           | horizon |
| -------------- | --------------------------------- | ------------------- | ------: |
| 主分类模型     | `finsight_cls_abs_h15_v1.2`       | LogisticRegression  |      15 |
| 辅助强信号模型 | `finsight_cls_action1p5_h10_v1.2` | RidgeClassifier     |      10 |
| 回归路径模型   | `finsight_reg_return_path_v1.2`   | ExtraTreesRegressor |       5 |

预测返回结果包括：

- 预测方向；
- 上涨概率；
- 下跌概率；
- 推荐分数；
- 推荐等级；
- 未来价格路径；
- 新闻情绪摘要；
- 新闻影响说明；
- 综合中文解释报告。

---

## 模拟回测说明

回测用于回答：如果历史上按模型信号和策略规则进行模拟交易，组合表现如何。

回测输入包括：

| 参数                 | 说明                 |
| -------------------- | -------------------- |
| `tickers`            | 股票池               |
| `start_date`         | 回测开始日期         |
| `end_date`           | 回测结束日期         |
| `initial_cash`       | 初始资金             |
| `max_position_ratio` | 单只股票最大仓位比例 |
| `max_holding_count`  | 最大同时持仓数量     |
| `fee_rate`           | 交易手续费率         |
| `take_profit_pct`    | 止盈比例             |
| `stop_loss_pct`      | 止损比例             |

回测执行方式：

```text
POST /api/backtest/run
→ 后端创建 backtest_run
→ BackgroundTasks 后台异步计算
→ 每个交易日生成模型信号和策略动作
→ 写入资金快照、持仓快照、交易记录和事件日志
→ 前端轮询 status / frames / logs / summary
→ 回测完成后查询 final positions
```

回测结果包括：

- 总资产曲线；
- 现金与股票市值；
- 每日收益；
- 总收益率；
- 最大回撤；
- 胜率；
- 交易次数；
- SPY 基准对比；
- 每日持仓；
- 买入/卖出日志；
- 最终模拟持仓。

---

## 数据流水线与爬虫

后端提供数据流水线，用于按股票和日期补齐数据。

支持模块包括：

```text
market      日频行情
intraday    分钟级行情
technical   技术指标
news        新闻数据
sentiment   新闻情绪聚合
features    模型特征快照
```

查询数据覆盖情况：

```bash
curl "http://localhost:8002/api/data-pipeline/coverage?ticker=AAPL"
```

创建数据流水线任务：

```bash
curl -X POST "http://localhost:8002/api/data-pipeline/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "AAPL",
    "target_date": "2026-06-12",
    "modules": ["market", "technical", "features"]
  }'
```

---

## 自动化测试

项目提供自动化测试脚本，用于验证主要 API 是否可用。

示例：

```bash
python finsight_api_auto_test.py \
  --base-url http://localhost:8002 \
  --admin-user admin \
  --admin-pass Admin123 \
  --user user01 \
  --user-pass User123
```

测试报告会输出为 JSON 和 Markdown，建议提交前至少确认：

```text
核心接口通过
没有 500 级错误
登录、股票详情、预测、回测启动、日志、管理员查询均可用
```

---

## 项目目录结构

```text
.
├── app/
│   ├── main.py                     # FastAPI 应用入口
│   ├── core/                       # 配置、安全、响应、异常处理
│   ├── db/                         # 数据库连接与初始化
│   ├── models/                     # SQLAlchemy ORM 模型
│   ├── routers/                    # FastAPI 路由
│   ├── schemas/                    # Pydantic 请求/响应模型
│   ├── services/                   # 业务逻辑、数据服务、模型服务、回测服务
│   ├── scripts/                    # 数据导入、训练、修复和刷新脚本
│   └── scripts_tmp/                # 临时维护脚本
├── artifacts/
│   └── models/                     # 后端加载的模型文件
├── docker/
│   └── entrypoint.sh               # Docker 启动脚本
├── docs/                           # 项目说明文档
├── tools/                          # 临时工具脚本
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── run.py
├── finsight_api_auto_test.py
├── .env.example
├── .env.docker.example
└── README.md
```

---

## 常见问题

### 1. 后端启动后访问不了接口

先检查容器状态：

```bash
docker compose ps
```

再查看日志：

```bash
docker compose logs -f backend
```

确认后端是否绑定到宿主机 `8002` 端口。

### 2. 数据库连接失败

检查 `.env.docker` 中的 `DATABASE_URL` 是否与 `docker-compose.yml` 中的数据库账号、密码、库名一致。

### 3. 登录失败

新版登录接口不接受明文 `password` 字段，必须提交 `password_sha256`。前端输入原始密码后，会自动计算摘要再提交。

### 4. 预测很慢

预测可能触发数据补齐、模型加载和 LLM 报告生成。首次预测通常会比后续预测更慢。若 LLM 服务不可用，后端会使用模板报告降级。

### 5. 回测启动后没有马上完成

回测是后台异步任务。前端需要轮询：

```text
/api/backtest/{run_id}/status
/api/backtest/{run_id}/frames
/api/backtest/{run_id}/logs
```

### 6. 新闻或分钟行情为空

可能原因包括：

- 外部 API Key 未配置；
- 当前股票没有对应数据；
- 数据流水线尚未运行；
- 外部数据源达到调用限制。

---

## 开发建议

### 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

### 重新构建 Docker

```bash
docker compose down
docker compose up -d --build
```

### 查看数据库表

```bash
docker compose exec db mysql -ufinsight_user -pfinsight_password finsight -e "SHOW TABLES;"
```

---

## 安全与免责声明

- `.env`、`.env.docker`、API Key、数据库密码不得提交到 GitHub。
- `password_sha256` 虽然不是明文密码，但仍等价于登录凭证，正式部署必须使用 HTTPS。
- 本系统是课程实践和原型展示项目，不接入真实券商账户。
- 系统不执行真实交易，不保存真实证券账户信息。
- 所有预测、推荐分、情绪分析和回测结果仅供学习和实验参考，不构成投资建议。
- 历史回测收益不代表未来表现。

---

## 版本总结

Finsight Backend v1.5 已经形成较完整的后端闭环：

```text
用户登录
→ 自选股管理
→ 股票行情与新闻展示
→ 新闻情绪分析
→ 机器学习预测
→ LLM 中文解释
→ 模拟回测
→ 最终持仓与日志记录
→ 管理员运维查看
```

该版本适合作为课程项目展示、GitHub 项目说明和后续功能迭代的基础版本。