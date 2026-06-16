#!/usr/bin/env python3
"""
Import Member B v1.2 real data into the Finsight MySQL database.

用途：
1. 从 B 同学的回测期 SQLite 数据库导入真实 price_data / technical_indicators / sentiment_daily / news_data / stocks。
2. 从 v1.2 最终训练集 dataset_h5_v1.csv 导入 50 维模型特征快照到 model_feature_snapshots。
3. 注册 v1.2 模型版本到 model_versions，并设为 active。

推荐在 Docker backend 容器内运行：
PYTHONPATH=/app python app/scripts/import_member_b_real_data.py \
  --sqlite-db /external_outputs/backtest_after_20250520/finsight_price_backtest_after_20250520.db \
  --training-dir /external_outputs/expanded_60_no_weak10_news48_quality_fundamental/training_dataset \
  --batch-size 1000

注意：
- 本脚本不会删除 users / predictions / watchlists / operation_logs 等用户业务数据。
- 对导入表采用“有则更新、无则插入”的 upsert 思路。
- 如果目标表没有唯一索引，脚本会先按业务键查询 id，再决定 update/insert，避免重复。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


# -----------------------------
# 基础工具
# -----------------------------

def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Please run this inside backend container or set .env.docker.")
    return url


def normalize_value(value: Any) -> Any:
    """把 pandas/numpy 的 NaN、Timestamp 等转换为 MySQL 友好值。"""
    if value is None:
        return None

    # pandas NaN / NaT
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.to_pydatetime()

    # numpy scalar
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None

    return value


def normalize_date_string(value: Any) -> Any:
    """尽量把日期/时间值整理成 MySQL 可识别格式。"""
    value = normalize_value(value)
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    s = str(value).strip()
    if not s:
        return None

    # 兼容 pandas 读出的 "2025-05-21 00:00:00"
    return s


def table_exists(engine: Engine, table: str) -> bool:
    return inspect(engine).has_table(table)


def get_mysql_columns(engine: Engine, table: str) -> List[str]:
    if not table_exists(engine, table):
        return []
    return [c["name"] for c in inspect(engine).get_columns(table)]


def get_sqlite_tables(sqlite_path: Path) -> List[str]:
    with sqlite3.connect(sqlite_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return [r[0] for r in rows]


def get_sqlite_columns(sqlite_path: Path, table: str) -> List[str]:
    with sqlite3.connect(sqlite_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def read_sqlite_table(sqlite_path: Path, table: str) -> pd.DataFrame:
    with sqlite3.connect(sqlite_path) as conn:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def make_insert_sql(table: str, columns: Sequence[str]) -> str:
    col_sql = ", ".join(f"`{c}`" for c in columns)
    val_sql = ", ".join(f":{c}" for c in columns)
    return f"INSERT INTO `{table}` ({col_sql}) VALUES ({val_sql})"


def make_update_sql(table: str, columns: Sequence[str], where_cols: Sequence[str]) -> str:
    update_cols = [c for c in columns if c not in where_cols and c != "id"]
    if not update_cols:
        return ""
    set_sql = ", ".join(f"`{c}` = :{c}" for c in update_cols)
    where_sql = " AND ".join(f"`{c}` = :__where_{c}" for c in where_cols)
    return f"UPDATE `{table}` SET {set_sql} WHERE {where_sql}"


def record_exists(conn, table: str, where_cols: Sequence[str], row: Dict[str, Any]) -> bool:
    where_sql = " AND ".join(f"`{c}` <=> :{c}" for c in where_cols)
    sql = text(f"SELECT 1 FROM `{table}` WHERE {where_sql} LIMIT 1")
    params = {c: row.get(c) for c in where_cols}
    return conn.execute(sql, params).first() is not None


def upsert_dataframe_by_keys(
    engine: Engine,
    table: str,
    df: pd.DataFrame,
    key_cols: Sequence[str],
    batch_size: int = 1000,
) -> Tuple[int, int, int]:
    """按业务键逐行 upsert。返回 inserted, updated, skipped。"""
    mysql_cols = get_mysql_columns(engine, table)
    if not mysql_cols:
        print(f"[SKIP] MySQL table not found: {table}")
        return 0, 0, len(df)

    usable_cols = [c for c in df.columns if c in mysql_cols and c != "id"]
    key_cols = [c for c in key_cols if c in usable_cols]

    if not key_cols:
        print(f"[SKIP] Table {table}: no usable key columns found. expected keys={key_cols}")
        return 0, 0, len(df)

    if not usable_cols:
        print(f"[SKIP] Table {table}: no common columns.")
        return 0, 0, len(df)

    inserted = updated = skipped = 0
    insert_sql = text(make_insert_sql(table, usable_cols))
    update_sql_raw = make_update_sql(table, usable_cols, key_cols)
    update_sql = text(update_sql_raw) if update_sql_raw else None

    print(f"[IMPORT] {table}: rows={len(df)}, cols={len(usable_cols)}, keys={key_cols}")

    rows: List[Dict[str, Any]] = []
    for _, series in df.iterrows():
        row = {c: normalize_value(series.get(c)) for c in usable_cols}

        # 跳过 key 缺失的行
        if any(row.get(k) is None for k in key_cols):
            skipped += 1
            continue

        rows.append(row)

        if len(rows) >= batch_size:
            i, u = _flush_rows(engine, table, rows, key_cols, insert_sql, update_sql)
            inserted += i
            updated += u
            rows.clear()
            print(f"  progress: inserted={inserted}, updated={updated}, skipped={skipped}")

    if rows:
        i, u = _flush_rows(engine, table, rows, key_cols, insert_sql, update_sql)
        inserted += i
        updated += u

    print(f"[DONE] {table}: inserted={inserted}, updated={updated}, skipped={skipped}")
    return inserted, updated, skipped


def _flush_rows(engine: Engine, table: str, rows: List[Dict[str, Any]], key_cols: Sequence[str], insert_sql, update_sql) -> Tuple[int, int]:
    inserted = updated = 0

    with engine.begin() as conn:
        for row in rows:
            if record_exists(conn, table, key_cols, row):
                if update_sql is not None:
                    params = dict(row)
                    for k in key_cols:
                        params[f"__where_{k}"] = row.get(k)
                    conn.execute(update_sql, params)
                updated += 1
            else:
                conn.execute(insert_sql, row)
                inserted += 1

    return inserted, updated


# -----------------------------
# 表结构：model_feature_snapshots
# -----------------------------

def ensure_model_feature_snapshots(engine: Engine) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS model_feature_snapshots (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        dataset_version VARCHAR(100) NOT NULL,
        ticker VARCHAR(20) NOT NULL,
        base_trading_date DATE NOT NULL,
        target_date_d5 DATE NULL,
        current_price DECIMAL(14,4) NULL,
        features_json JSON NOT NULL,
        target_json JSON NULL,
        raw_row_json JSON NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_dataset_ticker_date (dataset_version, ticker, base_trading_date),
        INDEX idx_ticker_date (ticker, base_trading_date)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    """
    with engine.begin() as conn:
        conn.execute(text(sql))
    print("[OK] ensured table model_feature_snapshots")


