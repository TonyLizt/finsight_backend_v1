#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch market_data_service.py to use AKShare daily market data first."""

from __future__ import annotations

import re
from pathlib import Path


AKSHARE_FUNCTION = '\ndef _row_get_any(row: dict[str, Any], *keys: str) -> Any:\n    """从 pandas row dict 中兼容读取英文/中文字段。"""\n    if not row:\n        return None\n\n    for key in keys:\n        if key in row:\n            return row[key]\n\n    normalized = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}\n    for key in keys:\n        k = str(key).strip().lower().replace(" ", "_")\n        if k in normalized:\n            return normalized[k]\n\n    return None\n\n\ndef fetch_akshare_daily_prices(\n    ticker: str,\n    start_date: date | None = None,\n    end_date: date | None = None,\n) -> list[dict[str, Any]]:\n    """从 AKShare stock_us_daily 抓取美股日频行情。\n\n    数据源：\n    - ak.stock_us_daily(symbol="AAPL")\n    - 返回常见字段：date/open/high/low/close/volume\n\n    说明：\n    - 本函数只负责抓取并标准化，不直接入库；\n    - previous_close / daily_return / change_percent / amplitude 会在完整序列上统一计算；\n    - 如果 AKShare 未安装或上游失败，抛出异常，由 ensure_price_data 回退到数据库缓存。\n    """\n    ticker = ticker.upper().strip()\n    if not ticker:\n        return []\n\n    try:\n        import akshare as ak\n    except Exception as exc:\n        raise RuntimeError("akshare is not installed; please run `pip install akshare`") from exc\n\n    adjust = os.getenv("AKSHARE_US_DAILY_ADJUST", "").strip()\n\n    try:\n        df = ak.stock_us_daily(symbol=ticker, adjust=adjust)\n    except TypeError:\n        df = ak.stock_us_daily(symbol=ticker)\n\n    if df is None or getattr(df, "empty", True):\n        return []\n\n    if "date" not in {str(c).lower() for c in df.columns} and "日期" not in set(map(str, df.columns)):\n        df = df.reset_index()\n\n    raw_records: list[dict[str, Any]] = []\n\n    for row in df.to_dict(orient="records"):\n        raw = {\n            "date": _row_get_any(row, "date", "日期", "index", "datetime", "timestamp"),\n            "open": _row_get_any(row, "open", "开盘"),\n            "high": _row_get_any(row, "high", "最高"),\n            "low": _row_get_any(row, "low", "最低"),\n            "close": _row_get_any(row, "close", "收盘"),\n            "adj_close": _row_get_any(row, "adj_close", "adjusted_close", "Adj Close", "复权收盘"),\n            "volume": _row_get_any(row, "volume", "成交量"),\n        }\n        rec = _normalize_price_record(ticker, raw, source="akshare_stock_us_daily")\n        if rec is not None:\n            raw_records.append(rec)\n\n    if not raw_records:\n        return []\n\n    records = _add_previous_close(raw_records)\n\n    filtered: list[dict[str, Any]] = []\n    for rec in records:\n        dt = rec["trading_date"]\n        if start_date and dt < start_date:\n            continue\n        if end_date and dt > end_date:\n            continue\n        filtered.append(rec)\n\n    return filtered\n\n'
NEW_FETCH_BLOCK = '    records: list[dict[str, Any]] = []\n    source = None\n    errors: list[str] = []\n\n    # v1.3+ 行情源优先级。\n    # 默认：AKShare 日频行情优先；失败后使用数据库已有缓存。\n    # 可选值：\n    # - akshare / akshare_stock_us_daily\n    # - local_raw_csv\n    # - yahoo_chart\n    # - database / mysql_price_data\n    priority_raw = os.getenv("MARKET_DATA_SOURCE_PRIORITY", "akshare,database")\n    priority = [x.strip().lower() for x in priority_raw.split(",") if x.strip()]\n    if not priority:\n        priority = ["akshare", "database"]\n    if not any(x in {"database", "mysql", "mysql_price_data"} for x in priority):\n        priority.append("database")\n\n    for source_name in priority:\n        if records:\n            break\n\n        if source_name in {"database", "mysql", "mysql_price_data"}:\n            # 数据库缓存 fallback 在下面统一处理。\n            continue\n\n        if source_name in {"akshare", "akshare_daily", "akshare_stock_us_daily"}:\n            try:\n                records = fetch_akshare_daily_prices(ticker, start_date=start_date, end_date=target_date)\n                if records:\n                    source = "akshare_stock_us_daily"\n            except Exception as exc:\n                errors.append(f"akshare_stock_us_daily failed: {exc}")\n            continue\n\n        if source_name == "local_raw_csv":\n            if os.getenv("ENABLE_LOCAL_RAW_CSV_FALLBACK", "0").strip() not in {"1", "true", "True", "yes", "YES"}:\n                errors.append("local_raw_csv skipped: ENABLE_LOCAL_RAW_CSV_FALLBACK is not enabled")\n                continue\n            try:\n                records, source_file = fetch_local_raw_csv_prices(ticker, start_date=start_date, end_date=target_date)\n                if records:\n                    source = f"local_raw_csv:{source_file}"\n            except Exception as exc:\n                errors.append(f"local_raw_csv failed: {exc}")\n            continue\n\n        if source_name == "yahoo_chart":\n            if os.getenv("ENABLE_YAHOO_CHART_FALLBACK", "1").strip() in {"0", "false", "False", "no", "NO"}:\n                errors.append("yahoo_chart skipped: ENABLE_YAHOO_CHART_FALLBACK is disabled")\n                continue\n            try:\n                records = fetch_yahoo_chart_prices(ticker, start_date=start_date, end_date=target_date)\n                if records:\n                    source = "yahoo_chart"\n            except Exception as exc:\n                errors.append(f"yahoo_chart failed: {exc}")\n            continue\n\n        errors.append(f"unknown market data source skipped: {source_name}")\n\n'
TEST_SCRIPT = '"""单独测试 AKShare 日频行情补全。\n\n用法：\n    PYTHONPATH=/app python -m app.scripts.test_akshare_daily_market_fetch AAPL 2026-06-05\n\n说明：\n    该脚本会调用 app.services.market_data_service.ensure_price_data，\n    因此会把 AKShare 抓到的日频行情 upsert 到 MySQL price_data。\n"""\n\nfrom __future__ import annotations\n\nimport json\nimport sys\nfrom datetime import date\n\nfrom app.db.init_db import init_db\nfrom app.db.session import SessionLocal\nfrom app.services.market_data_service import ensure_price_data, fetch_akshare_daily_prices\n\n\ndef main() -> None:\n    ticker = sys.argv[1] if len(sys.argv) >= 2 else "AAPL"\n    target_date = date.fromisoformat(sys.argv[2]) if len(sys.argv) >= 3 else date.today()\n\n    init_db()\n\n    preview_start = date(target_date.year, target_date.month, 1)\n    preview_records = fetch_akshare_daily_prices(\n        ticker,\n        start_date=preview_start,\n        end_date=target_date,\n    )\n\n    db = SessionLocal()\n    try:\n        result = ensure_price_data(\n            db,\n            ticker=ticker,\n            target_date=target_date,\n            force_refresh=True,\n        )\n    finally:\n        db.close()\n\n    print(json.dumps(\n        {\n            "ticker": ticker.upper(),\n            "target_date": target_date.isoformat(),\n            "akshare_preview_count": len(preview_records),\n            "akshare_preview_latest_date": (\n                max(r["trading_date"] for r in preview_records).isoformat()\n                if preview_records else None\n            ),\n            "ensure_price_data_result": result,\n        },\n        ensure_ascii=False,\n        indent=2,\n        default=str,\n    ))\n\n\nif __name__ == "__main__":\n    main()\n'


