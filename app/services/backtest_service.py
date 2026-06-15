"""回测服务。

本文件实现文档 v5.0 中的真实逐日模拟回测主流程：
1. 创建回测任务；
2. 后台逐交易日读取 price_data / technical_indicators / sentiment_daily；
3. 生成信号、买入、卖出、持有决策；
4. 写入 portfolio_snapshots / backtest_daily_positions / backtest_trades / backtest_event_logs；
5. 完成后写入 user_simulated_positions，并更新 backtest_runs 最终汇总。

当前策略优先使用服务器 active 模型生成每日信号；当某只股票某一天特征缺失时，
才降级使用数据库驱动的规则信号，避免单点数据缺失中断整段回测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AppException,
    BACKTEST_FINAL_POSITION_NOT_FOUND,
    BACKTEST_NOT_READY,
    BACKTEST_RUN_NOT_FOUND,
    DATA_NOT_FOUND,
)
from app.db.session import SessionLocal
from app.models.all_models import (
    BacktestDailyPosition,
    BacktestEventLog,
    BacktestRun,
    BacktestTrade,
    PortfolioSnapshot,
    PriceData,
    SentimentDaily,
    Stock,
    TechnicalIndicator,
    UserSimulatedPosition,
)
from app.schemas.backtest import BacktestRunRequest
from app.services.feature_service import build_feature_dict, validate_feature_columns
from app.services.model_service import (
    load_active_model,
    predict_aux_classifier,
    predict_classifier,
    predict_regressor,
)


@dataclass
class RuntimePosition:
    """回测执行期内的内存持仓对象，避免每天重复从数据库反推持仓。"""

    ticker: str
    buy_date: date
    quantity: int
    cost_price: float
    cost_amount: float
    price_curve: list[dict[str, Any]] = field(default_factory=list)
    latest_score: float | None = None
    latest_situation_score: float | None = None
    latest_signal: dict[str, Any] = field(default_factory=dict)

@dataclass
class RuntimeTickerTrack:
    """股票池单只股票的全程展示状态。

    RuntimePosition 只代表真实持仓；本对象代表前端动画需要展示的股票池状态。
    因此前端即使在卖出后，也可以继续拿到该 ticker 后续每日价格、评分和交易标记。
    """

    ticker: str
    price_curve: list[dict[str, Any]] = field(default_factory=list)
    trade_markers: list[dict[str, Any]] = field(default_factory=list)
    latest_score: float | None = None
    latest_situation_score: float | None = None
    latest_signal: dict[str, Any] = field(default_factory=dict)
    latest_buy_date: date | None = None
    latest_sell_date: date | None = None
    sell_order: int | None = None
    current_quantity: int = 0
    cost_price: float | None = None
    cost_amount: float | None = None
    realized_pnl: float | None = None
    realized_pnl_pct: float | None = None
    holding_status: str = "watching"  # watching / holding / sold

@dataclass
class RuntimeBacktestModelBundle:
    """单次回测复用的模型对象，避免每天、每只股票重复查库和加载 joblib。"""

    classifier_model: Any
    classifier_match_status: str
    reg_model: Any
    reg_match_status: str
    aux_model: Any | None = None
    aux_match_status: str | None = None

class BacktestLogWriter:
    """统一维护单次回测的 log_seq，保证日志顺序稳定。"""

    def __init__(self, db: Session, run_id: int, enabled: bool):
        self.db = db
        self.run_id = run_id
        self.enabled = enabled
        self.seq = 0

    def write(
        self,
        *,
        level: str = "info",
        event_type: str = "system",
        trading_date: date | None = None,
        ticker: str | None = None,
        action: str = "none",
        message: str,
        detail: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        """save_event_logs=false 时只写强制日志，减少长周期回测日志量。"""
        if not self.enabled and not force:
            return

        self.seq += 1
        self.db.add(
            BacktestEventLog(
                run_id=self.run_id,
                log_seq=self.seq,
                log_time=datetime.utcnow(),
                trading_date=trading_date,
                level=level,
                event_type=event_type,
                ticker=ticker,
                action=action,
                message=message,
                detail_json=detail or {},
            )
        )


def _safe_float(value: Any) -> float | None:
    """把 Decimal / Numeric / None 安全转换成 JSON 可序列化的 float。"""
    if value is None:
        return None
    return float(value)


def _round_money(value: float | None) -> float | None:
    """金额字段统一保留 2 位，避免浮点误差污染接口。"""
    if value is None:
        return None
    return round(float(value), 2)


def _round_price(value: float | None) -> float | None:
    """价格字段统一保留 4 位，对齐 price_data 的 DECIMAL(12,4)。"""
    if value is None:
        return None
    return round(float(value), 4)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_div(numerator: float, denominator: float | None) -> float | None:
    if denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _normalize_tickers(tickers: list[str]) -> list[str]:
    """去重并统一大写，避免同一股票重复参与回测。"""
    seen = set()
    normalized = []

    for item in tickers:
        ticker = (item or "").strip().upper()
        if ticker and ticker not in seen:
            normalized.append(ticker)
            seen.add(ticker)

    return normalized


def _current_price(row: PriceData | None) -> float | None:
    """优先使用 close，缺失时用 adj_close 兜底。"""
    if row is None:
        return None
    return _safe_float(row.close if row.close is not None else row.adj_close)


def _get_strategy_params(run: BacktestRun) -> dict[str, Any]:
    """集中读取策略参数，并给旧数据补默认值。

    用户当前只允许修改：
    max_position_ratio、max_holding_count、fee_rate、take_profit_pct、stop_loss_pct。

    其他策略参数统一由后端默认值控制。
    """
    params = dict(run.strategy_params_json or {})

    # 用户可配置参数。
    params.setdefault("max_position_ratio", 0.2)
    params.setdefault("max_holding_count", 5)
    params.setdefault("fee_rate", 0.0005)
    params.setdefault("take_profit_pct", 0.18)
    params.setdefault("stop_loss_pct", -0.08)

    # 后端固定参数，不开放给用户修改。
    params.setdefault("forecast_days", 5)
    params.setdefault("save_daily_positions", True)
    params.setdefault("save_event_logs", True)
    params.setdefault("animation_mode", "fast")
    params.setdefault("buy_score_threshold", 54.0)
    params.setdefault("buy_situation_threshold", 59.0)
    params.setdefault("sell_score_threshold", 42.0)
    params.setdefault("min_cash_reserve_ratio", 0.02)

    return params


def create_backtest_run(db: Session, user_id: int, req: BacktestRunRequest) -> BacktestRun:
    """创建回测任务。

    该函数只负责创建任务和初始日志；真实逐日执行由 execute_backtest_run 在后台完成。
    """
    tickers = _normalize_tickers(req.tickers)

    if not tickers:
        raise AppException(DATA_NOT_FOUND, "回测股票池不能为空。", 400)

    if req.end_date < req.start_date:
        raise AppException(DATA_NOT_FOUND, "回测结束日期不能早于开始日期。", 400)

    run = BacktestRun(
        user_id=user_id,
        run_name=f"回测 {req.start_date.isoformat()} 至 {req.end_date.isoformat()}",
        tickers_json=tickers,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_cash=req.initial_cash,
        benchmark="SPY",
        strategy_params_json={
            # 用户可配置参数。
            "max_position_ratio": req.max_position_ratio,
            "max_holding_count": req.max_holding_count,
            "fee_rate": req.fee_rate,
            "take_profit_pct": req.take_profit_pct,
            "stop_loss_pct": req.stop_loss_pct,

            # 后端固定参数，不开放给用户修改。
            "forecast_days": 5,
            "save_daily_positions": True,
            "save_event_logs": True,
            "animation_mode": "fast",
            "buy_score_threshold": 54.0,
            "buy_situation_threshold": 59.0,
            "sell_score_threshold": 42.0,
            "min_cash_reserve_ratio": 0.02,
        },
        status="pending",
        progress=0.0,
        trading_days_done=0,
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    # 保留任务创建日志，方便前端立即看到任务已进入队列。
    db.add(
        BacktestEventLog(
            run_id=run.id,
            log_seq=1,
            log_time=datetime.utcnow(),
            level="info",
            event_type="system",
            action="none",
            message="回测任务已创建，等待后台逐日回测引擎执行。",
            detail_json={"status": "pending", "implementation": "daily_engine"},
        )
    )
    db.commit()

    return run

def execute_backtest_run(run_id: int) -> None:
    """后台执行入口。

    FastAPI BackgroundTasks 会在响应返回后调用本函数；这里必须创建新的数据库 Session，不能复用请求 Session。
    """
    db = SessionLocal()

    try:
        _execute_backtest_run_in_session(db, run_id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()

        run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = datetime.utcnow()

            db.add(
                BacktestEventLog(
                    run_id=run.id,
                    log_seq=_next_log_seq(db, run.id),
                    log_time=datetime.utcnow(),
                    level="error",
                    event_type="system",
                    action="none",
                    message=f"回测执行失败：{exc}",
                    detail_json={"error": str(exc)},
                )
            )
            db.commit()
    finally:
        db.close()


def _execute_backtest_run_in_session(db: Session, run_id: int) -> None:
    """真实逐日回测执行器。"""
    run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()

    if not run:
        raise AppException(BACKTEST_RUN_NOT_FOUND, "回测任务不存在。", 404)

    if run.status in {"running", "finished", "cancelled"}:
        return

    params = _get_strategy_params(run)
    save_daily_positions = bool(params.get("save_daily_positions", True))
    save_event_logs = bool(params.get("save_event_logs", True))
    verbose_logs = save_event_logs and params.get("animation_mode") == "realtime"

    # 重新执行同一个 run_id 前先清理旧明细，保证幂等性。
    _clear_backtest_outputs(db, run.id)

    logger = BacktestLogWriter(db, run.id, enabled=save_event_logs)

    tickers = _normalize_tickers(run.tickers_json or [])
    if not tickers:
        raise RuntimeError("回测股票池为空，无法执行。")

    price_rows = (
        db.query(PriceData)
        .filter(
            PriceData.ticker.in_(tickers),
            PriceData.trading_date >= run.start_date,
            PriceData.trading_date <= run.end_date,
            PriceData.close.isnot(None),
        )
        .order_by(PriceData.trading_date.asc(), PriceData.ticker.asc())
        .all()
    )

    if not price_rows:
        raise RuntimeError("回测区间内没有可用日频行情数据，请先补全 price_data。")

    prices_by_date: dict[date, dict[str, PriceData]] = {}
    prices_by_ticker: dict[str, dict[date, PriceData]] = {ticker: {} for ticker in tickers}

    for row in price_rows:
        prices_by_date.setdefault(row.trading_date, {})[row.ticker] = row
        prices_by_ticker.setdefault(row.ticker, {})[row.trading_date] = row

    trading_days = sorted(prices_by_date.keys())

    if not trading_days:
        raise RuntimeError("回测区间内没有交易日。")

    stock_map = {
        s.ticker: s
        for s in db.query(Stock).filter(Stock.ticker.in_(tickers)).all()
    }
    indicator_map = _load_indicator_map(db, tickers, run.start_date, run.end_date)
    sentiment_map = _load_sentiment_map(db, tickers, run.start_date, run.end_date)
    benchmark_map = _load_benchmark_map(db, run.benchmark, run.start_date, run.end_date)

    run.status = "running"
    run.started_at = datetime.utcnow()
    run.current_date = None
    run.trading_days_total = len(trading_days)
    run.trading_days_done = 0
    run.progress = 0.0
    run.error_message = None
    db.commit()

    logger.write(
        event_type="system",
        message=f"回测开始执行，股票池 {tickers}，交易日数量 {len(trading_days)}。",
        detail={
            "tickers": tickers,
            "start_date": run.start_date.isoformat() if run.start_date else None,
            "end_date": run.end_date.isoformat() if run.end_date else None,
            "trading_days_total": len(trading_days),
            "strategy_params": params,
        },
        force=True,
    )
    db.commit()

    forecast_days = int(params.get("forecast_days") or 5)

    try:
        classifier_model, classifier_match_status = load_active_model(db, "classifier", forecast_days)
        reg_model, reg_match_status = load_active_model(db, "regressor", forecast_days)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"回测无法启动：active 分类/回归模型加载失败：{exc}") from exc

    if classifier_model.feature_columns != reg_model.feature_columns:
        raise RuntimeError("回测无法启动：分类模型与回归模型的 feature_columns 不一致。")

    aux_model = None
    aux_match_status = None
    try:
        aux_model, aux_match_status = load_active_model(db, "aux_classifier", 10)
    except Exception as exc:  # noqa: BLE001
        logger.write(
            level="warning",
            event_type="model",
            action="skip_aux_model",
            message=f"辅助强信号模型加载失败，回测继续使用主分类/回归模型：{exc}",
            detail={"error": str(exc)},
            force=True,
        )

    model_bundle = RuntimeBacktestModelBundle(
        classifier_model=classifier_model,
        classifier_match_status=classifier_match_status,
        reg_model=reg_model,
        reg_match_status=reg_match_status,
        aux_model=aux_model,
        aux_match_status=aux_match_status,
    )

    logger.write(
        event_type="model",
        action="load_active_model",
        message=(
            "回测已接入 active 模型推理："
            f"classifier={classifier_model.version.version_name}({classifier_match_status})，"
            f"regressor={reg_model.version.version_name}({reg_match_status})。"
        ),
        detail={
            "forecast_days": forecast_days,
            "classifier_model_id": classifier_model.version.id,
            "classifier_model_version": classifier_model.version.version_name,
            "classifier_match_status": classifier_match_status,
            "reg_model_id": reg_model.version.id,
            "reg_model_version": reg_model.version.version_name,
            "reg_match_status": reg_match_status,
            "aux_model_id": aux_model.version.id if aux_model else None,
            "aux_model_version": aux_model.version.version_name if aux_model else None,
            "aux_match_status": aux_match_status,
        },
        force=True,
    )
    db.commit()

    initial_cash = float(run.initial_cash or 0)
    cash = initial_cash
    positions: dict[str, RuntimePosition] = {}
    ticker_tracks: dict[str, RuntimeTickerTrack] = {
        ticker: RuntimeTickerTrack(ticker=ticker)
        for ticker in tickers
    }
    sell_sequence = 0
    daily_returns: list[float] = []
    total_values: list[float] = []

    peak_value = initial_cash
    max_drawdown = 0.0
    trade_count = 0
    sell_count = 0
    winning_sell_count = 0
    previous_total_value = initial_cash
    benchmark_base_price = _first_available_benchmark_price(benchmark_map, trading_days)

    for index, trading_day in enumerate(trading_days, start=1):
        day_prices = prices_by_date.get(trading_day, {})

        # 先为当天所有股票生成信号，后续交易和持仓快照都复用同一份信号，避免前后不一致。
        signals: dict[str, dict[str, Any]] = {}

        for ticker in tickers:
            signals[ticker] = _build_signal(
                db=db,
                ticker=ticker,
                trading_day=trading_day,
                price_row=day_prices.get(ticker),
                indicator=indicator_map.get((ticker, trading_day)),
                sentiment=sentiment_map.get((ticker, trading_day)),
                model_bundle=model_bundle,
                forecast_days=forecast_days,
            )

            if verbose_logs and ticker in day_prices:
                logger.write(
                    event_type="signal",
                    trading_date=trading_day,
                    ticker=ticker,
                    action="hold" if ticker in positions else "watch",
                    message=(
                        f"{ticker} 生成当日信号：股票评分 {signals[ticker]['stock_score']:.2f}，"
                        f"情况分 {signals[ticker]['situation_score']:.2f}。"
                    ),
                    detail=signals[ticker],
                )

        # 更新股票池全程展示状态：无论是否持仓，都记录当日价格和评分。
        for ticker in tickers:
            track = ticker_tracks[ticker]
            price = _current_price(day_prices.get(ticker))
            signal = signals.get(ticker, {})
            date_text = trading_day.isoformat()

            if price is not None:
                if not track.price_curve or track.price_curve[-1].get("date") != date_text:
                    track.price_curve.append({"date": date_text, "close": _round_price(price)})

            track.latest_score = signal.get("stock_score")
            track.latest_situation_score = signal.get("situation_score")
            track.latest_signal = {
                **signal,
                "action": "hold" if ticker in positions else track.holding_status,
                "holding_status": track.holding_status,
                "has_price_today": price is not None,
            }

        # 先处理卖出，再处理买入。这样卖出释放的现金可用于当天新买入。
        for ticker in list(positions.keys()):
            position = positions[ticker]
            price = _current_price(day_prices.get(ticker))

            if price is None:
                logger.write(
                    level="warning",
                    event_type="data",
                    trading_date=trading_day,
                    ticker=ticker,
                    action="skip",
                    message=f"{ticker} 当日缺少收盘价，持仓沿用上一交易日价格。",
                    detail={"ticker": ticker, "date": trading_day.isoformat()},
                )
                continue

            signal = signals.get(ticker, {})
            sell_reason = _should_sell(position, price, signal, params)

            if sell_reason:
                amount = position.quantity * price
                fee = amount * float(params["fee_rate"])
                net_amount = amount - fee
                cash += net_amount

                realized_pnl = net_amount - position.cost_amount
                realized_pnl_pct = _safe_div(realized_pnl, position.cost_amount) or 0.0

                trade_count += 1
                sell_count += 1

                if realized_pnl > 0:
                    winning_sell_count += 1

                db.add(
                    BacktestTrade(
                        run_id=run.id,
                        trade_date=trading_day,
                        ticker=ticker,
                        side="sell",
                        price=_round_price(price),
                        quantity=position.quantity,
                        amount=_round_money(amount),
                        fee=_round_money(fee),
                        cash_after=_round_money(cash),
                        position_after=0,
                        reason=sell_reason,
                        signal_json={
                            **signal,
                            "side": "sell",
                            "reason": sell_reason,
                            "cost_price": position.cost_price,
                            "realized_pnl": _round_money(realized_pnl),
                            "realized_pnl_pct": realized_pnl_pct,
                        },
                    )
                )

                logger.write(
                    event_type="trade",
                    trading_date=trading_day,
                    ticker=ticker,
                    action="sell",
                    message=(
                        f"{ticker} 触发卖出条件，以 {price:.2f} 卖出 {position.quantity} 股，"
                        f"成交金额 {amount:.2f}，手续费 {fee:.2f}，实际到账 {net_amount:.2f}，"
                        f"交易后现金 {cash:.2f}，剩余持仓 0 股。原因：{sell_reason}。"
                    ),
                    detail={
                        "ticker": ticker,
                        "side": "sell",
                        "price": _round_price(price),
                        "quantity": position.quantity,
                        "amount": _round_money(amount),
                        "fee": _round_money(fee),
                        "net_amount": _round_money(net_amount),
                        "cash_after": _round_money(cash),
                        "position_before": position.quantity,
                        "position_after": 0,
                        "cost_price": _round_price(position.cost_price),
                        "realized_pnl": _round_money(realized_pnl),
                        "realized_pnl_pct": realized_pnl_pct,
                        "reason": sell_reason,
                    },
                )

                sell_sequence += 1
                track = ticker_tracks[ticker]
                track.holding_status = "sold"
                track.current_quantity = 0
                track.latest_sell_date = trading_day
                track.sell_order = sell_sequence
                track.cost_price = position.cost_price
                track.cost_amount = position.cost_amount
                track.realized_pnl = realized_pnl
                track.realized_pnl_pct = realized_pnl_pct
                track.latest_signal = {
                    **signal,
                    "action": "sell",
                    "side": "sell",
                    "reason": sell_reason,
                    "holding_status": "sold",
                    "latest_buy_date": position.buy_date.isoformat() if position.buy_date else None,
                    "latest_sell_date": trading_day.isoformat(),
                    "sell_order": sell_sequence,
                    "position_after": 0,
                    "realized_pnl": _round_money(realized_pnl),
                    "realized_pnl_pct": realized_pnl_pct,
                }
                track.trade_markers.append(
                    {
                        "date": trading_day.isoformat(),
                        "side": "sell",
                        "price": _round_price(price),
                        "quantity": position.quantity,
                        "reason": sell_reason,
                        "sell_order": sell_sequence,
                    }
                )
                del positions[ticker]

        marked_stock_value = _calculate_stock_value(positions, day_prices)
        total_value_before_buy = cash + marked_stock_value
        available_slots = max(0, int(params["max_holding_count"]) - len(positions))
        reserve_cash = initial_cash * float(params["min_cash_reserve_ratio"])

        candidates = sorted(
            [
                (ticker, signal)
                for ticker, signal in signals.items()
                if ticker not in positions
                and ticker in day_prices
                and _current_price(day_prices.get(ticker)) is not None
            ],
            key=lambda item: (item[1]["stock_score"], item[1]["situation_score"]),
            reverse=True,
        )

        for ticker, signal in candidates:
            if available_slots <= 0:
                break

            if signal["stock_score"] < float(params["buy_score_threshold"]):
                continue
            
            if signal["situation_score"] < float(params["buy_situation_threshold"]):
                continue

            price = _current_price(day_prices.get(ticker))
            if price is None or price <= 0:
                continue

            target_position_value = total_value_before_buy * float(params["max_position_ratio"])
            cash_budget = max(0.0, min(cash - reserve_cash, target_position_value))
            quantity = math.floor(cash_budget / (price * (1 + float(params["fee_rate"]))))

            if quantity <= 0:
                logger.write(
                    event_type="risk",
                    trading_date=trading_day,
                    ticker=ticker,
                    action="skip",
                    message=f"{ticker} 满足买入信号，但现金不足，跳过买入。",
                    detail={
                        "ticker": ticker,
                        "price": price,
                        "cash": cash,
                        "cash_budget": cash_budget,
                    },
                )
                continue

            amount = quantity * price
            fee = amount * float(params["fee_rate"])
            total_cost = amount + fee

            if total_cost > cash:
                continue

            cash -= total_cost
            cost_price = total_cost / quantity
            trade_count += 1
            available_slots -= 1

            positions[ticker] = RuntimePosition(
                ticker=ticker,
                buy_date=trading_day,
                quantity=quantity,
                cost_price=cost_price,
                cost_amount=total_cost,
                price_curve=[{"date": trading_day.isoformat(), "close": _round_price(price)}],
                latest_score=signal["stock_score"],
                latest_situation_score=signal["situation_score"],
                latest_signal={**signal, "action": "buy"},
            )

            track = ticker_tracks[ticker]
            track.holding_status = "holding"
            track.current_quantity = quantity
            track.latest_buy_date = trading_day
            track.latest_sell_date = None
            track.sell_order = None
            track.cost_price = cost_price
            track.cost_amount = total_cost
            track.realized_pnl = None
            track.realized_pnl_pct = None
            track.latest_score = signal["stock_score"]
            track.latest_situation_score = signal["situation_score"]
            track.latest_signal = {
                **signal,
                "action": "buy",
                "side": "buy",
                "reason": "股票评分和情况分达到买入阈值",
                "holding_status": "holding",
                "latest_buy_date": trading_day.isoformat(),
                "position_after": quantity,
            }
            track.trade_markers.append(
                {
                    "date": trading_day.isoformat(),
                    "side": "buy",
                    "price": _round_price(price),
                    "quantity": quantity,
                    "reason": "股票评分和情况分达到买入阈值",
                }
            )

            db.add(
                BacktestTrade(
                    run_id=run.id,
                    trade_date=trading_day,
                    ticker=ticker,
                    side="buy",
                    price=_round_price(price),
                    quantity=quantity,
                    amount=_round_money(amount),
                    fee=_round_money(fee),
                    cash_after=_round_money(cash),
                    position_after=quantity,
                    reason="股票评分和情况分达到买入阈值",
                    signal_json={
                        **signal,
                        "side": "buy",
                        "reason": "股票评分和情况分达到买入阈值",
                        "target_position_value": _round_money(target_position_value),
                    },
                )
            )

            logger.write(
                event_type="trade",
                trading_date=trading_day,
                ticker=ticker,
                action="buy",
                message=(
                    f"{ticker} 触发买入条件，以 {price:.2f} 买入 {quantity} 股，"
                    f"成交金额 {amount:.2f}，手续费 {fee:.2f}，实际支出 {total_cost:.2f}，"
                    f"交易后现金 {cash:.2f}，交易后持仓 {quantity} 股。原因：股票评分和情况分达到买入阈值。"
                ),
                detail={
                    "ticker": ticker,
                    "side": "buy",
                    "price": _round_price(price),
                    "quantity": quantity,
                    "amount": _round_money(amount),
                    "fee": _round_money(fee),
                    "gross_cost": _round_money(total_cost),
                    "cash_after": _round_money(cash),
                    "position_before": 0,
                    "position_after": quantity,
                    "cost_price": _round_price(cost_price),
                    "reason": "股票评分和情况分达到买入阈值",
                },
            )

        # 买入完成后更新所有持仓当天价格曲线。
        for ticker, position in positions.items():
            price = _current_price(day_prices.get(ticker))

            if price is None:
                continue

            if not position.price_curve or position.price_curve[-1].get("date") != trading_day.isoformat():
                position.price_curve.append({"date": trading_day.isoformat(), "close": _round_price(price)})

            signal = signals.get(ticker, {})
            position.latest_score = signal.get("stock_score")
            position.latest_situation_score = signal.get("situation_score")
            position.latest_signal = {**signal, "action": "hold"}

        stock_value = _calculate_stock_value(positions, day_prices)
        total_value = cash + stock_value

        daily_return = _safe_div(total_value - previous_total_value, previous_total_value) or 0.0
        total_return = _safe_div(total_value - initial_cash, initial_cash) or 0.0
        annual_return = _annualized_return(total_return, index)

        daily_returns.append(daily_return)
        total_values.append(total_value)

        peak_value = max(peak_value, total_value)
        current_drawdown = _safe_div(total_value - peak_value, peak_value) or 0.0
        max_drawdown = min(max_drawdown, current_drawdown)

        win_rate = (winning_sell_count / sell_count) if sell_count else 0.0
        sharpe_ratio = _sharpe_ratio(daily_returns)
        benchmark_value, benchmark_return = _benchmark_metrics(
            benchmark_map,
            benchmark_base_price,
            trading_day,
            initial_cash,
        )

        db.add(
            PortfolioSnapshot(
                run_id=run.id,
                snapshot_date=trading_day,
                cash=_round_money(cash),
                stock_value=_round_money(stock_value),
                total_value=_round_money(total_value),
                daily_return=daily_return,
                total_return=total_return,
                annual_return=annual_return,
                max_drawdown=max_drawdown,
                win_rate=win_rate,
                trade_count=trade_count,
                sharpe_ratio=sharpe_ratio,
                benchmark_value=_round_money(benchmark_value),
                benchmark_return=benchmark_return,
            )
        )

        if save_daily_positions:
            _write_daily_positions(
                db=db,
                run=run,
                trading_day=trading_day,
                positions=positions,
                ticker_tracks=ticker_tracks,
                day_prices=day_prices,
                total_value=total_value,
            )

        logger.write(
            event_type="system",
            trading_date=trading_day,
            action="none",
            message=(
                f"完成 {trading_day.isoformat()} 回测计算，总资产 {total_value:.2f}，"
                f"现金 {cash:.2f}，股票市值 {stock_value:.2f}，进度 {index}/{len(trading_days)}。"
            ),
            detail={
                "date": trading_day.isoformat(),
                "cash": _round_money(cash),
                "stock_value": _round_money(stock_value),
                "total_value": _round_money(total_value),
                "daily_return": daily_return,
                "total_return": total_return,
                "trade_count": trade_count,
                "holding_count": len(positions),
            },
        )

        run.current_date = trading_day
        run.trading_days_done = index
        run.progress = index / len(trading_days)

        db.commit()

        previous_total_value = total_value

    final_date = trading_days[-1]
    final_total_value = total_values[-1] if total_values else initial_cash
    final_stock_value = _calculate_stock_value(positions, prices_by_date.get(final_date, {}))
    final_cash = final_total_value - final_stock_value
    final_total_return = _safe_div(final_total_value - initial_cash, initial_cash) or 0.0
    final_annual_return = _annualized_return(final_total_return, len(trading_days))
    final_win_rate = (winning_sell_count / sell_count) if sell_count else 0.0
    final_sharpe = _sharpe_ratio(daily_returns)
    _, final_benchmark_return = _benchmark_metrics(
        benchmark_map,
        benchmark_base_price,
        final_date,
        initial_cash,
    )

    _write_final_positions(
        db=db,
        run=run,
        final_date=final_date,
        positions=positions,
        day_prices=prices_by_date.get(final_date, {}),
        total_value=final_total_value,
        stock_map=stock_map,
    )

    run.status = "finished"
    run.current_date = final_date
    run.trading_days_done = len(trading_days)
    run.progress = 1.0
    run.final_snapshot_date = final_date
    run.final_equity = _round_money(final_total_value)
    run.total_return = final_total_return
    run.annual_return = final_annual_return
    run.max_drawdown = max_drawdown
    run.win_rate = final_win_rate
    run.trade_count = trade_count
    run.sharpe_ratio = final_sharpe
    run.benchmark_return = final_benchmark_return
    run.finished_at = datetime.utcnow()

    logger.write(
        event_type="system",
        message=(
            f"回测完成，最终总资产 {final_total_value:.2f}，现金 {final_cash:.2f}，"
            f"股票市值 {final_stock_value:.2f}，总收益率 {final_total_return:.2%}。"
        ),
        detail={
            "status": "finished",
            "final_snapshot_date": final_date.isoformat(),
            "final_equity": _round_money(final_total_value),
            "cash": _round_money(final_cash),
            "stock_value": _round_money(final_stock_value),
            "total_return": final_total_return,
            "annual_return": final_annual_return,
            "max_drawdown": max_drawdown,
            "win_rate": final_win_rate,
            "trade_count": trade_count,
            "sharpe_ratio": final_sharpe,
            "benchmark_return": final_benchmark_return,
        },
        force=True,
    )

    db.commit()


def _clear_backtest_outputs(db: Session, run_id: int) -> None:
    """清理回测明细，便于失败后重新执行同一 run_id。"""
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
    db.commit()


def _next_log_seq(db: Session, run_id: int) -> int:
    max_seq = db.query(func.max(BacktestEventLog.log_seq)).filter(
        BacktestEventLog.run_id == run_id
    ).scalar()
    return int(max_seq or 0) + 1


def _load_indicator_map(
    db: Session,
    tickers: list[str],
    start_date: date,
    end_date: date,
) -> dict[tuple[str, date], TechnicalIndicator]:
    rows = (
        db.query(TechnicalIndicator)
        .filter(
            TechnicalIndicator.ticker.in_(tickers),
            TechnicalIndicator.trading_date >= start_date,
            TechnicalIndicator.trading_date <= end_date,
        )
        .all()
    )
    return {(r.ticker, r.trading_date): r for r in rows}


def _load_sentiment_map(
    db: Session,
    tickers: list[str],
    start_date: date,
    end_date: date,
) -> dict[tuple[str, date], SentimentDaily]:
    rows = (
        db.query(SentimentDaily)
        .filter(
            SentimentDaily.ticker.in_(tickers),
            SentimentDaily.trading_date >= start_date,
            SentimentDaily.trading_date <= end_date,
        )
        .all()
    )
    return {(r.ticker, r.trading_date): r for r in rows}


def _load_benchmark_map(
    db: Session,
    benchmark: str | None,
    start_date: date,
    end_date: date,
) -> dict[date, PriceData]:
    """加载基准行情数据。
    
    1. benchmark 默认使用 SPY，即追踪 S&P 500 的可交易 ETF。
    2. 这里统一 upper，避免前端传 spy / Spy 时查不到数据。
    3. benchmark_return 允许使用区间内最近可用交易日价格计算，避免因为日期不完全对齐导致 null。
    """
    if not benchmark:
        return {}

    benchmark_ticker = benchmark.strip().upper()

    rows = (
        db.query(PriceData)
        .filter(
            PriceData.ticker == benchmark_ticker,
            PriceData.trading_date >= start_date,
            PriceData.trading_date <= end_date,
            PriceData.close.isnot(None),
        )
        .order_by(PriceData.trading_date.asc())
        .all()
    )

    return {r.trading_date: r for r in rows}

def _first_available_benchmark_price(
    benchmark_map: dict[date, PriceData],
    trading_days: list[date],
) -> float | None:
    """返回 benchmark 在回测区间内的第一笔可用价格。
    
    原本逻辑要求 benchmark 日期必须刚好等于股票池交易日。
    如果 SPY 数据存在，但日期和股票池交易日不完全对齐，就会导致 benchmark_base_price=None。
    这里改为直接从 benchmark_map 中取第一笔有效价格，更稳。
    """
    if not benchmark_map:
        return None

    for trading_day in sorted(benchmark_map.keys()):
        price = _current_price(benchmark_map.get(trading_day))
        if price is not None and price > 0:
            return price

    return None

def _benchmark_price_on_or_before(
    benchmark_map: dict[date, PriceData],
    trading_day: date,
) -> float | None:
    """获取指定日期当日或之前最近一日的 benchmark 收盘价。

    备注：
    组合回测交易日来自股票池，例如 AAPL / MSFT / NVDA。
    benchmark 使用 SPY 时，理论上交易日应大致一致，但实际落库时可能出现：
    1. SPY 数据缺某一天；
    2. 股票池最后交易日和 SPY 最后交易日不完全一致；
    3. 用户后补 SPY 数据后，日期范围不是完全从 start_date 开始。

    因此这里使用 <= trading_day 的最近一笔 benchmark 价格兜底。
    """
    if not benchmark_map:
        return None

    available_dates = [d for d in benchmark_map.keys() if d <= trading_day]

    if not available_dates:
        return None

    nearest_date = max(available_dates)
    return _current_price(benchmark_map.get(nearest_date))

def _benchmark_metrics(
    benchmark_map: dict[date, PriceData],
    benchmark_base_price: float | None,
    trading_day: date,
    initial_cash: float,
) -> tuple[float | None, float | None]:
    """计算 benchmark 当前价值和收益率。
    
    使用 trading_day 当天或之前最近一笔 benchmark 价格。
    这样即使 SPY 某一天缺数据，也不会直接导致 benchmark_return=null。
    """
    price = _benchmark_price_on_or_before(benchmark_map, trading_day)

    if price is None or benchmark_base_price is None or benchmark_base_price <= 0:
        return None, None

    benchmark_return = price / benchmark_base_price - 1
    benchmark_value = initial_cash * (1 + benchmark_return)

    return benchmark_value, benchmark_return

def _build_signal(
    *,
    db: Session,
    ticker: str,
    trading_day: date,
    price_row: PriceData | None,
    indicator: TechnicalIndicator | None,
    sentiment: SentimentDaily | None,
    model_bundle: RuntimeBacktestModelBundle,
    forecast_days: int,
) -> dict[str, Any]:
    """生成单日交易信号。

    优先调用 active classifier / regressor 真实模型；如果某只股票某一天特征缺失，
    则降级到旧规则信号，保证回测动画不中断。主模型加载失败会在回测启动阶段直接失败。
    """
    try:
        return _build_model_signal(
            db=db,
            ticker=ticker,
            trading_day=trading_day,
            price_row=price_row,
            sentiment=sentiment,
            model_bundle=model_bundle,
            forecast_days=forecast_days,
        )
    except Exception as exc:  # noqa: BLE001
        signal = _build_rule_signal(price_row=price_row, indicator=indicator, sentiment=sentiment)
        signal["signal_source"] = "rule_fallback"
        signal["model_signal_available"] = False
        signal["model_error"] = str(exc)
        signal.setdefault("reasons", []).append(f"模型信号生成失败，已降级规则信号：{exc}")
        return signal


def _build_model_signal(
    *,
    db: Session,
    ticker: str,
    trading_day: date,
    price_row: PriceData | None,
    sentiment: SentimentDaily | None,
    model_bundle: RuntimeBacktestModelBundle,
    forecast_days: int,
) -> dict[str, Any]:
    """使用与 PredictionService 相同的模型推理链路生成回测信号。"""
    feature_result = build_feature_dict(db, ticker, base_trading_date=trading_day)
    feature_dict = feature_result["feature_dict"]

    validate_feature_columns(feature_dict, model_bundle.classifier_model.feature_columns)
    validate_feature_columns(feature_dict, model_bundle.reg_model.feature_columns)

    classification = predict_classifier(model_bundle.classifier_model, feature_dict)
    predicted_returns = predict_regressor(model_bundle.reg_model, feature_dict)[:forecast_days]

    predicted_label = classification["predicted_label"]
    prob_up = float(classification["prob_up"])
    prob_neutral = float(classification["prob_neutral"])
    prob_down = float(classification["prob_down"])

    close = _current_price(price_row)
    previous_close = _safe_float(price_row.previous_close) if price_row else None

    daily_return = None
    if price_row and price_row.daily_return is not None:
        daily_return = float(price_row.daily_return)
    elif close is not None and previous_close:
        daily_return = close / previous_close - 1

    volatility = float(feature_dict.get("volatility_20d", 0.0) or 0.0)
    sentiment_score = float(feature_dict.get("sentiment_score", 0.0) or 0.0)
    news_count = int(float(feature_dict.get("news_count", 0.0) or 0.0))

    if predicted_returns:
        max_upside = max(0.0, max(float(x) for x in predicted_returns))
        max_downside = max(0.0, -min(float(x) for x in predicted_returns))
        avg_predicted_return = mean(float(x) for x in predicted_returns)
    else:
        max_upside = 0.0
        max_downside = 0.0
        avg_predicted_return = 0.0

    aux_signal = None
    strong_signal_score = None

    if model_bundle.aux_model is not None:
        try:
            validate_feature_columns(feature_dict, model_bundle.aux_model.feature_columns)
            aux_signal = predict_aux_classifier(model_bundle.aux_model, feature_dict)
            strong_signal_score = float(aux_signal.get("strong_signal_score"))
        except Exception:  # noqa: BLE001
            aux_signal = None
            strong_signal_score = None

    # stock_score 对齐 PredictionService 的 recommendation_score 思路：
    # 分类概率 + 回归上/下行空间 + 新闻情绪 + 波动风险 + 辅助强信号。
    stock_score = (
        50
        + (prob_up - prob_down) * 40
        + max_upside * 300
        - max_downside * 200
        + sentiment_score * 10
        - volatility * 100
    )

    if strong_signal_score is not None:
        stock_score += (strong_signal_score - 0.5) * 20

    # situation_score 更偏“市场/风险环境”：保留与原买入阈值 59 的尺度兼容。
    situation_score = (
        50
        + (prob_up - prob_down) * 30
        + avg_predicted_return * 450
        - max_downside * 220
        + sentiment_score * 15
        - volatility * 120
    )

    if predicted_label == "up":
        situation_score += 4
    elif predicted_label == "down":
        situation_score -= 4

    if strong_signal_score is not None:
        situation_score += (strong_signal_score - 0.5) * 10

    stock_score = _clamp(stock_score, 0, 100)
    situation_score = _clamp(situation_score, 0, 100)
    predicted_growth_prob = prob_up

    if stock_score >= 54 and situation_score >= 59:
        action_hint = "buy"
    elif stock_score <= 42 or situation_score <= 35 or predicted_label == "down":
        action_hint = "sell"
    else:
        action_hint = "hold"

    model_match_status = model_bundle.classifier_match_status
    if model_bundle.classifier_match_status != model_bundle.reg_match_status:
        model_match_status = (
            "nearest_active_model"
            if "nearest_active_model"
            in {model_bundle.classifier_match_status, model_bundle.reg_match_status}
            else model_bundle.classifier_match_status
        )

    reasons = [
        f"模型预测方向 {predicted_label}",
        f"上涨概率 {prob_up:.1%}，震荡概率 {prob_neutral:.1%}，下跌概率 {prob_down:.1%}",
        f"未来 {forecast_days} 日最大上行 {max_upside:.2%}，最大下行 {max_downside:.2%}",
        f"新闻情绪分 {sentiment_score:.2f}",
    ]

    if strong_signal_score is not None:
        reasons.append(f"辅助强信号模型得分 {strong_signal_score:.3f}")

    return {
        # 保持原有信号字段，前端接口结构不用改。
        "stock_score": round(stock_score, 4),
        "situation_score": round(situation_score, 4),
        "predicted_growth_prob": round(predicted_growth_prob, 4),
        "action_hint": action_hint,
        "close": _round_price(close),
        "daily_return": daily_return,
        "sentiment_score": (
            float(sentiment.sentiment_score)
            if sentiment and sentiment.sentiment_score is not None
            else sentiment_score
        ),
        "news_count": sentiment.news_count if sentiment else news_count,
        "reasons": reasons,

        # 这些字段只进入 signal_json，frames/status/summary 的顶层结构不变。
        "signal_source": "model",
        "model_signal_available": True,
        "feature_source": feature_result.get("feature_source"),
        "feature_base_trading_date": (
            feature_result.get("base_trading_date").isoformat()
            if feature_result.get("base_trading_date")
            else None
        ),
        "predicted_label": predicted_label,
        "prob_up": round(prob_up, 6),
        "prob_neutral": round(prob_neutral, 6),
        "prob_down": round(prob_down, 6),
        "predicted_returns": [round(float(x), 6) for x in predicted_returns],
        "avg_predicted_return": round(avg_predicted_return, 6),
        "max_predicted_upside_pct": round(max_upside, 6),
        "max_predicted_downside_pct": round(max_downside, 6),
        "volatility_20d": round(volatility, 6),
        "classifier_model_version": model_bundle.classifier_model.version.version_name,
        "reg_model_version": model_bundle.reg_model.version.version_name,
        "model_match_status": model_match_status,
        "aux_model": aux_signal,
    }


def _build_rule_signal(
    price_row: PriceData | None,
    indicator: TechnicalIndicator | None,
    sentiment: SentimentDaily | None,
) -> dict[str, Any]:
    """旧版规则信号，只作为单日模型特征缺失时的降级方案。"""
    close = _current_price(price_row)
    previous_close = _safe_float(price_row.previous_close) if price_row else None

    daily_return = None
    if price_row and price_row.daily_return is not None:
        daily_return = float(price_row.daily_return)
    elif close is not None and previous_close:
        daily_return = close / previous_close - 1

    stock_score = 50.0
    situation_score = 50.0
    reasons: list[str] = []

    if daily_return is not None:
        stock_score += _clamp(daily_return * 180, -12, 12)
        situation_score += _clamp(daily_return * 120, -8, 8)
        reasons.append(f"当日收益率 {daily_return:.2%}")

    if indicator:
        ma5 = _safe_float(indicator.ma5)
        ma20 = _safe_float(indicator.ma20)
        ma60 = _safe_float(indicator.ma60)
        rsi = indicator.rsi
        macd = indicator.macd
        volatility_20d = indicator.volatility_20d

        if close and ma5:
            stock_score += _clamp((close / ma5 - 1) * 120, -8, 8)

        if close and ma20:
            stock_score += _clamp((close / ma20 - 1) * 160, -12, 12)
            situation_score += 6 if close >= ma20 else -6
            reasons.append("价格高于 MA20" if close >= ma20 else "价格低于 MA20")

        if close and ma60:
            stock_score += _clamp((close / ma60 - 1) * 80, -6, 6)

        if rsi is not None:
            if rsi < 30:
                stock_score += 6
                reasons.append("RSI 偏低，存在技术反弹可能")
            elif rsi > 75:
                stock_score -= 8
                situation_score -= 5
                reasons.append("RSI 偏高，存在短期过热风险")
            elif 45 <= rsi <= 65:
                stock_score += 3

        if macd is not None:
            stock_score += 5 if macd > 0 else -4
            situation_score += 3 if macd > 0 else -3

        if volatility_20d is not None and volatility_20d > 0.06:
            situation_score -= 5
            reasons.append("20 日波动率较高")

    if sentiment:
        sentiment_score = float(sentiment.sentiment_score or 0)
        stock_score += _clamp(sentiment_score * 18, -10, 10)
        situation_score += _clamp(sentiment_score * 25, -15, 15)

        if (sentiment.positive_news_count or 0) > (sentiment.negative_news_count or 0):
            situation_score += 3
        elif (sentiment.negative_news_count or 0) > (sentiment.positive_news_count or 0):
            situation_score -= 3

        reasons.append(f"新闻情绪分 {sentiment_score:.2f}")

    stock_score = _clamp(stock_score, 0, 100)
    situation_score = _clamp(situation_score, 0, 100)
    predicted_growth_prob = stock_score / 100

    if stock_score >= 54 and situation_score >= 59:
        action_hint = "buy"
    elif stock_score <= 42 or situation_score <= 35:
        action_hint = "sell"
    else:
        action_hint = "hold"

    return {
        "stock_score": round(stock_score, 4),
        "situation_score": round(situation_score, 4),
        "predicted_growth_prob": round(predicted_growth_prob, 4),
        "action_hint": action_hint,
        "close": _round_price(close),
        "daily_return": daily_return,
        "sentiment_score": (
            float(sentiment.sentiment_score)
            if sentiment and sentiment.sentiment_score is not None
            else None
        ),
        "news_count": sentiment.news_count if sentiment else None,
        "reasons": reasons,
        "signal_source": "rule",
        "model_signal_available": False,
    }

def _should_sell(
    position: RuntimePosition,
    price: float,
    signal: dict[str, Any],
    params: dict[str, Any],
) -> str | None:
    total_pnl_pct = _safe_div(
        price * position.quantity - position.cost_amount,
        position.cost_amount,
    ) or 0.0

    if total_pnl_pct <= float(params["stop_loss_pct"]):
        return f"触发止损，累计收益率 {total_pnl_pct:.2%}"

    if total_pnl_pct >= float(params["take_profit_pct"]):
        return f"触发止盈，累计收益率 {total_pnl_pct:.2%}"

    if signal.get("stock_score", 50) < float(params["sell_score_threshold"]):
        return f"股票评分下降到 {signal.get('stock_score'):.2f}，低于卖出阈值"

    if signal.get("situation_score", 50) < 35:
        return f"情况分下降到 {signal.get('situation_score'):.2f}，风险偏高"

    return None


def _calculate_stock_value(
    positions: dict[str, RuntimePosition],
    day_prices: dict[str, PriceData],
) -> float:
    total = 0.0

    for ticker, position in positions.items():
        price = _current_price(day_prices.get(ticker))

        if price is None and position.price_curve:
            price = float(position.price_curve[-1]["close"])

        if price is not None:
            total += position.quantity * price

    return total

def _write_daily_positions(
    *,
    db: Session,
    run: BacktestRun,
    trading_day: date,
    positions: dict[str, RuntimePosition],
    ticker_tracks: dict[str, RuntimeTickerTrack],
    day_prices: dict[str, PriceData],
    total_value: float,
) -> None:
    """写入前端动画每日股票池状态。

    旧逻辑只写仍在持仓的 positions，导致股票卖出后从 active_positions 消失。
    新逻辑按 run.tickers_json 写入股票池所有 ticker：
    1. 持仓中：quantity / stock_value / position_ratio 正常计算；
    2. 已卖出：quantity=0、stock_value=0、position_ratio=0，但继续保留后续价格曲线；
    3. 从未买入：quantity=0、stock_value=0、position_ratio=0，也返回 stock_score / situation_score。
    """
    tickers = _normalize_tickers(run.tickers_json or [])

    for ticker in tickers:
        track = ticker_tracks.get(ticker) or RuntimeTickerTrack(ticker=ticker)
        position = positions.get(ticker)

        raw_price = _current_price(day_prices.get(ticker))
        price = raw_price
        has_price_today = raw_price is not None

        # 某只股票当天缺行情时，用它最近一个有效收盘价兜底，保证前端列表字段稳定。
        # price_curve 只记录真实有行情的交易日，不伪造缺失日价格点。
        if price is None and track.price_curve:
            price = float(track.price_curve[-1]["close"])

        previous_close = None
        if position and len(position.price_curve) >= 2:
            previous_close = float(position.price_curve[-2]["close"])
        elif len(track.price_curve) >= 2:
            previous_close = float(track.price_curve[-2]["close"])

        if position and price is not None:
            quantity = position.quantity
            current_value = quantity * price
            cost_price = position.cost_price
            cost_amount = position.cost_amount
            buy_date = position.buy_date
            daily_pnl = (price - previous_close) * quantity if previous_close else 0.0
            daily_pnl_pct = _safe_div(price - previous_close, previous_close) if previous_close else 0.0
            total_pnl = current_value - position.cost_amount
            total_pnl_pct = _safe_div(total_pnl, position.cost_amount) or 0.0
            position_ratio = _safe_div(current_value, total_value) or 0.0
            holding_status = "holding"
        else:
            quantity = 0
            current_value = 0.0
            cost_price = track.cost_price
            cost_amount = track.cost_amount
            buy_date = track.latest_buy_date
            daily_pnl = 0.0
            daily_pnl_pct = 0.0
            total_pnl = track.realized_pnl if track.realized_pnl is not None else 0.0
            total_pnl_pct = track.realized_pnl_pct if track.realized_pnl_pct is not None else 0.0
            position_ratio = 0.0
            holding_status = track.holding_status

        signal_json = {
            **(track.latest_signal or {}),
            "holding_status": holding_status,
            "is_active_position": quantity > 0,
            "latest_buy_date": buy_date.isoformat() if buy_date else None,
            "latest_sell_date": track.latest_sell_date.isoformat() if track.latest_sell_date else None,
            "sell_order": track.sell_order,
            "trade_markers": track.trade_markers,
            "has_price_today": has_price_today,
        }

        db.add(
            BacktestDailyPosition(
                run_id=run.id,
                snapshot_date=trading_day,
                ticker=ticker,
                buy_date=buy_date,
                quantity=quantity,
                current_price=_round_price(price),
                cost_price=_round_price(cost_price),
                cost_amount=_round_money(cost_amount),
                stock_value=_round_money(current_value),
                daily_pnl=_round_money(daily_pnl),
                daily_pnl_pct=daily_pnl_pct,
                total_pnl=_round_money(total_pnl),
                total_pnl_pct=total_pnl_pct,
                position_ratio=position_ratio,
                stock_score=track.latest_score,
                situation_score=track.latest_situation_score,
                price_curve_json=track.price_curve,
                signal_json=signal_json,
            )
        )

def _write_final_positions(
    *,
    db: Session,
    run: BacktestRun,
    final_date: date,
    positions: dict[str, RuntimePosition],
    day_prices: dict[str, PriceData],
    total_value: float,
    stock_map: dict[str, Stock],
) -> None:
    db.query(UserSimulatedPosition).filter(
        UserSimulatedPosition.source_run_id == run.id
    ).delete(synchronize_session=False)

    for ticker, position in positions.items():
        price = _current_price(day_prices.get(ticker))

        if price is None and position.price_curve:
            price = float(position.price_curve[-1]["close"])

        if price is None:
            continue

        current_value = position.quantity * price
        total_pnl = current_value - position.cost_amount
        total_pnl_pct = _safe_div(total_pnl, position.cost_amount) or 0.0
        position_ratio = _safe_div(current_value, total_value) or 0.0
        stock = stock_map.get(ticker)

        db.add(
            UserSimulatedPosition(
                user_id=run.user_id,
                source_run_id=run.id,
                snapshot_date=final_date,
                ticker=ticker,
                company_name=stock.company_name if stock else ticker,
                quantity=position.quantity,
                current_price=_round_price(price),
                cost_price=_round_price(position.cost_price),
                cost_amount=_round_money(position.cost_amount),
                stock_value=_round_money(current_value),
                total_pnl=_round_money(total_pnl),
                total_pnl_pct=total_pnl_pct,
                position_ratio=position_ratio,
                price_curve_json=position.price_curve,
            )
        )


def _annualized_return(total_return: float, trading_day_count: int) -> float:
    if trading_day_count <= 0:
        return 0.0

    if total_return <= -1:
        return -1.0

    return (1 + total_return) ** (252 / trading_day_count) - 1


def _sharpe_ratio(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0

    std = pstdev(daily_returns)

    if std == 0:
        return 0.0

    return (mean(daily_returns) / std) * math.sqrt(252)


def get_run_for_user(db: Session, run_id: int, user_id: int, is_admin: bool) -> BacktestRun:
    q = db.query(BacktestRun).filter(BacktestRun.id == run_id)

    if not is_admin:
        q = q.filter(BacktestRun.user_id == user_id)

    run = q.first()

    if not run:
        raise AppException(BACKTEST_RUN_NOT_FOUND, "回测任务不存在。", 404)

    return run


def run_status(db: Session, run: BacktestRun) -> dict:
    last_log = (
        db.query(BacktestEventLog)
        .filter(BacktestEventLog.run_id == run.id)
        .order_by(BacktestEventLog.id.desc())
        .first()
    )
    last_snapshot = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.run_id == run.id)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .first()
    )

    final_positions_ready = (
        db.query(UserSimulatedPosition)
        .filter(UserSimulatedPosition.source_run_id == run.id)
        .count()
        > 0
    ) or (run.status == "finished" and run.final_snapshot_date is not None)

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
        "total_value": _safe_float(s.total_value),
        "stock_value": _safe_float(s.stock_value),
        "cash": _safe_float(s.cash),
        "daily_return": s.daily_return,
        "total_return": s.total_return,
        "annual_return": s.annual_return,
        "max_drawdown": s.max_drawdown,
        "win_rate": s.win_rate,
        "trade_count": s.trade_count,
        "sharpe_ratio": s.sharpe_ratio,
        "benchmark_return": s.benchmark_return,
    }

def _position_signal(p: BacktestDailyPosition) -> dict[str, Any]:
    return dict(p.signal_json or {})


def position_to_dict(db: Session, p: BacktestDailyPosition) -> dict:
    stock = db.query(Stock).filter(Stock.ticker == p.ticker).first()
    signal = _position_signal(p)
    trade_markers = signal.get("trade_markers") or []
    holding_status = signal.get("holding_status") or ("holding" if (p.quantity or 0) > 0 else "watching")
    is_active_position = bool(signal.get("is_active_position", (p.quantity or 0) > 0))

    return {
        "ticker": p.ticker,
        "company_name": stock.company_name if stock else None,
        "buy_date": p.buy_date.isoformat() if p.buy_date else None,
        "latest_buy_date": signal.get("latest_buy_date") or (p.buy_date.isoformat() if p.buy_date else None),
        "latest_sell_date": signal.get("latest_sell_date"),
        "sell_order": signal.get("sell_order"),
        "holding_status": holding_status,
        "is_active_position": is_active_position,
        "price_curve_from_buy": p.price_curve_json or [],
        "price_curve": p.price_curve_json or [],
        "trade_markers": trade_markers,
        "stock_score": p.stock_score,
        "situation_score": p.situation_score,
        "signal": signal,
        "has_price_today": signal.get("has_price_today"),
        "stock_value": _safe_float(p.stock_value),
        "quantity": p.quantity,
        "quantitiy": p.quantity,  # 兼容旧前端拼写错误，推荐前端改用 quantity。
        "current_price": _safe_float(p.current_price),
        "cost_price": _safe_float(p.cost_price),
        "cost_amount": _safe_float(p.cost_amount),
        "daily_pnl": _safe_float(p.daily_pnl),
        "daily_pnl_pct": p.daily_pnl_pct,
        "total_pnl": _safe_float(p.total_pnl),
        "total_pnl_pct": p.total_pnl_pct,
        "position_ratio": p.position_ratio,
        "stock_value_pct": p.position_ratio,  # 前端可直接当“总股票价值百分比”使用。
        "postion_ration": p.position_ratio,  # 兼容旧前端拼写错误，推荐前端改用 position_ratio。
        "pnl_pct": p.total_pnl_pct,  # 兼容旧前端，推荐前端改用 total_pnl_pct。
    }


def sort_position_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """前端展示顺序：持仓在上，0% 在下，已卖出按卖出先后放到底部。

    规则：
    1. position_ratio > 0 的真实持仓优先，比例越大越靠前；
    2. 从未买入 / 观察中股票在中间，ticker 升序；
    3. 已卖出股票放最后；若多个均为 0%，越早卖出的越靠后。
    """
    def key(item: dict[str, Any]) -> tuple:
        ratio = float(item.get("position_ratio") or 0.0)
        status = item.get("holding_status")
        sell_order = item.get("sell_order")

        if ratio > 0:
            return (0, -ratio, item.get("ticker") or "")

        if status == "sold":
            # sell_order 越小代表越早卖出；用负号让越早卖出的排在更后面。
            return (2, -(int(sell_order or 0)), item.get("ticker") or "")

        return (1, item.get("ticker") or "")

    return sorted(items, key=key)


def trade_to_dict(t: BacktestTrade) -> dict:
    return {
        "trade_id": t.id,
        "trade_date": t.trade_date.isoformat() if t.trade_date else None,
        "ticker": t.ticker,
        "side": t.side,
        "price": _safe_float(t.price),
        "quantity": t.quantity,
        "amount": _safe_float(t.amount),
        "fee": _safe_float(t.fee),
        "cash_after": _safe_float(t.cash_after),
        "position_after": t.position_after,
        "reason": t.reason,
        "signal": t.signal_json or {},
    }

def _normalize_trade_log_detail(log: BacktestEventLog) -> dict:
    detail = dict(log.detail_json or {})

    if log.ticker and not detail.get("ticker"):
        detail["ticker"] = log.ticker

    if log.action in ("buy", "sell") and not detail.get("side"):
        detail["side"] = log.action

    return detail


def log_to_dict(log: BacktestEventLog) -> dict:
    log_time = log.log_time or log.created_at

    return {
        "log_id": log.id,
        "log_seq": log.log_seq,
        "time": log_time.isoformat() if log_time else None,
        "trading_date": log.trading_date.isoformat() if log.trading_date else None,
        "level": log.level,
        "event_type": log.event_type,
        "ticker": log.ticker,
        "action": log.action,
        "message": log.message,
        "detail": _normalize_trade_log_detail(log),
    }


def build_day_detail(db: Session, run: BacktestRun, target_date: date) -> dict:
    snapshot = (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.run_id == run.id,
            PortfolioSnapshot.snapshot_date == target_date,
        )
        .first()
    )

    if not snapshot:
        raise AppException(BACKTEST_NOT_READY, "回测结果尚未生成到对应日期。", 404)

    positions = (
        db.query(BacktestDailyPosition)
        .filter(
            BacktestDailyPosition.run_id == run.id,
            BacktestDailyPosition.snapshot_date == target_date,
        )
        .order_by(BacktestDailyPosition.ticker.asc(), BacktestDailyPosition.buy_date.asc())
        .all()
    )
    trades = (
        db.query(BacktestTrade)
        .filter(
            BacktestTrade.run_id == run.id,
            BacktestTrade.trade_date == target_date,
        )
        .order_by(BacktestTrade.id.asc())
        .all()
    )
    logs = (
        db.query(BacktestEventLog)
        .filter(
            BacktestEventLog.run_id == run.id,
            BacktestEventLog.trading_date == target_date,
        )
        .order_by(BacktestEventLog.id.asc())
        .all()
    )

    return {
        "run_id": run.id,
        "date": target_date.isoformat(),
        "metrics": snapshot_to_metrics(snapshot),
        "active_positions": sort_position_items([position_to_dict(db, p) for p in positions]),
        "trades": [trade_to_dict(t) for t in trades],
        "logs": [log_to_dict(l) for l in logs],
    }

def final_positions(db: Session, run: BacktestRun) -> dict:
    snapshot = None

    if run.final_snapshot_date:
        snapshot = (
            db.query(PortfolioSnapshot)
            .filter(
                PortfolioSnapshot.run_id == run.id,
                PortfolioSnapshot.snapshot_date == run.final_snapshot_date,
            )
            .first()
        )

    if not snapshot:
        snapshot = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.run_id == run.id)
            .order_by(PortfolioSnapshot.snapshot_date.desc())
            .first()
        )

    final_date = run.final_snapshot_date or (snapshot.snapshot_date if snapshot else None)

    # 新逻辑优先使用最终日 backtest_daily_positions。
    # 因为该表现在包含股票池所有 ticker，而 user_simulated_positions 只保存最终真实持仓。
    final_daily_positions = []
    if final_date:
        final_daily_positions = (
            db.query(BacktestDailyPosition)
            .filter(
                BacktestDailyPosition.run_id == run.id,
                BacktestDailyPosition.snapshot_date == final_date,
            )
            .order_by(BacktestDailyPosition.ticker.asc(), BacktestDailyPosition.buy_date.asc())
            .all()
        )

    if final_daily_positions:
        position_items = sort_position_items([position_to_dict(db, p) for p in final_daily_positions])
    else:
        positions = (
            db.query(UserSimulatedPosition)
            .filter(UserSimulatedPosition.source_run_id == run.id)
            .order_by(UserSimulatedPosition.ticker.asc())
            .all()
        )

        if not snapshot and not positions:
            raise AppException(BACKTEST_FINAL_POSITION_NOT_FOUND, "回测最终持仓不存在或尚未生成。", 404)

        position_items = [
            {
                "ticker": p.ticker,
                "company_name": p.company_name,
                "quantity": p.quantity,
                "quantitiy": p.quantity,
                "current_price": _safe_float(p.current_price),
                "cost_price": _safe_float(p.cost_price),
                "cost_amount": _safe_float(p.cost_amount),
                "stock_value": _safe_float(p.stock_value),
                "total_pnl": _safe_float(p.total_pnl),
                "total_pnl_pct": p.total_pnl_pct,
                "pnl_pct": p.total_pnl_pct,
                "position_ratio": p.position_ratio,
                "stock_value_pct": p.position_ratio,
                "postion_ration": p.position_ratio,
                "holding_status": "holding" if (p.quantity or 0) > 0 else "watching",
                "is_active_position": (p.quantity or 0) > 0,
                "price_curve_from_buy": p.price_curve_json or [],
                "price_curve": p.price_curve_json or [],
                "trade_markers": [],
            }
            for p in positions
        ]

    return {
        "run_id": run.id,
        "snapshot_date": final_date.isoformat() if final_date else None,
        "total_value": _safe_float(snapshot.total_value) if snapshot else _safe_float(run.final_equity),
        "cash": _safe_float(snapshot.cash) if snapshot else None,
        "stock_value": _safe_float(snapshot.stock_value) if snapshot else None,
        "total_return": run.total_return,
        "positions": position_items,
    }