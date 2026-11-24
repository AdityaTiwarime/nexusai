"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.db.models  # noqa: F401  (registers all ORM models before first use)
from app import __version__
from app.api import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.rate_limit import build_rate_limiter
from app.db.session import Database
from app.llm.embeddings import build_embedder
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.documents.router import router as documents_router
from app.modules.documents.storage import LocalStorage
from app.modules.organizations.router import router as organizations_router
from app.modules.rag.answer import build_answerer
from app.modules.rag.router import router as rag_router
from app.modules.users.router import router as users_router

logger = logging.getLogger("nexusai")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings)
        app.state.storage = LocalStorage(settings.storage_dir)
        app.state.rate_limiter = build_rate_limiter(settings.redis_url, settings.rate_limit_enabled)
        app.state.embedder = build_embedder(settings)  # embeds search queries
        app.state.answerer = build_answerer(settings)
        logger.info("startup", extra={"environment": settings.environment, "version": __version__})
        try:
            yield
        finally:
            await app.state.rate_limiter.aclose()
            for client in (app.state.embedder, app.state.answerer):
                if hasattr(client, "aclose"):
                    await client.aclose()
            await app.state.db.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are handy locally but should not be public in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    # Added last so it wraps everything (including CORS) and every response gets an ID.
    app.add_middleware(RequestContextMiddleware)

    # Unhandled exceptions are turned into a 500 JSON response (with the request ID)
    # by RequestContextMiddleware, so no generic exception handler is registered here.
    routers = (
        health.router,
        auth_router,
        users_router,
        organizations_router,
        audit_router,
        documents_router,
        rag_router,
    )
    for router in routers:
        app.include_router(router, prefix=settings.api_prefix)
    return app
