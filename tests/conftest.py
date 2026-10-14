"""Shared fixtures. No test talks to the real Claude API or an OTEL collector."""

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.api.dependencies import get_claude_service
from app.config import Settings
from app.main import create_app
from app.services.claude_service import ClaudeService

_SPAN_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _tracer_provider() -> None:
    """Install an in-memory tracer provider once so spans can be asserted on."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))
    trace.set_tracer_provider(provider)


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    _SPAN_EXPORTER.clear()
    yield _SPAN_EXPORTER
    _SPAN_EXPORTER.clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, otel_enabled=False)


@pytest.fixture
def make_message() -> Callable[..., BetaMessage]:
    def _make(
        text: str = "Paris is the capital of France.",
        *,
        stop_reason: str = "end_turn",
        content: list[dict[str, Any]] | None = None,
        model: str = "claude-opus-5-5",
    ) -> BetaMessage:
        return BetaMessage.model_validate(
            {
                "id": "msg_test_123",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": content if content is not None else [{"type": "text", "text": text}],
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 34},
            }
        )

    return _make


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock(spec_set=["beta", "close"])
    client.beta.messages.create = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def claude_service(mock_client: MagicMock, settings: Settings) -> ClaudeService:
    return ClaudeService(mock_client, settings)


@pytest.fixture
def client(settings: Settings, claude_service: ClaudeService) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_claude_service] = lambda: claude_service
    with TestClient(app) as test_client:
        yield test_client


_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def make_status_error(
    error_cls: type[anthropic.APIStatusError],
    status_code: int,
    headers: dict[str, str] | None = None,
) -> anthropic.APIStatusError:
    response = httpx2.Response(status_code, request=_REQUEST, headers=headers)
    return error_cls("error", response=response, body=None)


def make_connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_REQUEST)


def make_timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=_REQUEST)
