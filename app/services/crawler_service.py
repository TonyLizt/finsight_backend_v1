"""爬虫与股票基础库同步服务。

第一版只实现股票基础库同步。行情/新闻爬虫由成员 B 后续完善。
"""

from datetime import datetime
import csv
import io
import requests
from sqlalchemy.orm import Session

from app.models.all_models import Stock, StockUniverseSyncLog, CrawlerLog

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"


def _yn(value: str | None) -> bool:
    return str(value or "").upper() == "Y"


def _upsert_stock(db: Session, ticker: str, values: dict) -> str:
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if stock:
        for k, v in values.items():
            setattr(stock, k, v)
        stock.last_synced_at = datetime.utcnow()
        return "updated"
    db.add(Stock(ticker=ticker, first_seen_at=datetime.utcnow(), last_synced_at=datetime.utcnow(), **values))
    return "inserted"


def sync_stock_universe(db: Session) -> dict:
    """同步 nasdaqlisted 和 otherlisted。生产环境可放入定时任务。"""
    started = datetime.utcnow()
    total_fetched = inserted = updated = 0
    source_results = []

    for source_name, url in [("nasdaqlisted", NASDAQ_LISTED_URL), ("otherlisted", OTHER_LISTED_URL)]:
        src_started = datetime.utcnow()
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            text = resp.text
            rows = [line for line in text.splitlines() if line and not line.startswith("File Creation Time")]
            reader = csv.DictReader(io.StringIO("\n".join(rows)), delimiter="|")
            fetched = inserted_one = updated_one = 0
            for row in reader:
                ticker = (row.get("Symbol") or row.get("ACT Symbol") or "").strip().upper()
                if not ticker:
                    continue
                fetched += 1
                if source_name == "nasdaqlisted":
                    values = {
                        "company_name": row.get("Security Name"),
                        "security_name": row.get("Security Name"),
                        "market": "NASDAQ",
                        "exchange": "Q",
                        "listing_source": source_name,
                        "market_category": row.get("Market Category"),
                        "etf": _yn(row.get("ETF")),
                        "test_issue": _yn(row.get("Test Issue")),
                        "financial_status": row.get("Financial Status"),
                        "round_lot_size": int(row.get("Round Lot Size") or 100),
                        "is_supported": not _yn(row.get("Test Issue")),
                        "raw_json": row,
                    }
                else:
                    values = {
                        "company_name": row.get("Security Name"),
                        "security_name": row.get("Security Name"),
                        "market": row.get("Exchange"),
                        "exchange": row.get("Exchange"),
                        "listing_source": source_name,
                        "cqs_symbol": row.get("CQS Symbol"),
                        "nasdaq_symbol": row.get("NASDAQ Symbol"),
                        "etf": _yn(row.get("ETF")),
                        "test_issue": _yn(row.get("Test Issue")),
                        "round_lot_size": int(row.get("Round Lot Size") or 100),
                        "is_supported": not _yn(row.get("Test Issue")),
                        "raw_json": row,
                    }
                action = _upsert_stock(db, ticker, values)
                if action == "inserted":
                    inserted_one += 1
                else:
                    updated_one += 1
            db.add(
                StockUniverseSyncLog(
                    source_name=source_name,
                    source_url=url,
                    started_at=src_started,
                    finished_at=datetime.utcnow(),
                    status="success",
                    fetched_count=fetched,
                    inserted_count=inserted_one,
                    updated_count=updated_one,
                    message=f"{source_name} synced",
                )
            )
            total_fetched += fetched
            inserted += inserted_one
            updated += updated_one
            source_results.append({"source_name": source_name, "status": "success", "fetched_count": fetched})
        except Exception as exc:
            db.add(
                StockUniverseSyncLog(
                    source_name=source_name,
                    source_url=url,
                    started_at=src_started,
                    finished_at=datetime.utcnow(),
                    status="error",
                    fetched_count=0,
                    inserted_count=0,
                    updated_count=0,
                    message=str(exc),
                )
            )
            source_results.append({"source_name": source_name, "status": "error", "message": str(exc)})
    db.add(
        CrawlerLog(
            task_type="stock_universe_sync",
            start_time=started,
            end_time=datetime.utcnow(),
            status="success",
            message="stock universe sync finished",
            fetched_count=total_fetched,
        )
    )
    db.commit()
    return {"fetched_count": total_fetched, "inserted_count": inserted, "updated_count": updated, "sources": source_results}
