"""手动同步股票基础库脚本。"""

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.crawler_service import sync_stock_universe


def main():
    init_db()
    db = SessionLocal()
    try:
        print(sync_stock_universe(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
