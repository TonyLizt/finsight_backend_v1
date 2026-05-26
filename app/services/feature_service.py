"""特征构造服务。

职责：
1. 根据 ticker 和 base_trading_date 读取 price_data 与 technical_indicators；
2. 读取 sentiment_daily，构造新闻情绪特征；
3. 构造与训练时 feature_columns.json 完全一致的 feature_dict。

注意：
- 如果某个交易日没有 sentiment_daily，则新闻特征填 0；
- 当前 sentiment_daily 使用前 14 天新闻窗口聚合。
"""

from __future__ import annotations

from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.models.all_models import PriceData, TechnicalIndicator, SentimentDaily


def get_latest_feature_date(db: Session, ticker: str) -> date | None:
    row = (
        db.query(TechnicalIndicator)
        .filter(TechnicalIndicator.ticker == ticker.upper())
        .order_by(TechnicalIndicator.trading_date.desc())
        .first()
    )
    return row.trading_date if row else None


def _read_sentiment_features(db: Session, ticker: str, trading_date: date) -> dict:
    """读取新闻情绪特征。

    与训练集 feature_columns 保持一致：
    news_count
    positive_news_count
    negative_news_count
    neutral_news_count
    sentiment_score
    sentiment_score_3d_avg
    sentiment_score_7d_avg
    positive_ratio
    negative_ratio
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
            "news_count": 0,
            "positive_news_count": 0,
            "negative_news_count": 0,
            "neutral_news_count": 0,
            "sentiment_score": 0.0,
            "sentiment_score_3d_avg": 0.0,
            "sentiment_score_7d_avg": 0.0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
        }

    news_count = int(row.news_count or 0)
    positive_count = int(row.positive_news_count or 0)
    negative_count = int(row.negative_news_count or 0)
    neutral_count = int(row.neutral_news_count or 0)

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
        scores = [float(x.sentiment_score) for x in rows if x.sentiment_score is not None]
        return sum(scores) / len(scores) if scores else 0.0

    return {
        "news_count": news_count,
        "positive_news_count": positive_count,
        "negative_news_count": negative_count,
        "neutral_news_count": neutral_count,
        "sentiment_score": float(row.sentiment_score or 0.0),
        "sentiment_score_3d_avg": avg_sentiment(3),
        "sentiment_score_7d_avg": avg_sentiment(7),
        "positive_ratio": positive_count / news_count if news_count > 0 else 0.0,
        "negative_ratio": negative_count / news_count if news_count > 0 else 0.0,
    }


def build_feature_dict(
    db: Session,
    ticker: str,
    base_trading_date: date | None = None,
) -> dict:
    """构造单只股票某交易日的模型特征。

    如果 base_trading_date 为空，则使用该股票最新的 technical_indicators 日期。
    """
    ticker = ticker.upper()

    indicator_query = db.query(TechnicalIndicator).filter(TechnicalIndicator.ticker == ticker)

    if base_trading_date is None:
        indicator = indicator_query.order_by(TechnicalIndicator.trading_date.desc()).first()
    else:
        indicator = indicator_query.filter(TechnicalIndicator.trading_date == base_trading_date).first()

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

    feature_dict = {
        "close": float(price.close),
        "open": float(price.open) if price.open is not None else 0.0,
        "high": float(price.high) if price.high is not None else 0.0,
        "low": float(price.low) if price.low is not None else 0.0,
        "volume": int(price.volume or 0),
        "daily_return": float(price.daily_return or 0.0),
        "change_percent": float(price.change_percent or 0.0),
        "amplitude": float(price.amplitude or 0.0),

        "return_1d": float(indicator.return_1d or 0.0),
        "return_3d": float(indicator.return_3d or 0.0),
        "return_5d": float(indicator.return_5d or 0.0),
        "ma5": float(indicator.ma5 or 0.0),
        "ma20": float(indicator.ma20 or 0.0),
        "ma60": float(indicator.ma60 or 0.0),
        "ma5_gap": float(indicator.ma5_gap or 0.0),
        "ma20_gap": float(indicator.ma20_gap or 0.0),
        "ma60_gap": float(indicator.ma60_gap or 0.0),
        "rsi": float(indicator.rsi or 50.0),
        "macd": float(indicator.macd or 0.0),
        "volatility_20d": float(indicator.volatility_20d or 0.0),
        "drawdown_20d": float(indicator.drawdown_20d or 0.0),
        "volume_zscore": float(indicator.volume_zscore or 0.0),

        **sentiment_features,
    }

    return {
        "ticker": ticker,
        "base_trading_date": indicator.trading_date,
        "current_price": float(price.close),
        "feature_dict": feature_dict,
    }


def validate_feature_columns(feature_dict: dict, feature_columns: list[str]) -> None:
    missing = [c for c in feature_columns if c not in feature_dict]
    if missing:
        raise RuntimeError(f"Missing feature columns: {missing}")
