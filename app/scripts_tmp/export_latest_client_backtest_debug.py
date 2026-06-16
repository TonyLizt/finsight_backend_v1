from __future__ import annotations

import csv
import json
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models.all_models import (
    BacktestRun,
    PortfolioSnapshot,
    BacktestDailyPosition,
    BacktestTrade,
    BacktestEventLog,
)


def plain(value: Any) -> Any:
    """Convert DB values into CSV / JSON friendly values."""
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    return value


def json_plain(value: Any) -> Any:
    """Convert nested DB values for json.dump."""
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, date):
        return value.isoformat()

    return value


def write_csv(path: Path, rows: list[Any], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            writer.writerow({field: plain(getattr(row, field, None)) for field in fields})


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=json_plain)


def main() -> None:
    db = SessionLocal()

    try:
        # 最近一次「用戶端 / 前端」建立的回測：
        # 排除 [PARAM_SCAN] 掃參任務。
        run = (
            db.query(BacktestRun)
            .filter(
                or_(
                    BacktestRun.run_name.is_(None),
                    ~BacktestRun.run_name.like("[PARAM_SCAN]%"),
                )
            )
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .first()
        )

        if not run:
            raise SystemExit("No client-created backtest run found.")

        run_id = run.id
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("outputs/backtest_debug") / f"latest_client_run_{run_id}_{timestamp}"
        out_dir.mkdir(parents=True, exist_ok=True)

        snapshots = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.run_id == run_id)
            .order_by(PortfolioSnapshot.snapshot_date.asc())
            .all()
        )

        positions = (
            db.query(BacktestDailyPosition)
            .filter(BacktestDailyPosition.run_id == run_id)
            .order_by(
                BacktestDailyPosition.snapshot_date.asc(),
                BacktestDailyPosition.ticker.asc(),
                BacktestDailyPosition.buy_date.asc(),
            )
            .all()
        )

        trades = (
            db.query(BacktestTrade)
            .filter(BacktestTrade.run_id == run_id)
            .order_by(
                BacktestTrade.trade_date.asc(),
                BacktestTrade.id.asc(),
            )
            .all()
        )

        event_logs = (
            db.query(BacktestEventLog)
            .filter(BacktestEventLog.run_id == run_id)
            .order_by(
                BacktestEventLog.trading_date.asc(),
                BacktestEventLog.log_seq.asc(),
                BacktestEventLog.id.asc(),
            )
            .all()
        )

        summary = {
            "run_id": run.id,
            "user_id": run.user_id,
            "run_name": run.run_name,
            "status": run.status,
            "tickers": run.tickers_json,
            "start_date": plain(run.start_date),
            "end_date": plain(run.end_date),
            "initial_cash": plain(run.initial_cash),
            "benchmark": run.benchmark,
            "strategy_params": run.strategy_params_json,
            "current_date": plain(run.current_date),
            "trading_days_total": run.trading_days_total,
            "trading_days_done": run.trading_days_done,
            "progress": run.progress,
            "final_snapshot_date": plain(run.final_snapshot_date),
            "final_equity": plain(run.final_equity),
            "total_return": run.total_return,
            "annual_return": run.annual_return,
            "max_drawdown": run.max_drawdown,
            "win_rate": run.win_rate,
            "trade_count": run.trade_count,
            "sharpe_ratio": run.sharpe_ratio,
            "benchmark_return": run.benchmark_return,
            "error_message": run.error_message,
            "created_at": plain(run.created_at),
            "started_at": plain(run.started_at),
            "finished_at": plain(run.finished_at),
            "snapshot_count": len(snapshots),
            "daily_position_count": len(positions),
            "trade_count_rows": len(trades),
            "event_log_count": len(event_logs),
        }

        write_json(out_dir / "summary.json", summary)

        write_csv(
            out_dir / "daily_portfolio.csv",
            snapshots,
            [
                "snapshot_date",
                "cash",
                "stock_value",
                "total_value",
                "daily_return",
                "total_return",
                "annual_return",
                "max_drawdown",
                "win_rate",
                "trade_count",
                "sharpe_ratio",
                "benchmark_value",
                "benchmark_return",
            ],
        )

        write_csv(
            out_dir / "daily_positions.csv",
            positions,
            [
                "snapshot_date",
                "ticker",
                "buy_date",
                "quantity",
                "current_price",
                "cost_price",
                "cost_amount",
                "stock_value",
                "daily_pnl",
                "daily_pnl_pct",
                "total_pnl",
                "total_pnl_pct",
                "position_ratio",
                "stock_score",
                "situation_score",
                "signal_json",
            ],
        )

        write_csv(
            out_dir / "trades.csv",
            trades,
            [
                "trade_date",
                "ticker",
                "side",
                "price",
                "quantity",
                "amount",
                "fee",
                "cash_after",
                "position_after",
                "reason",
                "signal_json",
            ],
        )

        write_csv(
            out_dir / "event_logs.csv",
            event_logs,
            [
                "log_seq",
                "log_time",
                "trading_date",
                "level",
                "event_type",
                "ticker",
                "action",
                "message",
                "detail_json",
            ],
        )

        positions_by_date: dict[str, list[BacktestDailyPosition]] = {}
        for p in positions:
            key = plain(p.snapshot_date)
            positions_by_date.setdefault(key, []).append(p)

        trades_by_date: dict[str, list[BacktestTrade]] = {}
        for t in trades:
            key = plain(t.trade_date)
            trades_by_date.setdefault(key, []).append(t)

        logs_by_date: dict[str, list[BacktestEventLog]] = {}
        for e in event_logs:
            key = plain(e.trading_date)
            logs_by_date.setdefault(key, []).append(e)

        report_path = out_dir / "report.log"

        with report_path.open("w", encoding="utf-8") as f:
            f.write("============================================================\n")
            f.write("Latest client-created backtest full debug report\n")
            f.write("============================================================\n")
            f.write(f"generated_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"run_id: {run.id}\n")
            f.write(f"user_id: {run.user_id}\n")
            f.write(f"run_name: {run.run_name}\n")
            f.write(f"status: {run.status}\n")
            f.write(f"tickers: {json.dumps(run.tickers_json, ensure_ascii=False)}\n")
            f.write(f"date_range: {plain(run.start_date)} to {plain(run.end_date)}\n")
            f.write(f"initial_cash: {plain(run.initial_cash)}\n")
            f.write(f"benchmark: {run.benchmark}\n")
            f.write(f"strategy_params: {json.dumps(run.strategy_params_json, ensure_ascii=False, default=json_plain)}\n")
            f.write(f"current_date: {plain(run.current_date)}\n")
            f.write(f"progress: {run.progress}\n")
            f.write(f"final_equity: {plain(run.final_equity)}\n")
            f.write(f"total_return: {run.total_return}\n")
            f.write(f"annual_return: {run.annual_return}\n")
            f.write(f"max_drawdown: {run.max_drawdown}\n")
            f.write(f"win_rate: {run.win_rate}\n")
            f.write(f"trade_count: {run.trade_count}\n")
            f.write(f"sharpe_ratio: {run.sharpe_ratio}\n")
            f.write(f"benchmark_return: {run.benchmark_return}\n")
            f.write(f"snapshot_count: {len(snapshots)}\n")
            f.write(f"daily_position_count: {len(positions)}\n")
            f.write(f"trade_count_rows: {len(trades)}\n")
            f.write(f"event_log_count: {len(event_logs)}\n")
            f.write("\n")

            f.write("============================================================\n")
            f.write("Daily data\n")
            f.write("============================================================\n")

            for s in snapshots:
                d = plain(s.snapshot_date)

                f.write("\n")
                f.write("------------------------------------------------------------\n")
                f.write(f"DATE {d}\n")
                f.write("------------------------------------------------------------\n")
                f.write(
                    "PORTFOLIO "
                    f"cash={plain(s.cash)} "
                    f"stock_value={plain(s.stock_value)} "
                    f"total_value={plain(s.total_value)} "
                    f"daily_return={s.daily_return} "
                    f"total_return={s.total_return} "
                    f"annual_return={s.annual_return} "
                    f"max_drawdown={s.max_drawdown} "
                    f"win_rate={s.win_rate} "
                    f"trade_count={s.trade_count} "
                    f"sharpe_ratio={s.sharpe_ratio} "
                    f"benchmark_value={plain(s.benchmark_value)} "
                    f"benchmark_return={s.benchmark_return}\n"
                )

                day_positions = positions_by_date.get(d, [])
                f.write(f"POSITIONS count={len(day_positions)}\n")

                for p in day_positions:
                    f.write(
                        "  "
                        f"ticker={p.ticker} "
                        f"buy_date={plain(p.buy_date)} "
                        f"qty={p.quantity} "
                        f"current_price={plain(p.current_price)} "
                        f"cost_price={plain(p.cost_price)} "
                        f"cost_amount={plain(p.cost_amount)} "
                        f"stock_value={plain(p.stock_value)} "
                        f"position_ratio={p.position_ratio} "
                        f"daily_pnl={plain(p.daily_pnl)} "
                        f"daily_pnl_pct={p.daily_pnl_pct} "
                        f"total_pnl={plain(p.total_pnl)} "
                        f"total_pnl_pct={p.total_pnl_pct} "
                        f"stock_score={p.stock_score} "
                        f"situation_score={p.situation_score}\n"
                    )

                    if p.signal_json:
                        f.write(
                            "    signal_json="
                            f"{json.dumps(p.signal_json, ensure_ascii=False, default=json_plain)}\n"
                        )

                day_trades = trades_by_date.get(d, [])
                f.write(f"TRADES count={len(day_trades)}\n")

                for t in day_trades:
                    f.write(
                        "  "
                        f"{t.side} {t.ticker} "
                        f"qty={t.quantity} "
                        f"price={plain(t.price)} "
                        f"amount={plain(t.amount)} "
                        f"fee={plain(t.fee)} "
                        f"cash_after={plain(t.cash_after)} "
                        f"position_after={t.position_after} "
                        f"reason={t.reason}\n"
                    )

                    if t.signal_json:
                        f.write(
                            "    signal_json="
                            f"{json.dumps(t.signal_json, ensure_ascii=False, default=json_plain)}\n"
                        )

                day_logs = logs_by_date.get(d, [])
                f.write(f"EVENT_LOGS count={len(day_logs)}\n")

                for e in day_logs:
                    f.write(
                        "  "
                        f"seq={e.log_seq} "
                        f"time={plain(e.log_time)} "
                        f"level={e.level} "
                        f"type={e.event_type} "
                        f"ticker={e.ticker} "
                        f"action={e.action} "
                        f"message={e.message}\n"
                    )

                    if e.detail_json:
                        f.write(
                            "    detail_json="
                            f"{json.dumps(e.detail_json, ensure_ascii=False, default=json_plain)}\n"
                        )

        print("Export finished.")
        print(f"run_id={run.id}")
        print(f"run_name={run.run_name}")
        print(f"status={run.status}")
        print(f"out_dir={out_dir}")
        print(f"report_log={report_path}")
        print(f"daily_portfolio_csv={out_dir / 'daily_portfolio.csv'}")
        print(f"daily_positions_csv={out_dir / 'daily_positions.csv'}")
        print(f"trades_csv={out_dir / 'trades.csv'}")
        print(f"event_logs_csv={out_dir / 'event_logs.csv'}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
