from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as exc:  # noqa: BLE001
    plt = None
    MATPLOTLIB_IMPORT_ERROR = exc
else:
    MATPLOTLIB_IMPORT_ERROR = None

from app.db.session import SessionLocal
from app.models.all_models import (
    BacktestDailyPosition,
    BacktestEventLog,
    BacktestRun,
    BacktestTrade,
    PortfolioSnapshot,
    User,
    UserSimulatedPosition,
)
from app.services.backtest_service import _execute_backtest_run_in_session


def parse_range(raw: str) -> list[float]:
    """解析形如 50:76:2 的范围，含起点，不保证含终点。"""
    parts = raw.split(":")

    if len(parts) != 3:
        raise ValueError("范围格式必须是 start:stop:step，例如 50:76:2")

    start = float(parts[0])
    stop = float(parts[1])
    step = float(parts[2])

    if step <= 0:
        raise ValueError("step 必须大于 0")

    values = []
    current = start

    while current < stop:
        values.append(round(current, 6))
        current += step

    return values


def parse_tickers(raw_items: list[str]) -> list[str]:
    """支持 --tickers AAPL MSFT 或 --tickers AAPL,MSFT 两种写法。"""
    tickers = []

    for item in raw_items:
        for part in item.split(","):
            ticker = part.strip().upper()
            if ticker and ticker not in tickers:
                tickers.append(ticker)

    if not tickers:
        raise ValueError("股票池不能为空")

    return tickers


def safe_float(value: Any) -> float | None:
    """把 Decimal 等数据库数值安全转换成 float。"""
    if value is None:
        return None
    return float(value)


def objective_value(row: dict[str, Any], objective: str) -> float:
    """根据指定目标函数返回排序分数，数值越大越好。"""
    if row.get("status") != "finished" or row.get("final_equity") is None:
        return -999999.0

    total_return = row.get("total_return") or 0.0
    annual_return = row.get("annual_return") or 0.0
    sharpe_ratio = row.get("sharpe_ratio") or 0.0
    max_drawdown = row.get("max_drawdown") or 0.0
    trade_count = row.get("trade_count") or 0

    if objective == "total_return":
        return total_return

    if objective == "annual_return":
        return annual_return

    if objective == "sharpe_ratio":
        return sharpe_ratio

    if objective == "risk_adjusted":
        # 风险调整分数不是金融标准公式，只用于参数筛选时惩罚高回撤和过度交易。
        return total_return + 0.10 * sharpe_ratio - 0.70 * abs(max_drawdown) - 0.0002 * trade_count

    raise ValueError(f"不支持的 objective: {objective}")


def delete_run_outputs(db, run_id: int, delete_run: bool = True) -> None:
    """删除参数扫描产生的回测明细，避免长期扫描污染数据库。"""
    db.query(UserSimulatedPosition).filter(
        UserSimulatedPosition.source_run_id == run_id
    ).delete(synchronize_session=False)
    db.query(BacktestDailyPosition).filter(
        BacktestDailyPosition.run_id == run_id
    ).delete(synchronize_session=False)
    db.query(BacktestTrade).filter(
        BacktestTrade.run_id == run_id
    ).delete(synchronize_session=False)
    db.query(PortfolioSnapshot).filter(
        PortfolioSnapshot.run_id == run_id
    ).delete(synchronize_session=False)
    db.query(BacktestEventLog).filter(
        BacktestEventLog.run_id == run_id
    ).delete(synchronize_session=False)

    if delete_run:
        db.query(BacktestRun).filter(BacktestRun.id == run_id).delete(synchronize_session=False)

    db.commit()


