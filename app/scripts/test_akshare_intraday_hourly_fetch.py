"""Test AKShare US intraday hourly fetch.

Usage:
    PYTHONPATH=/app python -m app.scripts.test_akshare_intraday_hourly_fetch AAPL
"""

from __future__ import annotations

import json
import sys

from app.services.intraday_market_service import get_hourly_intraday_curve


def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) >= 2 else "AAPL"
    result = get_hourly_intraday_curve(ticker, target_date=None)
    preview = dict(result)
    preview["items"] = result.get("items", [])[:10]
    preview["returned_preview_count"] = len(preview["items"])
    preview["total_count"] = len(result.get("items", []))
    print(json.dumps(preview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