# -----------------------------
# SQLite -> MySQL
# -----------------------------

def import_sqlite_business_tables(engine: Engine, sqlite_path: Path, batch_size: int) -> None:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

    sqlite_tables = set(get_sqlite_tables(sqlite_path))
    print(f"[INFO] SQLite tables: {sorted(sqlite_tables)}")

    # 按依赖顺序导入。只导入当前 MySQL 中已有且 SQLite 中也存在的表。
    table_keys = {
        "stocks": ["ticker"],
        "price_data": ["ticker", "trading_date"],
        "technical_indicators": ["ticker", "trading_date"],
        "sentiment_daily": ["ticker", "trading_date"],
        "news_data": ["ticker", "url"],
        # model_versions 由 register_model_versions 单独处理，避免旧模型覆盖问题。
    }

    for table, keys in table_keys.items():
        if table not in sqlite_tables:
            print(f"[SKIP] SQLite table not found: {table}")
            continue
        if not table_exists(engine, table):
            print(f"[SKIP] MySQL table not found: {table}")
            continue

        df = read_sqlite_table(sqlite_path, table)
        if df.empty:
            print(f"[SKIP] {table}: empty")
            continue

        # 防止 news_data 没 url 时无法用 ticker+url 去重
        if table == "news_data" and "url" not in df.columns:
            if "news_id" in df.columns:
                keys = ["news_id"]
            else:
                keys = ["ticker", "title", "publish_time"]

        upsert_dataframe_by_keys(engine, table, df, keys, batch_size=batch_size)


# -----------------------------
# dataset_h5_v1.csv -> model_feature_snapshots
# -----------------------------

