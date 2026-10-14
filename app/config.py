"""Application settings, loaded from environment variables and an optional ``.env`` file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions submitted through an API. "
    "Answer accurately and concisely. If you are unsure, say so."
)


class Settings(BaseSettings):
    """Runtime configuration.

    ``ANTHROPIC_API_KEY`` is read here so a key kept in ``.env`` reaches the SDK (pydantic-settings
    does not export ``.env`` values to the process environment). When unset, the SDK falls back to
    its own credential resolution.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "python-api-with-claude"
    app_host: str = "127.0.0.1"
    app_port: int = 56601
    log_level: str = "INFO"

    anthropic_api_key: SecretStr | None = None
    claude_model: str = "claude-opus-5-5"
    claude_max_tokens: int = Field(default=16000, gt=0)
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_timeout_seconds: float = Field(default=120.0, gt=0)
    claude_max_retries: int = Field(default=2, ge=0)
    claude_refusal_fallback: bool = True
    claude_system_prompt: str = DEFAULT_SYSTEM_PROMPT

    otel_enabled: bool = True
    otel_service_name: str = "python-api-with-claude"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_exporter_otlp_protocol: Literal["grpc", "http/protobuf"] = "grpc"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
