# Finsight Member B v1.2 模型加载说明

## 1. 模型目录

本次 v1.2 交付三个模型目录：

```text
artifacts/models/classifier/finsight_cls_abs_h15_v1.2/
artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/
artifacts/models/regressor/finsight_reg_return_path_v1.2/
```

每个模型目录均包含：

```text
model.joblib
feature_columns.json
metrics.json
train_config.json
sample_prediction_input.json
sample_prediction_output.json
```

分类模型额外包含：

```text
label_config.json
```

回归模型额外包含：

```text
target_config.json
rolling_regression_metrics_by_fold.csv
```

## 2. 特征顺序要求

加载模型时，必须严格按照 `feature_columns.json` 中的顺序构造输入特征。

不能手动改变列顺序，不能漏列，也不能多列。

示例：

```python
import json
import joblib
import pandas as pd

model_dir = "artifacts/models/regressor/finsight_reg_return_path_v1.2"

model = joblib.load(f"{model_dir}/model.joblib")

with open(f"{model_dir}/feature_columns.json", "r", encoding="utf-8") as f:
    feature_columns = json.load(f)

with open(f"{model_dir}/sample_prediction_input.json", "r", encoding="utf-8") as f:
    sample = json.load(f)

x = pd.DataFrame([sample["features"]])[feature_columns]
pred = model.predict(x)

print(pred.shape)
print(pred)
```

回归模型的期望输出形状：

```text
(1, 5)
```

## 3. 主分类模型加载

主分类模型目录：

```text
artifacts/models/classifier/finsight_cls_abs_h15_v1.2/
```

模型任务：

```text
未来 15 日方向二分类
```

该模型是 logreg 模型，通常可以使用：

```python
model.predict(x)
model.predict_proba(x)
```

输出不是三分类的：

```text
prob_down / prob_neutral / prob_up
```

而是二分类概率。后端接入时必须读取 `label_config.json` 确认标签含义。

## 4. 强信号模型加载

强信号模型目录：

```text
artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/
```

模型任务：

```text
未来 10 日收益是否超过 1.5%
```

该模型是 ridge 强信号模型，不一定提供原生 `predict_proba`。

如果需要辅助 score，可以使用：

```python
import numpy as np

score_raw = model.decision_function(x)
score = 1.0 / (1.0 + np.exp(-score_raw))
```

该 score 是 sigmoid 转换后的 pseudo-score，不是严格校准概率。

强信号模型建议作为 auxiliary classifier 使用，是否注册为 active 模型由后端同学决定。

## 5. 回归模型加载

回归模型目录：

```text
artifacts/models/regressor/finsight_reg_return_path_v1.2/
```

模型类型：

```text
ExtraTreesRegressor
```

输出：

```text
[pred_return_d1, pred_return_d2, pred_return_d3, pred_return_d4, pred_return_d5]
```

如果需要还原价格路径：

```text
predicted_price_i = current_price * (1 + pred_return_di)
```

## 6. 推荐接入方式

建议后端接入时：

```text
分类主模型用于趋势方向判断；
强信号模型用于辅助识别强上涨机会；
回归模型用于生成未来 1~5 日收益率路径和价格曲线。
```

不要让回归模型单独承担买卖推荐判断。