def import_training_feature_snapshots(
    engine: Engine,
    training_dir: Path,
    dataset_version: str,
    batch_size: int,
    limit: Optional[int] = None,
) -> None:
    dataset_path = training_dir / "dataset_h5_v1.csv"
    feature_path = training_dir / "feature_columns_h5_v1.json"

    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset_h5_v1.csv not found: {dataset_path}")
    if not feature_path.exists():
        raise FileNotFoundError(f"feature_columns_h5_v1.json not found: {feature_path}")

    with open(feature_path, "r", encoding="utf-8") as f:
        feature_columns = json.load(f)

    print(f"[INFO] feature columns count={len(feature_columns)}")
    print(f"[INFO] dataset path={dataset_path}")

    ensure_model_feature_snapshots(engine)

    # 先读 header，判断列名
    header = pd.read_csv(dataset_path, nrows=0)
    columns = list(header.columns)

    ticker_col = "ticker" if "ticker" in columns else None
    base_date_col = "base_trading_date" if "base_trading_date" in columns else None

    if not ticker_col or not base_date_col:
        raise RuntimeError(f"dataset must contain ticker and base_trading_date. columns={columns[:20]}...")

    target_date_col = "target_date_d5" if "target_date_d5" in columns else None
    current_price_col = None
    for cand in ["current_price", "close", "base_close", "close_t"]:
        if cand in columns:
            current_price_col = cand
            break

    target_cols = [
        c for c in columns
        if c.startswith("target_") or c.startswith("label") or c in ("y", "future_return", "future_return_d5")
    ]

    inserted = updated = skipped = 0
    total_read = 0

    insert_cols = [
        "dataset_version",
        "ticker",
        "base_trading_date",
        "target_date_d5",
        "current_price",
        "features_json",
        "target_json",
        "raw_row_json",
    ]

    insert_sql = text("""
        INSERT INTO model_feature_snapshots
        (dataset_version, ticker, base_trading_date, target_date_d5, current_price, features_json, target_json, raw_row_json)
        VALUES
        (:dataset_version, :ticker, :base_trading_date, :target_date_d5, :current_price, :features_json, :target_json, :raw_row_json)
        ON DUPLICATE KEY UPDATE
          target_date_d5 = VALUES(target_date_d5),
          current_price = VALUES(current_price),
          features_json = VALUES(features_json),
          target_json = VALUES(target_json),
          raw_row_json = VALUES(raw_row_json)
    """)

    for chunk in pd.read_csv(dataset_path, chunksize=batch_size):
        if limit is not None:
            remain = limit - total_read
            if remain <= 0:
                break
            if len(chunk) > remain:
                chunk = chunk.iloc[:remain]

        rows = []
        for _, s in chunk.iterrows():
            ticker = normalize_value(s.get(ticker_col))
            base_date = normalize_date_string(s.get(base_date_col))
            if ticker is None or base_date is None:
                skipped += 1
                continue

            features = {}
            missing_features = []
            for col in feature_columns:
                if col in chunk.columns:
                    features[col] = normalize_value(s.get(col))
                else:
                    features[col] = None
                    missing_features.append(col)

            target_json = {c: normalize_value(s.get(c)) for c in target_cols if c in chunk.columns}
            raw_small = {
                "ticker": ticker,
                "base_trading_date": base_date,
                "source_dataset": "dataset_h5_v1.csv",
            }

            rows.append({
                "dataset_version": dataset_version,
                "ticker": ticker,
                "base_trading_date": base_date,
                "target_date_d5": normalize_date_string(s.get(target_date_col)) if target_date_col else None,
                "current_price": normalize_value(s.get(current_price_col)) if current_price_col else None,
                "features_json": json.dumps(features, ensure_ascii=False),
                "target_json": json.dumps(target_json, ensure_ascii=False) if target_json else None,
                "raw_row_json": json.dumps(raw_small, ensure_ascii=False),
            })

        if rows:
            with engine.begin() as conn:
                conn.execute(insert_sql, rows)
            # ON DUPLICATE 情况不好区分 insert/update，这里按 processed 统计。
            inserted += len(rows)

        total_read += len(chunk)
        print(f"  feature snapshots progress: processed={total_read}, written={inserted}, skipped={skipped}")

    print(f"[DONE] model_feature_snapshots: processed={total_read}, written={inserted}, skipped={skipped}")


# -----------------------------
# 注册 v1.2 模型版本
# -----------------------------

