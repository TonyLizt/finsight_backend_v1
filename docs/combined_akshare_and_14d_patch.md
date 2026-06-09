# Finsight v1.3 Combined Patch: AKShare Daily Market Source + 14-Day Sentiment Counts

本压缩包合并了两部分改动：

1. **日频行情源改为 AKShare 优先**
   - 使用 `ak.stock_us_daily(symbol=ticker)` 抓取美股日频行情；
   - 写入 MySQL `price_data`；
   - 默认行情源优先级建议为 `akshare,database`；
   - 保留本地 CSV / Yahoo Chart 作为可选 fallback，但默认不再依赖 Alpha Vantage 行情。

2. **新闻正负面数量统一为 14 天统计**
   - 新增统一字段 `sentiment_counts`；
   - 固定统计最近 14 个自然日；
   - 返回 `start_date` / `end_date` / `news_start_time` / `news_end_time`；
   - 直接基于 `news_data.publish_time` 统计，避免 `sentiment_daily` 滚动聚合导致重复累计。

---

## 1. 覆盖方式

在项目根目录执行：

```bash
cd ~/projects/projects/finsight_backend_member_b_v1_2

unzip -o /mnt/data/finsight_v13_akshare_and_14d_patch.zip

python tools/apply_v13_akshare_and_14d_patch.py
```

该总脚本会依次执行：

```bash
python tools/patch_akshare_daily_market_source.py
python tools/patch_two_week_sentiment_counts.py
python -m py_compile app/services/market_data_service.py app/scripts/test_akshare_daily_market_fetch.py app/services/stock_service.py app/routers/stocks.py
```

---

## 2. Docker 中加入 AKShare 依赖

补丁脚本会自动向 `requirements.txt` 追加：

```text
akshare>=1.18.0
```

### 推荐长期方式：重新构建镜像

```bash
docker compose build backend
docker compose up -d
```

或者一步执行：

```bash
docker compose up -d --build backend
```

这种方式会把 `akshare` 固化进镜像，后续容器重建不会丢失依赖。

### 临时测试方式：容器内手动安装

如果你想先快速测试，不想立刻 rebuild：

```bash
docker compose exec backend bash -lc "pip install akshare -i https://pypi.tuna.tsinghua.edu.cn/simple"
docker compose restart backend
```

注意：这种方式只是装进当前容器，重新 build 或重建容器后可能丢失，所以最终仍建议 `docker compose build backend`。

---

## 3. `.env.docker` 推荐配置

把行情源配置改成：

```env
# =========================
# Market data source - v1.3
# =========================
MARKET_DATA_SOURCE_PRIORITY=akshare,database
AKSHARE_US_DAILY_ADJUST=

# 默认不再走 Yahoo/本地 CSV，除非你明确要 fallback。
ENABLE_YAHOO_CHART_FALLBACK=0
ENABLE_LOCAL_RAW_CSV_FALLBACK=0
```

如果你想 AKShare 失败后再使用 B 同学本地 CSV：

```env
MARKET_DATA_SOURCE_PRIORITY=akshare,local_raw_csv,database
ENABLE_LOCAL_RAW_CSV_FALLBACK=1
MARKET_DATA_LOCAL_RAW_ROOT=/external_datasets/market_data/backtest_market_raw_20250521_20260531
ENABLE_YAHOO_CHART_FALLBACK=0
```

修改 `.env.docker` 后执行：

```bash
docker compose up -d --force-recreate backend
```

如果你同时改了 `requirements.txt` 并需要安装 AKShare，则执行：

```bash
docker compose up -d --build backend
```

---

## 4. 测试 AKShare 日频行情入库

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.test_akshare_daily_market_fetch AAPL 2026-06-05"
```

成功时应看到：

```json
{
  "ensure_price_data_result": {
    "status": "updated",
    "source": "akshare_stock_us_daily",
    "latest_price_date": "2026-06-05"
  }
}
```

也可以通过 Data Pipeline 测：

```bash
curl -X POST http://127.0.0.1:8002/api/data-pipeline/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "tickers": ["AAPL"],
    "end_date": "2026-06-05",
    "modules": ["market", "technical", "features"],
    "force_refresh": false,
    "run_async": false
  }'
```

然后查覆盖情况：

```bash
curl "http://127.0.0.1:8002/api/data-pipeline/coverage?ticker=AAPL&end_date=2026-06-05"
```

理想结果：

```text
price_data.latest_date = 2026-06-05
technical_indicators.latest_date = 2026-06-05
model_feature_snapshots.latest_base_trading_date = 2026-06-05
recommendation.status = ready
```

---

## 5. 测试 14 天 sentiment_counts

### 新闻列表

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/news?return_all=false&limit=5" \
  -H "Authorization: Bearer $USER_TOKEN"
```

返回中应包含：

```json
"sentiment_counts": {
  "window_days": 14,
  "start_date": "2026-05-26",
  "end_date": "2026-06-08",
  "news_start_time": "2026-05-26T00:00:00",
  "news_end_time": "2026-06-08T23:59:59.999999",
  "count_source": "news_data",
  "positive_news_count": 70,
  "negative_news_count": 8,
  "neutral_news_count": 5,
  "total_news_count": 83
}
```

### 股票详情

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=5d&include_news=false" \
  -H "Authorization: Bearer $USER_TOKEN"
```

### 情绪摘要

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/sentiment-summary" \
  -H "Authorization: Bearer $USER_TOKEN"
```

---

## 6. 注意事项

- 这次只把**日频行情补全**切换为 AKShare；
- 新闻仍然使用你原来的 Alpha Vantage News Sentiment；
- `range=1d` 的小时级行情不属于本补丁范围，AKShare 美股分钟接口在你的服务器测试中不可用；
- `sentiment_counts` 是基于 `news_data` 的 14 天新闻行统计，不是 `sentiment_daily` 的滚动窗口累计。
