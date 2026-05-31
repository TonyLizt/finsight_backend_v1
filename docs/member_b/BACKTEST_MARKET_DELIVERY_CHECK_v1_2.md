# Finsight Member B v1.2 回测行情交付检查说明

## 1. 回测行情数据

回测期行情 raw CSV 路径：

```text
/data/hmt/datasets/finsight/market_data/backtest_market_raw_20250521_20260531
```

检查结果：

```text
stock csv count = 50
bad filename count = 0
date range = 2025-05-21 -> 2026-05-29
每个 ticker 257 行
```

## 2. 回测数据库

回测数据库路径：

```text
/data/hmt/projects/finsight/finsight_backend_v1_git/local_experiments/outputs/backtest_after_20250520/finsight_price_backtest_after_20250520.db
```

该数据库从训练期数据库复制后，再导入 2025-05-21 之后行情。

这样做是合理的，因为 MA60、RSI、MACD 等技术指标需要历史 warm-up。

## 3. 技术指标

回测期技术指标已经生成。回测同学不能只拿 OHLCV，还需要 `technical_indicators`。

模型输入中相关技术指标包括：

```text
return_1d
return_3d
return_5d
ma5
ma20
ma60
ma5_gap
ma20_gap
ma60_gap
rsi
macd
volatility_20d
drawdown_20d
volume_zscore
```

## 4. 回测期新闻状态

回测期新闻暂未补齐。

缺失内容包括：

```text
2025-05-21 之后新闻 raw JSON
news_data
sentiment_daily
14 天窗口新闻情绪聚合
```

建议后续路径：

```text
/data/hmt/datasets/finsight/news/raw/alpha_vantage_backtest_after_20250520
```

该问题不阻塞当前 v1.2 模型 artifact 交付，但会影响后续真实回测完整性。

## 5. 后续回测接入建议

后续回测逻辑应复用单股预测同一套底层模型服务，包括：

```text
FeatureService
ModelService
RecommendationService
```

目标是保证同一股票、同一日期、同一模型版本下，单股预测和回测信号一致。

不要为回测单独写一套完全不同的模型逻辑。
