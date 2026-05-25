from pydantic import BaseModel


class StockUniverseSyncRequest(BaseModel):
    force: bool = False
