"""HTTP routes."""

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models import Link
from app.repository import LinkRepository
from app.schemas import LinkCreate, LinkOut
from app.service import ShortenerService

router = APIRouter()


def get_service(request: Request) -> Iterator[ShortenerService]:
    session = request.app.state.session_factory()
    try:
        yield ShortenerService(LinkRepository(session))
    finally:
        session.close()


def to_out(link: Link, base_url: str) -> LinkOut:
    return LinkOut(
        code=link.code,
        short_url=f"{base_url}/{link.code}",
        target_url=link.target_url,
        created_at=link.created_at,
        clicks=link.clicks,
    )


@router.post("/api/v1/links", status_code=201, response_model=LinkOut)
def create_link(body: LinkCreate, request: Request, service: ShortenerService = Depends(get_service)) -> LinkOut:
    link = service.shorten(str(body.url), body.custom_alias)
    return to_out(link, request.app.state.settings.base_url)


@router.get("/api/v1/links/{code}", response_model=LinkOut)
def get_link_stats(code: str, request: Request, service: ShortenerService = Depends(get_service)) -> LinkOut:
    link = service.stats(code)
    if link is None:
        raise HTTPException(status_code=404, detail="Short link not found")
    return to_out(link, request.app.state.settings.base_url)


@router.get("/{code}", status_code=307, response_class=RedirectResponse)
def redirect(code: str, service: ShortenerService = Depends(get_service)) -> RedirectResponse:
    link = service.resolve(code)
    if link is None:
        raise HTTPException(status_code=404, detail="Short link not found")
    return RedirectResponse(link.target_url, status_code=307)
