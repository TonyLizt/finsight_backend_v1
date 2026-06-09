from __future__ import annotations

import argparse
import json
from datetime import datetime, date, time
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.all_models import NewsData, PriceData


def parse_alpha_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%S")


def find_ticker_sentiment(item: dict, ticker: str) -> tuple[float | None, str | None]:
    ticker = ticker.upper()

    for ts in item.get("ticker_sentiment", []) or []:
        if str(ts.get("ticker", "")).upper() == ticker:
            score = ts.get("ticker_sentiment_score")
            label = ts.get("ticker_sentiment_label")
            try:
                score = float(score) if score is not None else None
            except Exception:
                score = None
            return score, label

    # 兜底：如果 ticker_sentiment 里没有该 ticker，就用 overall sentiment
    score = item.get("overall_sentiment_score")
    label = item.get("overall_sentiment_label")
    try:
        score = float(score) if score is not None else None
    except Exception:
        score = None
    return score, label


def normalize_label(label: str | None) -> str:
    if not label:
        return "neutral"

    x = label.lower().replace("_", "-")

    if "bullish" in x:
        return "positive"
    if "bearish" in x:
        return "negative"
    return "neutral"


def assign_trading_date(db: Session, ticker: str, publish_dt: datetime) -> date | None:
    """把新闻时间对齐到交易日。

    规则：
    - 如果是交易日且发布时间不晚于 16:00，归到当天；
    - 如果是非交易日或 16:00 后发布，归到下一个有 price_data 的交易日；
    - 如果找不到后续交易日，则退回最近一个已有交易日。
    """
    ticker = ticker.upper()
    publish_day = publish_dt.date()

    if publish_dt.time() <= time(16, 0):
        same_day = (
            db.query(PriceData.trading_date)
            .filter(
                PriceData.ticker == ticker,
                PriceData.trading_date == publish_day,
            )
            .first()
        )
        if same_day:
            return same_day[0]

    next_day = (
        db.query(PriceData.trading_date)
        .filter(
            PriceData.ticker == ticker,
            PriceData.trading_date > publish_day,
        )
        .order_by(PriceData.trading_date.asc())
        .first()
    )
    if next_day:
        return next_day[0]

    prev_day = (
        db.query(PriceData.trading_date)
        .filter(
            PriceData.ticker == ticker,
            PriceData.trading_date <= publish_day,
        )
        .order_by(PriceData.trading_date.desc())
        .first()
    )
    return prev_day[0] if prev_day else None


def import_file(db: Session, ticker: str, path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    feed = data.get("feed", [])

    inserted = 0
    updated = 0
    skipped = 0

    for item in feed:
        title = item.get("title")
        url = item.get("url")
        time_published = item.get("time_published")

        if not title or not url or not time_published:
            skipped += 1
            continue

        try:
            publish_dt = parse_alpha_time(time_published)
        except Exception:
            skipped += 1
            continue

        sentiment_score, raw_label = find_ticker_sentiment(item, ticker)
        sentiment_label = normalize_label(raw_label)
        assigned_date = assign_trading_date(db, ticker, publish_dt)

        existing = (
            db.query(NewsData)
            .filter(
                NewsData.ticker == ticker,
                NewsData.url == url,
            )
            .first()
        )

        if existing:
            existing.publish_time = publish_dt
            existing.assigned_trading_date = assigned_date
            existing.title = title
            existing.summary = item.get("summary")
            existing.content_text = None
            existing.source = item.get("source") or item.get("source_domain")
            existing.sentiment_score = sentiment_score
            existing.sentiment_label = sentiment_label
            existing.content_status = "summary_only"
            updated += 1
        else:
            row = NewsData(
                ticker=ticker,
                publish_time=publish_dt,
                assigned_trading_date=assigned_date,
                title=title,
                summary=item.get("summary"),
                content_text=None,
                content_html=None,
                source=item.get("source") or item.get("source_domain"),
                url=url,
                sentiment_score=sentiment_score,
                sentiment_label=sentiment_label,
                news_llm_analysis=None,
                content_status="summary_only",
                content_fetched_at=None,
            )
            db.add(row)
            inserted += 1

    db.commit()

    return {
        "file": str(path),
        "feed_count": len(feed),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--raw-dir",
        default="/data/hmt/datasets/finsight/news/raw/alpha_vantage",
    )
    args = parser.parse_args()

    ticker = args.ticker.upper()
    ticker_dir = Path(args.raw_dir) / ticker

    if not ticker_dir.exists():
        raise FileNotFoundError(f"ticker raw dir not found: {ticker_dir}")

    init_db()

    files = sorted(ticker_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no json files found in: {ticker_dir}")

    db = SessionLocal()
    try:
        total = {
            "ticker": ticker,
            "files": len(files),
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }

        for path in files:
            result = import_file(db, ticker, path)
            print(result)

            total["inserted"] += result["inserted"]
            total["updated"] += result["updated"]
            total["skipped"] += result["skipped"]

        print({"done": True, **total})

    finally:
        db.close()


if __name__ == "__main__":
    main()
