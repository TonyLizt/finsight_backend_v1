"""回测服务第一版。

真正的逐日策略和模型信号后续接入；当前先实现：
1. 创建 run_id；
2. 查询状态；
3. 查询已经存在的快照、日志、最终持仓；
4. 若暂无计算结果，返回空数组或 BACKTEST_NOT_READY。
"""

from datetime import datetime, date
from sqlalchemy.orm import Session

from app.core.exceptions import AppException, BACKTEST_RUN_NOT_FOUND, BACKTEST_NOT_READY, BACKTEST_FINAL_POSITION_NOT_FOUND
from app.models.all_models import (
    BacktestRun,
    BacktestEventLog,
    BacktestTrade,
    PortfolioSnapshot,
    BacktestDailyPosition,
    UserSimulatedPosition,
    Stock,
)
from app.schemas.backtest import BacktestRunRequest


def create_backtest_run(db: Session, user_id: int, req: BacktestRunRequest) -> BacktestRun:
    run = BacktestRun(
        user_id=user_id,
        run_name=req.run_name or "Untitled Backtest",
        tickers_json=[t.upper() for t in req.tickers],
        start_date=req.start_date,
        end_date=req.end_date,
        initial_cash=req.initial_cash,
        benchmark=req.benchmark,
        strategy_params_json={
            "forecast_days": req.forecast_days,
            "max_position_ratio": req.max_position_ratio,
            "max_holding_count": req.max_holding_count,
            "fee_rate": req.fee_rate,
            "save_daily_positions": req.save_daily_positions,
            "save_event_logs": req.save_event_logs,
            "animation_mode": req.animation_mode,
        },
        status="pending",
        progress=0.0,
        trading_days_done=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # 写入一条系统日志，说明第一版暂未接入真实回测引擎。
    db.add(
        BacktestEventLog(
            run_id=run.id,
            log_seq=1,
            log_time=datetime.utcnow(),
            level="info",
            event_type="system",
            action="none",
            message="回测任务已创建。第一版后端暂未接入真实逐日回测引擎，后续可在 BacktestService 中接入。",
            detail_json={"status": "pending", "implementation": "stub"},
        )
    )
    db.commit()
    return run


def get_run_for_user(db: Session, run_id: int, user_id: int, is_admin: bool) -> BacktestRun:
    q = db.query(BacktestRun).filter(BacktestRun.id == run_id)
    if not is_admin:
        q = q.filter(BacktestRun.user_id == user_id)
    run = q.first()
    if not run:
        raise AppException(BACKTEST_RUN_NOT_FOUND, "回测任务不存在。", 404)
    return run


def run_status(db: Session, run: BacktestRun) -> dict:
    last_log = db.query(BacktestEventLog).filter(BacktestEventLog.run_id == run.id).order_by(BacktestEventLog.id.desc()).first()
    last_snapshot = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.run_id == run.id).order_by(PortfolioSnapshot.snapshot_date.desc()).first()
    final_positions_ready = db.query(UserSimulatedPosition).filter(UserSimulatedPosition.source_run_id == run.id).count() > 0
    return {
        "run_id": run.id,
        "status": run.status,
        "start_date": run.start_date.isoformat() if run.start_date else None,
        "end_date": run.end_date.isoformat() if run.end_date else None,
        "current_date": run.current_date.isoformat() if run.current_date else None,
        "trading_days_total": run.trading_days_total,
        "trading_days_done": run.trading_days_done or 0,
        "progress": run.progress or 0.0,
        "last_snapshot_date": last_snapshot.snapshot_date.isoformat() if last_snapshot else None,
        "last_log_id": last_log.id if last_log else None,
        "final_positions_ready": final_positions_ready,
        "error_message": run.error_message,
    }


def snapshot_to_metrics(s: PortfolioSnapshot) -> dict:
    return {
        "total_value": float(s.total_value) if s.total_value is not None else None,
        "stock_value": float(s.stock_value) if s.stock_value is not None else None,
        "cash": float(s.cash) if s.cash is not None else None,
        "daily_return": s.daily_return,
        "total_return": s.total_return,
        "annual_return": s.annual_return,
        "max_drawdown": s.max_drawdown,
        "win_rate": s.win_rate,
        "trade_count": s.trade_count,
        "sharpe_ratio": s.sharpe_ratio,
        "benchmark_return": s.benchmark_return,
    }


