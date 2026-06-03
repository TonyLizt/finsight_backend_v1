"""测试线上行情自爬。

用法：
PYTHONPATH=/app python -m app.scripts.test_online_market_fetch AAPL 2026-06-02

该脚本只调用 ensure_price_data，不执行模型预测。
"""

from __future__ import annotations

import json
import sys
from datetime import date

from app.db.session import SessionLocal
from app.services.market_data_service import ensure_price_data


def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) >= 2 else "AAPL"
    target_date = date.fromisoformat(sys.argv[2]) if len(sys.argv) >= 3 else None

    db = SessionLocal()
    try:
        result = ensure_price_data(
            db=db,
            ticker=ticker,
            force_refresh=True,
            target_date=target_date,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
