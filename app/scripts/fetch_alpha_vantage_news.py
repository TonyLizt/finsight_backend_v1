from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


API_URL = "https://www.alphavantage.co/query"
DEFAULT_RAW_ROOT = Path("/data/hmt/datasets/finsight/news/raw/alpha_vantage")


def parse_alpha_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%dT%H%M%S")


def format_alpha_minute(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M")


def collect_existing_times(ticker_dir: Path) -> list[datetime]:
    times: list[datetime] = []

    for p in sorted(ticker_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        for item in data.get("feed", []):
            ts = item.get("time_published")
            if not ts:
                continue
            try:
                times.append(parse_alpha_time(ts))
            except Exception:
                pass

    return times


def existing_article_count(ticker_dir: Path) -> int:
    urls = set()
    count = 0

    for p in sorted(ticker_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        for item in data.get("feed", []):
            count += 1
            url = item.get("url")
            if url:
                urls.add(url)

    return count, len(urls)


def next_part_no(ticker_dir: Path) -> int:
    return len(list(ticker_dir.glob("*.json"))) + 1


def fetch_news(
    ticker: str,
    time_from: str,
    time_to: str,
    limit: int,
    api_key: str,
) -> dict:
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "time_from": time_from,
        "time_to": time_to,
        "sort": "EARLIEST",
        "limit": limit,
        "apikey": api_key,
    }

    resp = requests.get(API_URL, params=params, timeout=120)
    print("status:", resp.status_code)
    print("first 300 chars:")
    print(resp.text[:300])

    resp.raise_for_status()
    data = resp.json()

    if "Note" in data:
        raise RuntimeError("Alpha Vantage rate limit Note: " + str(data["Note"]))
    if "Information" in data:
        raise RuntimeError("Alpha Vantage Information: " + str(data["Information"]))
    if "Error Message" in data:
        raise RuntimeError("Alpha Vantage Error: " + str(data["Error Message"]))

    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--start", default="20240101T0000")
    parser.add_argument("--end", default="20250520T2359")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--sleep", type=int, default=15)
    parser.add_argument("--raw-root", default=str(DEFAULT_RAW_ROOT))
    args = parser.parse_args()

    ticker = args.ticker.upper()
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")

    if not api_key:
        raise RuntimeError("请先 export ALPHA_VANTAGE_API_KEY")

    raw_root = Path(args.raw_root)
    ticker_dir = raw_root / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    print("ticker:", ticker)
    print("ticker_dir:", ticker_dir)
    print("target range:", args.start, "to", args.end)

    old_count, old_unique = existing_article_count(ticker_dir)
    existing_times = collect_existing_times(ticker_dir)

    if existing_times:
        max_time = max(existing_times)
        resume_dt = max_time + timedelta(minutes=1)
        current_from = format_alpha_minute(resume_dt)
        print("existing_articles:", old_count)
        print("existing_unique_urls:", old_unique)
        print("latest_existing_time:", max_time.strftime("%Y%m%dT%H%M%S"))
        print("resume_from:", current_from)
    else:
        current_from = args.start
        print("no existing raw files, start from:", current_from)

    end_dt = datetime.strptime(args.end, "%Y%m%dT%H%M")

    while True:
        current_dt = datetime.strptime(current_from, "%Y%m%dT%H%M")
        if current_dt > end_dt:
            print("already complete: current_from > end")
            break

        part_no = next_part_no(ticker_dir)
        out_path = ticker_dir / f"{ticker}_{current_from}_{args.end}_part{part_no:03d}.json"

        print("=" * 80)
        print("fetch:", ticker, current_from, "to", args.end, "part:", part_no)

        data = fetch_news(
            ticker=ticker,
            time_from=current_from,
            time_to=args.end,
            limit=args.limit,
            api_key=api_key,
        )

        feed = data.get("feed", [])
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("saved:", out_path)
        print("feed_count:", len(feed))

        if feed:
            first_time = feed[0].get("time_published")
            last_time = feed[-1].get("time_published")
            print("first_time:", first_time)
            print("last_time :", last_time)
        else:
            print("empty feed, stop")
            break

        if len(feed) < args.limit:
            print("last page reached: feed_count < limit")
            break

        last_dt = parse_alpha_time(feed[-1]["time_published"])
        current_from = format_alpha_minute(last_dt + timedelta(minutes=1))

        print(f"sleep {args.sleep}s before next request...")
        time.sleep(args.sleep)

    new_count, new_unique = existing_article_count(ticker_dir)
    print("=" * 80)
    print("DONE")
    print({
        "ticker": ticker,
        "raw_files": len(list(ticker_dir.glob('*.json'))),
        "article_count": new_count,
        "unique_url_count": new_unique,
        "raw_dir": str(ticker_dir),
    })


if __name__ == "__main__":
    main()