def upsert_model_version(engine: Engine, payload: Dict[str, Any]) -> None:
    table = "model_versions"
    cols = get_mysql_columns(engine, table)
    if not cols:
        print("[SKIP] model_versions table not found")
        return

    usable = {k: normalize_value(v) for k, v in payload.items() if k in cols}

    # 先按 version_name 查是否存在，再 update/insert，避免依赖唯一索引。
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM model_versions WHERE version_name = :version_name LIMIT 1"),
            {"version_name": usable["version_name"]},
        ).first()

        if row:
            update_cols = [k for k in usable.keys() if k not in ("id", "version_name")]
            set_sql = ", ".join(f"`{k}` = :{k}" for k in update_cols)
            params = dict(usable)
            conn.execute(
                text(f"UPDATE model_versions SET {set_sql} WHERE version_name = :version_name"),
                params,
            )
            print(f"[MODEL] updated {usable['version_name']}")
        else:
            col_sql = ", ".join(f"`{k}`" for k in usable.keys())
            val_sql = ", ".join(f":{k}" for k in usable.keys())
            conn.execute(
                text(f"INSERT INTO model_versions ({col_sql}) VALUES ({val_sql})"),
                usable,
            )
            print(f"[MODEL] inserted {usable['version_name']}")


def register_v12_models(engine: Engine) -> None:
    if not table_exists(engine, "model_versions"):
        print("[SKIP] model_versions table not found")
        return

    with engine.begin() as conn:
        # 只关闭已有 classifier / regressor / aux_classifier，避免影响未来其他类型。
        conn.execute(text("""
            UPDATE model_versions
            SET is_active = 0
            WHERE model_type IN ('classifier', 'regressor', 'aux_classifier')
        """))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    models = [
        {
            "version_name": "finsight_cls_abs_h15_v1.2",
            "model_type": "classifier",
            "algorithm": "LogisticRegression",
            "horizon_days": 15,
            "model_path": "artifacts/models/classifier/finsight_cls_abs_h15_v1.2/model.joblib",
            "feature_version": "feature_v12_50d",
            "accuracy": 0.606938,
            "f1_score": 0.588123,
            "mae": None,
            "rmse": None,
            "is_active": 1,
            "created_at": now,
        },
        {
            "version_name": "finsight_cls_action1p5_h10_v1.2",
            "model_type": "aux_classifier",
            "algorithm": "RidgeClassifier",
            "horizon_days": 10,
            "model_path": "artifacts/models/classifier/finsight_cls_action1p5_h10_v1.2/model.joblib",
            "feature_version": "feature_v12_50d",
            "accuracy": None,
            "f1_score": None,
            "mae": None,
            "rmse": None,
            "is_active": 1,
            "created_at": now,
        },
        {
            "version_name": "finsight_reg_return_path_v1.2",
            "model_type": "regressor",
            "algorithm": "ExtraTreesRegressor",
            "horizon_days": 5,
            "model_path": "artifacts/models/regressor/finsight_reg_return_path_v1.2/model.joblib",
            "feature_version": "feature_v12_50d",
            "accuracy": None,
            "f1_score": None,
            "mae": 0.027229,
            "rmse": 0.040618,
            "is_active": 1,
            "created_at": now,
        },
    ]

    for payload in models:
        upsert_model_version(engine, payload)

    print("[DONE] registered v1.2 model_versions")


# -----------------------------
# 主程序
# -----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Import Member B v1.2 real data into MySQL.")
    parser.add_argument(
        "--sqlite-db",
        default="/external_outputs/backtest_after_20250520/finsight_price_backtest_after_20250520.db",
        help="Path to SQLite DB that contains price_data and technical_indicators.",
    )
    parser.add_argument(
        "--training-dir",
        default="/external_outputs/expanded_60_no_weak10_news48_quality_fundamental/training_dataset",
        help="Path to training_dataset directory containing dataset_h5_v1.csv and feature_columns_h5_v1.json.",
    )
    parser.add_argument("--dataset-version", default="h5_v1_member_b_v1_2")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--limit-feature-rows", type=int, default=None, help="For testing only.")
    parser.add_argument("--skip-sqlite", action="store_true")
    parser.add_argument("--skip-feature-snapshots", action="store_true")
    parser.add_argument("--skip-model-versions", action="store_true")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_db)
    training_dir = Path(args.training_dir)

    print("========== Member B Real Data Import ==========")
    print(f"sqlite_db     = {sqlite_path}")
    print(f"training_dir  = {training_dir}")
    print(f"dataset_ver   = {args.dataset_version}")
    print(f"batch_size    = {args.batch_size}")
    print("================================================")

    engine = create_engine(get_database_url(), pool_pre_ping=True, future=True)

    if not args.skip_sqlite:
        import_sqlite_business_tables(engine, sqlite_path, batch_size=args.batch_size)

    if not args.skip_feature_snapshots:
        import_training_feature_snapshots(
            engine,
            training_dir=training_dir,
            dataset_version=args.dataset_version,
            batch_size=args.batch_size,
            limit=args.limit_feature_rows,
        )

    if not args.skip_model_versions:
        register_v12_models(engine)

    print("[ALL DONE] import completed.")


if __name__ == "__main__":
    main()
