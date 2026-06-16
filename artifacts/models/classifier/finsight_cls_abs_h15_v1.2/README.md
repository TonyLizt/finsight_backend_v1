# abs_h15_market_ext_logreg

## Purpose

This directory contains the final deployable model artifact for Finsight.

## Candidate

- candidate: `abs_h15_market_ext_logreg`
- task: `abs_sign`
- horizon: `15`
- feature_set: `f0_market_ext`
- model: `logreg`

## Files

- `model.joblib`: sklearn pipeline, including imputer, scaler and classifier.
- `feature_columns.json`: ordered feature list required by the model.
- `label_config.json`: label definition and cutoff rule.
- `train_config.json`: final training configuration.
- `metrics.json`: rolling validation metrics and fitted-sample diagnostics.
- `sample_prediction_input.json`: example model input.
- `sample_prediction_output.json`: example model output.

## Data cutoff

Training uses only rows whose future target date is no later than `2025-05-20`.
Data after 2025-05-20 must be reserved for backtesting/out-of-sample use.

## Important

Large raw datasets, SQLite databases, raw news JSON, raw financial reports and API keys must not be committed to GitHub.