def position_to_dict(db: Session, p: BacktestDailyPosition) -> dict:
    stock = db.query(Stock).filter(Stock.ticker == p.ticker).first()
    return {
        "ticker": p.ticker,
        "company_name": stock.company_name if stock else None,
        "buy_date": p.buy_date.isoformat() if p.buy_date else None,
        "price_curve_from_buy": p.price_curve_json or [],
        "stock_score": p.stock_score,
        "situation_score": p.situation_score,
        "stock_value": float(p.stock_value) if p.stock_value is not None else None,
        "quantity": p.quantity,
        "current_price": float(p.current_price) if p.current_price is not None else None,
        "cost_price": float(p.cost_price) if p.cost_price is not None else None,
        "cost_amount": float(p.cost_amount) if p.cost_amount is not None else None,
        "daily_pnl": float(p.daily_pnl) if p.daily_pnl is not None else None,
        "daily_pnl_pct": p.daily_pnl_pct,
        "total_pnl": float(p.total_pnl) if p.total_pnl is not None else None,
        "total_pnl_pct": p.total_pnl_pct,
        "position_ratio": p.position_ratio,
    }


def trade_to_dict(t: BacktestTrade) -> dict:
    return {
        "trade_id": t.id,
        "ticker": t.ticker,
        "side": t.side,
        "price": float(t.price) if t.price is not None else None,
        "quantity": t.quantity,
        "amount": float(t.amount) if t.amount is not None else None,
        "fee": float(t.fee) if t.fee is not None else None,
        "cash_after": float(t.cash_after) if t.cash_after is not None else None,
        "reason": t.reason,
    }


def log_to_dict(log: BacktestEventLog) -> dict:
    return {
        "log_id": log.id,
        "log_seq": log.log_seq,
        "trading_date": log.trading_date.isoformat() if log.trading_date else None,
        "level": log.level,
        "event_type": log.event_type,
        "ticker": log.ticker,
        "action": log.action,
        "message": log.message,
        "detail": log.detail_json,
    }


def build_day_detail(db: Session, run: BacktestRun, target_date: date) -> dict:
    snapshot = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.run_id == run.id, PortfolioSnapshot.snapshot_date == target_date).first()
    if not snapshot:
        raise AppException(BACKTEST_NOT_READY, "回测结果尚未生成到对应日期。", 404)
    positions = db.query(BacktestDailyPosition).filter(BacktestDailyPosition.run_id == run.id, BacktestDailyPosition.snapshot_date == target_date).all()
    trades = db.query(BacktestTrade).filter(BacktestTrade.run_id == run.id, BacktestTrade.trade_date == target_date).all()
    logs = db.query(BacktestEventLog).filter(BacktestEventLog.run_id == run.id, BacktestEventLog.trading_date == target_date).order_by(BacktestEventLog.id.asc()).all()
    return {
        "run_id": run.id,
        "date": target_date.isoformat(),
        "metrics": snapshot_to_metrics(snapshot),
        "active_positions": [position_to_dict(db, p) for p in positions],
        "trades": [trade_to_dict(t) for t in trades],
        "logs": [log_to_dict(l) for l in logs],
    }


def final_positions(db: Session, run: BacktestRun) -> dict:
    snapshot = None
    if run.final_snapshot_date:
        snapshot = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.run_id == run.id, PortfolioSnapshot.snapshot_date == run.final_snapshot_date).first()
    if not snapshot:
        snapshot = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.run_id == run.id).order_by(PortfolioSnapshot.snapshot_date.desc()).first()
    positions = db.query(UserSimulatedPosition).filter(UserSimulatedPosition.source_run_id == run.id).all()
    if not snapshot and not positions:
        raise AppException(BACKTEST_FINAL_POSITION_NOT_FOUND, "回测最终持仓不存在或尚未生成。", 404)
    return {
        "run_id": run.id,
        "snapshot_date": (run.final_snapshot_date or (snapshot.snapshot_date if snapshot else None)).isoformat() if (run.final_snapshot_date or snapshot) else None,
        "total_value": float(snapshot.total_value) if snapshot and snapshot.total_value is not None else None,
        "cash": float(snapshot.cash) if snapshot and snapshot.cash is not None else None,
        "stock_value": float(snapshot.stock_value) if snapshot and snapshot.stock_value is not None else None,
        "positions": [
            {
                "ticker": p.ticker,
                "company_name": p.company_name,
                "quantity": p.quantity,
                "current_price": float(p.current_price) if p.current_price is not None else None,
                "cost_price": float(p.cost_price) if p.cost_price is not None else None,
                "cost_amount": float(p.cost_amount) if p.cost_amount is not None else None,
                "stock_value": float(p.stock_value) if p.stock_value is not None else None,
                "total_pnl": float(p.total_pnl) if p.total_pnl is not None else None,
                "total_pnl_pct": p.total_pnl_pct,
                "position_ratio": p.position_ratio,
                "price_curve_from_buy": p.price_curve_json or [],
            }
            for p in positions
        ],
    }
