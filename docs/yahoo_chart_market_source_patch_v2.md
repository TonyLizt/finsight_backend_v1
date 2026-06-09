# v1.3 Yahoo Chart Market Source Patch v2

上一版 `tools/patch_yahoo_chart_market_source.py` 有字符串换行转义问题，会报：

```text
SyntaxError: unterminated string literal
```

本 v2 修复该问题，并把后端行情源改为：

```text
Yahoo Chart -> optional local_raw_csv fallback
```

行情模块不再调用 Alpha Vantage 日频接口。Alpha Vantage 仍可用于新闻。

## 使用

```bash
cd ~/projects/projects/finsight_backend_member_b_v1_2
unzip -o /mnt/data/finsight_v13_yahoo_chart_market_integration_patch_v2.zip
python -m py_compile tools/patch_yahoo_chart_market_source_v2.py
python tools/patch_yahoo_chart_market_source_v2.py
python -m py_compile app/services/market_data_service.py

docker compose up -d --force-recreate backend
```

## 推荐 .env.docker

```env
MARKET_DATA_SOURCE_PRIORITY=yahoo_chart,local_raw_csv
ENABLE_YAHOO_CHART_FALLBACK=1
ENABLE_LOCAL_RAW_CSV_FALLBACK=0
MARKET_DATA_TIMEOUT_SECONDS=60
MARKET_DATA_USER_AGENT=Mozilla/5.0
```

如果 Yahoo 仍然 403，并且你允许 B 同学 CSV 兜底：

```env
ENABLE_LOCAL_RAW_CSV_FALLBACK=1
MARKET_DATA_SOURCE_PRIORITY=yahoo_chart,local_raw_csv
```

## 测试

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.test_yahoo_chart_market_fetch AAPL 2026-06-05"

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

下载并写入 MySQL：

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.download_market_yahoo_chart \
--tickers AAPL \
--start-date 2025-05-21 \
--end-date 2026-06-05 \
--write-db"
```
