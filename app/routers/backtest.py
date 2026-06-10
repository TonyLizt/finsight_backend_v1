"""Backtest API：异步回测接口第一版。

当前只实现接口与数据库读写壳；真正逐日回测引擎后续接入 BacktestService。
"""

from datetime import date
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.responses import ok
from app.db.session import get_db
from app.models.all_models import User, PortfolioSnapshot, BacktestEventLog, UserSimulatedPosition, BacktestRun
from app.schemas.backtest import BacktestRunRequest
from app.services.backtest_service import (
    create_backtest_run,
    execute_backtest_run,
    get_run_for_user,
    run_status,
    build_day_detail,
    snapshot_to_metrics,
    position_to_dict,
    trade_to_dict,
    log_to_dict,
    final_positions,
)
from app.models.all_models import BacktestDailyPosition, BacktestTrade
from app.services.log_service import write_operation_log

router = APIRouter(prefix="/api/backtest", tags=["Backtest API"])


@router.post("/run")
def run(
    req: BacktestRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    backtest = create_backtest_run(db, user.id, req)

    # 创建任务后立即放入 FastAPI 后台任务，接口先返回 run_id，后台逐日写入 frames / logs / summary。
    background_tasks.add_task(execute_backtest_run, backtest.id)

    write_operation_log(db, user.id, "BacktestService", "create_backtest_run", "success", f"run_id={backtest.id}")
    return ok(
        {
            "run_id": backtest.id,
            "run_name": backtest.run_name,
            "status": backtest.status,
            "start_date": backtest.start_date.isoformat() if backtest.start_date else None,
            "end_date": backtest.end_date.isoformat() if backtest.end_date else None,
            "created_at": backtest.created_at.isoformat() if backtest.created_at else None,
            "polling": {
                "status_url": f"/api/backtest/{backtest.id}/status",
                "frames_url": f"/api/backtest/{backtest.id}/frames",
                "logs_url": f"/api/backtest/{backtest.id}/logs",
                "final_positions_url": f"/api/backtest/{backtest.id}/final-positions",
            },
        },
        "backtest task created",
    )


@router.get("/latest/final-positions")
def latest_final_positions(include_empty: bool = True, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == user.id, BacktestRun.status == "finished")
        .order_by(BacktestRun.finished_at.desc(), BacktestRun.id.desc())
        .first()
    )
    if not run:
        if include_empty:
            # 返回稳定字段，避免前端在空结果场景下缺字段报错。
            return ok({
                "user_id": user.id,
                "run_id": None,
                "run_name": None,
                "snapshot_date": None,
                "total_value": None,
                "cash": None,
                "stock_value": None,
                "total_return": None,
                "positions": [],
            })
        from app.core.exceptions import AppException, BACKTEST_FINAL_POSITION_NOT_FOUND
        raise AppException(BACKTEST_FINAL_POSITION_NOT_FOUND, "当前用户没有已完成回测最终持仓。", 404)
    data = final_positions(db, run)
    data.update({"user_id": user.id, "run_name": run.run_name, "total_return": run.total_return})
    return ok(data)


@router.get("/{run_id}/status")
def status(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = get_run_for_user(db, run_id, user.id, user.role.role_name == "admin")
    return ok(run_status(db, run))


@router.get("/{run_id}/frames")
def frames(
    run_id: int,
    after_date: date | None = None,
    limit: int = 5,
    include_positions: bool = True,
    include_position_curves: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = get_run_for_user(db, run_id, user.id, user.role.role_name == "admin")
    q = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.run_id == run.id)
    if after_date:
        q = q.filter(PortfolioSnapshot.snapshot_date > after_date)
    snapshots = q.order_by(PortfolioSnapshot.snapshot_date.asc()).limit(min(limit, 100)).all()
    frame_items = []
    for s in snapshots:
        positions = []
        if include_positions:
            pos_rows = (
                db.query(BacktestDailyPosition)
                .filter(
                    BacktestDailyPosition.run_id == run.id,
                    BacktestDailyPosition.snapshot_date == s.snapshot_date,
                )
                .order_by(BacktestDailyPosition.ticker.asc(), BacktestDailyPosition.buy_date.asc())
                .all()
            )
            positions = [position_to_dict(db, p) for p in pos_rows]
            if not include_position_curves:
                for p in positions:
                    p["price_curve_from_buy"] = []
        trade_rows = (
            db.query(BacktestTrade)
            .filter(
                BacktestTrade.run_id == run.id,
                BacktestTrade.trade_date == s.snapshot_date,
            )
            .order_by(BacktestTrade.id.asc())
            .all()
        )
        log_count = db.query(BacktestEventLog).filter(BacktestEventLog.run_id == run.id, BacktestEventLog.trading_date == s.snapshot_date).count()
        frame_items.append(
            {
                "date": s.snapshot_date.isoformat(),
                "metrics": snapshot_to_metrics(s),
                "active_positions": positions,
                "trades": [trade_to_dict(t) for t in trade_rows],
                "log_count": log_count,
            }
        )
    next_after_date = frame_items[-1]["date"] if frame_items else (after_date.isoformat() if after_date else None)
    has_more = False
    if snapshots:
        has_more = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.run_id == run.id, PortfolioSnapshot.snapshot_date > snapshots[-1].snapshot_date).count() > 0
    return ok({"run_id": run.id, "status": run.status, "frames": frame_items, "next_after_date": next_after_date, "has_more": has_more})


