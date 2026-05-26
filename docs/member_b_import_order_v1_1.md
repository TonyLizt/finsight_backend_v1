# Member B v1.1 MySQL 导入建议顺序

1. stocks
2. price_data
3. technical_indicators
4. news_data
5. sentiment_daily
6. model_versions

说明：
- predictions 不需要预先导入，由用户调用预测接口时生成。
- price_data、technical_indicators、sentiment_daily 建议按 ticker + trading_date 去重。
- news_data 建议按 ticker + url 去重。
- model_versions 建议按 version_name 去重或更新。
- xgb_cls_h5_news_v1.1 需要复制到正式后端 artifacts/models/classifier/。
- xgb_reg_h5_news_v1.1 需要复制到正式后端 artifacts/models/regressor/。
- 完整交付包位于：/data/hmt/datasets/finsight/member_b_delivery_v1_1_20260526
