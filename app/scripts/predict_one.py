"""单股预测测试脚本。

复用：
- app.services.model_service
- app.services.feature_service

用途：
验证后端服务层能从 model_versions 加载 active 模型，
并基于 price_data + technical_indicators 构造特征，输出 v5 预测结构。
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta

from app.db.session import SessionLocal
from app.services.feature_service import build_feature_dict, validate_feature_columns
from app.services.model_service import load_active_model, predict_classifier, predict_regressor


def next_business_days(base_date, n: int) -> list[str]:
    days = []
    cur = base_date
    while len(days) < n:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            days.append(cur.isoformat())
    return days


def recommendation_level(score: float) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--forecast-days", type=int, default=5)
    parser.add_argument("--base-date", default=None)
    args = parser.parse_args()

    ticker = args.ticker.upper()

    db = SessionLocal()

    try:
        cls_model, cls_match_status = load_active_model(db, "classifier", args.forecast_days)
        reg_model, reg_match_status = load_active_model(db, "regressor", args.forecast_days)

        if cls_model.feature_columns != reg_model.feature_columns:
            raise RuntimeError("Classifier and regressor feature columns are not identical.")

        feature_result = build_feature_dict(db, ticker, None if args.base_date is None else args.base_date)
        feature_dict = feature_result["feature_dict"]
        validate_feature_columns(feature_dict, cls_model.feature_columns)

        classification = predict_classifier(cls_model, feature_dict)
        predicted_returns = predict_regressor(reg_model, feature_dict)

        current_price = feature_result["current_price"]
        base_trading_date = feature_result["base_trading_date"]
        target_dates = next_business_days(base_trading_date, len(predicted_returns))

        volatility = float(feature_dict.get("volatility_20d", 0.0) or 0.0)

        price_path = []
        for i, r in enumerate(predicted_returns, start=1):
            predicted_price = current_price * (1 + r)
            band = current_price * volatility * (i ** 0.5)

            price_path.append({
                "day_index": i,
                "target_date": target_dates[i - 1],
                "predicted_return": r,
                "predicted_price": predicted_price,
                "lower_bound": predicted_price - band,
                "upper_bound": predicted_price + band,
            })

        max_upside = max(p["predicted_return"] for p in price_path)
        max_downside = min(p["predicted_return"] for p in price_path)

        prob_up = classification["prob_up"]
        prob_down = classification["prob_down"]

        raw_score = (
            50
            + (prob_up - prob_down) * 40
            + max_upside * 300
            - abs(min(0, max_downside)) * 200
            - volatility * 100
        )
        recommendation_score = max(0.0, min(100.0, raw_score))

        result = {
            "ticker": ticker,
            "base_trading_date": base_trading_date.isoformat(),
            "forecast_days": args.forecast_days,
            "current_price": current_price,
            "model_version": cls_model.version.version_name,
            "reg_model_version": reg_model.version.version_name,
            "model_match_status": cls_match_status if cls_match_status == reg_match_status else {
                "classifier": cls_match_status,
                "regressor": reg_match_status,
            },
            "classification": classification,
            "regression": {
                "current_price": current_price,
                "price_path": price_path,
            },
            "recommendation": {
                "recommendation_score": recommendation_score,
                "recommendation_level": recommendation_level(recommendation_score),
                "meaning": "分数越高越推荐，满分 100",
            },
            "note": "News sentiment features are placeholders in v1 and set to 0.",
        }

        print(json.dumps(result, ensure_ascii=False, indent=2))

    finally:
        db.close()


if __name__ == "__main__":
    main()
