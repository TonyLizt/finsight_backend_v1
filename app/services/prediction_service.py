"""预测服务第一版。

说明：
- 真正的 XGBoost 分类/回归模型后续可在这里替换。
- 当前实现采用基于最近行情与新闻情绪的确定性占位逻辑，保证接口和数据库流程先跑通。
- LLM 报告当前使用模板生成，后续可替换为真实大模型调用。
"""

from datetime import date, datetime, timedelta
from math import exp
from sqlalchemy.orm import Session

from app.core.exceptions import AppException, DATA_NOT_FOUND, PREDICTION_NOT_FOUND, STOCK_NOT_SUPPORTED
from app.models.all_models import Prediction, ModelVersion, Stock, PriceData, NewsData
from app.schemas.prediction import PredictionRunRequest
from app.services.stock_service import get_stock_or_404, latest_price, price_curve, latest_sentiment_summary, normalize_ticker
from app.services.model_service import load_active_model, predict_aux_classifier, predict_classifier, predict_regressor
from app.services.feature_service import build_feature_dict, validate_feature_columns
from app.services.prediction_input_service import ensure_prediction_inputs
from app.services.llm_service import generate_news_llm_report, generate_overall_llm_report


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


def _parse_date(value) -> date | None:
    """把 ensure_prediction_inputs 返回的日期统一转换为 date。

    daily refresh / prediction input service 为了方便 JSON 返回，通常会把日期转成
    "YYYY-MM-DD" 字符串；而 build_feature_dict 需要 date 或 None。这里统一处理，
    避免用户指定 base_trading_date 后因为类型不一致而误判 DATA_NOT_FOUND。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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


def _infer_model_match_status(db: Session, pred: Prediction) -> str:
    """为历史预测详情补齐模型匹配状态。

    run_prediction 新生成预测时会直接传入 model_match_status；
    但历史详情查询只保存了 model_version_id，因此需要根据预测周期和模型周期动态推断。
    """
    classifier = db.query(ModelVersion).filter(ModelVersion.id == pred.model_version_id).first() if pred.model_version_id else None
    if not classifier:
        return "placeholder_no_active_model"
    if classifier.horizon_days == pred.forecast_days:
        return "exact_match"
    return "nearest_active_model"


def _recommendation_level(score: float) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "high"
    if score >= 50:
        return "medium"
    return "low"



def _clean_request_params(params: dict | None) -> dict:
    """清理保存和返回给前端的请求参数。

    request_params 只应该保存用户原始请求参数，不应重复嵌入运行时状态。
    运行时状态统一放在顶层 data_refresh_status，以及 explanation_json.data_refresh_status。
    """
    if not isinstance(params, dict):
        return {}

    cleaned = dict(params)
    cleaned.pop("data_refresh_status", None)
    return cleaned


def _normalize_news_summary(summary: dict | None) -> dict | None:
    """补全新闻情绪摘要的起止时间。

    当前 sentiment_daily 聚合通常能给出 sentiment_curve，但部分路径下
    news_start_time / news_end_time 为空。前端展示时需要明确窗口范围，因此：
    - 如果 news_start_time 为空，则使用 sentiment_curve 第一项的 date；
    - 如果 news_end_time 为空，则使用 sentiment_curve 最后一项的 date。
    """
    if not isinstance(summary, dict):
        return summary

    normalized = dict(summary)
    curve = normalized.get("sentiment_curve")

    if isinstance(curve, list) and curve:
        dates = [
            str(item.get("date"))
            for item in curve
            if isinstance(item, dict) and item.get("date")
        ]

        if dates:
            if not normalized.get("news_start_time"):
                normalized["news_start_time"] = dates[0]
            if not normalized.get("news_end_time"):
                normalized["news_end_time"] = dates[-1]

    return normalized


def run_prediction(db: Session, user_id: int, req: PredictionRunRequest) -> dict:
    """运行真实模型预测。

    v1.0 接入内容：
    - 从 model_versions 读取 active 分类模型和回归模型；
    - 从 price_data + technical_indicators 构造特征；
    - 调用 XGBoost 分类模型输出 up/neutral/down 概率；
    - 调用 XGBoost 回归模型输出未来收益率路径；
    - 生成 price_path、recommendation_score；
    - 保存 predictions 记录并返回 v5 API 结构。

    当前限制：
    - 新闻情绪特征仍为 v1.0 占位 0；
    - LLM 报告仍使用模板降级。
    """
    ticker = normalize_ticker(req.ticker)

    stock = get_stock_or_404(db, ticker)
    if not stock.is_supported:
        raise AppException(STOCK_NOT_SUPPORTED, "该股票存在于基础库，但当前系统暂不支持分析。", 400)

    # 预测前自动补全输入数据：
    # 1. 如行情缺失或过旧，自动尝试拉取最新可用日频行情；
    # 2. 重算 technical_indicators；
    # 3. 基于最新行情/指标/情绪生成 runtime model_feature_snapshots；
    # 4. 后续 build_feature_dict 将优先读取最新快照。
    data_refresh_status = ensure_prediction_inputs(
        db=db,
        ticker=ticker,
        forecast_days=req.forecast_days,
        news_window_days=req.news_window_days,
        force_refresh=req.force_refresh,
        base_trading_date=req.base_trading_date,
    )

    # 如果预测输入准备失败，不继续调用模型。
    # 典型失败场景：
    # 1. 外部行情抓取失败，且缓存行情缺失或存在疑似异常；
    # 2. 用户指定的 base_trading_date 之前没有可用行情；
    # 3. runtime feature snapshot 无法生成。
    if not data_refresh_status.get("can_continue", False):
        raise AppException(
            DATA_NOT_FOUND,
            data_refresh_status.get("message") or f"未找到 {ticker} 的可用行情/特征数据。",
            404,
        )

    actual_base_date = _parse_date(data_refresh_status.get("actual_base_trading_date"))

    # 如果用户指定了 base_trading_date，必须使用该日期或该日期之前最近一个可用交易日。
    # 不能静默退回全库最新日期，否则前端会误以为真的按指定日期预测。
    if req.base_trading_date and actual_base_date is None:
        raise AppException(
            DATA_NOT_FOUND,
            f"未找到 {ticker} 在 {req.base_trading_date} 或之前的可用行情/特征数据。",
            404,
        )

    # 如果没有指定日期，但自动补全也没有给出实际基准日，则让 feature_service 走最新快照。
    latest = latest_price(db, ticker)
    if not latest or latest.close is None:
        raise AppException(DATA_NOT_FOUND, "未能获取该股票足够历史行情数据。", 404)

    # 读取 active 模型。
    classifier_model, classifier_match_status = load_active_model(db, "classifier", req.forecast_days)
    reg_model, reg_match_status = load_active_model(db, "regressor", req.forecast_days)

    if classifier_model.feature_columns != reg_model.feature_columns:
        raise AppException(DATA_NOT_FOUND, "分类模型与回归模型的特征列不一致，无法执行预测。", 500)

    # 构造与训练时完全一致的特征。
    # 关键：这里必须使用 ensure_prediction_inputs 返回的 actual_base_trading_date。
    # 它可能等于用户指定日期，也可能是该日期之前最近一个有行情的交易日。
    feature_result = build_feature_dict(db, ticker, base_trading_date=actual_base_date)
    feature_dict = feature_result["feature_dict"]
    validate_feature_columns(feature_dict, classifier_model.feature_columns)

    current_price = float(feature_result["current_price"])
    base_date = feature_result["base_trading_date"]

    trading_days = _next_trading_days(base_date, req.forecast_days)
    forecast_start = trading_days[0]
    forecast_end = trading_days[-1]

    # 真实分类模型输出。
    classification = predict_classifier(classifier_model, feature_dict)
    predicted_label = classification["predicted_label"]
    prob_up = classification["prob_up"]
    prob_neutral = classification["prob_neutral"]
    prob_down = classification["prob_down"]

    # 辅助强信号模型。B 同学 v1.2 的 aux_classifier 使用 RidgeClassifier，
    # 没有 predict_proba，model_service 中用 decision_function + sigmoid 生成 strong_signal_score。
    aux_signal = None
    strong_signal_score = None
    try:
        aux_model, _aux_match_status = load_active_model(db, "aux_classifier", 10)
        aux_signal = predict_aux_classifier(aux_model, feature_dict)
        strong_signal_score = aux_signal.get("strong_signal_score")
    except Exception:
        # 辅助模型是增强项，失败时不应阻断主预测流程。
        aux_signal = None
        strong_signal_score = None

    # 真实回归模型输出收益率路径。
    predicted_returns = predict_regressor(reg_model, feature_dict)
    predicted_returns = predicted_returns[: req.forecast_days]

    volatility = float(feature_dict.get("volatility_20d", 0.0) or 0.0)

    price_path = []
    for idx, target_day in enumerate(trading_days, start=1):
        predicted_return = float(predicted_returns[idx - 1])
        predicted_price = current_price * (1 + predicted_return)

        # v1.0 区间：用历史 20 日波动率近似，不作为确定预测。
        band = current_price * volatility * (idx ** 0.5)

        price_path.append(
            {
                "day_index": idx,
                "target_date": target_day.isoformat(),
                "predicted_return": predicted_return,
                "predicted_price": round(predicted_price, 4),
                "lower_bound": round(predicted_price - band, 4),
                "upper_bound": round(predicted_price + band, 4),
            }
        )

    predicted_prices = [p["predicted_price"] for p in price_path]
    max_upside = max(0.0, (max(predicted_prices) - current_price) / current_price)
    max_downside = max(0.0, (current_price - min(predicted_prices)) / current_price)

    sentiment = latest_sentiment_summary(db, ticker, end_date=base_date, window_days=req.news_window_days)
    sentiment_score = float(sentiment.get("sentiment_score") or 0.0)

    recommendation_score = (
        50
        + (prob_up - prob_down) * 40
        + max_upside * 300
        - max_downside * 200
        + sentiment_score * 10
        - volatility * 100
    )

    # 辅助强信号模型参与推荐分数，但权重保持较小，避免覆盖主分类/回归信号。
    if strong_signal_score is not None:
        recommendation_score += (float(strong_signal_score) - 0.5) * 20
    recommendation_score = max(0, min(100, recommendation_score))
    recommendation_level = _recommendation_level(recommendation_score)

    if classifier_match_status == reg_match_status:
        model_match_status = classifier_match_status
    elif "nearest_active_model" in {classifier_match_status, reg_match_status}:
        model_match_status = "nearest_active_model"
    else:
        model_match_status = classifier_match_status

    # 保证 news_summary 中有可展示的窗口起止时间。
    sentiment = _normalize_news_summary(sentiment)

    latest_news_rows = (
        db.query(NewsData)
        .filter(NewsData.ticker == ticker)
        .order_by(NewsData.publish_time.desc())
        .limit(10)
        .all()
    )
    latest_news_for_llm = [
        {
            "news_id": item.id,
            "title": item.title,
            "summary": item.summary,
            "source": item.source,
            "publish_time": item.publish_time.isoformat() if item.publish_time else None,
            "sentiment_score": item.sentiment_score,
            "sentiment_label": item.sentiment_label,
        }
        for item in latest_news_rows
    ]

    news_llm_report = generate_news_llm_report(
        ticker=ticker,
        company_name=stock.company_name,
        base_trading_date=base_date.isoformat() if base_date else None,
        news_summary=sentiment,
        latest_news=latest_news_for_llm,
    )
    if not news_llm_report:
        news_llm_report = (
            "当前版本已接入新闻情绪摘要，但百炼新闻 LLM 深度分析暂时不可用。"
            f"近窗口新闻情绪分数为 {sentiment_score:.3f}，"
            "该分数已参与推荐分数计算。"
        )

    explanations = [
        f"分类模型预测方向为 {predicted_label}。",
        f"上涨概率为 {prob_up:.1%}，震荡概率为 {prob_neutral:.1%}，下跌概率为 {prob_down:.1%}。",
        f"回归模型预测未来 {req.forecast_days} 个交易日最大上行空间约为 {max_upside:.2%}，最大下行空间约为 {max_downside:.2%}。",
    ]

    if strong_signal_score is not None:
        explanations.append(f"辅助强信号模型得分为 {strong_signal_score:.3f}，已小权重参与推荐分数。")

    explanations.append(f"推荐购买分数为 {recommendation_score:.1f}，等级为 {recommendation_level}。")

    classification_for_llm = {
        "predicted_label": predicted_label,
        "prob_up": prob_up,
        "prob_neutral": prob_neutral,
        "prob_down": prob_down,
        "predicted_growth_prob": prob_up,
        "aux_model": aux_signal,
    }
    regression_for_llm = {
        "current_price": current_price,
        "price_path": price_path,
        "max_predicted_upside_pct": max_upside,
        "max_predicted_downside_pct": max_downside,
    }
    recommendation_for_llm = {
        "recommendation_score": recommendation_score,
        "recommendation_level": recommendation_level,
        "meaning": "分数越高越推荐，满分 100",
    }

    report_text = generate_overall_llm_report(
        ticker=ticker,
        company_name=stock.company_name,
        base_trading_date=base_date.isoformat() if base_date else None,
        forecast_days=req.forecast_days,
        current_price=current_price,
        classification=classification_for_llm,
        regression=regression_for_llm,
        recommendation=recommendation_for_llm,
        news_summary=sentiment,
        news_llm_report=news_llm_report,
        explanations=explanations,
    )
    if not report_text:
        report_text = (
            f"综合来看，{ticker} 在未来 {req.forecast_days} 个交易日的模型预测方向为 {predicted_label}。"
            "本结果由当前激活的新闻增强版分类模型与回归模型生成。"
            "当前已接入新闻情绪摘要并参与推荐分数计算，但百炼整体 LLM 报告暂时不可用。"
            "结果仅用于课程实践和模拟分析，不构成真实投资建议。"
        )

    # 返回给前端和保存到数据库的 request_params 只保留用户原始请求，
    # 不重复嵌入 data_refresh_status。
    clean_request_params = _clean_request_params(req.model_dump(mode="json"))

    pred = Prediction(
        user_id=user_id,
        ticker=ticker,
        model_version_id=classifier_model.version.id,
        reg_model_version_id=reg_model.version.id,
        base_trading_date=base_date,
        forecast_days=req.forecast_days,
        forecast_start_date=forecast_start,
        forecast_end_date=forecast_end,
        request_params_json=clean_request_params,
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
        explanation_json={
            "main_reasons": explanations,
            "aux_model": aux_signal,
            "data_refresh_status": data_refresh_status,
            "risk_notes": [
                "当前使用 v1.2 模型：主分类模型为二分类适配三分类 API，回归模型输出未来 1~5 日收益率路径。",
                "预测输入会优先使用自动补全后的 model_feature_snapshots；若外部行情源不可用，则使用数据库已有最新特征。",
                "预测结果仅用于课程实践和模拟分析，不构成真实投资建议。",
            ],
        },
        report_text=report_text,
    )

    db.add(pred)
    db.commit()
    db.refresh(pred)

    return prediction_to_detail(db, pred, model_match_status=model_match_status, saved=True)

def prediction_to_card(db: Session, pred: Prediction) -> dict:
    stock = db.query(Stock).filter(Stock.ticker == pred.ticker).first()
    path = (pred.predicted_prices_json or {}).get("path", [])
    summary = _normalize_news_summary(pred.sentiment_summary_json or {}) or {}
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
        "news_start_time": summary.get("news_start_time"),
        "news_end_time": summary.get("news_end_time"),
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
        "model_match_status": model_match_status or _infer_model_match_status(db, pred),
        "request_params": _clean_request_params(pred.request_params_json),
        "current_price": float(pred.current_price) if pred.current_price is not None else None,
        "classification": {
            "predicted_label": pred.predicted_label,
            "prob_up": pred.prob_up,
            "prob_neutral": pred.prob_neutral,
            "prob_down": pred.prob_down,
            "predicted_growth_prob": pred.predicted_growth_prob,
            "aux_model": (pred.explanation_json or {}).get("aux_model"),
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
        "data_refresh_status": (pred.explanation_json or {}).get("data_refresh_status"),
        "base_trading_date_source": ((pred.explanation_json or {}).get("data_refresh_status") or {}).get("base_trading_date_source"),
        "news_summary": _normalize_news_summary(pred.sentiment_summary_json),
        "news_llm_report": pred.news_llm_report,
        "explanations": (pred.explanation_json or {}).get("main_reasons", []),
        "llm_report": pred.report_text,
    }
    # 详情接口读取的是已保存预测记录，因此 saved 默认返回 true。
    data["saved"] = True if saved is None else bool(saved)
    return data


def get_prediction_for_user(db: Session, prediction_id: int, user_id: int, is_admin: bool) -> Prediction:
    q = db.query(Prediction).filter(Prediction.id == prediction_id)
    if not is_admin:
        q = q.filter(Prediction.user_id == user_id)
    pred = q.first()
    if not pred:
        raise AppException(PREDICTION_NOT_FOUND, "预测记录不存在。", 404)
    return pred