@router.get("/{run_id}/days/{target_date}")
def day_detail(run_id: int, target_date: date, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = get_run_for_user(db, run_id, user.id, user.role.role_name == "admin")
    return ok(build_day_detail(db, run, target_date))


@router.get("/{run_id}/logs")
def logs(
    run_id: int,
    after_log_id: int | None = None,
    date: date | None = None,
    limit: int = 50,
    event_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = get_run_for_user(db, run_id, user.id, user.role.role_name == "admin")
    q = db.query(BacktestEventLog).filter(BacktestEventLog.run_id == run.id)
    if after_log_id:
        q = q.filter(BacktestEventLog.id > after_log_id)
    if date:
        q = q.filter(BacktestEventLog.trading_date == date)
    if event_type:
        q = q.filter(BacktestEventLog.event_type == event_type)
    rows = q.order_by(BacktestEventLog.id.asc()).limit(min(limit, 200)).all()
    next_id = rows[-1].id if rows else after_log_id
    has_more = False
    if rows:
        has_more = db.query(BacktestEventLog).filter(BacktestEventLog.run_id == run.id, BacktestEventLog.id > rows[-1].id).count() > 0
    return ok({"run_id": run.id, "items": [log_to_dict(r) for r in rows], "next_after_log_id": next_id, "has_more": has_more})


@router.get("/{run_id}/summary")
def summary(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = get_run_for_user(db, run_id, user.id, user.role.role_name == "admin")

    # summary 只返回最终汇总；total_value / cash / stock_value 从最终快照兜底读取，避免前端缺字段。
    final_snapshot = None

    if run.final_snapshot_date:
        final_snapshot = (
            db.query(PortfolioSnapshot)
            .filter(
                PortfolioSnapshot.run_id == run.id,
                PortfolioSnapshot.snapshot_date == run.final_snapshot_date,
            )
            .first()
        )

    if not final_snapshot:
        final_snapshot = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.run_id == run.id)
            .order_by(PortfolioSnapshot.snapshot_date.desc())
            .first()
        )

    final_equity = float(run.final_equity) if run.final_equity is not None else None
    total_value = (
        float(final_snapshot.total_value)
        if final_snapshot and final_snapshot.total_value is not None
        else final_equity
    )

    return ok(
        {
            "run_id": run.id,
            "run_name": run.run_name,
            "status": run.status,
            "start_date": run.start_date.isoformat() if run.start_date else None,
            "end_date": run.end_date.isoformat() if run.end_date else None,
            "initial_cash": float(run.initial_cash) if run.initial_cash is not None else None,
            "final_snapshot_date": run.final_snapshot_date.isoformat() if run.final_snapshot_date else None,
            "final_equity": final_equity,
            "total_value": total_value,
            "stock_value": float(final_snapshot.stock_value) if final_snapshot and final_snapshot.stock_value is not None else None,
            "cash": float(final_snapshot.cash) if final_snapshot and final_snapshot.cash is not None else None,
            "total_return": run.total_return,
            "annual_return": run.annual_return,
            "max_drawdown": run.max_drawdown,
            "win_rate": run.win_rate,
            "trade_count": run.trade_count,
            "sharpe_ratio": run.sharpe_ratio,
            "benchmark": run.benchmark,
            "benchmark_return": run.benchmark_return,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    )

@router.get("/{run_id}/final-positions")
def final_positions_by_run(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = get_run_for_user(db, run_id, user.id, user.role.role_name == "admin")
    return ok(final_positions(db, run))
