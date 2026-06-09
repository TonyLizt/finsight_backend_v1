"""快速测试 Yahoo Chart 行情抓取。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from app.services.market_data_service import fetch_yahoo_chart_prices


def main() -> None:
    ticker = sys.argv[1].upper() if len(sys.argv) >= 2 else "AAPL"
    end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date() if len(sys.argv) >= 3 else datetime.utcnow().date()
    start_date = end_date - timedelta(days=400)

    try:
        records = fetch_yahoo_chart_prices(ticker, start_date, end_date)
        print(
            json.dumps(
                {
                    "ticker": ticker,
                    "status": "success",
                    "source": "yahoo_chart",
                    "requested_end_date": end_date.isoformat(),
                    "rows": len(records),
                    "first_date": records[0]["trading_date"].isoformat() if records else None,
                    "latest_date": records[-1]["trading_date"].isoformat() if records else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ticker": ticker,
                    "status": "failed",
                    "source": "yahoo_chart",
                    "requested_end_date": end_date.isoformat(),
                    "error": repr(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
