"""AKShare intraday hourly market data service.

用途：
- 给 ``GET /api/stocks/{ticker}/detail?range=1d`` 返回美股 1 日小时级走势；
- 使用 AKShare 的 ``stock_us_hist_min_em`` 获取最近 5 个交易日美股分钟数据；
- 在服务内按美股交易日和小时聚合为 hourly bars；
- 抓取失败时返回明确状态，不伪造小时数据。

说明：
AKShare 美股分时接口的时间字段通常是北京时间，例如美股 09:30 ET
会显示为北京时间 21:30。这里会转换到 America/New_York 后再按美股交易日过滤。
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

SOURCE_TZ = ZoneInfo("Asia/Shanghai")
MARKET_TZ = ZoneInfo("America/New_York")


class AkshareIntradayError(RuntimeError):
    pass


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return int(float(value))
    except Exception:
        return None


def _candidate_ak_symbols(ticker: str) -> list[str]:
    """生成 AKShare 美股分时接口的候选 symbol。"""
    ticker = ticker.upper().strip()
    env_prefixes = os.getenv("AKSHARE_US_SYMBOL_PREFIXES", "105,106,107")
    prefixes = [p.strip() for p in env_prefixes.split(",") if p.strip()]
    candidates = [ticker]
    candidates.extend(f"{prefix}.{ticker}" for prefix in prefixes)
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _lookup_ak_symbol_from_spot(ticker: str) -> str | None:
    """通过 stock_us_spot_em 尝试把 AAPL 映射为 105.AAPL 这类代码。"""
    if os.getenv("AKSHARE_ENABLE_SPOT_SYMBOL_LOOKUP", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return None

    try:
        import akshare as ak  # type: ignore
    except Exception:
        return None

    ticker = ticker.upper().strip()
    try:
        df = ak.stock_us_spot_em()
    except Exception:
        return None

    if df is None or df.empty or "代码" not in df.columns:
        return None

    for code in df["代码"].astype(str).tolist():
        if code.upper().endswith(f".{ticker}") or code.upper() == ticker:
            return code
    return None


def _fetch_akshare_minute_df(ticker: str) -> tuple[pd.DataFrame, str, list[str]]:
    """尝试多个 AKShare symbol，返回非空分钟数据。"""
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        raise AkshareIntradayError("akshare is not installed. Install it with: pip install akshare") from exc

    errors: list[str] = []
    candidates = _candidate_ak_symbols(ticker)
    mapped = _lookup_ak_symbol_from_spot(ticker)
    if mapped and mapped not in candidates:
        candidates.insert(0, mapped)

    for symbol in candidates:
        try:
            df = ak.stock_us_hist_min_em(symbol=symbol)
            if df is not None and not df.empty:
                return df, symbol, errors
            errors.append(f"{symbol}: empty")
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")

    raise AkshareIntradayError("; ".join(errors) if errors else "AKShare returned no data")


def _normalize_minute_df(df: pd.DataFrame) -> pd.DataFrame:
    """把 AKShare 中文列名分钟数据标准化。"""
    required = {"时间", "开盘", "收盘", "最高", "最低", "成交量"}
    missing = required - set(df.columns)
    if missing:
        raise AkshareIntradayError(f"AKShare minute dataframe missing columns: {sorted(missing)}")

    out = df.copy()
    out["source_datetime"] = pd.to_datetime(out["时间"], errors="coerce")
    out = out.dropna(subset=["source_datetime"])
    if out.empty:
        raise AkshareIntradayError("AKShare minute dataframe has no valid 时间")

    out["source_datetime"] = out["source_datetime"].dt.tz_localize(SOURCE_TZ, nonexistent="shift_forward", ambiguous="NaT")
    out["market_datetime"] = out["source_datetime"].dt.tz_convert(MARKET_TZ)
    out["market_date"] = out["market_datetime"].dt.date

    for col in ["开盘", "收盘", "最高", "最低", "成交量", "成交额", "最新价"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "开盘" in out.columns and "收盘" in out.columns:
        out.loc[(out["开盘"].isna()) | (out["开盘"] <= 0), "开盘"] = out["收盘"]
    return out


def _aggregate_hourly(df: pd.DataFrame, target_date: date | None) -> tuple[pd.DataFrame, date | None]:
    if target_date is None:
        target_date = max(df["market_date"].tolist())

    day_df = df[df["market_date"] == target_date].copy()
    if day_df.empty:
        return day_df, target_date

    day_df = day_df.set_index("market_datetime").sort_index()

    agg_spec: dict[str, str] = {
        "开盘": "first",
        "最高": "max",
        "最低": "min",
        "收盘": "last",
        "成交量": "sum",
    }
    if "成交额" in day_df.columns:
        agg_spec["成交额"] = "sum"
    if "最新价" in day_df.columns:
        agg_spec["最新价"] = "last"

    agg = day_df.resample("60min", label="left", closed="left").agg(agg_spec)
    agg = agg.dropna(subset=["收盘"])
    return agg, target_date


def get_hourly_intraday_curve(ticker: str, target_date: date | None = None) -> dict[str, Any]:
    """返回美股某日小时级走势。

    target_date 为美股市场日期；不传时取 AKShare 返回数据中的最新美股交易日。
    """
    ticker = ticker.upper().strip()
    try:
        raw_df, ak_symbol, candidate_errors = _fetch_akshare_minute_df(ticker)
        minute_df = _normalize_minute_df(raw_df)
        hourly_df, actual_date = _aggregate_hourly(minute_df, target_date)

        if hourly_df.empty:
            return {
                "status": "empty",
                "source": "akshare_stock_us_hist_min_em",
                "data_frequency": "hourly",
                "ticker": ticker,
                "ak_symbol": ak_symbol,
                "target_date": target_date.isoformat() if target_date else None,
                "actual_date": actual_date.isoformat() if actual_date else None,
                "items": [],
                "message": "AKShare returned minute data, but no bars for target market date",
                "candidate_errors": candidate_errors,
            }

        items: list[dict[str, Any]] = []
        for idx, row in hourly_df.iterrows():
            market_dt = idx.to_pydatetime()
            items.append(
                {
                    "timestamp": market_dt.isoformat(),
                    "date": market_dt.date().isoformat(),
                    "time": market_dt.strftime("%H:%M"),
                    "open": _to_float(row.get("开盘")),
                    "high": _to_float(row.get("最高")),
                    "low": _to_float(row.get("最低")),
                    "close": _to_float(row.get("收盘")),
                    "volume": _to_int(row.get("成交量")),
                    "amount": _to_float(row.get("成交额")) if "成交额" in hourly_df.columns else None,
                    "latest_price": _to_float(row.get("最新价")) if "最新价" in hourly_df.columns else None,
                    "data_frequency": "hourly",
                    "source": "akshare_stock_us_hist_min_em",
                }
            )

        return {
            "status": "success",
            "source": "akshare_stock_us_hist_min_em",
            "data_frequency": "hourly",
            "ticker": ticker,
            "ak_symbol": ak_symbol,
            "target_date": target_date.isoformat() if target_date else None,
            "actual_date": actual_date.isoformat() if actual_date else None,
            "items": items,
            "message": "ok",
            "candidate_errors": candidate_errors,
        }

    except Exception as exc:
        return {
            "status": "failed",
            "source": "akshare_stock_us_hist_min_em",
            "data_frequency": "hourly",
            "ticker": ticker,
            "target_date": target_date.isoformat() if target_date else None,
            "actual_date": None,
            "items": [],
            "message": "AKShare hourly intraday fetch failed",
            "error": str(exc),
        }
