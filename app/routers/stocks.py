"""Stock API：股票搜索、详情、新闻、情绪摘要。"""

from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import User, TechnicalIndicator
from app.services.indicator_service import rebuild_technical_indicators_for_ticker
from app.services.market_data_service import ensure_price_data
from app.services.stock_service import (
    normalize_ticker,
    get_stock_or_404,
    search_stocks,
    latest_price,
    price_curve,
    calc_52_week_high_low,
    latest_sentiment_summary,
    news_query,
    get_news_or_404,
    display_change_percent,
    stock_data_status,
)

router = APIRouter(prefix="/api/stocks", tags=["Stock API"])


@router.get("/search")
def search(
    keyword: str,
    only_supported: bool = False,
    include_etf: bool = True,
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, total = search_stocks(db, keyword, only_supported, include_etf, limit)
    return ok(
        {
            "items": [
                {
                    "ticker": s.ticker,
                    "company_name": s.company_name,
                    "security_name": s.security_name,
                    "market": s.market,
                    "exchange": s.exchange,
                    "listing_source": s.listing_source,
                    "etf": s.etf,
                    # is_supported 面向前端使用：只有基础库标记支持且已有行情数据时才返回 true。
                    # 原始股票基础库支持状态可通过 raw_is_supported 查看。
                    "is_supported": s.is_supported and stock_data_status(db, s) == "ready",
                    "raw_is_supported": s.is_supported,
                    "data_status": stock_data_status(db, s),
                }
                for s in items
            ],
            "total": total,
        }
    )


@router.get("/{ticker}/detail")
def stock_detail(
    ticker: str,
    range: str = "3m",
    include_news: bool = True,
    include_indicators: bool = True,
    auto_refresh: bool = False,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ticker = normalize_ticker(ticker)
    stock = get_stock_or_404(db, ticker)

    refresh_status = None
    if auto_refresh:
        # 可选刷新：股票详情默认读库，传 auto_refresh=true 时尝试补齐最新可用日频行情。
        # 这不是毫秒级实时行情，而是最新可用日频数据。
        refresh_status = ensure_price_data(db, ticker, force_refresh=force_refresh)
        rebuild_technical_indicators_for_ticker(db, ticker)

    latest = latest_price(db, ticker)
    days_map = {"1m": 22, "3m": 66, "6m": 132, "1y": 252}
    days = days_map.get(range, 66)
    curve = price_curve(db, ticker, days)
    high52, low52 = calc_52_week_high_low(db, ticker)

    latest_news = []
    if include_news:
        latest_news = news_query(db, ticker).limit(10).all()

    indicator_curve = []
    if include_indicators:
        indicators = db.query(TechnicalIndicator).filter(TechnicalIndicator.ticker == ticker).order_by(TechnicalIndicator.trading_date.desc()).limit(days).all()[::-1]
        indicator_curve = [
            {
                "date": i.trading_date.isoformat(),
                "ma5": i.ma5,
                "ma20": i.ma20,
                "ma60": i.ma60,
                "rsi": i.rsi,
                "macd": i.macd,
            }
            for i in indicators
        ]

    data_status = stock_data_status(db, stock)
    current_quote = {
        "current_price": None,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "previous_close": None,
        "change": None,
        "change_percent": None,
        "daily_return": None,
        "amplitude": None,
        "fifty_two_week_high": high52,
        "fifty_two_week_low": low52,
        "volume": None,
        "trading_date": None,
    }
    if latest:
        current_quote = {
            "current_price": float(latest.close) if latest.close is not None else None,
            "open": float(latest.open) if latest.open is not None else None,
            "high": float(latest.high) if latest.high is not None else None,
            "low": float(latest.low) if latest.low is not None else None,
            "close": float(latest.close) if latest.close is not None else None,
            "previous_close": float(latest.previous_close) if latest.previous_close is not None else None,
            "change": float(latest.change_amount) if latest.change_amount is not None else None,
            "change_percent": display_change_percent(latest.change_percent),
            "daily_return": latest.daily_return,
            "amplitude": latest.amplitude,
            "fifty_two_week_high": high52,
            "fifty_two_week_low": low52,
            "volume": latest.volume,
            "trading_date": latest.trading_date.isoformat(),
            "quote_source": "yahoo_chart" if auto_refresh and refresh_status else "mysql_price_data",
            "quote_fetched_at": datetime.now().isoformat() if auto_refresh and refresh_status else None,
            "is_realtime": False,
            "data_frequency": "daily",
        }

    return ok(
        {
            "ticker": stock.ticker,
            "company_name": stock.company_name,
            "market": stock.market,
            "sector": None,
            "is_supported": stock.is_supported and data_status == "ready",
            "raw_is_supported": stock.is_supported,
            "data_status": data_status,
            "data_refresh_status": refresh_status,
            "current_quote": current_quote,
            "price_curve": [
                {
                    "date": p.trading_date.isoformat(),
                    "open": float(p.open) if p.open is not None else None,
                    "high": float(p.high) if p.high is not None else None,
                    "low": float(p.low) if p.low is not None else None,
                    "close": float(p.close) if p.close is not None else None,
                    "daily_return": p.daily_return,
                    "amplitude": p.amplitude,
                    "volume": p.volume,
                }
                for p in curve
            ],
            "indicator_curve": indicator_curve,
            "latest_news": [
                {
                    "news_id": n.id,
                    "title": n.title,
                    "summary": n.summary,
                    "source": n.source,
                    "publish_time": n.publish_time.isoformat() if n.publish_time else None,
                    "sentiment_score": n.sentiment_score if n.sentiment_score is not None else 0.0,
                    "sentiment_label": n.sentiment_label or "neutral",
                }
                for n in latest_news
            ],
            "sentiment_summary": latest_sentiment_summary(db, ticker),
        }
    )


@router.get("/{ticker}/news")
def stock_news(
    ticker: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 20,
    sentiment_label: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ticker = normalize_ticker(ticker)
    get_stock_or_404(db, ticker)
    q = news_query(db, ticker, start_time, end_time, sentiment_label)
    items = q.limit(min(limit, 100)).all()
    return ok(
        {
            "ticker": ticker,
            "news_start_time": start_time.isoformat() if start_time else None,
            "news_end_time": end_time.isoformat() if end_time else None,
            "items": [
                {
                    "news_id": n.id,
                    "title": n.title,
                    "summary": n.summary,
                    "source": n.source,
                    "url": n.url,
                    "publish_time": n.publish_time.isoformat() if n.publish_time else None,
                    "assigned_trading_date": n.assigned_trading_date.isoformat() if n.assigned_trading_date else None,
                    "sentiment_score": n.sentiment_score if n.sentiment_score is not None else 0.0,
                    "sentiment_label": n.sentiment_label or "neutral",
                    "has_detail": bool(n.content_text or n.content_html or n.news_llm_analysis),
                }
                for n in items
            ],
            "total": q.count(),
        }
    )


@router.get("/news/{news_id}")
def news_detail(news_id: int, include_html: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = get_news_or_404(db, news_id)
    return ok(
        {
            "news_id": n.id,
            "ticker": n.ticker,
            "title": n.title,
            "summary": n.summary,
            "content_text": n.content_text,
            "content_html": n.content_html if include_html else None,
            "source": n.source,
            "url": n.url,
            "publish_time": n.publish_time.isoformat() if n.publish_time else None,
            "assigned_trading_date": n.assigned_trading_date.isoformat() if n.assigned_trading_date else None,
            "sentiment_score": n.sentiment_score if n.sentiment_score is not None else 0.0,
            "sentiment_label": n.sentiment_label or "neutral",
            "news_llm_analysis": n.news_llm_analysis,
            "content_status": n.content_status,
            "content_fetched_at": n.content_fetched_at.isoformat() if n.content_fetched_at else None,
        }
    )


@router.get("/{ticker}/sentiment-summary")
def sentiment_summary(
    ticker: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    window_days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ticker = normalize_ticker(ticker)
    get_stock_or_404(db, ticker)
    end_date = end_time.date() if end_time else None
    return ok({"ticker": ticker, **latest_sentiment_summary(db, ticker, end_date=end_date, window_days=window_days)})
