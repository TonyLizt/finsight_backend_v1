# Finsight v1.5 后端自抓取补丁说明

## 目标

v1.5 以后，数据不再依赖 B 同学每天交 CSV 给你导入，而是由后端自行抓取、自行入库、自行增量追加。

本补丁重点实现：

1. 行情数据源统一切换到 Twelve Data。
2. 不再使用 AKShare / Yahoo 作为行情源。
3. 日频行情写入 `price_data`。
4. 1 分钟行情写入新增表 `intraday_price_data`。
5. 每次运行自动检测数据库最新日期/时间戳，只从最近数据继续抓。
6. 默认刷新 7 只核心股票：`AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META`。
7. 非核心股票如果数据库缺数据，可以手动调用 on-demand 脚本现场尝试入库。
8. 新闻正文支持批量补全模块 `news_fulltext`。

## 修改/新增文件

```text
app/core/config.py
app/models/all_models.py
app/services/twelvedata_market_service.py
app/services/market_data_service.py
app/services/intraday_market_service.py
app/services/data_pipeline_service.py
app/services/daily_refresh_service.py
app/routers/stocks.py
app/routers/crawler.py
app/schemas/crawler.py
app/scripts/create_intraday_price_data_table.py
app/scripts/run_twelvedata_incremental_refresh.py
app/scripts/ensure_ticker_data_on_demand.py
```

## .env.docker 新增配置

```env
TWELVEDATA_API_KEY=你的TwelveDataKey
TWELVEDATA_BASE_URL=https://api.twelvedata.com
TWELVEDATA_TIMEZONE=America/New_York
TWELVEDATA_DAILY_INTERVAL=1day
TWELVEDATA_INTRADAY_INTERVAL=1min
TWELVEDATA_DAILY_OUTPUTSIZE=5000
TWELVEDATA_INTRADAY_OUTPUTSIZE=5000
TWELVEDATA_INTRADAY_PREPOST=false
TWELVEDATA_TIMEOUT_SECONDS=30
TWELVEDATA_REQUEST_SLEEP_SECONDS=8

FINSIGHT_CORE_TICKERS=AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META
FINSIGHT_ENABLE_ON_DEMAND_INGEST=true

# 第一次没有任何历史行情时，默认回补窗口
TWELVEDATA_DAILY_INITIAL_BACKFILL_DAYS=1260
TWELVEDATA_INTRADAY_INITIAL_BACKFILL_DAYS=7

# 每日刷新模块
DAILY_AUTO_REFRESH_MODULES=market,intraday,technical,news,news_fulltext,sentiment,features
DAILY_AUTO_REFRESH_TICKERS=AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META
```

## 应用补丁后重建容器

如果只改 Python 文件和 `.env.docker`，不用重新 build：

```bash
docker compose up -d --force-recreate backend
docker compose logs -f backend
```

如果新增依赖才需要 build。本补丁不新增第三方依赖。

## 第一次创建新增表

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.create_intraday_price_data_table"
```

如果你的后端启动时会执行 `init_db()`，也会通过 `Base.metadata.create_all()` 自动创建新表。

## 每日增量刷新 7 只核心股票

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh"
```

等价于：

```bash
PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh \
  --tickers AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META \
  --modules market,intraday,technical,news,news_fulltext,sentiment,features
```

## 现场补某只非核心股票

如果前端或预测时发现某只股票数据库没有数据，可以手动执行：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.ensure_ticker_data_on_demand --ticker NFLX"
```

只补行情和特征：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.ensure_ticker_data_on_demand --ticker NFLX --modules market,intraday,technical,features"
```

## 通过 API 触发每日刷新

`POST /api/crawler/daily-refresh/run` 现在支持可选 `modules` 字段：

```json
{
  "tickers": ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"],
  "modules": ["market", "intraday", "technical", "news", "news_fulltext", "sentiment", "features"],
  "force_refresh": false,
  "limit": 7
}
```

## 增量逻辑

### 日频行情

- 查询 `price_data` 中该 ticker 的 `MAX(trading_date)`。
- 如果已经覆盖目标日期，直接返回 `cached`。
- 如果没覆盖，从最近交易日附近继续抓 Twelve Data `1day`。
- 写入时按 `ticker + trading_date` upsert。

### 1 分钟行情

- 查询 `intraday_price_data` 中该 ticker 的 `MAX(market_timestamp)`。
- 如果已有目标交易日约 350 条以上分钟数据，直接返回 `cached`。
- 否则从最新分钟附近继续抓 Twelve Data `1min`。
- 写入时按 `ticker + market_timestamp + interval_type` upsert。

### 新闻正文

- 扫描 `content_status in ('not_fetched','summary_only','fetch_failed','empty','blocked')` 或 `content_text` 为空的新闻。
- 已经 `fetched` 且正文存在的新闻默认跳过。
- 抓成功才更新正文，失败不会清掉已有正文。

## 数据源

- 股票基础库：Nasdaq Trader，保留。
- 日频行情：Twelve Data `/time_series?interval=1day`。
- 1 分钟行情：Twelve Data `/time_series?interval=1min`。
- 新闻列表/情绪：Alpha Vantage NEWS_SENTIMENT，保留。
- 新闻正文：原新闻 URL 网页抓取。

Twelve Data 官方文档说明其 API 支持 JSON / CSV 格式的金融市场数据，Python 客户端示例也使用 `time_series(symbol="AAPL", interval="1min", outputsize=10, timezone="America/New_York")`。因此 v1.5 使用 `/time_series` 的 `1day` 和 `1min` 两种 interval。
