"""Application factory. Run with: uvicorn app.main:create_app --factory"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.api import router
from app.config import load_settings
from app.db import Base, make_engine, make_session_factory


def create_app(database_url: str | None = None) -> FastAPI:
    settings = load_settings(database_url)
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="URL Shortener", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)
    app.include_router(router)
    return app
