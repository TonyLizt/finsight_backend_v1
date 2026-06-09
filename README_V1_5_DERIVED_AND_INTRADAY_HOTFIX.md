# Finsight v1.5 hotfix: 派生行情字段与分钟行情目标日修复

本补丁修复两个 v1.5 问题：

1. Twelve Data 日频行情 upsert 后，`price_data.previous_close / change_amount / change_percent / daily_return / amplitude` 被覆盖成 `NULL`，导致前端涨跌幅显示为 0 或空。
2. 分钟行情默认抓到当前盘中日期，例如 `2026-06-09 09:30~10:35`，而不是最近一个完整交易日。

## 修改文件

```text
app/services/market_data_service.py
app/services/twelvedata_market_service.py
app/services/data_pipeline_service.py
app/scripts/repair_price_derived_fields.py
app/scripts/delete_incomplete_intraday_rows.py
README_V1_5_DERIVED_AND_INTRADAY_HOTFIX.md
```

## 应用

在项目根目录解压覆盖：

```bash
unzip finsight_backend_v1_5_derived_intraday_hotfix.zip -d .
```

重启后端：

```bash
docker compose up -d --force-recreate backend
```

## 一次性修复现有 price_data 派生字段

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.repair_price_derived_fields"
```

修复指定股票：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.repair_price_derived_fields --tickers GOOGL,AAPL"
```

## 清理错误写入的盘中残缺分钟数据

如果你的库里已经有 `2026-06-09` 这种只有几十条的盘中残缺数据，先 dry-run：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.delete_incomplete_intraday_rows --date 2026-06-09 --dry-run"
```

确认后删除：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.delete_incomplete_intraday_rows --date 2026-06-09"
```

## 重新抓最近完整交易日分钟行情

当前 `price_data.max(trading_date)=2026-06-08` 时，下面命令会抓 `2026-06-08 09:30~16:00`：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules intraday"
```

也可以指定日期：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules intraday --target-date 2026-06-08"
```

## 验证

```bash
curl "http://127.0.0.1:8002/api/stocks/GOOGL/detail?range=1m&include_news=false&include_indicators=false&auto_refresh=false" \
  -H "Authorization: Bearer $USER_TOKEN"
```

确认：

```text
current_quote.previous_close != null
current_quote.change != null
current_quote.change_percent != null
current_quote.daily_return != null
current_quote.amplitude != null
```

检查分钟数据行数：

```bash
docker compose exec backend bash -lc 'PYTHONPATH=/app python - <<'"'"'PY'"'"'
from sqlalchemy import text
from app.db.session import SessionLocal

db = SessionLocal()
rows = db.execute(text("""
SELECT ticker, trading_date, COUNT(*) rows_count, MIN(market_timestamp) min_ts, MAX(market_timestamp) max_ts
FROM intraday_price_data
WHERE ticker IN ("AAPL","MSFT","NVDA","TSLA","AMZN","GOOGL","META")
GROUP BY ticker, trading_date
ORDER BY ticker, trading_date DESC
""")).mappings().all()
for r in rows:
    print(dict(r))
db.close()
PY'
```

完整交易日通常应该接近 390 条 1min K 线。
