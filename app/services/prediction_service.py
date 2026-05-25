"""预测服务第一版。

说明：
- 真正的 XGBoost 分类/回归模型后续可在这里替换。
- 当前实现采用基于最近行情与新闻情绪的确定性占位逻辑，保证接口和数据库流程先跑通。
- LLM 报告当前使用模板生成，后续可替换为真实大模型调用。
"""

from datetime import date, timedelta
from math import exp
from sqlalchemy.orm import Session

from app.core.exceptions import AppException, DATA_NOT_FOUND, PREDICTION_NOT_FOUND, STOCK_NOT_SUPPORTED
from app.models.all_models import Prediction, ModelVersion, Stock, PriceData
from app.schemas.prediction import PredictionRunRequest
from app.services.stock_service import get_stock_or_404, latest_price, price_curve, latest_sentiment_summary, normalize_ticker


def _sigmoid(x: float) -> float:
    return 1 / (1 + exp(-x))


def _next_trading_days(base: date, n: int) -> list[date]:
    """简单交易日估算：跳过周六周日。后续可接入真实交易日历。"""
    days: list[date] = []
    current = base
    while len(days) < n:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days.append(current)
    return days


def _active_model(db: Session, model_type: str, forecast_days: int) -> ModelVersion | None:
    exact = (
        db.query(ModelVersion)
        .filter(ModelVersion.model_type == model_type, ModelVersion.horizon_days == forecast_days, ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    if exact:
        return exact
    return db.query(ModelVersion).filter(ModelVersion.model_type == model_type, ModelVersion.is_active.is_(True)).order_by(ModelVersion.created_at.desc()).first()


def _model_version_name(db: Session, model_id: int | None) -> str | None:
    """根据 model_versions.id 获取模型版本名。"""
    if not model_id:
        return None
    model = db.query(ModelVersion).filter(ModelVersion.id == model_id).first()
    return model.version_name if model else None


def _recommendation_level(score: float) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def run_prediction(db: Session, user_id: int, req: PredictionRunRequest) -> dict:
    ticker = normalize_ticker(req.ticker)
    stock = get_stock_or_404(db, ticker)
    if not stock.is_supported:
        raise AppException(STOCK_NOT_SUPPORTED, "该股票存在于基础库，但当前系统暂不支持分析。", 400)

    latest = latest_price(db, ticker)
    if not latest or latest.close is None:
        raise AppException(DATA_NOT_FOUND, "未能获取该股票足够历史行情数据。", 404)

    current_price = float(latest.close)
    base_date = latest.trading_date
    trading_days = _next_trading_days(base_date, req.forecast_days)
    forecast_start = trading_days[0]
    forecast_end = trading_days[-1]

    # 读取最近行情，计算一个轻量趋势特征。
    curve = price_curve(db, ticker, 20)
    if len(curve) >= 2 and curve[0].close:
        recent_return = (float(curve[-1].close) - float(curve[0].close)) / float(curve[0].close)
    else:
        recent_return = float(latest.daily_return or 0)

    sentiment = latest_sentiment_summary(db, ticker)
    sentiment_score = float(sentiment.get("sentiment_score") or 0)

    # 占位分类逻辑：趋势 + 情绪共同影响上涨概率。
    up_raw = 2.2 * recent_return + 0.8 * sentiment_score
    prob_up = max(0.05, min(0.9, _sigmoid(up_raw)))
    prob_down = max(0.05, min(0.8, _sigmoid(-up_raw) * 0.5))
    prob_neutral = max(0.05, 1 - prob_up - prob_down)
    total = prob_up + prob_neutral + prob_down
    prob_up, prob_neutral, prob_down = prob_up / total, prob_neutral / total, prob_down / total
    predicted_label = max({"up": prob_up, "neutral": prob_neutral, "down": prob_down}, key={"up": prob_up, "neutral": prob_neutral, "down": prob_down}.get)

    # 占位回归逻辑：根据趋势和情绪生成价格路径。
    daily_drift = max(-0.02, min(0.02, 0.35 * recent_return / max(len(curve), 1) + 0.003 * sentiment_score))
    price_path = []
    for idx, target_day in enumerate(trading_days, start=1):
        predicted_return = daily_drift * idx
        predicted_price = current_price * (1 + predicted_return)
        price_path.append(
            {
                "day_index": idx,
                "target_date": target_day.isoformat(),
                "predicted_return": round(predicted_return, 6),
                "predicted_price": round(predicted_price, 4),
                "lower_bound": round(predicted_price * 0.98, 4),
                "upper_bound": round(predicted_price * 1.02, 4),
            }
        )

    predicted_prices = [p["predicted_price"] for p in price_path]
    max_upside = max(0.0, (max(predicted_prices) - current_price) / current_price)
    max_downside = max(0.0, (current_price - min(predicted_prices)) / current_price)

    recommendation_score = max(0, min(100, prob_up * 70 + max_upside * 1200 - max_downside * 700 + sentiment_score * 10))
    recommendation_level = _recommendation_level(recommendation_score)

    classifier_model = _active_model(db, "classifier", req.forecast_days)
    reg_model = _active_model(db, "regressor", req.forecast_days)
    model_match_status = "exact_match"
    if classifier_model and classifier_model.horizon_days != req.forecast_days:
        model_match_status = "nearest_active_model"
    if not classifier_model:
        model_match_status = "placeholder_no_active_model"

    news_llm_report = (
        "当前版本尚未接入真实大模型。模板分析：近期新闻情绪"
        f"为 {sentiment.get('sentiment_label', 'neutral')}，综合情绪分数为 {sentiment_score:.3f}，"
        "该信息已作为推荐分数的辅助因素。"
    )
    explanations = [
        f"最近行情趋势收益约为 {recent_return:.2%}。",
        f"上涨概率为 {prob_up:.1%}，下跌概率为 {prob_down:.1%}。",
        f"推荐购买分数为 {recommendation_score:.1f}，等级为 {recommendation_level}。",
    ]
    report_text = (
        f"综合来看，{ticker} 在未来 {req.forecast_days} 个交易日的预测方向为 {predicted_label}。"
        "当前结果由第一版占位推理服务生成，仅用于接口联调和课程项目演示，不构成真实投资建议。"
    )

    pred = Prediction(
        user_id=user_id,
        ticker=ticker,
        model_version_id=classifier_model.id if classifier_model else None,
        reg_model_version_id=reg_model.id if reg_model else None,
        base_trading_date=base_date,
        forecast_days=req.forecast_days,
        forecast_start_date=forecast_start,
        forecast_end_date=forecast_end,
        request_params_json=req.model_dump(),
        current_price=current_price,
        predicted_label=predicted_label,
        prob_up=prob_up,
        prob_neutral=prob_neutral,
        prob_down=prob_down,
        predicted_growth_prob=prob_up,
        recommendation_score=recommendation_score,
        recommendation_level=recommendation_level,
        max_predicted_upside_pct=max_upside,
        max_predicted_downside_pct=max_downside,
        predicted_prices_json={
            "current_price": current_price,
            "base_trading_date": base_date.isoformat(),
            "forecast_days": req.forecast_days,
            "path": price_path,
        },
        sentiment_summary_json=sentiment,
        news_llm_report=news_llm_report,
        explanation_json={"main_reasons": explanations, "risk_notes": ["第一版预测服务为占位实现，后续需接入真实模型。"]},
        report_text=report_text,
    )
    db.add(pred)
    db.commit()
    db.refresh(pred)

    return prediction_to_detail(db, pred, model_match_status=model_match_status, saved=True)


def prediction_to_card(db: Session, pred: Prediction) -> dict:
    stock = db.query(Stock).filter(Stock.ticker == pred.ticker).first()
    path = (pred.predicted_prices_json or {}).get("path", [])
    return {
        "prediction_id": pred.id,
        "ticker": pred.ticker,
        "company_name": stock.company_name if stock else None,
        "prediction_time": pred.prediction_time.isoformat() if pred.prediction_time else None,
        "base_trading_date": pred.base_trading_date.isoformat() if pred.base_trading_date else None,
        "forecast_start_date": pred.forecast_start_date.isoformat() if pred.forecast_start_date else None,
        "forecast_end_date": pred.forecast_end_date.isoformat() if pred.forecast_end_date else None,
        "forecast_days": pred.forecast_days,
        "current_price": float(pred.current_price) if pred.current_price is not None else None,
        "predicted_label": pred.predicted_label,
        "prob_up": pred.prob_up,
        "prob_neutral": pred.prob_neutral,
        "prob_down": pred.prob_down,
        "recommendation_score": pred.recommendation_score,
        "recommendation_level": pred.recommendation_level,
        "max_predicted_upside_pct": pred.max_predicted_upside_pct,
        "max_predicted_downside_pct": pred.max_predicted_downside_pct,
        "predicted_mini_curve": [{"date": p.get("target_date"), "predicted_price": p.get("predicted_price")} for p in path],
        "news_start_time": (pred.sentiment_summary_json or {}).get("news_start_time"),
        "news_end_time": (pred.sentiment_summary_json or {}).get("news_end_time"),
        "model_version": _model_version_name(db, pred.model_version_id),
    }


def prediction_to_detail(db: Session, pred: Prediction, model_match_status: str | None = None, saved: bool | None = None) -> dict:
    stock = db.query(Stock).filter(Stock.ticker == pred.ticker).first()
    path = (pred.predicted_prices_json or {}).get("path", [])
    data = {
        "prediction_id": pred.id,
        "ticker": pred.ticker,
        "company_name": stock.company_name if stock else None,
        "prediction_time": pred.prediction_time.isoformat() if pred.prediction_time else None,
        "base_trading_date": pred.base_trading_date.isoformat() if pred.base_trading_date else None,
        "forecast_start_date": pred.forecast_start_date.isoformat() if pred.forecast_start_date else None,
        "forecast_end_date": pred.forecast_end_date.isoformat() if pred.forecast_end_date else None,
        "forecast_days": pred.forecast_days,
        "coverage_status": "core_pool" if stock and stock.is_core_pool else "supported" if stock and stock.is_supported else "unknown",
        "model_version": _model_version_name(db, pred.model_version_id),
        "reg_model_version": _model_version_name(db, pred.reg_model_version_id),
        "model_match_status": model_match_status,
        "request_params": pred.request_params_json,
        "current_price": float(pred.current_price) if pred.current_price is not None else None,
        "classification": {
            "predicted_label": pred.predicted_label,
            "prob_up": pred.prob_up,
            "prob_neutral": pred.prob_neutral,
            "prob_down": pred.prob_down,
            "predicted_growth_prob": pred.predicted_growth_prob,
        },
        "regression": {
            "current_price": float(pred.current_price) if pred.current_price is not None else None,
            "price_path": path,
            "max_predicted_upside_pct": pred.max_predicted_upside_pct,
            "max_predicted_downside_pct": pred.max_predicted_downside_pct,
        },
        "recommendation": {
            "recommendation_score": pred.recommendation_score,
            "recommendation_level": pred.recommendation_level,
            "meaning": "分数越高越推荐，满分 100",
        },
        "news_summary": pred.sentiment_summary_json,
        "news_llm_report": pred.news_llm_report,
        "explanations": (pred.explanation_json or {}).get("main_reasons", []),
        "llm_report": pred.report_text,
    }
    if saved is not None:
        data["saved"] = saved
    return data


def get_prediction_for_user(db: Session, prediction_id: int, user_id: int, is_admin: bool) -> Prediction:
    q = db.query(Prediction).filter(Prediction.id == prediction_id)
    if not is_admin:
        q = q.filter(Prediction.user_id == user_id)
    pred = q.first()
    if not pred:
        raise AppException(PREDICTION_NOT_FOUND, "预测记录不存在。", 404)
    return pred
