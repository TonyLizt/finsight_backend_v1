# Finsight Backend v1.5 Intraday Default 1min Hotfix

本补丁将 `GET /api/stocks/{ticker}/detail?range=1d` 的默认日内曲线从小时级聚合改为 1 分钟级返回。

## 行为变更

```text
默认分钟级：
GET /api/stocks/AAPL/detail?range=1d

显式分钟级：
GET /api/stocks/AAPL/detail?range=1d&interval=1min

显式小时级：
GET /api/stocks/AAPL/detail?range=1d&interval=hourly
```

## 修改文件

```text
app/routers/stocks.py
app/services/intraday_market_service.py
```

## 应用方式

```bash
unzip finsight_backend_v1_5_intraday_default_1min_hotfix.zip -d .
docker compose restart backend
```

如果没有 volume 挂载：

```bash
docker compose up -d --force-recreate backend
```

## 测试默认分钟级

```bash
curl -s "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1d&include_news=false&include_indicators=false&auto_refresh=false" \
  -H "Authorization: Bearer $USER_TOKEN" \
  | python -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(d["data_frequency"], d["intraday_interval"], d["price_curve_count"], d["price_curve_start"], d["price_curve_end"])'
```

预期：

```text
1min 1min 390 2026-06-08T09:30:00 2026-06-08T15:59:00
```

## 测试小时级聚合

```bash
curl -s "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1d&interval=hourly&include_news=false&include_indicators=false&auto_refresh=false" \
  -H "Authorization: Bearer $USER_TOKEN" \
  | python -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(d["data_frequency"], d["intraday_interval"], d["price_curve_count"], d["price_curve_start"], d["price_curve_end"])'
```

预期：

```text
hourly hourly 7 2026-06-08T09:00:00 2026-06-08T15:00:00
```
