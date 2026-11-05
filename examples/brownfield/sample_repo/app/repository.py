"""Persistence for links."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Link


class LinkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, code: str, target_url: str) -> Link:
        link = Link(code=code, target_url=target_url)
        self._session.add(link)
        self._session.commit()
        self._session.refresh(link)
        return link

    def get_by_code(self, code: str) -> Link | None:
        return self._session.scalar(select(Link).where(Link.code == code))

    def code_exists(self, code: str) -> bool:
        return self.get_by_code(code) is not None

    def record_click(self, link: Link) -> None:
        link.clicks += 1
        self._session.commit()
