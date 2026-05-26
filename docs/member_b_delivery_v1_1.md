# Member B 数据与模型交付说明

## 交付目录

/data/hmt/datasets/finsight/member_b_delivery_v1_1_20260526

## 数据内容

### 1. db_csv

包含可导入正式 MySQL 的 CSV：

- stocks.csv
- price_data.csv
- technical_indicators.csv
- news_data.csv
- sentiment_daily.csv
- model_versions.csv

建议导入顺序：

1. stocks
2. price_data
3. technical_indicators
4. news_data
5. sentiment_daily
6. model_versions

### 2. 行情数据

price_data 覆盖：

- AAPL, MSFT, NVDA, TSLA, GOOGL, AMZN, META, AMD, NFLX, INTC, SPY, QQQ
- 时间范围：2023-01-03 至 2025-05-20
- 每个标的 597 个交易日

### 3. 新闻数据

news_data 覆盖：

- AAPL, MSFT, NVDA, TSLA, GOOGL, AMZN, META, AMD, NFLX, INTC
- 时间范围：2023-01 至 2025-05-20
- 来源：Alpha Vantage News & Sentiment API

说明：

- content_text 当前使用 Alpha Vantage summary 级文本
- content_html 为空
- news_llm_analysis 尚未接入
- sentiment_score / sentiment_label 已写入

### 4. 情绪聚合数据

sentiment_daily 覆盖：

- 10 只核心个股
- 时间范围：2023-01-03 至 2025-05-20
- 每只股票 597 行
- 聚合窗口：前 14 天新闻窗口

### 5. 训练集

training/training_news_v1_1/

包含：

- dataset_h5_v1.csv
- feature_columns_h5_v1.json
- label_config_h5_v1.json
- dataset_summary_h5_v1.json

训练样本：

- 10 只股票
- 每只 533 条样本
- 总样本 5330 条
- base_trading_date: 2023-03-29 至 2025-05-13

训练脚本内部采用时间顺序 70% / 15% / 15% 划分，不 shuffle，避免时间泄漏。

### 6. 模型文件

models/ 中包含：

- xgb_cls_h5_news_v1.1
- xgb_reg_h5_news_v1.1

当前 active 模型：

- classifier: xgb_cls_h5_news_v1.1
- regressor: xgb_reg_h5_news_v1.1

分类模型指标：

- accuracy = 0.41125
- macro_f1 = 0.3607832729556744

回归模型指标：

- mae = 0.04509145079016288
- rmse = 0.06298616307146268
- direction_accuracy = 0.5245

### 7. 原始数据

raw/ 中包含：

- market_data_raw_csv：原始行情 CSV
- alpha_vantage_news_raw_json：Alpha Vantage 原始新闻 JSON

这些用于追溯和重跑，不建议直接提交 GitHub。

## 注意事项

1. 不要提交 Alpha Vantage API key。
2. 不建议将 raw 新闻 JSON 和 model.joblib 直接提交 GitHub。
3. predictions 表不需要预先导入，它由用户调用预测接口时生成。
4. 新闻 LLM 深度分析尚未接入，目前 prediction 接口使用模板降级报告。
