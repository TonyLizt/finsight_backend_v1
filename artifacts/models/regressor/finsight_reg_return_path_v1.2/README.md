# Finsight 回归价格路径模型 v1.2

本目录是 Finsight / 智融洞察项目 Member B v1.2 回归模型交付目录。

## 1. 模型基本信息

- 模型名称：finsight_reg_return_path_v1.2
- 模型类型：ExtraTreesRegressor
- 候选实验名称：extra_trees_shallow
- 任务：预测未来 1~5 个交易日收益率路径
- 目标列：target_return_d1 到 target_return_d5

## 2. 数据边界

训练样本过滤规则：

target_date_d5 <= 2025-05-20

训练数据来自：

/data/hmt/projects/finsight/finsight_backend_v1_git/local_experiments/outputs/expanded_60_no_weak10_news48_quality_fundamental/training_dataset/dataset_h5_v1.csv

训练样本 base_trading_date 范围：

2023-03-29 -> 2025-05-13

目标标签 target_date_d5 范围：

2023-04-05 -> 2025-05-20

## 3. 滚动验证指标

整体 rolling validation 指标：

MAE = 0.027229451392252406
RMSE = 0.04061780723236577
MAPE = 124.5342089005544
Direction Accuracy = 0.5346422018348623
Curve MAE = 0.027229451392252406

相比原 XGB baseline，本版本在 MAE、RMSE、Curve MAE 和 Direction Accuracy 上均有提升。

## 4. 指标解释

MAE 和 Curve MAE 越低，说明未来 1~5 日收益率路径预测误差越小。

RMSE 越低，说明模型较少出现较大的离谱误差。

Direction Accuracy 越高，说明模型对未来收益率正负方向判断越准。

MAPE 在收益率接近 0 时容易失真，因此只作为补充参考。

## 5. 加载方式

加载时必须按 feature_columns.json 中的顺序构造输入特征。

示例：

import json
import joblib
import pandas as pd

model = joblib.load("model.joblib")
feature_columns = json.load(open("feature_columns.json", "r", encoding="utf-8"))
sample = json.load(open("sample_prediction_input.json", "r", encoding="utf-8"))

x = pd.DataFrame([sample["features"]])[feature_columns]
pred = model.predict(x)

print(pred.shape)  # 应为 (1, 5)

## 6. 文件清单

model.joblib
feature_columns.json
target_config.json
metrics.json
train_config.json
README.md
sample_prediction_input.json
sample_prediction_output.json
rolling_regression_metrics_by_fold.csv
