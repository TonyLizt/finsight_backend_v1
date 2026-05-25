from pydantic import BaseModel


class AddWatchlistRequest(BaseModel):
    ticker: str
    auto_fetch: bool = True
