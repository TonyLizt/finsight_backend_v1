# AKShare hourly 1d + range=all fix

## 改动

- `GET /api/stocks/{ticker}/detail?range=1d` 使用 AKShare `stock_us_hist_min_em` 获取美股最近 5 个交易日分钟数据，再按小时聚合返回。
- `GET /api/stocks/{ticker}/detail?range=all` 不再返回 1 年，而是返回 MySQL `price_data` 里该股票的全部日频数据。
- 如果 AKShare 不可用或未安装，`range=1d` 返回 `price_curve=[]`，并在 `intraday_status.error` 中说明原因，不伪造数据。

## 覆盖

```bash
cd ~/projects/projects/finsight_backend_member_b_v1_2
unzip -o /mnt/data/finsight_stock_detail_akshare_hourly_all_fix.zip
python tools/patch_stock_detail_akshare_hourly_all.py
```

## 安装依赖

如果你想马上测试，不重建镜像也可以先临时安装：

```bash
docker compose exec backend bash -lc "pip install akshare -i https://pypi.tuna.tsinghua.edu.cn/simple"
```

长期建议重新构建，因为补丁会把 `akshare>=1.18.0` 加到 `requirements.txt`：

```bash
docker compose build backend
docker compose up -d
```

## 编译检查

```bash
python -m py_compile app/services/intraday_market_service.py app/services/stock_service.py app/routers/stocks.py
```

## 测试 range=1d

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=1d&include_news=false&include_indicators=true"   -H "Authorization: Bearer $USER_TOKEN"
```

成功时应看到：

```json
{
  "price_range": "1d",
  "data_frequency": "hourly",
  "intraday_status": {
    "status": "success",
    "source": "akshare_stock_us_hist_min_em",
    "ak_symbol": "105.AAPL"
  },
  "price_curve_count": 7
}
```

## 测试 range=all

```bash
curl "http://127.0.0.1:8002/api/stocks/AAPL/detail?range=all&include_news=false&include_indicators=false"   -H "Authorization: Bearer $USER_TOKEN"
```

重点看：

```json
{
  "price_range": "all",
  "data_frequency": "daily",
  "price_curve_count": 854
}
```

如果数据库里 AAPL 有 854 条日频记录，`range=all` 就应该返回 854 条，而不是 252 条。
