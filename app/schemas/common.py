"""通用 Pydantic Schema。"""

from pydantic import BaseModel, Field


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)


class ReasonPayload(BaseModel):
    reason: str | None = None
