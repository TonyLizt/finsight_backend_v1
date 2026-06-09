# Two-week sentiment_counts patch

## 改动目标

统一 `positive_news_count` / `negative_news_count` / `neutral_news_count` / `total_news_count` 的前端统计口径。

新增统一字段：

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

## 统计口径

- 固定最近 14 个自然日；
- 包含截止日；
- 直接基于 `news_data.publish_time` 统计，不再基于 `sentiment_daily` 的滚动聚合结果，避免重复累计；
- 如果 API 传入 `end_time`，则以 `end_time` 所在日期为截止日；
- 如果没有传入 `end_time`，则以该 ticker 最新新闻日期为截止日。

## 影响 API

### GET /api/stocks/{ticker}/detail

新增：

```json
"sentiment_counts": {}
```

### GET /api/stocks/{ticker}/news

新增：

```json
"sentiment_counts": {}
```

该字段与分页无关，始终统计两周窗口内全部新闻。

### GET /api/stocks/{ticker}/sentiment-summary

新增：

```json
"sentiment_counts": {}
```

并将默认 `window_days` 从 7 改成 14。

## 覆盖方式

```bash
cd ~/projects/projects/finsight_backend_member_b_v1_2

unzip -o /mnt/data/finsight_two_week_sentiment_counts_patch.zip

python tools/patch_two_week_sentiment_counts.py

python -m py_compile app/services/stock_service.py app/routers/stocks.py

docker compose restart backend
```
