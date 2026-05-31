# Finsight Member B Model v1.2 交付清单

## 1. 版本信息

交付版本：

```text
Finsight Member-B Model v1.2
```

训练集版本：

```text
expanded_60_no_weak10_news48_quality_fundamental
```

训练数据范围：

```text
base_trading_date = 2023-03-29 -> 2025-05-13
target_date_d5 = 2023-04-05 -> 2025-05-20
```

训练截止边界：

```text
2025-05-20
```

回测起始日期：

```text
2025-05-21
```

特征数：

```text
50
```

股票数：

```text
50
```

## 2. 主分类模型

模型定位：

```text
Primary classifier
```

模型目录：

```text
artifacts/models/classifier/finsight_cls_abs_h15_v1.2/
```

模型文件：

```text
artifacts/models/classifier/finsight_cls_abs_h15_v1.2/model.joblib
```

算法：

```text
LogisticRegression
```

任务：

```text
未来 15 日方向二分类，不是 up / neutral / down 三分类
```

指标：

```text
accuracy = 0.606938
macro_f1 = 0.588123
mean_hc_test_accuracy = 0.688357
```

## 3. 辅助强信号模型

模型定位：

```text
Auxiliary strong-signal classifier
```

模型目录：

```text
artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/
```

模型文件：

```text
artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/model.joblib
```

算法：

```text
RidgeClassifier
```

任务：

```text
未来 10 日收益是否超过 1.5% 的辅助二分类信号
```

指标：

```text
accuracy = 0.612031
macro_f1 = 0.573192
mean_hc_test_accuracy = 0.647277
```

说明：

```text
该模型建议作为辅助信号模型使用，不建议在未确认后端表结构前直接注册为唯一 active classifier。
```

## 4. 回归价格路径模型

模型定位：

```text
Return-path regressor
```

模型目录：

```text
artifacts/models/regressor/finsight_reg_return_path_v1.2/
```

模型文件：

```text
artifacts/models/regressor/finsight_reg_return_path_v1.2/model.joblib
```

正式算法：

```text
ExtraTreesRegressor
```

候选实验名称：

```text
extra_trees_shallow
```

说明：

```text
XGBRegressor 只是候选搜索中的 baseline，不是最终 v1.2 回归交付模型。
```

任务：

```text
预测未来 1~5 个交易日收益率路径
```

输出：

```text
target_return_d1
target_return_d2
target_return_d3
target_return_d4
target_return_d5
```

指标：

```text
MAE = 0.027229451392252406
RMSE = 0.04061780723236577
MAPE = 124.5342089005544
direction_accuracy = 0.5346422018348623
curve_mae = 0.027229451392252406
```

## 5. 脚本目录

Member B v1.2 脚本目录：

```text
app/scripts/member_b_v1_2/
```

核心脚本：

```text
add_fundamentals_to_training_dataset.py
build_fundamental_reports_from_alpha_raw.py
build_robust_news_dataset.py
check_backtest_market_delivery.py
export_final_model_artifacts.py
export_v12_regressor_extra_trees_final.py
import_alpha_vantage_news_quality.py
search_v12_regressor_candidates.py
test_load_v12_models.py
train_v12_regressor.py
```

财报抓取脚本：

```text
app/scripts/fetch_alpha_vantage_fundamentals.py
```

## 6. 加载测试

可运行：

```bash
PYTHONPATH=. python app/scripts/member_b_v1_2/test_load_v12_models.py
```

成功标志：

```text
[DONE] 三套 v1.2 模型均已成功加载并完成 sample 预测。
```

回归模型必须输出：

```text
pred shape: (1, 5)
```

## 7. 文档目录

文档目录：

```text
docs/member_b/
```

文档文件：

```text
MODEL_DELIVERY_v1_2.md
HOW_TO_LOAD_MODEL_v1_2.md
DATA_CUTOFF_AND_LEAKAGE_CONTROL_v1_2.md
BACKTEST_MARKET_DELIVERY_CHECK_v1_2.md
MODEL_VERSION_REGISTRATION_v1_2.md
MEMBER_B_MODEL_V1_2_MANIFEST.md
```

## 8. 不包含内容

本次 GitHub 交付不包含：

```text
local_experiments/
local_experiments/outputs/
artifacts/experiments/
*.db
*.sqlite
*.sqlite3
原始行情 CSV
原始新闻 JSON
原始财报 JSON
dataset_h5_v1.csv
logs/
.env
API key
__pycache__/
*.pyc
```

## 9. 接入建议

推荐使用方式：

```text
主分类模型：负责未来 15 日方向判断；
辅助强信号模型：负责未来 10 日强上涨信号判断；
回归模型：负责未来 1~5 日收益率路径和价格曲线。
```

推荐等级和 recommendation_score 不建议只依赖回归模型。回归模型主要用于价格路径、收益率幅度和报告解释。
