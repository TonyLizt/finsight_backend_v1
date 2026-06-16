"""根据 price_data 计算 technical_indicators。

输入：price_data
输出：technical_indicators

第一版指标：
return_1d, return_3d, return_5d,
ma5, ma20, ma60,
ma5_gap, ma20_gap, ma60_gap,
rsi, macd,
volatility_20d, drawdown_20d, volume_zscore
"""

from __future__ import annotations

import argparse
from datetime import datetime

import pandas as pd

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.all_models import PriceData, TechnicalIndicator


def calc_rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def build_for_ticker(db, ticker: str) -> dict:
    rows = (
        db.query(PriceData)
        .filter(PriceData.ticker == ticker)
        .order_by(PriceData.trading_date.asc())
        .all()
    )

    if not rows:
        return {"ticker": ticker, "status": "no_price_data"}

    df = pd.DataFrame(
        [
            {
                "trading_date": r.trading_date,
                "close": float(r.close) if r.close is not None else None,
                "volume": int(r.volume) if r.volume is not None else None,
            }
            for r in rows
        ]
    )

    df = df.dropna(subset=["close"]).sort_values("trading_date").reset_index(drop=True)

    if df.empty:
        return {"ticker": ticker, "status": "empty_after_dropna"}

    close = df["close"]
    volume = df["volume"].fillna(0)

    df["return_1d"] = close.pct_change(1)
    df["return_3d"] = close.pct_change(3)
    df["return_5d"] = close.pct_change(5)

    df["ma5"] = close.rolling(5, min_periods=5).mean()
    df["ma20"] = close.rolling(20, min_periods=20).mean()
    df["ma60"] = close.rolling(60, min_periods=60).mean()

    df["ma5_gap"] = (close - df["ma5"]) / df["ma5"]
    df["ma20_gap"] = (close - df["ma20"]) / df["ma20"]
    df["ma60_gap"] = (close - df["ma60"]) / df["ma60"]

    df["rsi"] = calc_rsi(close)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26

    df["volatility_20d"] = df["return_1d"].rolling(20, min_periods=20).std()

    rolling_max_20 = close.rolling(20, min_periods=20).max()
    df["drawdown_20d"] = (close - rolling_max_20) / rolling_max_20

    volume_ma20 = volume.rolling(20, min_periods=20).mean()
    volume_std20 = volume.rolling(20, min_periods=20).std()
    df["volume_zscore"] = (volume - volume_ma20) / volume_std20.replace(0, pd.NA)

    inserted = 0
    updated = 0
    skipped = 0

    for _, row in df.iterrows():
        # MA60 不足时跳过，避免训练集早期样本技术指标缺失过多
        if pd.isna(row["ma60"]):
            skipped += 1
            continue

        existing = (
            db.query(TechnicalIndicator)
            .filter(
                TechnicalIndicator.ticker == ticker,
                TechnicalIndicator.trading_date == row["trading_date"],
            )
            .first()
        )

        values = {
            "return_1d": None if pd.isna(row["return_1d"]) else float(row["return_1d"]),
            "return_3d": None if pd.isna(row["return_3d"]) else float(row["return_3d"]),
            "return_5d": None if pd.isna(row["return_5d"]) else float(row["return_5d"]),
            "ma5": None if pd.isna(row["ma5"]) else float(row["ma5"]),
            "ma20": None if pd.isna(row["ma20"]) else float(row["ma20"]),
            "ma60": None if pd.isna(row["ma60"]) else float(row["ma60"]),
            "ma5_gap": None if pd.isna(row["ma5_gap"]) else float(row["ma5_gap"]),
            "ma20_gap": None if pd.isna(row["ma20_gap"]) else float(row["ma20_gap"]),
            "ma60_gap": None if pd.isna(row["ma60_gap"]) else float(row["ma60_gap"]),
            "rsi": None if pd.isna(row["rsi"]) else float(row["rsi"]),
            "macd": None if pd.isna(row["macd"]) else float(row["macd"]),
            "volatility_20d": None if pd.isna(row["volatility_20d"]) else float(row["volatility_20d"]),
            "drawdown_20d": None if pd.isna(row["drawdown_20d"]) else float(row["drawdown_20d"]),
            "volume_zscore": None if pd.isna(row["volume_zscore"]) else float(row["volume_zscore"]),
        }

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
            updated += 1
        else:
            db.add(
                TechnicalIndicator(
                    ticker=ticker,
                    trading_date=row["trading_date"],
                    **values,
                )
            )
            inserted += 1

    db.commit()

    return {
        "ticker": ticker,
        "price_rows": len(df),
        "inserted": inserted,
        "updated": updated,
        "skipped_early_rows": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", required=True)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()

    try:
        for ticker in args.tickers:
            result = build_for_ticker(db, ticker.upper())
            print(result)
        print({"done": True})
    finally:
        db.close()


if __name__ == "__main__":
    main()
