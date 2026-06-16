#!/usr/bin/env python3
"""Smart S&P 500 daily price backfill for Finsight.

Run it inside the backend Docker container with PYTHONPATH=. .
It reuses the existing backend ingestion functions:
- app.services.twelvedata_market_service.fetch_daily_records
- app.services.twelvedata_market_service.upsert_daily_records

Target table: price_data
Data granularity: 1day
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.twelvedata_market_service import fetch_daily_records, upsert_daily_records

DEFAULT_SP500_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
)

# Some data providers may use slightly different symbols for class shares.
# The script tries the canonical ticker first, then fallback variants.
SYMBOL_VARIANTS: dict[str, list[str]] = {
    "BRK.B": ["BRK.B", "BRK-B", "BRK/B"],
    "BF.B": ["BF.B", "BF-B", "BF/B"],
}

SUCCESS_STATUSES = {"updated", "cached", "empty"}
CORE7_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]



@dataclass
class TickerResult:
    ticker: str
    status: str
    start_date: str
    end_date: str
    db_min_before: str | None = None
    db_max_before: str | None = None
    db_rows_before: int = 0
    db_min_after: str | None = None
    db_max_after: str | None = None
    db_rows_after: int = 0
    source_symbol: str | None = None
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    repair_result: dict[str, Any] | None = None
    indicator_result: dict[str, Any] | None = None
    error: str | None = None
    elapsed_sec: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Docker-friendly smart S&P 500 daily OHLCV backfill into Finsight price_data using Twelve Data."
    )
    parser.add_argument(
        "--start-date",
        default=os.getenv("START_DATE", "auto"),
        help="YYYY-MM-DD or auto. auto = MIN(price_data.trading_date), fallback 2023-01-01.",
    )
    parser.add_argument(
        "--fallback-start-date",
        default=os.getenv("FALLBACK_START_DATE", "2023-01-01"),
        help="Used when --start-date=auto and price_data is empty. Default: 2023-01-01.",
    )
    parser.add_argument(
        "--end-date",
        default=os.getenv("END_DATE", date.today().isoformat()),
        help="YYYY-MM-DD. Default: today.",
    )
    parser.add_argument(
        "--tickers",
        default=os.getenv("TICKERS", ""),
        help="Comma-separated tickers. If empty, downloads S&P 500 constituents CSV.",
    )
    parser.add_argument(
        "--tickers-file",
        default=os.getenv("TICKERS_FILE", ""),
        help="Optional text/CSV file. One ticker per line or comma-separated.",
    )
    parser.add_argument(
        "--exclude-tickers",
        default=os.getenv("EXCLUDE_TICKERS", ""),
        help="Comma-separated tickers to skip, e.g. AAPL,MSFT,NVDA. Default: none.",
    )
    parser.add_argument(
        "--exclude-core7",
        action="store_true",
        default=os.getenv("EXCLUDE_CORE7", "").lower() in {"1", "true", "yes"},
        help="Skip AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META entirely.",
    )
    parser.add_argument(
        "--incremental-by-ticker",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("INCREMENTAL_BY_TICKER", "1").lower() not in {"0", "false", "no"},
        help="If ticker already has data from the requested start, only fetch after its DB max date. Default: true.",
    )
    parser.add_argument(
        "--sp500-csv-url",
        default=os.getenv("SP500_CSV_URL", DEFAULT_SP500_CSV_URL),
        help="CSV URL with a Symbol column.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("LIMIT", "0") or "0"),
        help="Limit number of tickers. 0 = no limit.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=int(os.getenv("OFFSET", "0") or "0"),
        help="Skip first N tickers. Useful for batching.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=float(os.getenv("SLEEP_SECONDS", os.getenv("TWELVEDATA_REQUEST_SLEEP_SECONDS", "8"))),
        help="Seconds to sleep between successful ticker requests. Default: 8.",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=int(os.getenv("RETRY", "3")),
        help="Retries per ticker on API/network errors. Default: 3.",
    )
    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=float(os.getenv("RETRY_SLEEP_SECONDS", "20")),
        help="Base sleep seconds before retry; exponential backoff is applied. Default: 20.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=os.getenv("FORCE_REFRESH", "").lower() in {"1", "true", "yes"},
        help="Always fetch even if DB coverage already looks complete.",
    )
    parser.add_argument(
        "--skip-covered",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("SKIP_COVERED", "1").lower() not in {"0", "false", "no"},
        help="Skip ticker if DB min <= start_date and DB max >= end_date. Default: true.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("RESUME", "1").lower() not in {"0", "false", "no"},
        help="Skip completed tickers found in the log file. Default: true.",
    )
    parser.add_argument(
        "--log-file",
        default=os.getenv("LOG_FILE", "sp500_daily_backfill_log.jsonl"),
        help="JSONL log path. Default: sp500_daily_backfill_log.jsonl.",
    )
    parser.add_argument(
        "--summary-file",
        default=os.getenv("SUMMARY_FILE", "sp500_daily_backfill_summary.json"),
        help="Summary JSON path. Default: sp500_daily_backfill_summary.json.",
    )
    parser.add_argument(
        "--no-repair-derived",
        action="store_true",
        help="Do not run repair_price_derived_fields.repair_ticker after each ticker.",
    )
    parser.add_argument(
        "--build-indicators",
        action="store_true",
        default=os.getenv("BUILD_INDICATORS", "").lower() in {"1", "true", "yes"},
        help="Also rebuild technical_indicators for each ticker after price_data update.",
    )
    parser.add_argument(
        "--verify-api",
        action="store_true",
        default=os.getenv("VERIFY_API", "").lower() in {"1", "true", "yes"},
        help="After DB write, login and call /api/stocks/{ticker}/detail for verification.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("API_BASE_URL", "http://127.0.0.1:8002"),
        help="Used only with --verify-api. Default: http://127.0.0.1:8002.",
    )
    parser.add_argument(
        "--api-username",
        default=os.getenv("API_USERNAME", "admin"),
        help="Used only with --verify-api. Default: admin.",
    )
    parser.add_argument(
        "--api-password",
        default=os.getenv("API_PASSWORD", ""),
        help="Used only with --verify-api. Prefer API_PASSWORD env var.",
    )
    parser.add_argument(
        "--verify-ticker",
        default=os.getenv("VERIFY_TICKER", "AAPL"),
        help="Ticker to verify by API. Default: AAPL.",
    )
    return parser.parse_args()


def to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    return date.fromisoformat(text_value[:10])


def next_calendar_date(value: date) -> date:
    return value + timedelta(days=1)


def http_get_text(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": "Finsight-SP500-Daily-Backfill/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def normalize_ticker(raw: str) -> str:
    return (raw or "").strip().upper()


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        ticker = normalize_ticker(item)
        if ticker and ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def load_tickers_from_csv_url(url: str) -> list[str]:
    text_data = http_get_text(url)
    rows = csv.DictReader(text_data.splitlines())
    tickers: list[str] = []
    for row in rows:
        symbol = row.get("Symbol") or row.get("symbol") or row.get("ticker") or row.get("Ticker")
        if symbol:
            tickers.append(symbol)
    return dedupe(tickers)


def load_tickers_from_file(path: str) -> list[str]:
    text_data = Path(path).read_text(encoding="utf-8")
    raw_items: list[str] = []
    for line in text_data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        raw_items.extend(part.strip() for part in line.split(","))
    return dedupe(raw_items)


def load_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers.strip():
        tickers = dedupe(args.tickers.split(","))
    elif args.tickers_file.strip():
        tickers = load_tickers_from_file(args.tickers_file)
    else:
        tickers = load_tickers_from_csv_url(args.sp500_csv_url)

    excludes = set(dedupe(args.exclude_tickers.split(","))) if args.exclude_tickers.strip() else set()
    if args.exclude_core7:
        excludes.update(CORE7_TICKERS)
    if excludes:
        tickers = [ticker for ticker in tickers if ticker not in excludes]

    if args.offset > 0:
        tickers = tickers[args.offset :]
    if args.limit and args.limit > 0:
        tickers = tickers[: args.limit]
    return tickers


def get_global_start_date(db, fallback: date) -> date:
    value = db.execute(text("SELECT MIN(trading_date) FROM price_data")).scalar()
    return to_date(value) or fallback


def get_ticker_coverage(db, ticker: str) -> dict[str, Any]:
    row = db.execute(
        text(
            """
            SELECT MIN(trading_date) AS min_date,
                   MAX(trading_date) AS max_date,
                   COUNT(*) AS row_count
            FROM price_data
            WHERE ticker = :ticker
            """
        ),
        {"ticker": ticker.upper()},
    ).mappings().first()
    return {
        "min_date": to_date(row["min_date"]) if row else None,
        "max_date": to_date(row["max_date"]) if row else None,
        "row_count": int(row["row_count"] or 0) if row else 0,
    }


def coverage_complete(coverage: dict[str, Any], start_date: date, end_date: date) -> bool:
    min_date = coverage.get("min_date")
    max_date = coverage.get("max_date")
    rows = int(coverage.get("row_count") or 0)
    return bool(rows > 0 and min_date and max_date and min_date <= start_date and max_date >= end_date)


def load_completed_from_log(log_file: str, start_date: date, end_date: date) -> set[str]:
    path = Path(log_file)
    if not path.exists():
        return set()

    completed: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            item.get("start_date") == start_date.isoformat()
            and item.get("end_date") == end_date.isoformat()
            and item.get("status") in SUCCESS_STATUSES
            and item.get("ticker")
        ):
            completed.add(str(item["ticker"]).upper())
    return completed


def write_jsonl(path: str, payload: dict[str, Any]) -> None:
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def fetch_with_symbol_fallback(canonical_ticker: str, start_date: date, end_date: date) -> tuple[str, list[dict[str, Any]]]:
    variants = SYMBOL_VARIANTS.get(canonical_ticker.upper(), [canonical_ticker.upper()])
    last_error: Exception | None = None
    for source_symbol in variants:
        try:
            records = fetch_daily_records(source_symbol, start_date, end_date)
            # Upsert under canonical S&P ticker even if source symbol used a fallback format.
            for record in records:
                record["ticker"] = canonical_ticker.upper()
            return source_symbol, records
        except Exception as exc:  # try next symbol variant
            last_error = exc
    assert last_error is not None
    raise last_error


def try_repair_ticker(db, ticker: str) -> dict[str, Any] | None:
    try:
        from app.scripts.repair_price_derived_fields import repair_ticker

        return repair_ticker(db, ticker, dry_run=False)
    except Exception as exc:
        return {"ticker": ticker, "status": "failed", "error": f"repair failed: {exc}"}


def try_build_indicators(db, ticker: str) -> dict[str, Any] | None:
    try:
        from app.scripts.build_technical_indicators import build_for_ticker

        return build_for_ticker(db, ticker)
    except Exception as exc:
        return {"ticker": ticker, "status": "failed", "error": f"indicator build failed: {exc}"}


def process_ticker(db, ticker: str, args: argparse.Namespace, start_date: date, end_date: date) -> TickerResult:
    started = time.monotonic()
    before = get_ticker_coverage(db, ticker)

    fetch_start_date = start_date
    if (
        args.incremental_by_ticker
        and not args.force_refresh
        and before["row_count"] > 0
        and before["min_date"]
        and before["max_date"]
        and before["min_date"] <= start_date
    ):
        fetch_start_date = max(start_date, next_calendar_date(before["max_date"]))

    result = TickerResult(
        ticker=ticker,
        status="started",
        start_date=fetch_start_date.isoformat(),
        end_date=end_date.isoformat(),
        db_min_before=before["min_date"].isoformat() if before["min_date"] else None,
        db_max_before=before["max_date"].isoformat() if before["max_date"] else None,
        db_rows_before=before["row_count"],
    )

    if fetch_start_date > end_date or (
        args.skip_covered and not args.force_refresh and coverage_complete(before, start_date, end_date)
    ):
        after = before
        result.status = "cached"
        result.db_min_after = after["min_date"].isoformat() if after["min_date"] else None
        result.db_max_after = after["max_date"].isoformat() if after["max_date"] else None
        result.db_rows_after = after["row_count"]
        result.elapsed_sec = round(time.monotonic() - started, 3)
        return result

    last_error: Exception | None = None
    for attempt in range(1, args.retry + 1):
        try:
            source_symbol, records = fetch_with_symbol_fallback(ticker, fetch_start_date, end_date)
            inserted, updated = upsert_daily_records(db, ticker, records)

            result.source_symbol = source_symbol
            result.fetched_count = len(records)
            result.inserted_count = inserted
            result.updated_count = updated
            result.status = "updated" if records else "empty"

            if not args.no_repair_derived:
                result.repair_result = try_repair_ticker(db, ticker)

            if args.build_indicators:
                result.indicator_result = try_build_indicators(db, ticker)

            after = get_ticker_coverage(db, ticker)
            result.db_min_after = after["min_date"].isoformat() if after["min_date"] else None
            result.db_max_after = after["max_date"].isoformat() if after["max_date"] else None
            result.db_rows_after = after["row_count"]
            result.elapsed_sec = round(time.monotonic() - started, 3)
            return result

        except Exception as exc:
            db.rollback()
            last_error = exc
            if attempt < args.retry:
                wait = args.retry_sleep * (2 ** (attempt - 1))
                print(f"[WARN] {ticker} attempt {attempt}/{args.retry} failed: {exc}; retry in {wait:.1f}s", flush=True)
                time.sleep(wait)

    result.status = "failed"
    result.error = str(last_error) if last_error else "unknown error"
    after = get_ticker_coverage(db, ticker)
    result.db_min_after = after["min_date"].isoformat() if after["min_date"] else None
    result.db_max_after = after["max_date"].isoformat() if after["max_date"] else None
    result.db_rows_after = after["row_count"]
    result.elapsed_sec = round(time.monotonic() - started, 3)
    return result


def api_login(base_url: str, username: str, password: str) -> str:
    import requests

    resp = requests.post(
        f"{base_url.rstrip('/')}/api/auth/login",
        json={"username": username, "password": password},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = ((payload.get("data") or {}).get("token") or "").strip()
    if not token:
        raise RuntimeError(f"login failed: {payload}")
    return token


def verify_api(base_url: str, username: str, password: str, ticker: str) -> dict[str, Any]:
    import requests

    token = api_login(base_url, username, password)
    resp = requests.get(
        f"{base_url.rstrip('/')}/api/stocks/{ticker.upper()}/detail",
        params={"range": "all", "include_news": "false", "include_indicators": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    return {
        "ticker": data.get("ticker"),
        "price_range": data.get("price_range"),
        "data_frequency": data.get("data_frequency"),
        "price_curve_count": data.get("price_curve_count"),
        "price_curve_start": data.get("price_curve_start"),
        "price_curve_end": data.get("price_curve_end"),
        "current_price": ((data.get("current_quote") or {}).get("current_price")),
    }


def main() -> int:
    args = parse_args()

    if not os.getenv("TWELVEDATA_API_KEY"):
        print("[ERROR] TWELVEDATA_API_KEY is not set.", file=sys.stderr)
        print("Example: export TWELVEDATA_API_KEY='your_key'", file=sys.stderr)
        return 2

    fallback_start = date.fromisoformat(args.fallback_start_date)
    end_date = date.fromisoformat(args.end_date)

    tickers = load_tickers(args)
    if not tickers:
        print("[ERROR] no tickers loaded", file=sys.stderr)
        return 2

    db = SessionLocal()
    all_results: list[dict[str, Any]] = []
    started_at = datetime.now()

    try:
        if args.start_date.strip().lower() == "auto":
            start_date = get_global_start_date(db, fallback_start)
        else:
            start_date = date.fromisoformat(args.start_date)

        completed = load_completed_from_log(args.log_file, start_date, end_date) if args.resume else set()

        print(
            json.dumps(
                {
                    "task": "finsight_sp500_daily_backfill",
                    "target_table": "price_data",
                    "granularity": "1day",
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "ticker_count": len(tickers),
                    "offset": args.offset,
                    "limit": args.limit,
                    "skip_covered": args.skip_covered,
                    "incremental_by_ticker": args.incremental_by_ticker,
                    "exclude_core7": args.exclude_core7,
                    "exclude_tickers": args.exclude_tickers,
                    "resume": args.resume,
                    "already_completed_in_log": len(completed),
                    "sleep_seconds": args.sleep,
                    "repair_derived": not args.no_repair_derived,
                    "build_indicators": args.build_indicators,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            flush=True,
        )

        counts = {"updated": 0, "cached": 0, "empty": 0, "failed": 0, "resumed": 0}

        for idx, ticker in enumerate(tickers, 1):
            if ticker in completed and not args.force_refresh:
                counts["resumed"] += 1
                print(f"[{idx}/{len(tickers)}] {ticker} resumed-skip", flush=True)
                continue

            print(f"[{idx}/{len(tickers)}] {ticker} fetching daily {start_date} -> {end_date}", flush=True)
            result = process_ticker(db, ticker, args, start_date, end_date)
            payload = asdict(result)
            all_results.append(payload)
            write_jsonl(args.log_file, payload)

            counts[result.status] = counts.get(result.status, 0) + 1
            print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)

            if idx < len(tickers) and args.sleep > 0:
                time.sleep(args.sleep)

        summary = {
            "task": "finsight_sp500_daily_backfill",
            "target_table": "price_data",
            "granularity": "1day",
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "ticker_count": len(tickers),
            "counts": counts,
            "log_file": args.log_file,
            "items": all_results,
        }

        if args.verify_api:
            if not args.api_password:
                summary["api_verification"] = {
                    "status": "skipped",
                    "reason": "--api-password or API_PASSWORD env var is not set",
                }
            else:
                try:
                    summary["api_verification"] = {
                        "status": "success",
                        "result": verify_api(
                            args.api_base_url,
                            args.api_username,
                            args.api_password,
                            args.verify_ticker,
                        ),
                    }
                except Exception as exc:
                    summary["api_verification"] = {"status": "failed", "error": str(exc)}

        Path(args.summary_file).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str), flush=True)

        return 1 if counts.get("failed", 0) else 0

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
