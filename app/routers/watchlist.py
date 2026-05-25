"""Watchlist API：自选股增删查。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import User, Watchlist
from app.schemas.watchlist import AddWatchlistRequest
from app.services.stock_service import normalize_ticker, get_stock_or_404, latest_price, price_curve, display_change_percent

router = APIRouter(prefix="/api/watchlist", tags=["Watchlist API"])


@router.get("")
def get_watchlist(include_curve: bool = True, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Watchlist).filter(Watchlist.user_id == user.id).order_by(Watchlist.created_at.desc()).all()
    items = []
    for w in rows:
        stock = get_stock_or_404(db, w.ticker)
        latest = latest_price(db, w.ticker)
        curve = price_curve(db, w.ticker, 5) if include_curve else []
        items.append(
            {
                "ticker": w.ticker,
                "company_name": stock.company_name,
                "market": stock.market,
                "current_price": float(latest.close) if latest and latest.close is not None else None,
                "change": float(latest.change_amount) if latest and latest.change_amount is not None else None,
                "change_percent": display_change_percent(latest.change_percent) if latest else None,
                "daily_return": latest.daily_return if latest else None,
                "amplitude": latest.amplitude if latest else None,
                "volume": latest.volume if latest else None,
                "last_trading_date": latest.trading_date.isoformat() if latest else None,
                "mini_curve": [{"date": p.trading_date.isoformat(), "close": float(p.close) if p.close is not None else None} for p in curve],
            }
        )
    return ok({"items": items})


@router.post("")
def add_watchlist(req: AddWatchlistRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ticker = normalize_ticker(req.ticker)
    stock = get_stock_or_404(db, ticker)
    exists = db.query(Watchlist).filter(Watchlist.user_id == user.id, Watchlist.ticker == ticker).first()
    if not exists:
        exists = Watchlist(user_id=user.id, ticker=ticker)
        db.add(exists)
        db.commit()
        db.refresh(exists)
    return ok(
        {
            "ticker": ticker,
            "company_name": stock.company_name,
            "is_supported": stock.is_supported,
            "data_quality_score": stock.data_quality_score,
            "added_at": exists.created_at.isoformat() if exists.created_at else None,
        },
        "watchlist item added",
    )


@router.delete("/{ticker}")
def remove_watchlist(ticker: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ticker = normalize_ticker(ticker)
    row = db.query(Watchlist).filter(Watchlist.user_id == user.id, Watchlist.ticker == ticker).first()
    if row:
        db.delete(row)
        db.commit()
    return ok({"ticker": ticker, "deleted": True}, "watchlist item deleted")
