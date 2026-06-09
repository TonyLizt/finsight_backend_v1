"""Stock API：股票搜索、详情、新闻、情绪摘要。"""

from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import User, TechnicalIndicator
from app.services.intraday_market_service import get_intraday_curve
from app.services.news_detail_fetch_service import enrich_news_detail_if_needed
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
    sentiment_counts_for_last_two_weeks,
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
    interval: str | None = None,
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
        # 可选刷新：股票详情默认读库，传 auto_refresh=true 时尝试用 Twelve Data
        # 增量补齐最新可用日频行情；range=1d 会优先读 intraday_price_data，
        # 缺失时由 Twelve Data 1min 分时数据现场补入库。
        # interval=1min 时返回分钟级曲线；默认返回 hourly 聚合以兼容旧前端。
        refresh_status = ensure_price_data(db, ticker, force_refresh=force_refresh)
        rebuild_technical_indicators_for_ticker(db, ticker)

    latest = latest_price(db, ticker)

    requested_range = (range or "3m").strip().lower()
    valid_ranges = {"1d", "5d", "1m", "3m", "6m", "1y", "all"}
    if requested_range not in valid_ranges:
        requested_range = "3m"

    daily_days_map = {
        "5d": 5,
        "1m": 22,
        "3m": 66,
        "6m": 132,
        "1y": 252,
    }

    is_intraday_range = requested_range == "1d"
    intraday_status = None

    def normalize_intraday_interval(value: str | None) -> str:
        normalized = (value or "hourly").strip().lower().replace("_", "-")
        if normalized in {"1min", "1-min", "1minute", "1-minute", "minute", "minutes", "min", "m1", "1m"}:
            return "1min"
        if normalized in {"hourly", "hour", "hours", "1h", "h1", "60min", "60-min"}:
            return "hourly"
        return "hourly"

    intraday_interval = normalize_intraday_interval(interval)

    if is_intraday_range:
        # 1d 图默认使用 intraday_price_data 中的 Twelve Data 1min 数据聚合成小时级；
        # 传 interval=1min 时直接返回原始分钟级曲线。
        # 数据库缺失时可现场调用 Twelve Data 补入库。
        intraday_result = get_intraday_curve(ticker, target_date=None, interval=intraday_interval)
        price_curve_items = intraday_result.get("items", [])
        data_frequency = intraday_result.get("data_frequency") or intraday_interval
        intraday_status = {
            "status": intraday_result.get("status"),
            "source": intraday_result.get("source"),
            "ak_symbol": intraday_result.get("ak_symbol"),
            "target_date": intraday_result.get("target_date"),
            "actual_date": intraday_result.get("actual_date"),
            "interval": data_frequency,
            "minute_count": intraday_result.get("minute_count"),
            "message": intraday_result.get("message"),
            "error": intraday_result.get("error"),
            "ingest_result": intraday_result.get("ingest_result"),
        }
        days = 1
    else:
        days = None if requested_range == "all" else daily_days_map.get(requested_range, 66)
        curve = price_curve(db, ticker, days)
        price_curve_items = [
            {
                "date": p.trading_date.isoformat(),
                "open": float(p.open) if p.open is not None else None,
                "high": float(p.high) if p.high is not None else None,
                "low": float(p.low) if p.low is not None else None,
                "close": float(p.close) if p.close is not None else None,
                "daily_return": p.daily_return,
                "amplitude": p.amplitude,
                "volume": p.volume,
                "data_frequency": "daily",
                "source": "mysql_price_data",
            }
            for p in curve
        ]
        data_frequency = "daily"

    high52, low52 = calc_52_week_high_low(db, ticker)

    latest_news = []
    if include_news:
        latest_news = news_query(db, ticker).limit(10).all()

    indicator_curve = []
    if include_indicators:
        indicator_q = (
            db.query(TechnicalIndicator)
            .filter(TechnicalIndicator.ticker == ticker)
            .order_by(TechnicalIndicator.trading_date.desc())
        )
        # range=all 时技术指标也返回全部；range=1d 时只返回最新一条日频技术指标。
        if days is not None:
            indicator_q = indicator_q.limit(days)
        indicators = indicator_q.all()[::-1]
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
            "quote_source": "mysql_price_data",
            "quote_fetched_at": datetime.now().isoformat() if auto_refresh and refresh_status else None,
            "is_realtime": False,
            "data_frequency": "daily",
        }

    curve_start = None
    curve_end = None
    if price_curve_items:
        if is_intraday_range:
            curve_start = price_curve_items[0].get("timestamp")
            curve_end = price_curve_items[-1].get("timestamp")
        else:
            curve_start = price_curve_items[0].get("date")
            curve_end = price_curve_items[-1].get("date")

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
            "price_range": requested_range,
            "data_frequency": data_frequency,
            "intraday_interval": data_frequency if is_intraday_range else None,
            "price_curve_count": len(price_curve_items),
            "price_curve_start": curve_start,
            "price_curve_end": curve_end,
            "intraday_status": intraday_status,
            "current_quote": current_quote,
            "price_curve": price_curve_items,
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
            "sentiment_counts": sentiment_counts_for_last_two_weeks(db, ticker),
            "sentiment_summary": latest_sentiment_summary(db, ticker, window_days=14),
        }
    )


