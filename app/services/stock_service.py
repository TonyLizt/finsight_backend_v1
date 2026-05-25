"""股票、行情、新闻查询服务。"""

from datetime import datetime, timedelta
from sqlalchemy import and_, or_, func, case
from sqlalchemy.orm import Session

from app.core.exceptions import AppException, DATA_NOT_FOUND, INVALID_TICKER, NEWS_NOT_FOUND
from app.models.all_models import Stock, PriceData, NewsData, TechnicalIndicator, SentimentDaily


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def display_change_percent(value: float | None) -> float | None:
    """将数据库中的涨跌幅转换为前端展示百分数。

    数据库里第一版 demo 数据把 change_percent 按比例保存，例如 0.0066；
    04 API 文档示例中 change_percent 用百分数展示，例如 0.66。
    为兼容历史数据，这里约定：绝对值不超过 1 的值视为比例，返回 value * 100；
    已经大于 1 的值视为百分数，直接返回。daily_return 仍保持比例值。
    """
    if value is None:
        return None
    v = float(value)
    return v * 100 if abs(v) <= 1 else v


def get_stock(db: Session, ticker: str) -> Stock | None:
    return db.query(Stock).filter(Stock.ticker == normalize_ticker(ticker)).first()


def get_stock_or_404(db: Session, ticker: str) -> Stock:
    stock = get_stock(db, ticker)
    if not stock:
        raise AppException(INVALID_TICKER, "股票代码无效或尚未同步到股票基础库。", 404)
    return stock


def search_stocks(db: Session, keyword: str, only_supported: bool, include_etf: bool, limit: int) -> tuple[list[Stock], int]:
    """搜索股票基础库。

    排序规则专门为前端联想搜索优化：
    1. ticker 完全等于关键词的结果排第一，例如搜索 AAPL 时 Apple Inc. 优先于 AAPB；
    2. ticker 以关键词开头的结果优先；
    3. 当前系统支持分析的证券优先；
    4. 普通股优先于 ETF；
    5. 核心股票池优先。
    """
    raw_keyword = keyword.strip()
    kw = f"%{raw_keyword}%"
    keyword_upper = raw_keyword.upper()

    q = db.query(Stock).filter(
        or_(
            Stock.ticker.ilike(kw),
            Stock.company_name.ilike(kw),
            Stock.security_name.ilike(kw),
        )
    )
    if only_supported:
        q = q.filter(Stock.is_supported.is_(True))
    if not include_etf:
        q = q.filter(or_(Stock.etf.is_(False), Stock.etf.is_(None)))

    total = q.count()

    exact_ticker_rank = case((func.upper(Stock.ticker) == keyword_upper, 0), else_=1)
    prefix_ticker_rank = case((func.upper(Stock.ticker).like(f"{keyword_upper}%"), 0), else_=1)
    supported_rank = case((Stock.is_supported.is_(True), 0), else_=1)
    non_etf_rank = case((or_(Stock.etf.is_(False), Stock.etf.is_(None)), 0), else_=1)
    core_pool_rank = case((Stock.is_core_pool.is_(True), 0), else_=1)

    items = (
        q.order_by(
            exact_ticker_rank,
            prefix_ticker_rank,
            supported_rank,
            non_etf_rank,
            core_pool_rank,
            Stock.ticker.asc(),
        )
        .limit(min(limit, 100))
        .all()
    )
    return items, total


def stock_has_price_data(db: Session, ticker: str) -> bool:
    """判断该 ticker 是否已经有可用于详情/预测/回测的行情数据。"""
    row = latest_price(db, normalize_ticker(ticker))
    return bool(row and row.close is not None)


def stock_data_status(db: Session, stock: Stock) -> str:
    """返回前端可直接理解的数据状态。

    synced：只代表股票基础库中存在；
    ready：代表已有行情数据，可支持详情、预测与回测；
    no_price_data：基础库中存在，但当前没有足够行情数据；
    unsupported：系统暂不支持分析该证券。
    """
    if not stock.is_supported:
        return "unsupported"
    if not stock_has_price_data(db, stock.ticker):
        return "no_price_data"
    return "ready"


def latest_price(db: Session, ticker: str) -> PriceData | None:
    return db.query(PriceData).filter(PriceData.ticker == ticker).order_by(PriceData.trading_date.desc()).first()


def price_curve(db: Session, ticker: str, days: int = 90) -> list[PriceData]:
    return db.query(PriceData).filter(PriceData.ticker == ticker).order_by(PriceData.trading_date.desc()).limit(days).all()[::-1]


def calc_52_week_high_low(db: Session, ticker: str) -> tuple[float | None, float | None]:
    rows = db.query(PriceData).filter(PriceData.ticker == ticker).order_by(PriceData.trading_date.desc()).limit(252).all()
    highs = [float(r.high) for r in rows if r.high is not None]
    lows = [float(r.low) for r in rows if r.low is not None]
    return (max(highs) if highs else None, min(lows) if lows else None)


def latest_sentiment_summary(db: Session, ticker: str) -> dict:
    rows = db.query(SentimentDaily).filter(SentimentDaily.ticker == ticker).order_by(SentimentDaily.trading_date.desc()).limit(7).all()[::-1]
    if not rows:
        return {
            "news_start_time": None,
            "news_end_time": None,
            "sentiment_score": 0.0,
            "sentiment_label": "neutral",
            "positive_news_count": 0,
            "negative_news_count": 0,
            "neutral_news_count": 0,
            "total_news_count": 0,
            "sentiment_curve": [],
        }
    total = sum(r.news_count or 0 for r in rows)
    pos = sum(r.positive_news_count or 0 for r in rows)
    neg = sum(r.negative_news_count or 0 for r in rows)
    neu = sum(r.neutral_news_count or 0 for r in rows)
    scores = [r.sentiment_score for r in rows if r.sentiment_score is not None]
    avg = sum(scores) / len(scores) if scores else 0
    label = "positive" if avg > 0.1 else "negative" if avg < -0.1 else "neutral"
    return {
        "news_start_time": rows[0].news_start_time.isoformat() if rows[0].news_start_time else None,
        "news_end_time": rows[-1].news_end_time.isoformat() if rows[-1].news_end_time else None,
        "sentiment_score": avg,
        "sentiment_label": label,
        "positive_news_count": pos,
        "negative_news_count": neg,
        "neutral_news_count": neu,
        "total_news_count": total,
        "sentiment_curve": [
            {
                "date": r.trading_date.isoformat(),
                "sentiment_score": r.sentiment_score,
                "positive_news_count": r.positive_news_count,
                "negative_news_count": r.negative_news_count,
                "total_news_count": r.news_count,
            }
            for r in rows
        ],
    }


def news_query(db: Session, ticker: str, start_time=None, end_time=None, sentiment_label=None):
    q = db.query(NewsData).filter(NewsData.ticker == normalize_ticker(ticker))
    if start_time:
        q = q.filter(NewsData.publish_time >= start_time)
    if end_time:
        q = q.filter(NewsData.publish_time <= end_time)
    if sentiment_label:
        q = q.filter(NewsData.sentiment_label == sentiment_label)
    return q.order_by(NewsData.publish_time.desc())


def get_news_or_404(db: Session, news_id: int) -> NewsData:
    news = db.query(NewsData).filter(NewsData.id == news_id).first()
    if not news:
        raise AppException(NEWS_NOT_FOUND, "新闻不存在。", 404)
    return news
