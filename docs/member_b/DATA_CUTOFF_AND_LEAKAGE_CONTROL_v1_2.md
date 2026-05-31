# Finsight Member B v1.2 数据截止与泄漏控制说明

## 1. 训练数据边界

v1.2 核心训练集：

```text
expanded_60_no_weak10_news48_quality_fundamental
```

训练集路径：

```text
/data/hmt/projects/finsight/finsight_backend_v1_git/local_experiments/outputs/expanded_60_no_weak10_news48_quality_fundamental/training_dataset
```

训练集范围：

```text
base_trading_date = 2023-03-29 -> 2025-05-13
target_date_d5 = 2023-04-05 -> 2025-05-20
```

回归模型训练过滤规则：

```text
target_date_d5 <= 2025-05-20
```

这样可以确保训练标签不进入 2025-05-21 之后的回测区间。

## 2. 回测数据边界

回测期行情数据：

```text
/data/hmt/datasets/finsight/market_data/backtest_market_raw_20250521_20260531
```

回测期行情范围：

```text
2025-05-21 -> 2026-05-29
```

该数据不进入模型训练，只用于后续样本外回测和模拟交易。

## 3. 财报泄漏控制

财报特征不能按照 `fiscalDateEnding` 直接对齐，因为市场在季度结束日并不知道完整财报内容。

正确规则：

```text
fund_available_date <= base_trading_date
```

post-market 财报按如下规则保守处理：

```text
fund_available_date = reported_date + 1 day
```

当前训练集检查结果：

```text
fundamental leak rows = 0
```

## 4. 新闻特征说明

训练集中的新闻特征已经被聚合为：

```text
news_count
positive_news_count
negative_news_count
neutral_news_count
sentiment_score
sentiment_score_3d_avg
sentiment_score_7d_avg
positive_ratio
negative_ratio
```

模型训练不直接读取 raw 新闻 JSON，而是读取已经融合好的 `dataset_h5_v1.csv`。

## 5. 回测期新闻缺口

目前回测期行情和技术指标已经完成，但 2025-05-21 之后的回测期新闻、`sentiment_daily` 和 14 天窗口聚合还未完成。

由于 v1.2 模型 `feature_columns` 中包含新闻情绪特征，后续构造回测期特征时必须保证新闻特征列齐全。

可选处理方式：

```text
1. 后续补齐 2025-05-21 之后新闻数据；
2. 暂时按训练逻辑将无新闻特征置 0；
3. 不能直接缺列。
```

## 6. GitHub 数据边界

不得提交：

```text
原始行情 CSV
原始新闻 JSON
原始财报 JSON
大训练集 CSV
SQLite 数据库
日志文件
API key
.env
local_experiments/outputs/
artifacts/experiments/
```

GitHub 只提交小型模型 artifact、脚本和文档。
