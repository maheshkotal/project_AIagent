"""Business logic for shortening and resolving links."""

from collections.abc import Callable

from app.codes import generate_code
from app.models import Link
from app.repository import LinkRepository

MAX_CODE_ATTEMPTS = 5


class CodeGenerationError(RuntimeError):
    """Could not find a free short code."""


class ShortenerService:
    def __init__(self, repository: LinkRepository, code_factory: Callable[[], str] = generate_code) -> None:
        self._repository = repository
        self._code_factory = code_factory

    def shorten(self, url: str, custom_alias: str | None = None) -> Link:
        if custom_alias:
            return self._repository.add(custom_alias, url)
        for _ in range(MAX_CODE_ATTEMPTS):
            code = self._code_factory()
            if not self._repository.code_exists(code):
                return self._repository.add(code, url)
        raise CodeGenerationError("No free short code after several attempts")

    def resolve(self, code: str) -> Link | None:
        """Look up a link for redirection and count the click."""
        link = self._repository.get_by_code(code)
        if link is not None:
            self._repository.record_click(link)
        return link

    def stats(self, code: str) -> Link | None:
        return self._repository.get_by_code(code)
