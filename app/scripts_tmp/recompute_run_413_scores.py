from __future__ import annotations

import os
from datetime import datetime, date
from typing import Any

from app.db.session import SessionLocal
from app.models.all_models import (
    BacktestRun,
    BacktestDailyPosition,
    PriceData,
    TechnicalIndicator,
    SentimentDaily,
)
from app.services.model_service import load_active_model
from app.services.backtest_service import (
    RuntimeBacktestModelBundle,
    _build_signal,
)


RUN_ID = int(os.environ.get("RUN_ID", "413"))
TICKER_FILTER = os.environ.get("TICKER", "").strip().upper() or None
START_DATE_TEXT = os.environ.get("START_DATE", "").strip()
END_DATE_TEXT = os.environ.get("END_DATE", "").strip()
APPLY = os.environ.get("APPLY", "0").strip() == "1"


def parse_date(text: str) -> date | None:
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


def safe_round(value: Any, ndigits: int = 4) -> float | None:
    if value is None:
        return None

    try:
        return round(float(value), ndigits)
    except Exception:
        return None


def main() -> None:
    start_date = parse_date(START_DATE_TEXT)
    end_date = parse_date(END_DATE_TEXT)

    db = SessionLocal()

    try:
        run = db.query(BacktestRun).filter(BacktestRun.id == RUN_ID).first()

        if not run:
            raise SystemExit(f"BacktestRun id={RUN_ID} not found.")

        params: dict[str, Any] = dict(run.strategy_params_json or {})
        forecast_days = int(params.get("forecast_days") or 5)

        print("============================================================")
        print("Recompute backtest daily scores")
        print("============================================================")
        print(f"RUN_ID={RUN_ID}")
        print(f"run_name={run.run_name}")
        print(f"status={run.status}")
        print(f"tickers={run.tickers_json}")
        print(f"date_range={run.start_date} to {run.end_date}")
        print(f"forecast_days={forecast_days}")
        print(f"TICKER_FILTER={TICKER_FILTER}")
        print(f"START_DATE={start_date}")
        print(f"END_DATE={end_date}")
        print(f"APPLY={APPLY}")
        print("")
        print("Algorithm alignment:")
        print("  using app.services.backtest_service._build_signal()")
        print("  _build_signal() calls the same model signal chain used by backtest")
        print("  no custom scoring formula is introduced here")
        print("")

        # 与回测服务保持一致：一次加载 active model，避免每一行重复加载。
        classifier_model, classifier_match_status = load_active_model(
            db,
            "classifier",
            forecast_days,
        )

        reg_model, reg_match_status = load_active_model(
            db,
            "regressor",
            forecast_days,
        )

        aux_model = None
        aux_match_status = None

        try:
            aux_model, aux_match_status = load_active_model(
                db,
                "aux_classifier",
                10,
            )
        except Exception as exc:
            print(f"[WARN] aux_classifier not loaded: {exc}")

        model_bundle = RuntimeBacktestModelBundle(
            classifier_model=classifier_model,
            classifier_match_status=classifier_match_status,
            reg_model=reg_model,
            reg_match_status=reg_match_status,
            aux_model=aux_model,
            aux_match_status=aux_match_status,
        )

        print("Loaded models:")
        print(f"  classifier={classifier_model.version.version_name} match={classifier_match_status}")
        print(f"  regressor={reg_model.version.version_name} match={reg_match_status}")
        print(f"  aux={aux_model.version.version_name if aux_model else None} match={aux_match_status}")
        print("")

        q = db.query(BacktestDailyPosition).filter(
            BacktestDailyPosition.run_id == RUN_ID
        )

        if TICKER_FILTER:
            q = q.filter(BacktestDailyPosition.ticker == TICKER_FILTER)

        if start_date:
            q = q.filter(BacktestDailyPosition.snapshot_date >= start_date)

        if end_date:
            q = q.filter(BacktestDailyPosition.snapshot_date <= end_date)

        rows = (
            q.order_by(
                BacktestDailyPosition.snapshot_date.asc(),
                BacktestDailyPosition.ticker.asc(),
                BacktestDailyPosition.id.asc(),
            )
            .all()
        )

        print(f"rows_to_check={len(rows)}")
        print("")

        changed = 0
        unchanged = 0
        model_count = 0
        fallback_count = 0
        error_count = 0
        feature_base_still_stale = 0

        for row in rows:
            price_row = (
                db.query(PriceData)
                .filter(
                    PriceData.ticker == row.ticker,
                    PriceData.trading_date == row.snapshot_date,
                )
                .first()
            )

            indicator = (
                db.query(TechnicalIndicator)
                .filter(
                    TechnicalIndicator.ticker == row.ticker,
                    TechnicalIndicator.trading_date == row.snapshot_date,
                )
                .first()
            )

            sentiment = (
                db.query(SentimentDaily)
                .filter(
                    SentimentDaily.ticker == row.ticker,
                    SentimentDaily.trading_date == row.snapshot_date,
                )
                .first()
            )

            try:
                # 关键：这里直接调用后端原 backtest 的 _build_signal()
                # 不引入任何新的打分算法。
                signal = _build_signal(
                    db=db,
                    ticker=row.ticker,
                    trading_day=row.snapshot_date,
                    price_row=price_row,
                    indicator=indicator,
                    sentiment=sentiment,
                    model_bundle=model_bundle,
                    forecast_days=forecast_days,
                )
            except Exception as exc:
                error_count += 1
                print(
                    "[ERROR]",
                    row.snapshot_date,
                    row.ticker,
                    "signal recompute failed:",
                    repr(exc),
                )
                continue

            source = signal.get("signal_source")

            if source == "model":
                model_count += 1
            else:
                fallback_count += 1

            feature_base = signal.get("feature_base_trading_date")

            # 如果补完 feature 后仍然不是当天，说明还有部分日期未补齐。
            if str(feature_base) != str(row.snapshot_date):
                feature_base_still_stale += 1

            new_stock_score = signal.get("stock_score")
            new_situation_score = signal.get("situation_score")

            old_stock_score = row.stock_score
            old_situation_score = row.situation_score

            old_stock_r = safe_round(old_stock_score)
            old_situation_r = safe_round(old_situation_score)
            new_stock_r = safe_round(new_stock_score)
            new_situation_r = safe_round(new_situation_score)

            is_changed = (
                old_stock_r != new_stock_r
                or old_situation_r != new_situation_r
            )

            if is_changed:
                changed += 1

                print(
                    "[CHANGE]",
                    row.snapshot_date,
                    row.ticker,
                    f"stock {old_stock_score} -> {new_stock_score}",
                    f"situation {old_situation_score} -> {new_situation_score}",
                    f"source={source}",
                    f"feature_base={feature_base}",
                    f"model_error={signal.get('model_error')}",
                )
            else:
                unchanged += 1

            if APPLY:
                old_signal = row.signal_json if isinstance(row.signal_json, dict) else {}

                row.stock_score = new_stock_score
                row.situation_score = new_situation_score
                row.signal_json = {
                    **old_signal,
                    **signal,
                    "score_recomputed": True,
                    "score_recomputed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    "score_recomputed_run_id": RUN_ID,
                    "score_recomputed_script": "app/scripts_tmp/recompute_run_413_scores.py",
                }

        print("")
        print("============================================================")
        print("Summary")
        print("============================================================")
        print(f"rows_checked={len(rows)}")
        print(f"changed_rows={changed}")
        print(f"unchanged_rows={unchanged}")
        print(f"model_signal_rows={model_count}")
        print(f"fallback_rows={fallback_count}")
        print(f"error_rows={error_count}")
        print(f"feature_base_still_stale_rows={feature_base_still_stale}")
        print(f"APPLY={APPLY}")

        if APPLY:
            db.commit()
            print("DB updated and committed.")
        else:
            db.rollback()
            print("Dry-run only. No DB changes committed.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