def create_param_scan_run(
    db,
    *,
    user_id: int,
    run_name: str,
    tickers: list[str],
    start_date,
    end_date,
    initial_cash: float,
    benchmark: str | None,
    max_holding_count: int,
    max_position_ratio: float,
    fee_rate: float,
    min_cash_reserve_ratio: float,
    stock_score_threshold: float,
    situation_score_threshold: float,
) -> BacktestRun:
    """直接创建内部参数扫描 run，不走 HTTP API，减少网络和鉴权开销。"""
    run = BacktestRun(
        user_id=user_id,
        run_name=run_name,
        tickers_json=tickers,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        benchmark=benchmark,
        strategy_params_json={
            "forecast_days": 5,
            "max_position_ratio": max_position_ratio,
            "max_holding_count": max_holding_count,
            "fee_rate": fee_rate,
            "save_daily_positions": True,
            "save_event_logs": False,
            "animation_mode": "fast",
            "buy_score_threshold": stock_score_threshold,
            "buy_situation_threshold": situation_score_threshold,
            "min_cash_reserve_ratio": min_cash_reserve_ratio,
        },
        status="pending",
        trading_days_done=0,
        progress=0.0,
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    return run

def execute_scan_run(db, run_id: int) -> BacktestRun:
    """参数扫描必须在同一个数据库 Session 中执行并读取结果。

    原因：
    1. execute_backtest_run() 内部会新建 SessionLocal()。
    2. 外层扫描脚本长期持有同一个 Session。
    3. 在 MySQL 默认事务隔离级别下，外层 Session 可能读到旧的 pending 快照。
    4. 因此这里直接调用同 Session 版本的执行器，确保执行和读取结果一致。
    """
    db.rollback()
    db.expire_all()

    _execute_backtest_run_in_session(db, run_id)

    db.rollback()
    db.expire_all()

    run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()

    if not run:
        raise RuntimeError(f"回测任务不存在，run_id={run_id}")

    return run

def collect_run_result(
    db,
    *,
    run: BacktestRun,
    stock_score_threshold: float,
    situation_score_threshold: float,
    objective: str,
) -> dict[str, Any]:
    """从 backtest_runs 和关联表汇总单组参数的测试结果。"""
    final_positions_count = (
        db.query(UserSimulatedPosition)
        .filter(UserSimulatedPosition.source_run_id == run.id)
        .count()
    )
    snapshot_count = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.run_id == run.id)
        .count()
    )

    row = {
        "run_id": run.id,
        "status": run.status,
        "stock_score_threshold": stock_score_threshold,
        "situation_score_threshold": situation_score_threshold,
        "start_date": run.start_date.isoformat() if run.start_date else None,
        "end_date": run.end_date.isoformat() if run.end_date else None,
        "final_snapshot_date": run.final_snapshot_date.isoformat() if run.final_snapshot_date else None,
        "initial_cash": safe_float(run.initial_cash),
        "final_equity": safe_float(run.final_equity),
        "profit": (
            safe_float(run.final_equity) - safe_float(run.initial_cash)
            if run.final_equity is not None and run.initial_cash is not None
            else None
        ),
        "total_return": run.total_return,
        "annual_return": run.annual_return,
        "max_drawdown": run.max_drawdown,
        "win_rate": run.win_rate,
        "trade_count": run.trade_count,
        "sharpe_ratio": run.sharpe_ratio,
        "benchmark_return": run.benchmark_return,
        "snapshot_count": snapshot_count,
        "final_positions_count": final_positions_count,
        "error_message": run.error_message,
    }

    row["objective"] = objective
    row["objective_score"] = objective_value(row, objective)

    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """写入完整 CSV，便于 Excel / pandas / 前端后续分析。"""
    if not rows:
        return

    fieldnames = list(rows[0].keys())

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_best_json(path: Path, best_row: dict[str, Any]) -> None:
    """保存最佳参数，便于后续直接复用。"""
    with path.open("w", encoding="utf-8") as f:
        json.dump(best_row, f, ensure_ascii=False, indent=2)


