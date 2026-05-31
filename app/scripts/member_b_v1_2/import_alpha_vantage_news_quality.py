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


def normalize_label(label: str | None) -> str:
    if not label:
        return "neutral"

    x = label.lower().replace("_", "-")

    if "bullish" in x:
        return "positive"
    if "bearish" in x:
        return "negative"
    return "neutral"


def find_quality_ticker_sentiment(
    item: dict,
    ticker: str,
    min_relevance: float,
) -> tuple[float | None, str | None, float | None, bool]:
    """只接受 ticker_sentiment 中明确包含当前 ticker 且 relevance 足够高的新闻。

    返回：
    score, label, relevance, accepted
    """
    ticker = ticker.upper()

    for ts in item.get("ticker_sentiment", []) or []:
        ts_ticker = str(ts.get("ticker", "")).upper()
        if ts_ticker != ticker:
            continue

        relevance_raw = ts.get("relevance_score")
        try:
            relevance = float(relevance_raw)
        except Exception:
            return None, None, None, False

        if relevance < min_relevance:
            return None, None, relevance, False

        score = ts.get("ticker_sentiment_score")
        label = ts.get("ticker_sentiment_label")

        try:
            score = float(score) if score is not None else None
        except Exception:
            score = None

        return score, label, relevance, True

    return None, None, None, False


def assign_trading_date(db: Session, ticker: str, publish_dt: datetime) -> date | None:
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


def import_file(db: Session, ticker: str, path: Path, min_relevance: float) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    feed = data.get("feed", [])

    inserted = 0
    updated = 0
    skipped_missing_core = 0
    skipped_low_relevance = 0
    skipped_bad_time = 0

    for item in feed:
        title = item.get("title")
        url = item.get("url")
        time_published = item.get("time_published")
        summary = item.get("summary")

        if not title or not url or not time_published:
            skipped_missing_core += 1
            continue

        try:
            publish_dt = parse_alpha_time(time_published)
        except Exception:
            skipped_bad_time += 1
            continue

        sentiment_score, raw_label, relevance, accepted = find_quality_ticker_sentiment(
            item=item,
            ticker=ticker,
            min_relevance=min_relevance,
        )

        if not accepted:
            skipped_low_relevance += 1
            continue

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

        content_status = f"alpha_quality_relevance_ge_{str(min_relevance).replace('.', '_')}"

        if existing:
            existing.publish_time = publish_dt
            existing.assigned_trading_date = assigned_date
            existing.title = title
            existing.summary = summary
            existing.content_text = summary
            existing.content_html = None
            existing.source = item.get("source") or item.get("source_domain")
            existing.sentiment_score = sentiment_score
            existing.sentiment_label = sentiment_label
            existing.content_status = content_status
            updated += 1
        else:
            row = NewsData(
                ticker=ticker,
                publish_time=publish_dt,
                assigned_trading_date=assigned_date,
                title=title,
                summary=summary,
                content_text=summary,
                content_html=None,
                source=item.get("source") or item.get("source_domain"),
                url=url,
                sentiment_score=sentiment_score,
                sentiment_label=sentiment_label,
                news_llm_analysis=None,
                content_status=content_status,
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
        "skipped_missing_core": skipped_missing_core,
        "skipped_low_relevance": skipped_low_relevance,
        "skipped_bad_time": skipped_bad_time,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--raw-dir",
        default="/data/hmt/datasets/finsight/news/raw/alpha_vantage",
    )
    parser.add_argument("--min-relevance", type=float, default=0.5)
    args = parser.parse_args()

    ticker = args.ticker.upper()
    ticker_dir = Path(args.raw_dir) / ticker

    if not ticker_dir.exists():
        raise FileNotFoundError(f"ticker raw dir not found: {ticker_dir}")

    files = sorted(ticker_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no json files found in: {ticker_dir}")

    init_db()

    db = SessionLocal()
    try:
        total = {
            "ticker": ticker,
            "files": len(files),
            "inserted": 0,
            "updated": 0,
            "skipped_missing_core": 0,
            "skipped_low_relevance": 0,
            "skipped_bad_time": 0,
        }

        for path in files:
            result = import_file(
                db=db,
                ticker=ticker,
                path=path,
                min_relevance=args.min_relevance,
            )
            print(result)

            for key in [
                "inserted",
                "updated",
                "skipped_missing_core",
                "skipped_low_relevance",
                "skipped_bad_time",
            ]:
                total[key] += result[key]

        print({"done": True, "min_relevance": args.min_relevance, **total})

    finally:
        db.close()


if __name__ == "__main__":
    main()
