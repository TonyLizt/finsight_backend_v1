# v1.3 Yahoo Chart Market Source Integration Patch

## 目的

按 B 同学的 `download_backtest_market_yahoo_chart.py`，把后端行情抓取改成：

```text
Yahoo Chart -> 可选 local_raw_csv fallback
```

不再让行情 market 模块调用 Alpha Vantage。新闻模块不受影响，仍可使用 Alpha Vantage News Sentiment。

## 应用补丁

```bash
cd ~/projects/projects/finsight_backend_member_b_v1_2
unzip -o /mnt/data/finsight_v13_yahoo_chart_market_integration_patch.zip
python tools/patch_yahoo_chart_market_source.py
```

推荐修改 `.env.docker`：

```env
# Market data source - aligned with member B Yahoo Chart script
MARKET_DATA_SOURCE_PRIORITY=yahoo_chart,local_raw_csv
ENABLE_YAHOO_CHART_FALLBACK=1
ENABLE_LOCAL_RAW_CSV_FALLBACK=0
MARKET_DATA_TIMEOUT_SECONDS=60
MARKET_DATA_USER_AGENT=Mozilla/5.0
```

改 `.env.docker` 后必须重新创建容器：

```bash
docker compose up -d --force-recreate backend
```

## 直接测试 Yahoo 函数

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.test_yahoo_chart_market_fetch AAPL 2026-06-05"
```

成功时会显示 `source=yahoo_chart`、`latest_date=2026-06-05` 或目标日期前最近交易日。

## 单独运行下载脚本

只下载 CSV：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.download_market_yahoo_chart \
--tickers AAPL,MSFT \
--start-date 2025-05-21 \
--end-date 2026-06-05 \
--out-dir /app/local_experiments/yahoo_market_raw"
```

下载并写入 MySQL `price_data`：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.download_market_yahoo_chart \
--tickers AAPL \
--start-date 2025-05-21 \
--end-date 2026-06-05 \
--write-db"
```

下载 B 同学 50 只核心股票并写库：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.download_market_yahoo_chart \
--core \
--start-date 2025-05-21 \
--end-date 2026-06-05 \
--write-db \
--sleep-seconds 2"
```

## 测 Data Pipeline

```bash
curl -X POST http://127.0.0.1:8002/api/data-pipeline/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "tickers": ["AAPL"],
    "end_date": "2026-06-05",
    "modules": ["market", "technical", "news", "sentiment", "features"],
    "force_refresh": false,
    "run_async": false
  }'
```

成功时 market 应返回：

```json
{
  "module": "market",
  "status": "updated",
  "source": "yahoo_chart"
}
```

如果仍然是 403，说明当前服务器网络访问 Yahoo Chart 被限制；这时可以选择开启 `ENABLE_LOCAL_RAW_CSV_FALLBACK=1`，或者换网络/代理/数据源。