def plot_heatmap(
    *,
    output_path: Path,
    rows: list[dict[str, Any]],
    value_key: str,
    title: str,
) -> None:
    """使用 matplotlib 生成二维参数热力图，不依赖 seaborn。"""
    if plt is None:
        raise RuntimeError(f"matplotlib 导入失败，无法生成图表：{MATPLOTLIB_IMPORT_ERROR}")

    stock_values = sorted({float(r["stock_score_threshold"]) for r in rows})
    situation_values = sorted({float(r["situation_score_threshold"]) for r in rows})

    matrix = []
    value_map = {
        (float(r["stock_score_threshold"]), float(r["situation_score_threshold"])): r.get(value_key)
        for r in rows
    }

    for situation in situation_values:
        matrix_row = []
        for stock in stock_values:
            value = value_map.get((stock, situation))
            matrix_row.append(float(value) if value is not None else math.nan)
        matrix.append(matrix_row)

    fig, ax = plt.subplots(figsize=(max(8, len(stock_values) * 0.45), max(6, len(situation_values) * 0.35)))
    image = ax.imshow(matrix, aspect="auto", origin="lower")

    ax.set_title(title)
    ax.set_xlabel("buy_score_threshold")
    ax.set_ylabel("buy_situation_threshold")
    ax.set_xticks(range(len(stock_values)))
    ax.set_xticklabels([str(int(v)) if v.is_integer() else str(v) for v in stock_values], rotation=45)
    ax.set_yticks(range(len(situation_values)))
    ax.set_yticklabels([str(int(v)) if v.is_integer() else str(v) for v in situation_values])

    fig.colorbar(image, ax=ax)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_top_bar(
    *,
    output_path: Path,
    rows: list[dict[str, Any]],
    value_key: str,
    title: str,
    top_n: int = 20,
) -> None:
    """生成 Top N 参数组合柱状图，方便快速筛选候选参数。"""
    if plt is None:
        raise RuntimeError(f"matplotlib 导入失败，无法生成图表：{MATPLOTLIB_IMPORT_ERROR}")

    top_rows = sorted(rows, key=lambda r: r.get(value_key) or -999999, reverse=True)[:top_n]
    labels = [
        f"S{r['stock_score_threshold']}/Q{r['situation_score_threshold']}"
        for r in top_rows
    ]
    values = [float(r.get(value_key) or 0) for r in top_rows]

    fig, ax = plt.subplots(figsize=(max(10, top_n * 0.5), 6))
    ax.bar(range(len(values)), values)
    ax.set_title(title)
    ax.set_xlabel("parameter pair")
    ax.set_ylabel(value_key)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=60, ha="right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize backtest buy score and situation score thresholds.")

    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"])
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--initial-cash", type=float, default=10000)
    parser.add_argument("--benchmark", default="SPY")

    parser.add_argument("--max-holding-count", type=int, default=5)
    parser.add_argument("--max-position-ratio", type=float, default=0.2)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--min-cash-reserve-ratio", type=float, default=0.02)

    parser.add_argument("--stock-scores", default="50:76:2")
    parser.add_argument("--situation-scores", default="35:66:2")
    parser.add_argument("--objective", choices=["total_return", "annual_return", "sharpe_ratio", "risk_adjusted"], default="total_return")

    parser.add_argument("--output-dir", default="outputs/backtest_threshold_optimization")
    parser.add_argument("--keep-runs", action="store_true", help="保留所有参数扫描产生的回测任务和明细")
    parser.add_argument("--keep-best-run", action="store_true", help="只保留最佳参数对应的回测任务和明细")
    parser.add_argument("--top-n", type=int, default=20)

    args = parser.parse_args()

    tickers = parse_tickers(args.tickers)
    stock_scores = parse_range(args.stock_scores)
    situation_scores = parse_range(args.situation_scores)

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) / f"scan_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()

    try:
        user = db.query(User).filter(User.id == args.user_id).first()
        if not user:
            raise RuntimeError(f"找不到 user_id={args.user_id}，请改用实际存在的用户 ID。")

        rows: list[dict[str, Any]] = []
        best_row: dict[str, Any] | None = None
        best_run_id: int | None = None
        total_jobs = len(stock_scores) * len(situation_scores)
        current_job = 0

        print("== Backtest threshold optimization started ==")
        print("tickers:", tickers)
        print("date range:", args.start_date, "to", args.end_date)
        print("stock_scores:", stock_scores)
        print("situation_scores:", situation_scores)
        print("total jobs:", total_jobs)
        print("output_dir:", output_dir)

        for stock_score in stock_scores:
            for situation_score in situation_scores:
                current_job += 1

                run_name = (
                    f"[PARAM_SCAN] M7 {args.start_date} to {args.end_date} "
                    f"S{stock_score}_Q{situation_score}"
                )

                print(
                    f"[{current_job}/{total_jobs}] running "
                    f"buy_score_threshold={stock_score}, "
                    f"buy_situation_threshold={situation_score}",
                    flush=True,
                )

                run = create_param_scan_run(
                    db,
                    user_id=args.user_id,
                    run_name=run_name,
                    tickers=tickers,
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=args.initial_cash,
                    benchmark=args.benchmark,
                    max_holding_count=args.max_holding_count,
                    max_position_ratio=args.max_position_ratio,
                    fee_rate=args.fee_rate,
                    min_cash_reserve_ratio=args.min_cash_reserve_ratio,
                    stock_score_threshold=stock_score,
                    situation_score_threshold=situation_score,
                )

                run_id = run.id
                run = execute_scan_run(db, run_id)

                row = collect_run_result(
                    db,
                    run=run,
                    stock_score_threshold=stock_score,
                    situation_score_threshold=situation_score,
                    objective=args.objective,
                )

                if row["status"] != "finished":
                    print(
                        f"    WARNING: run_id={run.id} 未完成，status={row['status']}，"
                        f"error_message={row['error_message']}",
                        flush=True,
                    )

                rows.append(row)

                is_new_best = best_row is None or row["objective_score"] > best_row["objective_score"]

                if is_new_best:
                    old_best_run_id = best_run_id
                    best_row = row
                    best_run_id = run.id

                    if (
                        old_best_run_id is not None
                        and not args.keep_runs
                        and args.keep_best_run
                    ):
                        delete_run_outputs(db, old_best_run_id, delete_run=True)

                if not args.keep_runs:
                    should_keep_current = args.keep_best_run and best_run_id == run.id
                    if not should_keep_current:
                        delete_run_outputs(db, run.id, delete_run=True)

                write_csv(output_dir / "results.csv", rows)
                write_csv(
                    output_dir / "results_sorted_by_objective.csv",
                    sorted(rows, key=lambda r: r["objective_score"], reverse=True),
                )

                if best_row:
                    save_best_json(output_dir / "best_params.json", best_row)

                print(
                    f"    status={row['status']} "
                    f"final_equity={row['final_equity']} "
                    f"total_return={row['total_return']} "
                    f"max_drawdown={row['max_drawdown']} "
                    f"sharpe={row['sharpe_ratio']} "
                    f"objective_score={row['objective_score']}",
                    flush=True,
                )

        sorted_rows = sorted(rows, key=lambda r: r["objective_score"], reverse=True)
        write_csv(output_dir / "results.csv", rows)
        write_csv(output_dir / "results_sorted_by_objective.csv", sorted_rows)

        if sorted_rows:
            best_row = sorted_rows[0]
            save_best_json(output_dir / "best_params.json", best_row)

        if rows:
            try:
                plot_heatmap(
                    output_path=output_dir / "heatmap_total_return.png",
                    rows=rows,
                    value_key="total_return",
                    title="Total return by thresholds",
                )
                plot_heatmap(
                    output_path=output_dir / "heatmap_sharpe_ratio.png",
                    rows=rows,
                    value_key="sharpe_ratio",
                    title="Sharpe ratio by thresholds",
                )
                plot_heatmap(
                    output_path=output_dir / "heatmap_max_drawdown.png",
                    rows=rows,
                    value_key="max_drawdown",
                    title="Max drawdown by thresholds",
                )
                plot_top_bar(
                    output_path=output_dir / "top_objective_scores.png",
                    rows=rows,
                    value_key="objective_score",
                    title=f"Top {args.top_n} parameter pairs by {args.objective}",
                    top_n=args.top_n,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"图表生成失败，但 CSV 已生成：{exc}", flush=True)

        print("\n== Optimization finished ==")
        print("output_dir:", output_dir)

        if best_row:
            print("best_params:")
            print(json.dumps(best_row, ensure_ascii=False, indent=2))

    finally:
        db.close()


if __name__ == "__main__":
    main()