def backup_once(path: Path, suffix: str, original: str) -> None:
    backup = path.with_suffix(path.suffix + suffix)
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")


def insert_akshare_function(text: str) -> str:
    if "def fetch_akshare_daily_prices(" in text:
        return text

    marker = "def fetch_yahoo_chart_prices("
    idx = text.find(marker)
    if idx < 0:
        marker = "def upsert_price_records("
        idx = text.find(marker)
    if idx < 0:
        raise RuntimeError("Could not find insertion point for fetch_akshare_daily_prices()")

    return text[:idx] + AKSHARE_FUNCTION + "\n" + text[idx:]


def patch_cached_condition(text: str) -> str:
    old = 'if not force_refresh and latest_date and not stale and not has_gap and quality_status == "ok":'
    new = (
        'if (\n'
        '        not force_refresh\n'
        '        and latest_date\n'
        '        and latest_date >= target_date\n'
        '        and not has_gap\n'
        '        and quality_status == "ok"\n'
        '    ):'
    )
    if old in text:
        return text.replace(old, new, 1)

    if "and latest_date >= target_date" in text:
        return text

    return text


def replace_fetch_block(text: str) -> str:
    marker = "    records: list[dict[str, Any]] = []\n    source = None\n    errors: list[str] = []"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError("Could not locate records/source/errors block in ensure_price_data()")

    end_marker = "\n    if not records:\n        latest_date = get_latest_price_date(db, ticker)"
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError("Could not locate `if not records` fallback block in ensure_price_data()")

    return text[:start] + NEW_FETCH_BLOCK + text[end:]


def patch_market_data_service() -> None:
    path = Path("app/services/market_data_service.py")
    text = path.read_text(encoding="utf-8")
    original = text

    text = insert_akshare_function(text)
    text = patch_cached_condition(text)
    text = replace_fetch_block(text)

    backup_once(path, ".bak_akshare_daily", original)
    path.write_text(text, encoding="utf-8")
    print("Updated app/services/market_data_service.py")


def write_test_script() -> None:
    path = Path("app/scripts/test_akshare_daily_market_fetch.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEST_SCRIPT, encoding="utf-8")
    print("Wrote app/scripts/test_akshare_daily_market_fetch.py")


def patch_requirements() -> None:
    path = Path("requirements.txt")
    if not path.exists():
        print("requirements.txt not found, skip dependency patch. Please install akshare manually.")
        return

    text = path.read_text(encoding="utf-8")
    if re.search(r"^akshare\b", text, flags=re.M):
        print("requirements.txt already contains akshare")
        return

    if text and not text.endswith("\n"):
        text += "\n"
    text += "akshare>=1.18.0\n"
    path.write_text(text, encoding="utf-8")
    print("Updated requirements.txt")


def main() -> None:
    patch_market_data_service()
    write_test_script()
    patch_requirements()
    print("Patch finished.")
    print("Next:")
    print("  python -m py_compile app/services/market_data_service.py app/scripts/test_akshare_daily_market_fetch.py")
    print("  docker compose build backend && docker compose up -d")


if __name__ == "__main__":
    main()
