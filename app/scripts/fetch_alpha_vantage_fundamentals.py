from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests


API_URL = "https://www.alphavantage.co/query"
RAW_ROOT = Path("/data/hmt/datasets/finsight/fundamentals/raw/alpha_vantage")


FUNCTIONS = [
    "EARNINGS",
    "INCOME_STATEMENT",
    "BALANCE_SHEET",
    "CASH_FLOW",
]


def fetch_function(symbol: str, function: str, api_key: str) -> dict:
    params = {
        "function": function,
        "symbol": symbol,
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
    parser.add_argument("--sleep", type=int, default=20)
    parser.add_argument(
        "--functions",
        nargs="+",
        default=FUNCTIONS,
        choices=FUNCTIONS,
    )
    args = parser.parse_args()

    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise RuntimeError("请先 export ALPHA_VANTAGE_API_KEY")

    ticker = args.ticker.upper()
    ticker_dir = RAW_ROOT / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)

    for i, function in enumerate(args.functions, start=1):
        out_path = ticker_dir / f"{ticker}_{function}.json"

        if out_path.exists() and out_path.stat().st_size > 0:
            print("skip existing:", out_path)
            continue

        print("=" * 80)
        print(f"[{i}/{len(args.functions)}] fetch {ticker} {function}")

        data = fetch_function(ticker, function, api_key)

        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("saved:", out_path)

        print(f"sleep {args.sleep}s...")
        time.sleep(args.sleep)

    print("DONE", ticker)


if __name__ == "__main__":
    main()
