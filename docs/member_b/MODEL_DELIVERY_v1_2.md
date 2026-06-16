# Finsight / 智融洞察 Member B 模型交付说明 v1.2

## 1. 交付目标

本次交付目标是将 Member B v1.2 做成完整模型交付版，包括：

1. 主分类模型；
2. 辅助强信号分类模型；
3. 回归价格路径模型；
4. 特征、训练、数据边界和模型加载说明文档。

本次交付不直接修改后端正式数据库，不直接修改 `PredictionService`、`ModelService` 或正式模型注册逻辑。后端接入由后续阶段完成。

## 2. 数据版本

本次 v1.2 使用的核心训练集为：

```text
expanded_60_no_weak10_news48_quality_fundamental
```

训练集路径：

```text
/data/hmt/projects/finsight/finsight_backend_v1_git/local_experiments/outputs/expanded_60_no_weak10_news48_quality_fundamental/training_dataset
```

核心文件：

```text
dataset_h5_v1.csv
feature_columns_h5_v1.json
label_config_h5_v1.json
dataset_summary_h5_v1.json
fundamental_coverage_by_ticker.csv
```

训练集基本情况：

```text
shape = (26650, 68)
ticker_count = 50
feature_count = 50
base_trading_date = 2023-03-29 -> 2025-05-13
target_date_d5 = 2023-04-05 -> 2025-05-20
fundamental leak rows = 0
```

特征包括：

```text
行情特征
技术指标特征
新闻情绪特征
财报基底特征
```

模型读取的是已经融合好的 `dataset_h5_v1.csv`，不是直接读取原始行情 CSV、原始新闻 JSON 或原始财报 JSON。

## 3. 模型目录

本次 v1.2 正式交付三个模型目录：

```text
artifacts/models/classifier/finsight_cls_abs_h15_v1.2/
artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/
artifacts/models/regressor/finsight_reg_return_path_v1.2/
```

## 4. 主分类模型

模型目录：

```text
artifacts/models/classifier/finsight_cls_abs_h15_v1.2/
```

模型配置：

```text
candidate = abs_h15_market_ext_logreg
task = abs_sign
horizon = 15
feature_set = f0_market_ext
model = logreg
```

注意：该模型不是 `up / neutral / down` 三分类，而是未来 15 日方向二分类模型。

rolling validation 指标：

```text
mean_test_accuracy    = 0.606938
mean_test_macro_f1    = 0.588123
mean_above_baseline   = 0.055125
min_above_baseline    = 0.002812
mean_hc_test_accuracy = 0.688357
```

模型定位：

```text
用于判断未来 15 个交易日方向倾向，是 recommendation_score 和 recommendation_level 的重要依据。
```

## 5. 辅助强信号模型

模型目录：

```text
artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/
```

模型配置：

```text
candidate = action1p5_h10_market_ext_ridge
task = action_1p5
horizon = 10
feature_set = f0_market_ext
model = ridge
```

注意：该模型是未来 10 日收益超过 1.5% 的辅助二分类信号模型，不是普通三分类模型。

rolling validation 指标：

```text
mean_test_accuracy    = 0.612031
mean_test_macro_f1    = 0.573192
mean_above_baseline   = 0.061544
min_above_baseline    = 0.003599
mean_hc_test_accuracy = 0.647277
```

模型定位：

```text
用于辅助判断是否存在较强上涨机会。是否注册为 active 模型由后端同学决定。
```

## 6. 回归价格路径模型

模型目录：

```text
artifacts/models/regressor/finsight_reg_return_path_v1.2/
```

模型配置：

```text
model_name = finsight_reg_return_path_v1.2
model_type = ExtraTreesRegressor
candidate_name = extra_trees_shallow
forecast_days = 5
```

预测目标：

```text
target_return_d1
target_return_d2
target_return_d3
target_return_d4
target_return_d5
```

正式 rolling validation 指标：

```text
MAE                = 0.027229451392252406
RMSE               = 0.04061780723236577
MAPE               = 124.5342089005544
MAPE_valid_ratio   = 0.9962385321100917
direction_accuracy = 0.5346422018348623
curve_mae          = 0.027229451392252406
```

说明：

```text
MAE、RMSE、curve_mae 越低越好。
direction_accuracy 越高越好。
MAPE 在收益率接近 0 时会被极小分母放大，因此仅作为补充参考。
```

相比原 v1.2 XGB baseline：

```text
原 v1.2 XGB:
MAE                = 0.02775220971095528
RMSE               = 0.041393802951685266
direction_accuracy = 0.5182201834862386
curve_mae          = 0.027752209710955274
```

ExtraTrees 版本提升：

```text
MAE 约下降 1.88%
RMSE 约下降 1.87%
direction_accuracy 约提升 1.64 个百分点
curve_mae 约下降 1.88%
```

相比 v1.1 回归模型：

```text
v1.1:
MAE                = 0.04509145079016288
RMSE               = 0.06298616307146268
direction_accuracy = 0.5245
curve_mae          = 0.045091450790162885
```

v1.2 ExtraTrees 在收益率路径误差上显著降低，方向准确率也略高于 v1.1。

## 7. 三类模型分工

推荐系统中建议这样使用：

```text
主分类模型：负责未来 15 日方向判断；
强信号模型：负责未来 10 日强上涨信号判断；
回归模型：负责未来 1~5 日收益率路径和预测价格曲线。
```

推荐等级和 `recommendation_score` 不建议只依赖回归模型。回归模型主要用于价格路径、收益率幅度和报告解释。

## 8. 不提交内容

本次 GitHub 交付不得提交：

```text
*.db
*.sqlite
*.sqlite3
大训练集 CSV
原始行情 CSV
原始新闻 JSON
原始财报 JSON
日志文件
API key
.env
local_experiments/outputs/
artifacts/experiments/
__pycache__/
*.pyc
```

本次允许提交小型模型 artifact，原因是课程项目需要后端同学直接拉取模型使用，且模型文件体积较小。
