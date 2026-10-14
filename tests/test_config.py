import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_port == 56601
    assert settings.claude_model == "claude-opus-5-5"
    assert settings.claude_effort == "medium"
    assert settings.claude_refusal_fallback is True
    assert settings.otel_exporter_otlp_endpoint == "http://localhost:4317"
    assert settings.otel_exporter_otlp_protocol == "grpc"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "8080")
    monkeypatch.setenv("CLAUDE_EFFORT", "high")
    monkeypatch.setenv("OTEL_ENABLED", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    settings = Settings(_env_file=None)

    assert settings.app_port == 8080
    assert settings.claude_effort == "high"
    assert settings.otel_enabled is False
    assert settings.otel_exporter_otlp_protocol == "http/protobuf"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claude_effort", "extreme"),
        ("claude_max_tokens", 0),
        ("otel_exporter_otlp_protocol", "udp"),
    ],
)
def test_invalid_values_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_api_key_read_from_env_file_is_secret(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-from-dotenv\n")

    settings = Settings(_env_file=env_file)

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-from-dotenv"
    assert "sk-from-dotenv" not in repr(settings)