@router.get("/{ticker}/news")
def stock_news(
    ticker: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 20,
    cursor: int = 0,
    return_all: bool = False,
    sentiment_label: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询股票新闻列表。

    支持两种模式：
    1. 滚动加载模式（默认）：return_all=false，使用 cursor + limit 分页；
    2. 全量返回模式：return_all=true，返回指定时间段内的全部新闻。

    cursor 是滚动分页索引，表示从筛选结果的第几条开始取。排序固定为
    publish_time desc，因此前端下一次请求直接传回 next_cursor 即可继续加载。
    """
    ticker = normalize_ticker(ticker)
    get_stock_or_404(db, ticker)

    q = news_query(db, ticker, start_time, end_time, sentiment_label)
    total = q.count()

    # 保护后端：滚动加载时一次最多返回 100 条；return_all=true 时不受 limit 限制。
    safe_cursor = max(cursor, 0)
    safe_limit = max(1, min(limit, 100))

    if return_all:
        items = q.all()
        next_cursor = None
        has_more = False
        effective_limit = None
        effective_cursor = 0
    else:
        items = q.offset(safe_cursor).limit(safe_limit).all()
        loaded_until = safe_cursor + len(items)
        has_more = loaded_until < total
        next_cursor = loaded_until if has_more else None
        effective_limit = safe_limit
        effective_cursor = safe_cursor

    serialized_items = [
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
    ]

    return ok(
        {
            "ticker": ticker,
            "news_start_time": start_time.isoformat() if start_time else None,
            "news_end_time": end_time.isoformat() if end_time else None,
            "sentiment_label": sentiment_label,
            "return_all": return_all,
            "pagination_mode": "all" if return_all else "scroll",
            "cursor": effective_cursor,
            "limit": effective_limit,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "returned_count": len(serialized_items),
            "sentiment_counts": sentiment_counts_for_last_two_weeks(db, ticker, end_time=end_time),
            "total": total,
            "items": serialized_items,
        }
    )


@router.get("/news/{news_id}")
def news_detail(news_id: int, include_html: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = get_news_or_404(db, news_id)
    # v1.5：新闻详情页按需补正文。批量补正文仍由 news_fulltext 模块负责。
    n = enrich_news_detail_if_needed(db, n, include_html=include_html, force_fetch=False)
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
    window_days: int = 14,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ticker = normalize_ticker(ticker)
    get_stock_or_404(db, ticker)
    end_date = end_time.date() if end_time else None
    summary = latest_sentiment_summary(db, ticker, end_date=end_date, window_days=window_days)
    counts = sentiment_counts_for_last_two_weeks(db, ticker, end_time=end_time)
    return ok({"ticker": ticker, **summary, "sentiment_counts": counts})
