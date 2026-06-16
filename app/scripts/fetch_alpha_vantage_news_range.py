from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


API_URL = "https://www.alphavantage.co/query"
RAW_ROOT = Path("/data/hmt/datasets/finsight/news/raw/alpha_vantage")


def parse_alpha_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%dT%H%M%S")


def format_alpha_minute(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M")


def fetch_news(ticker: str, time_from: str, time_to: str, limit: int, api_key: str) -> dict:
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
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--sleep", type=int, default=15)
    args = parser.parse_args()

    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise RuntimeError("请先 export ALPHA_VANTAGE_API_KEY")

    ticker = args.ticker.upper()
    ticker_dir = RAW_ROOT / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    current_from = args.start
    end_dt = datetime.strptime(args.end, "%Y%m%dT%H%M")

    part = 1
    while True:
        current_dt = datetime.strptime(current_from, "%Y%m%dT%H%M")
        if current_dt > end_dt:
            print("complete: current_from > end")
            break

        out_path = ticker_dir / f"{ticker}_{current_from}_{args.end}_2023part{part:03d}.json"

        if out_path.exists() and out_path.stat().st_size > 0:
            print("skip existing:", out_path)
            data = json.loads(out_path.read_text(encoding="utf-8"))
            feed = data.get("feed", [])
        else:
            print("=" * 80)
            print("fetch:", ticker, current_from, "to", args.end, "part:", part)

            data = fetch_news(
                ticker=ticker,
                time_from=current_from,
                time_to=args.end,
                limit=args.limit,
                api_key=api_key,
            )

            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            feed = data.get("feed", [])
            print("saved:", out_path)

        print("feed_count:", len(feed))

        if feed:
            print("first_time:", feed[0].get("time_published"))
            print("last_time :", feed[-1].get("time_published"))

        if not feed:
            print("empty feed, stop")
            break

        if len(feed) < args.limit:
            print("last page reached: feed_count < limit")
            break

        last_dt = parse_alpha_time(feed[-1]["time_published"])
        current_from = format_alpha_minute(last_dt + timedelta(minutes=1))
        part += 1

        print(f"sleep {args.sleep}s before next request...")
        time.sleep(args.sleep)

    print("DONE", ticker)


if __name__ == "__main__":
    main()
