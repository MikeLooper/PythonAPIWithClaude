from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.services.claude_service import ClaudeService


@pytest.fixture
def anthropic_cls(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace AsyncAnthropic so the lifespan never builds a real client."""
    client_cls = MagicMock(return_value=MagicMock(close=AsyncMock()))
    monkeypatch.setattr(main, "AsyncAnthropic", client_cls)
    return client_cls


def test_lifespan_creates_and_closes_claude_client(anthropic_cls: MagicMock) -> None:
    settings = Settings(_env_file=None, otel_enabled=False, anthropic_api_key="sk-test")

    app = main.create_app(settings)
    with TestClient(app):
        assert isinstance(app.state.claude_service, ClaudeService)

    anthropic_cls.assert_called_once_with(
        api_key="sk-test",
        timeout=settings.claude_timeout_seconds,
        max_retries=settings.claude_max_retries,
    )
    anthropic_cls.return_value.close.assert_awaited_once()


def test_lifespan_without_key_defers_to_sdk_resolution(
    monkeypatch: pytest.MonkeyPatch, anthropic_cls: MagicMock
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(_env_file=None, otel_enabled=False)

    with TestClient(main.create_app(settings)):
        pass

    assert anthropic_cls.call_args.kwargs["api_key"] is None


def test_telemetry_enabled_is_set_up_and_shut_down(
    monkeypatch: pytest.MonkeyPatch, anthropic_cls: MagicMock
) -> None:
    setup = MagicMock()
    monkeypatch.setattr(main, "setup_telemetry", setup)
    settings = Settings(_env_file=None, otel_enabled=True)

    app = main.create_app(settings)
    with TestClient(app):
        pass

    setup.assert_called_once_with(app, settings)
    setup.return_value.shutdown.assert_called_once()


def test_telemetry_disabled_is_skipped(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    setup = MagicMock()
    monkeypatch.setattr(main, "setup_telemetry", setup)

    main.create_app(settings)

    setup.assert_not_called()


def test_run_starts_uvicorn_on_configured_port(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    uvicorn_run = MagicMock()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main.uvicorn, "run", uvicorn_run)

    main.run()

    assert uvicorn_run.call_args.kwargs == {"host": "127.0.0.1", "port": 56601}
