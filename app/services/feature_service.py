"""模型特征构造服务。

本文件用于为 PredictionService / BacktestService 构造模型输入特征。

当前 v1.2 模型的输入特征来自 B 同学交付的 50 维训练特征。为了避免线上
预测时遗漏财报类 fund_* 特征，本服务采用以下优先级：

1. 优先从 MySQL 表 ``model_feature_snapshots`` 读取训练集导入后的真实 50 维特征；
2. 如果没有找到快照，再退回到 price_data / technical_indicators / sentiment_daily 动态构造；
3. 动态构造仅作为降级方案，无法保证与 v1.2 训练特征完全一致。

注意：
- ``model_feature_snapshots`` 不是 ORM 模型表，使用 SQLAlchemy text 查询；
- 快照表由 ``app/scripts/import_member_b_real_data.py`` 导入；
- 预测接口仍应使用 ``validate_feature_columns`` 校验模型要求的特征列是否齐全。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.all_models import PriceData, SentimentDaily, TechnicalIndicator


MODEL_FEATURE_SNAPSHOT_TABLE = "model_feature_snapshots"


# v1.2 模型中使用的财报基底特征。正常情况下应从 model_feature_snapshots 中读取真实值。
# 这里的默认值只用于动态构造降级路径，避免旧数据环境直接缺字段。
FUNDAMENTAL_DEFAULT_FEATURES: dict[str, float] = {
    "fundamental_available": 0.0,
    "fund_report_age_days": 9999.0,
    "fund_days_since_fiscal_end": 9999.0,
    "fund_reported_eps": 0.0,
    "fund_estimated_eps": 0.0,
    "fund_eps_surprise": 0.0,
    "fund_eps_surprise_pct": 0.0,
    "fund_total_revenue": 0.0,
    "fund_gross_profit": 0.0,
    "fund_operating_income": 0.0,
    "fund_net_income": 0.0,
    "fund_ebit": 0.0,
    "fund_ebitda": 0.0,
    "fund_gross_margin": 0.0,
    "fund_operating_margin": 0.0,
    "fund_net_margin": 0.0,
    "fund_revenue_yoy": 0.0,
    "fund_net_income_yoy": 0.0,
    "fund_eps_yoy": 0.0,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    """将数据库/JSON 值安全转换为 float。

    MySQL DECIMAL、pandas 导入后的数值、JSON 解析值可能存在 Decimal、None、
    空字符串等情况。模型输入统一使用 float。
    """
    if value is None:
        return default

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (int, float)):
        return float(value)

    try:
        text_value = str(value).strip()
        if text_value == "" or text_value.lower() in {"nan", "none", "null"}:
            return default
        return float(text_value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """将数据库值安全转换为 int。"""
    if value is None:
        return default

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_features_json(value: Any) -> dict[str, float]:
    """解析 model_feature_snapshots.features_json。

    PyMySQL 读取 MySQL JSON 字段时通常返回 str；部分环境可能返回 bytes 或 dict。
    这里统一解析为 ``dict[str, float]``，并尽量把所有值转换为 float。
    """
    if value is None:
        return {}

    if isinstance(value, bytes):
        value = value.decode("utf-8")

    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
    elif isinstance(value, dict):
        data = value
    else:
        return {}

    if not isinstance(data, dict):
        return {}

    return {str(k): _safe_float(v) for k, v in data.items()}


def _normalize_date(value: Any) -> date | None:
    """将数据库返回的日期值标准化为 ``datetime.date``。"""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _read_latest_feature_snapshot(
    db: Session,
    ticker: str,
    base_trading_date: date | None = None,
) -> dict[str, Any] | None:
    """读取某只股票最近一条真实模型特征快照。

    查询规则：
    - 如果传入 ``base_trading_date``，读取小于等于该日期的最新一条快照；
    - 如果没有传入日期，读取该 ticker 最新一条快照；
    - 如果快照表不存在或查询失败，返回 None，由上层走动态构造降级路径。

    返回结构与 ``build_feature_dict`` 兼容：
    {
        "ticker": "AAPL",
        "base_trading_date": date(2025, 5, 13),
        "current_price": 190.0,
        "feature_dict": {...50维真实特征...},
        "feature_source": "model_feature_snapshots"
    }
    """
    ticker = ticker.upper()

    if base_trading_date is None:
        sql = text(
            f"""
            SELECT base_trading_date, current_price, features_json
            FROM {MODEL_FEATURE_SNAPSHOT_TABLE}
            WHERE ticker = :ticker
            ORDER BY base_trading_date DESC
            LIMIT 1
            """
        )
        params: dict[str, Any] = {"ticker": ticker}
    else:
        sql = text(
            f"""
            SELECT base_trading_date, current_price, features_json
            FROM {MODEL_FEATURE_SNAPSHOT_TABLE}
            WHERE ticker = :ticker
              AND base_trading_date <= :base_trading_date
            ORDER BY base_trading_date DESC
            LIMIT 1
            """
        )
        params = {
            "ticker": ticker,
            "base_trading_date": base_trading_date,
        }

    try:
        row = db.execute(sql, params).mappings().first()
    except SQLAlchemyError:
        # 快照表不存在、字段不存在、连接异常等情况都不在这里中断预测流程。
        # 上层会继续尝试动态构造特征。
        return None

    if row is None:
        return None

    feature_dict = _parse_features_json(row.get("features_json"))
    if not feature_dict:
        return None

    snapshot_date = _normalize_date(row.get("base_trading_date"))
    current_price = _safe_float(row.get("current_price"), default=0.0)

    # 有些训练集未显式保存 current_price，此时可退回使用 close 特征。
    if current_price <= 0:
        current_price = _safe_float(feature_dict.get("close"), default=0.0)

    return {
        "ticker": ticker,
        "base_trading_date": snapshot_date,
        "current_price": current_price,
        "feature_dict": feature_dict,
        "feature_source": "model_feature_snapshots",
    }


def get_latest_feature_date(db: Session, ticker: str) -> date | None:
    """获取某只股票当前可用于模型预测的最新特征日期。

    优先使用 ``model_feature_snapshots`` 的最新日期，因为 v1.2 模型需要完整 50 维
    特征，其中包含财报基底特征。若快照表没有数据，再退回技术指标表。
    """
    ticker = ticker.upper()

    try:
        row = db.execute(
            text(
                f"""
                SELECT MAX(base_trading_date) AS latest_date
                FROM {MODEL_FEATURE_SNAPSHOT_TABLE}
                WHERE ticker = :ticker
                """
            ),
            {"ticker": ticker},
        ).mappings().first()

        latest_date = _normalize_date(row.get("latest_date")) if row else None
        if latest_date is not None:
            return latest_date
    except SQLAlchemyError:
        pass

    row = (
        db.query(TechnicalIndicator)
        .filter(TechnicalIndicator.ticker == ticker)
        .order_by(TechnicalIndicator.trading_date.desc())
        .first()
    )
    return row.trading_date if row else None


def _read_sentiment_features(db: Session, ticker: str, trading_date: date) -> dict[str, float]:
    """读取新闻情绪特征。

    与训练集 feature_columns 保持一致：
    - news_count
    - positive_news_count
    - negative_news_count
    - neutral_news_count
    - sentiment_score
    - sentiment_score_3d_avg
    - sentiment_score_7d_avg
    - positive_ratio
    - negative_ratio

    如果某一天没有 sentiment_daily，则新闻相关特征全部置为中性值。
    """
    ticker = ticker.upper()

    row = (
        db.query(SentimentDaily)
        .filter(
            SentimentDaily.ticker == ticker,
            SentimentDaily.trading_date == trading_date,
        )
        .first()
    )

    if row is None:
        return {
            "news_count": 0.0,
            "positive_news_count": 0.0,
            "negative_news_count": 0.0,
            "neutral_news_count": 0.0,
            "sentiment_score": 0.0,
            "sentiment_score_3d_avg": 0.0,
            "sentiment_score_7d_avg": 0.0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
        }

    news_count = _safe_int(row.news_count)
    positive_count = _safe_int(row.positive_news_count)
    negative_count = _safe_int(row.negative_news_count)
    neutral_count = _safe_int(row.neutral_news_count)

    def avg_sentiment(days: int) -> float:
        start_date = trading_date - timedelta(days=days - 1)
        rows = (
            db.query(SentimentDaily)
            .filter(
                SentimentDaily.ticker == ticker,
                SentimentDaily.trading_date >= start_date,
                SentimentDaily.trading_date <= trading_date,
            )
            .all()
        )
        scores = [_safe_float(x.sentiment_score) for x in rows if x.sentiment_score is not None]
        return sum(scores) / len(scores) if scores else 0.0

    return {
        "news_count": float(news_count),
        "positive_news_count": float(positive_count),
        "negative_news_count": float(negative_count),
        "neutral_news_count": float(neutral_count),
        "sentiment_score": _safe_float(row.sentiment_score),
        "sentiment_score_3d_avg": avg_sentiment(3),
        "sentiment_score_7d_avg": avg_sentiment(7),
        "positive_ratio": positive_count / news_count if news_count > 0 else 0.0,
        "negative_ratio": negative_count / news_count if news_count > 0 else 0.0,
    }


def _build_feature_dict_from_market_tables(
    db: Session,
    ticker: str,
    base_trading_date: date | None = None,
) -> dict[str, Any]:
    """从行情、技术指标、新闻情绪表动态构造特征。

    这是降级逻辑，适用于没有导入 ``model_feature_snapshots`` 的旧环境。
    对于 v1.2 模型，正式预测应优先使用快照表中的真实 50 维特征。
    """
    ticker = ticker.upper()

    indicator_query = db.query(TechnicalIndicator).filter(TechnicalIndicator.ticker == ticker)

    if base_trading_date is None:
        indicator = indicator_query.order_by(TechnicalIndicator.trading_date.desc()).first()
    else:
        indicator = (
            indicator_query
            .filter(TechnicalIndicator.trading_date <= base_trading_date)
            .order_by(TechnicalIndicator.trading_date.desc())
            .first()
        )

    if indicator is None:
        raise RuntimeError(f"No technical indicators found for ticker={ticker}, date={base_trading_date}")

    price = (
        db.query(PriceData)
        .filter(
            PriceData.ticker == ticker,
            PriceData.trading_date == indicator.trading_date,
        )
        .first()
    )

    if price is None:
        raise RuntimeError(f"No price data found for ticker={ticker}, date={indicator.trading_date}")

    if price.close is None:
        raise RuntimeError(f"Close price is missing for ticker={ticker}, date={indicator.trading_date}")

    sentiment_features = _read_sentiment_features(db, ticker, indicator.trading_date)

    feature_dict: dict[str, float] = {
        # 行情基础特征
        "close": _safe_float(price.close),
        "open": _safe_float(price.open),
        "high": _safe_float(price.high),
        "low": _safe_float(price.low),
        "volume": float(_safe_int(price.volume)),
        "daily_return": _safe_float(price.daily_return),
        "change_percent": _safe_float(price.change_percent),
        "amplitude": _safe_float(price.amplitude),

        # 技术指标特征
        "return_1d": _safe_float(getattr(indicator, "return_1d", None)),
        "return_3d": _safe_float(getattr(indicator, "return_3d", None)),
        "return_5d": _safe_float(getattr(indicator, "return_5d", None)),
        "ma5": _safe_float(indicator.ma5),
        "ma20": _safe_float(indicator.ma20),
        "ma60": _safe_float(indicator.ma60),
        "ma5_gap": _safe_float(getattr(indicator, "ma5_gap", None)),
        "ma20_gap": _safe_float(getattr(indicator, "ma20_gap", None)),
        "ma60_gap": _safe_float(getattr(indicator, "ma60_gap", None)),
        "rsi": _safe_float(indicator.rsi, default=50.0),
        "macd": _safe_float(indicator.macd),
        "volatility_20d": _safe_float(getattr(indicator, "volatility_20d", None)),
        "drawdown_20d": _safe_float(getattr(indicator, "drawdown_20d", None)),
        "volume_zscore": _safe_float(getattr(indicator, "volume_zscore", None)),

        # 新闻情绪特征
        **sentiment_features,

        # 财报特征降级默认值。正式环境应由 model_feature_snapshots 提供真实值。
        **FUNDAMENTAL_DEFAULT_FEATURES,
    }

    return {
        "ticker": ticker,
        "base_trading_date": indicator.trading_date,
        "current_price": _safe_float(price.close),
        "feature_dict": feature_dict,
        "feature_source": "market_tables_fallback",
    }


def build_feature_dict(
    db: Session,
    ticker: str,
    base_trading_date: date | None = None,
) -> dict[str, Any]:
    """构造单只股票某交易日的模型输入特征。

    优先策略：
    1. 从 ``model_feature_snapshots`` 读取已经导入 MySQL 的真实 50 维训练特征；
    2. 如果快照不存在，再从行情/技术指标/情绪表动态构造；
    3. 返回结构保持与原 PredictionService 兼容。
    """
    ticker = ticker.upper()

    snapshot = _read_latest_feature_snapshot(db, ticker, base_trading_date)
    if snapshot is not None:
        return snapshot

    return _build_feature_dict_from_market_tables(db, ticker, base_trading_date)


def validate_feature_columns(feature_dict: dict[str, Any], feature_columns: list[str]) -> None:
    """校验模型要求的特征列是否齐全。

    此函数只检查字段是否存在，不改变字段顺序。模型调用前应按
    ``feature_columns`` 顺序构造 DataFrame / ndarray。
    """
    missing = [c for c in feature_columns if c not in feature_dict]
    if missing:
        raise RuntimeError(f"Missing feature columns: {missing}")