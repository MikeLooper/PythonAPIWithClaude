"""Application factory and entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from anthropic import AsyncAnthropic
from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.routes import health, questions
from app.config import Settings, get_settings
from app.services.claude_service import ClaudeService
from app.telemetry import setup_telemetry

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        api_key = settings.anthropic_api_key
        client = AsyncAnthropic(
            api_key=api_key.get_secret_value() if api_key else None,
            timeout=settings.claude_timeout_seconds,
            max_retries=settings.claude_max_retries,
        )
        app.state.claude_service = ClaudeService(client, settings)
        logger.info("Service started (model=%s)", settings.claude_model)
        try:
            yield
        finally:
            await client.close()
            if telemetry is not None:
                telemetry.shutdown()

    app = FastAPI(
        title="Python API with Claude",
        description="Ask Claude a question and get an answer.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(questions.router)
    register_exception_handlers(app)

    telemetry = setup_telemetry(app, settings) if settings.otel_enabled else None
    return app


def run() -> None:
    """Run the service with uvicorn using configured host/port."""
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    run()
