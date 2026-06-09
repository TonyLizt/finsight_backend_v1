# Finsight Backend v1.5 Intraday Interval Hotfix

本补丁为 `GET /api/stocks/{ticker}/detail?range=1d` 增加日内曲线间隔参数：

- 默认：`interval=hourly`，继续返回 7 条小时级聚合 K 线，兼容现有前端。
- 新增：`interval=1min`，直接返回 `intraday_price_data` 中的原始 1 分钟 K 线，通常为 390 条。

## 修改文件

```text
app/routers/stocks.py
app/services/intraday_market_service.py
```

## 使用方式

```bash
unzip finsight_backend_v1_5_intraday_interval_hotfix.zip -d .
docker compose restart backend
```

如果没有代码 volume 挂载，使用：

```bash
docker compose up -d --force-recreate backend
```

## 测试命令

### 默认小时级

```bash
curl -s "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1d&include_news=false&include_indicators=false&auto_refresh=false" \
  -H "Authorization: Bearer $USER_TOKEN" \
  | python -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(d["data_frequency"], d["price_curve_count"], d["price_curve_start"], d["price_curve_end"])'
```

预期：

```text
hourly 7 2026-06-08T09:00:00 2026-06-08T15:00:00
```

### 新增分钟级

```bash
curl -s "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1d&interval=1min&include_news=false&include_indicators=false&auto_refresh=false" \
  -H "Authorization: Bearer $USER_TOKEN" \
  | python -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(d["data_frequency"], d["price_curve_count"], d["price_curve_start"], d["price_curve_end"], d["price_curve"][0], d["price_curve"][-1])'
```

预期：

```text
1min 390 2026-06-08T09:30:00 2026-06-08T15:59:00 ...
```

## 兼容性

`get_hourly_intraday_curve()` 仍保留为 wrapper，旧代码可继续调用。
