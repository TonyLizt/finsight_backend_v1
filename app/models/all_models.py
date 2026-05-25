"""SQLAlchemy 数据模型。

字段命名尽量与 04 数据库与 API 文档 v5.0 保持一致。
第一版代码以实现接口为主，复杂索引和外键约束可在 Alembic 迁移中继续补充。
"""

from datetime import datetime, date
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.session import Base

# SQLite 本地调试时，主键必须编译成 INTEGER 才能自增；MySQL 下仍是 BIGINT。
PK_TYPE = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    role_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    role: Mapped[Role] = relationship(back_populates="users")


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    security_name: Mapped[str | None] = mapped_column(Text)
    market: Mapped[str | None] = mapped_column(String(50))
    exchange: Mapped[str | None] = mapped_column(String(20))
    listing_source: Mapped[str | None] = mapped_column(String(30))
    market_category: Mapped[str | None] = mapped_column(String(10))
    cqs_symbol: Mapped[str | None] = mapped_column(String(30))
    nasdaq_symbol: Mapped[str | None] = mapped_column(String(30))
    etf: Mapped[bool] = mapped_column(Boolean, default=False)
    test_issue: Mapped[bool] = mapped_column(Boolean, default=False)
    financial_status: Mapped[str | None] = mapped_column(String(10))
    round_lot_size: Mapped[int | None] = mapped_column(Integer)
    is_supported: Mapped[bool] = mapped_column(Boolean, default=True)
    is_core_pool: Mapped[bool] = mapped_column(Boolean, default=False)
    data_quality_score: Mapped[float | None] = mapped_column(Float)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime)


class StockUniverseSyncLog(Base):
    __tablename__ = "stock_universe_sync_logs"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    source_name: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(30))
    fetched_count: Mapped[int | None] = mapped_column(Integer)
    inserted_count: Mapped[int | None] = mapped_column(Integer)
    updated_count: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)


