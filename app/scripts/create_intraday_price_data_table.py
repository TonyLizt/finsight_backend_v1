"""创建 v1.5 新增的 intraday_price_data 表。

用法：
PYTHONPATH=/app python -m app.scripts.create_intraday_price_data_table
"""

from app.services.twelvedata_market_service import ensure_extra_tables


def main() -> None:
    ensure_extra_tables()
    print("intraday_price_data table ensured")


if __name__ == "__main__":
    main()
