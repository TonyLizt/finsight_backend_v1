# Finsight Member B v1.2 model_versions 注册信息草案

## 1. 说明

本文档只提供 `model_versions` 注册信息草案，不直接修改正式数据库。

后端同学可以根据实际表结构调整字段名称。

## 2. 主分类模型

```text
model_name: finsight_cls_abs_h15_v1.2
model_type: classifier
task: abs_sign
horizon_days: 15
forecast_days: 15
artifact_path: artifacts/models/classifier/finsight_cls_abs_h15_v1.2/model.joblib
feature_columns_path: artifacts/models/classifier/finsight_cls_abs_h15_v1.2/feature_columns.json
label_config_path: artifacts/models/classifier/finsight_cls_abs_h15_v1.2/label_config.json
metrics_path: artifacts/models/classifier/finsight_cls_abs_h15_v1.2/metrics.json
is_active: true / 待后端确认
description: v1.2 主分类模型，未来 15 日方向二分类，不是 up-neutral-down 三分类。
```

指标：

```text
accuracy = 0.606938
macro_f1 = 0.588123
mean_hc_test_accuracy = 0.688357
```

## 3. 辅助强信号模型

```text
model_name: finsight_cls_action1p5_h10_v1.2
model_type: classifier / auxiliary_classifier
task: action_1p5
horizon_days: 10
forecast_days: 10
artifact_path: artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/model.joblib
feature_columns_path: artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/feature_columns.json
label_config_path: artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/label_config.json
metrics_path: artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/metrics.json
is_active: false / auxiliary / 待后端确认
description: v1.2 辅助强信号模型，判断未来 10 日收益是否超过 1.5%。
```

指标：

```text
accuracy = 0.612031
macro_f1 = 0.573192
mean_hc_test_accuracy = 0.647277
```

注意：如果现有 `model_versions` 表不支持 `auxiliary_classifier`，可先记录为 `classifier`，但在 description 中注明这是辅助信号模型。

## 4. 回归价格路径模型

```text
model_name: finsight_reg_return_path_v1.2
model_type: regressor
task: return_path_regression
horizon_days: 5
forecast_days: 5
artifact_path: artifacts/models/regressor/finsight_reg_return_path_v1.2/model.joblib
feature_columns_path: artifacts/models/regressor/finsight_reg_return_path_v1.2/feature_columns.json
target_config_path: artifacts/models/regressor/finsight_reg_return_path_v1.2/target_config.json
metrics_path: artifacts/models/regressor/finsight_reg_return_path_v1.2/metrics.json
is_active: true / 待后端确认
description: v1.2 回归价格路径模型，ExtraTreesRegressor，预测未来 1~5 个交易日收益率路径。
```

指标：

```text
MAE = 0.027229451392252406
RMSE = 0.04061780723236577
direction_accuracy = 0.5346422018348623
curve_mae = 0.027229451392252406
MAPE = 124.5342089005544
```

MAPE 仅作为补充参考。

## 5. 接入注意事项

1. v1.2 分类模型不是 up / neutral / down 三分类。
2. 后端不要直接假设分类模型输出 `prob_down / prob_neutral / prob_up`。
3. 主分类 logreg 模型可以使用 `predict_proba`。
4. 强信号 ridge 模型不一定有 `predict_proba`，可使用 `decision_function` 后 sigmoid 转 pseudo-score。
5. 回归模型输出 shape 应为 `(1, 5)`。
6. 所有模型输入都必须严格按照 `feature_columns.json` 顺序构造。