class Watchlist(Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class PriceData(Base):
    __tablename__ = "price_data"
    __table_args__ = (UniqueConstraint("ticker", "trading_date", name="uq_price_ticker_date"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    adj_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    previous_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    change_amount: Mapped[float | None] = mapped_column(Numeric(12, 4))
    change_percent: Mapped[float | None] = mapped_column(Float)
    daily_return: Mapped[float | None] = mapped_column(Float)
    amplitude: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class NewsData(Base):
    __tablename__ = "news_data"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    assigned_trading_date: Mapped[date | None] = mapped_column(Date, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    content_text: Mapped[str | None] = mapped_column(Text)
    content_html: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    news_llm_analysis: Mapped[str | None] = mapped_column(Text)
    content_status: Mapped[str | None] = mapped_column(String(30), default="not_fetched")
    content_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class TechnicalIndicator(Base):
    __tablename__ = "technical_indicators"
    __table_args__ = (UniqueConstraint("ticker", "trading_date", name="uq_indicator_ticker_date"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    return_1d: Mapped[float | None] = mapped_column(Float)
    return_3d: Mapped[float | None] = mapped_column(Float)
    return_5d: Mapped[float | None] = mapped_column(Float)
    ma5: Mapped[float | None] = mapped_column(Float)
    ma20: Mapped[float | None] = mapped_column(Float)
    ma60: Mapped[float | None] = mapped_column(Float)
    ma5_gap: Mapped[float | None] = mapped_column(Float)
    ma20_gap: Mapped[float | None] = mapped_column(Float)
    ma60_gap: Mapped[float | None] = mapped_column(Float)
    rsi: Mapped[float | None] = mapped_column(Float)
    macd: Mapped[float | None] = mapped_column(Float)
    volatility_20d: Mapped[float | None] = mapped_column(Float)
    drawdown_20d: Mapped[float | None] = mapped_column(Float)
    volume_zscore: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SentimentDaily(Base):
    __tablename__ = "sentiment_daily"
    __table_args__ = (UniqueConstraint("ticker", "trading_date", name="uq_sentiment_ticker_date"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    news_start_time: Mapped[datetime | None] = mapped_column(DateTime)
    news_end_time: Mapped[datetime | None] = mapped_column(DateTime)
    news_count: Mapped[int | None] = mapped_column(Integer)
    positive_news_count: Mapped[int | None] = mapped_column(Integer)
    negative_news_count: Mapped[int | None] = mapped_column(Integer)
    neutral_news_count: Mapped[int | None] = mapped_column(Integer)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    version_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)  # classifier/regressor
    algorithm: Mapped[str | None] = mapped_column(String(50))
    horizon_days: Mapped[int | None] = mapped_column(Integer)
    model_path: Mapped[str | None] = mapped_column(Text)
    feature_version: Mapped[str | None] = mapped_column(String(50))
    accuracy: Mapped[float | None] = mapped_column(Float)
    f1_score: Mapped[float | None] = mapped_column(Float)
    mae: Mapped[float | None] = mapped_column(Float)
    rmse: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    reg_model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    prediction_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    base_trading_date: Mapped[date | None] = mapped_column(Date)
    forecast_days: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    forecast_start_date: Mapped[date | None] = mapped_column(Date)
    forecast_end_date: Mapped[date | None] = mapped_column(Date)
    request_params_json: Mapped[dict | None] = mapped_column(JSON)
    current_price: Mapped[float | None] = mapped_column(Numeric(12, 4))
    predicted_label: Mapped[str | None] = mapped_column(String(20))
    prob_up: Mapped[float | None] = mapped_column(Float)
    prob_neutral: Mapped[float | None] = mapped_column(Float)
    prob_down: Mapped[float | None] = mapped_column(Float)
    predicted_growth_prob: Mapped[float | None] = mapped_column(Float)
    recommendation_score: Mapped[float | None] = mapped_column(Float)
    recommendation_level: Mapped[str | None] = mapped_column(String(20))
    max_predicted_upside_pct: Mapped[float | None] = mapped_column(Float)
    max_predicted_downside_pct: Mapped[float | None] = mapped_column(Float)
    predicted_prices_json: Mapped[dict | None] = mapped_column(JSON)
    sentiment_summary_json: Mapped[dict | None] = mapped_column(JSON)
    news_llm_report: Mapped[str | None] = mapped_column(Text)
    explanation_json: Mapped[dict | None] = mapped_column(JSON)
    report_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    run_name: Mapped[str | None] = mapped_column(String(100))
    tickers_json: Mapped[list | None] = mapped_column(JSON)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    initial_cash: Mapped[float | None] = mapped_column(Numeric(14, 2))
    benchmark: Mapped[str | None] = mapped_column(String(30))
    strategy_params_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    current_date: Mapped[date | None] = mapped_column(Date)
    trading_days_total: Mapped[int | None] = mapped_column(Integer)
    trading_days_done: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[float | None] = mapped_column(Float, default=0.0)
    final_snapshot_date: Mapped[date | None] = mapped_column(Date)
    final_equity: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_return: Mapped[float | None] = mapped_column(Float)
    annual_return: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True, nullable=False)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(12, 4))
    quantity: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    fee: Mapped[float | None] = mapped_column(Numeric(14, 2))
    cash_after: Mapped[float | None] = mapped_column(Numeric(14, 2))
    position_after: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    signal_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (UniqueConstraint("run_id", "snapshot_date", name="uq_portfolio_run_date"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True, nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    cash: Mapped[float | None] = mapped_column(Numeric(14, 2))
    stock_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    daily_return: Mapped[float | None] = mapped_column(Float)
    total_return: Mapped[float | None] = mapped_column(Float)
    annual_return: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float)
    benchmark_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class BacktestDailyPosition(Base):
    __tablename__ = "backtest_daily_positions"
    __table_args__ = (UniqueConstraint("run_id", "snapshot_date", "ticker", "buy_date", name="uq_position_run_date_ticker_buy"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True, nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    buy_date: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[int | None] = mapped_column(Integer)
    current_price: Mapped[float | None] = mapped_column(Numeric(12, 4))
    cost_price: Mapped[float | None] = mapped_column(Numeric(12, 4))
    cost_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    stock_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    daily_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2))
    daily_pnl_pct: Mapped[float | None] = mapped_column(Float)
    total_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_pnl_pct: Mapped[float | None] = mapped_column(Float)
    position_ratio: Mapped[float | None] = mapped_column(Float)
    stock_score: Mapped[float | None] = mapped_column(Float)
    situation_score: Mapped[float | None] = mapped_column(Float)
    price_curve_json: Mapped[list | None] = mapped_column(JSON)
    signal_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserSimulatedPosition(Base):
    __tablename__ = "user_simulated_positions"
    __table_args__ = (UniqueConstraint("user_id", "source_run_id", "ticker", name="uq_user_run_ticker"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    source_run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True, nullable=False)
    snapshot_date: Mapped[date | None] = mapped_column(Date)
    ticker: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[int | None] = mapped_column(Integer)
    current_price: Mapped[float | None] = mapped_column(Numeric(12, 4))
    cost_price: Mapped[float | None] = mapped_column(Numeric(12, 4))
    cost_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    stock_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_pnl: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_pnl_pct: Mapped[float | None] = mapped_column(Float)
    position_ratio: Mapped[float | None] = mapped_column(Float)
    price_curve_json: Mapped[list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class BacktestEventLog(Base):
    __tablename__ = "backtest_event_logs"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True, nullable=False)
    log_seq: Mapped[int | None] = mapped_column(Integer)
    log_time: Mapped[datetime | None] = mapped_column(DateTime)
    trading_date: Mapped[date | None] = mapped_column(Date, index=True)
    level: Mapped[str | None] = mapped_column(String(20))
    event_type: Mapped[str | None] = mapped_column(String(50))
    ticker: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str | None] = mapped_column(Text)
    detail_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    module: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class CrawlerLog(Base):
    __tablename__ = "crawler_logs"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(30), index=True)
    task_type: Mapped[str | None] = mapped_column(String(50))
    start_time: Mapped[datetime | None] = mapped_column(DateTime)
    end_time: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str | None] = mapped_column(Text)
    fetched_count: Mapped[int | None] = mapped_column(Integer)
