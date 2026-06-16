# Finsight v1.5 Feature Snapshot Hotfix

## 修复内容

本补丁修复 `model_feature_snapshots.features_json` 中行情派生字段可能被写成 `0.0` 的问题。

已修复字段：

- `open`
- `high`
- `low`
- `close`
- `volume`
- `previous_close`
- `change_amount`
- `daily_return`
- `change_percent`
- `amplitude`

核心原则：

1. 生成 runtime feature snapshot 时，行情派生字段强制以 `price_data` 为准。
2. 不再把 `daily_return / change_percent / amplitude` 的缺失值静默填成 `0.0`。
3. 如果 `price_data` 派生字段为空，代码会基于 OHLC 和前一交易日 `close` 兜底计算。
4. 不再依赖模板 snapshot 中已有字段是否存在；旧模板没有 `previous_close/change_amount` 时也会补入。

## 包含文件

```text
app/services/feature_snapshot_service.py
app/scripts/repair_feature_snapshots_from_price_data.py
README_V1_5_FEATURE_SNAPSHOT_HOTFIX.md
```

## 应用方式

在项目根目录执行：

```bash
unzip finsight_backend_v1_5_feature_snapshot_hotfix.zip -d .
docker compose up -d --force-recreate backend
```

## 先做 dry-run 检查

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data --latest-only --dry-run"
```

## 修复最新 snapshot

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data --latest-only"
```

如果希望修复所有历史 snapshot：

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.repair_feature_snapshots_from_price_data"
```

## 强制重建 features 验证代码层修复

```bash
docker compose exec backend bash -lc "PYTHONPATH=/app python -m app.scripts.run_twelvedata_incremental_refresh --modules features --force-refresh"
```

然后检查最新 snapshot：

```bash
docker compose exec backend bash -lc 'PYTHONPATH=/app python - <<PY
import json
from sqlalchemy import text
from app.db.session import SessionLocal

TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]

db = SessionLocal()
for ticker in TICKERS:
    row = db.execute(text("""
        SELECT id, ticker, base_trading_date, current_price, features_json, created_at
        FROM model_feature_snapshots
        WHERE ticker = :ticker
        ORDER BY base_trading_date DESC, id DESC
        LIMIT 1
    """), {"ticker": ticker}).mappings().first()
    features = row["features_json"]
    if isinstance(features, str):
        features = json.loads(features)
    print({
        "ticker": ticker,
        "id": row["id"],
        "base_trading_date": str(row["base_trading_date"]),
        "current_price": float(row["current_price"]),
        "feature_close": features.get("close"),
        "feature_daily_return": features.get("daily_return"),
        "feature_change_percent": features.get("change_percent"),
        "feature_amplitude": features.get("amplitude"),
        "created_at": str(row["created_at"]),
    })

db.close()
PY'
```

预期：7 只股票的 `feature_daily_return / feature_change_percent / feature_amplitude` 不应再错误地全部为 `0.0`。
