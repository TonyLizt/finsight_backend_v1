"""写入演示数据，方便前端和接口联调。

运行：
    python -m app.scripts.seed_demo

默认创建：
- admin / Admin123
- user01 / User123
- AAPL 股票、若干行情、新闻、模型版本
"""

from datetime import date, datetime, timedelta

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.core.security import get_password_hash
from app.models.all_models import (
    Role,
    User,
    Stock,
    PriceData,
    NewsData,
    SentimentDaily,
    TechnicalIndicator,
    ModelVersion,
)


def get_role(db, name: str):
    role = db.query(Role).filter(Role.role_name == name).first()
    if not role:
        role = Role(role_name=name, description=name)
        db.add(role)
        db.commit()
    return role


def upsert_user(db, username: str, password: str, role_name: str):
    role = get_role(db, role_name)
    u = db.query(User).filter(User.username == username).first()
    if not u:
        u = User(username=username, password_hash=get_password_hash(password), role_id=role.id, status="active")
        db.add(u)
        db.commit()
    return u


def main():
    init_db()
    db = SessionLocal()
    try:
        upsert_user(db, "admin", "Admin123", "admin")
        upsert_user(db, "user01", "User123", "user")

        stock = db.query(Stock).filter(Stock.ticker == "AAPL").first()
        if not stock:
            stock = Stock(
                ticker="AAPL",
                company_name="Apple Inc.",
                security_name="Apple Inc. - Common Stock",
                market="NASDAQ",
                exchange="Q",
                listing_source="demo",
                etf=False,
                is_supported=True,
                is_core_pool=True,
                data_quality_score=0.96,
            )
            db.add(stock)
            db.commit()

        start = date.today() - timedelta(days=40)
        price = 190.0
        prev_close = None
        for i in range(40):
            d = start + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            close = price + (i % 7 - 3) * 0.6
            open_ = close - 0.8
            high = close + 1.2
            low = close - 1.4
            previous_close = prev_close or close - 0.5
            exists = db.query(PriceData).filter(PriceData.ticker == "AAPL", PriceData.trading_date == d).first()
            if not exists:
                db.add(
                    PriceData(
                        ticker="AAPL",
                        trading_date=d,
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        adj_close=close,
                        previous_close=previous_close,
                        change_amount=close - previous_close,
                        change_percent=(close - previous_close) / previous_close,
                        daily_return=(close - previous_close) / previous_close,
                        amplitude=(high - low) / previous_close,
                        volume=50_000_000 + i * 1000,
                    )
                )
            prev_close = close
        db.commit()

        if db.query(NewsData).filter(NewsData.ticker == "AAPL").count() == 0:
            now = datetime.utcnow()
            db.add(
                NewsData(
                    ticker="AAPL",
                    publish_time=now,
                    assigned_trading_date=date.today(),
                    title="Apple shares rise after analyst upgrade",
                    summary="Analysts raised the target price for Apple.",
                    content_text="Full demo article text for Apple news.",
                    source="Demo News",
                    url="https://example.com/news/apple",
                    sentiment_score=0.42,
                    sentiment_label="positive",
                    news_llm_analysis="该新闻反映分析师预期改善，对短期情绪有正面作用。",
                    content_status="fetched",
                    content_fetched_at=now,
                )
            )
        if db.query(SentimentDaily).filter(SentimentDaily.ticker == "AAPL").count() == 0:
            db.add(
                SentimentDaily(
                    ticker="AAPL",
                    trading_date=date.today(),
                    news_start_time=datetime.utcnow() - timedelta(days=7),
                    news_end_time=datetime.utcnow(),
                    news_count=10,
                    positive_news_count=6,
                    negative_news_count=2,
                    neutral_news_count=2,
                    sentiment_score=0.31,
                    sentiment_label="positive",
                )
            )
        if db.query(ModelVersion).filter(ModelVersion.version_name == "xgb_cls_h5_v1.0").count() == 0:
            db.add(ModelVersion(version_name="xgb_cls_h5_v1.0", model_type="classifier", algorithm="XGBoost", horizon_days=5, accuracy=0.572, f1_score=0.56, feature_version="feature_v1", is_active=True))
        if db.query(ModelVersion).filter(ModelVersion.version_name == "xgb_reg_h5_v1.0").count() == 0:
            db.add(ModelVersion(version_name="xgb_reg_h5_v1.0", model_type="regressor", algorithm="XGBoost", horizon_days=5, mae=0.012, rmse=0.021, feature_version="feature_v1", is_active=True))
        db.commit()
        print("Seed demo data inserted.")
        print("Admin: admin / Admin123")
        print("User:  user01 / User123")
    finally:
        db.close()


if __name__ == "__main__":
    main()
