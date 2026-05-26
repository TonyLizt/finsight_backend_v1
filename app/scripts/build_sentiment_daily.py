from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.all_models import NewsData, PriceData, SentimentDaily


def sentiment_label_from_score(score: float | None) -> str:
    if score is None:
        return "neutral"
    if score >= 0.15:
        return "positive"
    if score <= -0.15:
        return "negative"
    return "neutral"


def build_for_ticker(ticker: str, window_days: int) -> dict:
    ticker = ticker.upper()

    db = SessionLocal()
    try:
        trading_dates = [
            r[0]
            for r in (
                db.query(PriceData.trading_date)
                .filter(PriceData.ticker == ticker)
                .order_by(PriceData.trading_date.asc())
                .all()
            )
        ]

        if not trading_dates:
            raise RuntimeError(f"No price_data trading dates found for ticker={ticker}")

        inserted = 0
        updated = 0

        for trading_date in trading_dates:
            window_start_dt = datetime.combine(
                trading_date - timedelta(days=window_days),
                time(0, 0, 0),
            )
            window_end_dt = datetime.combine(trading_date, time(23, 59, 59))

            # 用 assigned_trading_date 防止盘后新闻泄漏到当天。
            # import_news 时已经把盘后新闻分配到下一交易日。
            news_rows = (
                db.query(NewsData)
                .filter(
                    NewsData.ticker == ticker,
                    NewsData.assigned_trading_date.isnot(None),
                    NewsData.assigned_trading_date >= trading_date - timedelta(days=window_days),
                    NewsData.assigned_trading_date <= trading_date,
                )
                .all()
            )

            scores = [
                float(n.sentiment_score)
                for n in news_rows
                if n.sentiment_score is not None
            ]

            news_count = len(news_rows)
            positive_count = sum(1 for n in news_rows if n.sentiment_label == "positive")
            negative_count = sum(1 for n in news_rows if n.sentiment_label == "negative")
            neutral_count = sum(1 for n in news_rows if n.sentiment_label == "neutral")

            sentiment_score = sum(scores) / len(scores) if scores else 0.0
            sentiment_label = sentiment_label_from_score(sentiment_score)

            news_start_time = min((n.publish_time for n in news_rows if n.publish_time), default=None)
            news_end_time = max((n.publish_time for n in news_rows if n.publish_time), default=None)

            existing = (
                db.query(SentimentDaily)
                .filter(
                    SentimentDaily.ticker == ticker,
                    SentimentDaily.trading_date == trading_date,
                )
                .first()
            )

            if existing:
                existing.news_start_time = news_start_time or window_start_dt
                existing.news_end_time = news_end_time or window_end_dt
                existing.news_count = news_count
                existing.positive_news_count = positive_count
                existing.negative_news_count = negative_count
                existing.neutral_news_count = neutral_count
                existing.sentiment_score = sentiment_score
                existing.sentiment_label = sentiment_label
                updated += 1
            else:
                db.add(
                    SentimentDaily(
                        ticker=ticker,
                        trading_date=trading_date,
                        news_start_time=news_start_time or window_start_dt,
                        news_end_time=news_end_time or window_end_dt,
                        news_count=news_count,
                        positive_news_count=positive_count,
                        negative_news_count=negative_count,
                        neutral_news_count=neutral_count,
                        sentiment_score=sentiment_score,
                        sentiment_label=sentiment_label,
                    )
                )
                inserted += 1

        db.commit()

        return {
            "ticker": ticker,
            "window_days": window_days,
            "trading_dates": len(trading_dates),
            "inserted": inserted,
            "updated": updated,
        }

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--window-days", type=int, default=14)
    args = parser.parse_args()

    init_db()

    result = build_for_ticker(args.ticker, args.window_days)
    print(result)


if __name__ == "__main__":
    main()
