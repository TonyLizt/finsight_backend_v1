# Finsight v1.3 Data Pipeline 第一版补丁说明

## 目标

本补丁开始完善统一数据链路，采用数据库优先策略：

1. 优先使用数据库已有数据；
2. 数据库已有 `model_feature_snapshots` 时，不调用外部 API；
3. 没有 snapshot 但有 `price_data` 时，先重算技术指标并生成 snapshot；
4. `price_data` 也没有时，才调用外部行情 API；
5. 提供统一 Data Pipeline API 和 Coverage 查询；
6. 新闻、情绪、财报模块先保留接口，后续接 B 同学脚本。

## 新增文件

```text
app/schemas/data_pipeline.py
app/services/data_pipeline_service.py
app/routers/data_pipeline.py
docs/v1_3_data_pipeline_patch.md
```

## 需要手动修改 app/main.py

在 `app/main.py` 中引入并注册路由：

```python
from app.routers import data_pipeline

app.include_router(data_pipeline.router)
```

建议放在其他 router include 附近。

## 测试 Coverage

```bash
curl "http://127.0.0.1:8002/api/data-pipeline/coverage?ticker=AAPL&end_date=2026-05-29"
```

## 测试 Data Pipeline Job

```bash
curl -X POST http://127.0.0.1:8002/api/data-pipeline/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "tickers": ["AAPL"],
    "end_date": "2026-05-29",
    "modules": ["market", "technical", "features"],
    "force_refresh": false,
    "run_async": false
  }'
```

预期：
- 如果数据库已有 2026-05-29 的 snapshot，`features` 返回 cached；
- 如果只有 price_data，没有 snapshot，则生成 snapshot；
- 如果 price_data 也没有，才调用外部行情源。

## 后续 v1.3 第二步

接入 B 同学脚本：

- `news` 模块：Alpha Vantage NEWS_SENTIMENT → news_data；
- `sentiment` 模块：news_data → sentiment_daily；
- `fundamentals` 模块：EARNINGS / INCOME_STATEMENT → financial_reports / fund_*；
- `features` 模块：把 sentiment/fundamental 来源写入 raw_row_json。
