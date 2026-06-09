# AKShare daily market data patch

## 目标

将后端日频行情补全主源改为：

```text
AKShare stock_us_daily -> MySQL price_data
```

默认优先级：

```text
akshare -> database
```

也就是：

1. 需要补行情时，先调用 AKShare 日频接口；
2. AKShare 成功则写入 `price_data`；
3. AKShare 失败则回退到数据库已有缓存；
4. 不再默认优先 Alpha Vantage / Yahoo Chart。

新闻接口不受影响，新闻仍可继续用 Alpha Vantage News Sentiment。

## 覆盖方式

```bash
cd ~/projects/projects/finsight_backend_member_b_v1_2

unzip -o /mnt/data/finsight_akshare_daily_market_patch.zip

python tools/patch_akshare_daily_market_source.py

python -m py_compile app/services/market_data_service.py app/scripts/test_akshare_daily_market_fetch.py

docker compose build backend
docker compose up -d
```

如果暂时不想 rebuild，可以先在容器里安装依赖：

```bash
docker compose exec backend bash -lc "pip install akshare -i https://pypi.tuna.tsinghua.edu.cn/simple"
docker compose restart backend
```

长期仍建议 rebuild。

## .env.docker 推荐配置

```env
MARKET_DATA_SOURCE_PRIORITY=akshare,database
AKSHARE_US_DAILY_ADJUST=
ENABLE_LOCAL_RAW_CSV_FALLBACK=0
ENABLE_YAHOO_CHART_FALLBACK=0
```

如果你想在 AKShare 失败时再使用 B 同学 CSV：

```env
MARKET_DATA_SOURCE_PRIORITY=akshare,local_raw_csv,database
ENABLE_LOCAL_RAW_CSV_FALLBACK=1
MARKET_DATA_LOCAL_RAW_ROOT=/external_datasets/market_data/backtest_market_raw_20250521_20260531
```

## 测试命令

```bash
docker compose exec backend bash -lc \
"PYTHONPATH=/app python -m app.scripts.test_akshare_daily_market_fetch AAPL 2026-06-05"
```

或通过 Data Pipeline：

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

成功时 market 模块应出现：

```json
{
  "module": "market",
  "status": "updated",
  "source": "akshare_stock_us_daily",
  "latest_price_date": "2026-06-05"
}
```
