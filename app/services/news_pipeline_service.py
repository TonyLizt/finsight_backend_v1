"""新闻情绪补全服务占位封装。

B 同学已经提供了以下脚本：
- fetch_alpha_vantage_news.py
- fetch_alpha_vantage_news_range.py
- import_alpha_vantage_news.py
- build_sentiment_daily.py

这些脚本的字段处理已经和 news_data / sentiment_daily 对齐。当前服务先提供统一入口，
后续可把脚本中的 fetch/import/build 逻辑进一步拆成可直接调用的函数。

第一版不在预测接口中默认强制抓新闻，避免 Alpha Vantage API key 和频率限制影响预测。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.scripts.build_sentiment_daily import build_for_ticker as build_sentiment_for_ticker


def ensure_news_sentiment(
    db: Session,
    ticker: str,
    start_date: date | None = None,
    end_date: date | None = None,
    window_days: int = 14,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """确保指定 ticker 的 sentiment_daily 至少可由现有 news_data 重建。

    当前实现只复用 B 同学 build_sentiment_daily.py 的聚合逻辑，不自动请求 Alpha Vantage。
    真正 fetch raw JSON 可继续通过 fetch_alpha_vantage_news_range.py 运行。
    """
    # build_sentiment_daily.build_for_ticker 内部自己创建 SessionLocal。
    # 为了字段/方法和 B 同学脚本对齐，这里暂时复用原函数。
    try:
        result = build_sentiment_for_ticker(ticker=ticker.upper(), window_days=window_days)
        result["status"] = "rebuilt_from_existing_news"
        return result
    except Exception as exc:
        return {
            "ticker": ticker.upper(),
            "status": "failed",
            "error": str(exc),
            "window_days": window_days,
        }
