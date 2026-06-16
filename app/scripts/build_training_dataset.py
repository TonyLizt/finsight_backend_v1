"""构造 Finsight 第一版训练数据集。

当前版本：
1. 使用 price_data + technical_indicators；
2. 新闻情绪特征从 sentiment_daily 读取，缺失时填 0；
3. 每条样本 = ticker + base_trading_date；
4. 分类标签：未来 forecast_days 收益率 >= 2% 为 up，<= -2% 为 down，否则 neutral；
5. 回归标签：target_return_d1 到 target_return_d5；
6. 严格保证 target_date <= 2025-05-20，避免污染回测期。
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app.db.session import SessionLocal
from app.models.all_models import PriceData, TechnicalIndicator, SentimentDaily


TRAIN_END_DATE = date(2025, 5, 20)
FORECAST_DAYS = 5
UP_THRESHOLD = 0.02
DOWN_THRESHOLD = -0.02

FEATURE_COLUMNS = [
    "close",
    "open",
    "high",
    "low",
    "volume",
    "daily_return",
    "change_percent",
    "amplitude",
    "return_1d",
    "return_3d",
    "return_5d",
    "ma5",
    "ma20",
    "ma60",
    "ma5_gap",
    "ma20_gap",
    "ma60_gap",
    "rsi",
    "macd",
    "volatility_20d",
    "drawdown_20d",
    "volume_zscore",
    "news_count",
    "positive_news_count",
    "negative_news_count",
    "neutral_news_count",
    "sentiment_score",
    "sentiment_score_3d_avg",
    "sentiment_score_7d_avg",
    "positive_ratio",
    "negative_ratio",
]


def label_from_return(future_return: float) -> str:
    if future_return >= UP_THRESHOLD:
        return "up"
    if future_return <= DOWN_THRESHOLD:
        return "down"
    return "neutral"


def read_sentiment_features(db, ticker: str, trading_date: date) -> dict:
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


def build_for_ticker(db, ticker: str) -> list[dict]:
    price_rows = (
        db.query(PriceData)
        .filter(PriceData.ticker == ticker)
        .order_by(PriceData.trading_date.asc())
        .all()
    )

    indicator_rows = (
        db.query(TechnicalIndicator)
        .filter(TechnicalIndicator.ticker == ticker)
        .order_by(TechnicalIndicator.trading_date.asc())
        .all()
    )

    indicator_map = {x.trading_date: x for x in indicator_rows}

    prices = []
    for r in price_rows:
        if r.close is None:
            continue
        prices.append({
            "trading_date": r.trading_date,
            "open": float(r.open) if r.open is not None else None,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close) if r.close is not None else None,
            "volume": int(r.volume) if r.volume is not None else 0,
            "daily_return": r.daily_return,
            "change_percent": r.change_percent,
            "amplitude": r.amplitude,
        })

    samples = []

    for i in range(len(prices)):
        base = prices[i]
        base_date = base["trading_date"]

        if base_date > TRAIN_END_DATE:
            continue

        # 确保未来 5 个交易日标签都存在，并且 target_date 不超过训练截止日
        if i + FORECAST_DAYS >= len(prices):
            continue

        max_target_date = prices[i + FORECAST_DAYS]["trading_date"]
        if max_target_date > TRAIN_END_DATE:
            continue

        indicator = indicator_map.get(base_date)
        if indicator is None:
            continue

        current_close = base["close"]
        if current_close is None or current_close == 0:
            continue

        row = {
            "ticker": ticker,
            "base_trading_date": base_date.isoformat(),
            "forecast_days": FORECAST_DAYS,
            "target_date_d5": max_target_date.isoformat(),
            "open": base["open"],
            "high": base["high"],
            "low": base["low"],
            "close": base["close"],
            "volume": base["volume"],
            "daily_return": base["daily_return"],
            "change_percent": base["change_percent"],
            "amplitude": base["amplitude"],
            "return_1d": indicator.return_1d,
            "return_3d": indicator.return_3d,
            "return_5d": indicator.return_5d,
            "ma5": indicator.ma5,
            "ma20": indicator.ma20,
            "ma60": indicator.ma60,
            "ma5_gap": indicator.ma5_gap,
            "ma20_gap": indicator.ma20_gap,
            "ma60_gap": indicator.ma60_gap,
            "rsi": indicator.rsi,
            "macd": indicator.macd,
            "volatility_20d": indicator.volatility_20d,
            "drawdown_20d": indicator.drawdown_20d,
            "volume_zscore": indicator.volume_zscore,

            # 新闻情绪特征：来自 sentiment_daily；缺失时自动填 0
            **read_sentiment_features(db, ticker, base_date),
        }

        # 回归标签：未来 1~5 个交易日相对当前价格收益率
        for d in range(1, FORECAST_DAYS + 1):
            target = prices[i + d]
            target_return = (target["close"] - current_close) / current_close
            row[f"target_return_d{d}"] = target_return
            row[f"target_date_d{d}"] = target["trading_date"].isoformat()

        future_return_h = row[f"target_return_d{FORECAST_DAYS}"]
        row["future_return_h"] = future_return_h
        row["label"] = label_from_return(future_return_h)

        # 缺失特征跳过，避免训练阶段再爆错
        if any(row.get(col) is None for col in FEATURE_COLUMNS):
            continue

        samples.append(row)

    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--output-dir", default="data/training")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    all_samples = []

    try:
        for ticker in args.tickers:
            ticker = ticker.upper()
            samples = build_for_ticker(db, ticker)
            all_samples.extend(samples)
            print({"ticker": ticker, "samples": len(samples)})

    finally:
        db.close()

    df = pd.DataFrame(all_samples)
    dataset_path = out_dir / "dataset_h5_v1.csv"
    feature_path = out_dir / "feature_columns_h5_v1.json"
    label_config_path = out_dir / "label_config_h5_v1.json"
    summary_path = out_dir / "dataset_summary_h5_v1.json"

    df.to_csv(dataset_path, index=False)

    feature_path.write_text(
        json.dumps(FEATURE_COLUMNS, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    label_config = {
        "forecast_days": FORECAST_DAYS,
        "up_threshold": UP_THRESHOLD,
        "down_threshold": DOWN_THRESHOLD,
        "label_mapping": {
            "down": 0,
            "neutral": 1,
            "up": 2,
        },
    }
    label_config_path.write_text(
        json.dumps(label_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "sample_count": int(len(df)),
        "ticker_count": int(df["ticker"].nunique()) if not df.empty else 0,
        "start_date": str(df["base_trading_date"].min()) if not df.empty else None,
        "end_date": str(df["base_trading_date"].max()) if not df.empty else None,
        "forecast_days": FORECAST_DAYS,
        "train_end_date": TRAIN_END_DATE.isoformat(),
        "label_distribution": df["label"].value_counts().to_dict() if not df.empty else {},
        "feature_count": len(FEATURE_COLUMNS),
        "feature_columns_file": str(feature_path),
        "note": "news sentiment features are loaded from sentiment_daily when available; missing values are filled with 0.",
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print({
        "dataset": str(dataset_path),
        "features": str(feature_path),
        "label_config": str(label_config_path),
        "summary": str(summary_path),
        "samples": len(df),
    })


if __name__ == "__main__":
    main()
