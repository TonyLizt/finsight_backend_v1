#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply Finsight v1.3 AKShare market source + 14-day sentiment counts patches.

Run from project root:
    python tools/apply_v13_akshare_and_14d_patch.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    root = Path.cwd()

    required = [
        root / "app/services/market_data_service.py",
        root / "app/services/stock_service.py",
        root / "app/routers/stocks.py",
    ]
    missing_files = [str(p) for p in required if not p.exists()]
    if missing_files:
        raise SystemExit("Missing required project files:\n" + "\n".join(missing_files))

    run([sys.executable, "tools/patch_akshare_daily_market_source.py"])
    run([sys.executable, "tools/patch_two_week_sentiment_counts.py"])

    run([
        sys.executable,
        "-m",
        "py_compile",
        "app/services/market_data_service.py",
        "app/scripts/test_akshare_daily_market_fetch.py",
        "app/services/stock_service.py",
        "app/routers/stocks.py",
    ])

    print("\nPatch applied successfully.")
    print("Next:")
    print("  docker compose build backend")
    print("  docker compose up -d")


if __name__ == "__main__":
    main()
