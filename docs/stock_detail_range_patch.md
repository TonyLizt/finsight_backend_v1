# Stock detail range patch: 1d / 5d / all

本补丁扩展现有接口：

```http
GET /api/stocks/{ticker}/detail
```

新增支持：

```text
range=1d
range=5d
range=all
```

并保留原有：

```text
range=1m
range=3m
range=6m
range=1y
```

## 覆盖方式

```bash
cd ~/projects/projects/finsight_backend_member_b_v1_2
unzip -o /mnt/data/finsight_stock_detail_range_patch.zip
python tools/patch_stock_detail_range_1d_5d_all.py
python -m py_compile app/services/stock_service.py app/routers/stocks.py
docker compose restart backend
```

## 测试

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1d&include_news=false&include_indicators=true"   -H "Authorization: Bearer $USER_TOKEN"

curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=5d&include_news=false&include_indicators=true"   -H "Authorization: Bearer $USER_TOKEN"

curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=all&include_news=false&include_indicators=true"   -H "Authorization: Bearer $USER_TOKEN"
```

返回中会新增：

```json
{
  "price_range": "5d",
  "price_curve_count": 5,
  "price_curve_start_date": "...",
  "price_curve_end_date": "...",
  "data_frequency": "daily"
}
```
