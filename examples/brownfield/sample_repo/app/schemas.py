"""Request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class LinkCreate(BaseModel):
    url: HttpUrl
    custom_alias: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,32}$")


class LinkOut(BaseModel):
    code: str
    short_url: str
    target_url: str
    created_at: datetime
    clicks: int
