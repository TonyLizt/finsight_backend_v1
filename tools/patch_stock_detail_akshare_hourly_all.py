#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch stock detail: range=1d uses AKShare hourly bars; range=all returns full DB history."""

from __future__ import annotations

import re
from pathlib import Path


def asset_text(name: str) -> str:
    return (Path(__file__).parent / "patch_assets" / name).read_text(encoding="utf-8")


def backup_once(path: Path, suffix: str, original: str) -> None:
    backup = path.with_suffix(path.suffix + suffix)
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")


def patch_requirements() -> None:
    path = Path("requirements.txt")
    if not path.exists():
        print("Skip requirements.txt: file not found")
        return
    text = path.read_text(encoding="utf-8")
    if re.search(r"^akshare\b", text, flags=re.M | re.I):
        print("No change needed: requirements.txt already has akshare")
        return
    path.write_text(text.rstrip() + "\nakshare>=1.18.0\n", encoding="utf-8")
    print("Updated requirements.txt")


def patch_stock_service() -> None:
    path = Path("app/services/stock_service.py")
    text = path.read_text(encoding="utf-8")
    original = text
    replacement = asset_text("price_curve_function.txt") + "def calc_52_week_high_low"
    pattern = r"def price_curve\(db: Session, ticker: str, days: .*?\) -> list\[PriceData\]:\n.*?\n\ndef calc_52_week_high_low"
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError("Could not patch price_curve() in app/services/stock_service.py")
    if text != original:
        backup_once(path, ".bak_akshare_hourly_all", original)
        path.write_text(text, encoding="utf-8")
        print("Updated app/services/stock_service.py")
    else:
        print("No change needed: app/services/stock_service.py")


def patch_stocks_router() -> None:
    path = Path("app/routers/stocks.py")
    text = path.read_text(encoding="utf-8")
    original = text

    import_line = "from app.services.intraday_market_service import get_hourly_intraday_curve\n"
    if import_line not in text:
        marker = "from app.services.indicator_service import"
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx] + import_line + text[idx:]
        else:
            text = import_line + text

    replacement = asset_text("stock_detail_function.txt")
    pattern = r'@router\.get\("/\{ticker\}/detail"\)\ndef stock_detail\(.*?\n\n(?=@router\.get\()'
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError("Could not replace stock_detail() in app/routers/stocks.py")
    if text != original:
        backup_once(path, ".bak_akshare_hourly_all", original)
        path.write_text(text, encoding="utf-8")
        print("Updated app/routers/stocks.py")
    else:
        print("No change needed: app/routers/stocks.py")


def main() -> None:
    patch_requirements()
    patch_stock_service()
    patch_stocks_router()
    print("Patch finished. Next run:")
    print("  python -m py_compile app/services/intraday_market_service.py app/services/stock_service.py app/routers/stocks.py")
    print("  docker compose build backend && docker compose up -d")


if __name__ == "__main__":
    main()
