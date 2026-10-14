from unittest.mock import MagicMock

import anthropic
import pytest
from anthropic import omit
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.config import Settings
from app.services.claude_service import (
    REFUSAL_FALLBACK_BETA,
    ClaudeNotConfiguredError,
    ClaudeRefusalError,
    ClaudeService,
)
from tests.conftest import make_status_error


async def test_ask_returns_answer(
    claude_service: ClaudeService, mock_client: MagicMock, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message("Paris.")

    answer = await claude_service.ask("What is the capital of France?")

    assert answer.text == "Paris."
    assert answer.model == "claude-opus-5-5"
    assert answer.stop_reason == "end_turn"
    assert (answer.input_tokens, answer.output_tokens) == (12, 34)


async def test_ask_sends_expected_request(
    claude_service: ClaudeService, mock_client: MagicMock, settings: Settings, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message()

    await claude_service.ask("Hello?")

    mock_client.beta.messages.create.assert_awaited_once_with(
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        system=settings.claude_system_prompt,
        messages=[{"role": "user", "content": "Hello?"}],
        output_config={"effort": settings.claude_effort},
        betas=[REFUSAL_FALLBACK_BETA],
        fallbacks="default",
    )


async def test_ask_uses_overrides(
    claude_service: ClaudeService, mock_client: MagicMock, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message(model="claude-sonnet-5-5")

    await claude_service.ask("Hello?", model="claude-sonnet-5-5", max_tokens=500)

    kwargs = mock_client.beta.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["max_tokens"] == 500
    assert kwargs["fallbacks"] == "default"


async def test_fallback_omitted_for_models_without_support(
    claude_service: ClaudeService, mock_client: MagicMock, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message(model="claude-haiku-5-5")

    await claude_service.ask("Hello?", model="claude-haiku-5-5")

    kwargs = mock_client.beta.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["betas"] is omit
    assert kwargs["fallbacks"] is omit


async def test_overrides_recorded_on_span(
    claude_service: ClaudeService,
    mock_client: MagicMock,
    make_message,
    span_exporter: InMemorySpanExporter,
) -> None:
    mock_client.beta.messages.create.return_value = make_message(model="claude-sonnet-5-5")

    await claude_service.ask("Hello?", model="claude-sonnet-5-5", max_tokens=500)

    (span,) = span_exporter.get_finished_spans()
    assert span.name == "chat claude-sonnet-5-5"
    assert span.attributes["gen_ai.request.model"] == "claude-sonnet-5-5"
    assert span.attributes["gen_ai.request.max_tokens"] == 500


async def test_refusal_fallback_can_be_disabled(mock_client: MagicMock, make_message) -> None:
    service = ClaudeService(
        mock_client, Settings(_env_file=None, otel_enabled=False, claude_refusal_fallback=False)
    )
    mock_client.beta.messages.create.return_value = make_message()

    await service.ask("Hello?")

    kwargs = mock_client.beta.messages.create.call_args.kwargs
    assert kwargs["betas"] is omit
    assert kwargs["fallbacks"] is omit


async def test_ask_joins_text_blocks_and_ignores_others(
    claude_service: ClaudeService, mock_client: MagicMock, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message(
        content=[
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Hello, "},
            {"type": "text", "text": "world."},
        ]
    )

    answer = await claude_service.ask("Greet me")

    assert answer.text == "Hello, world."


async def test_truncated_answer_is_still_returned(
    claude_service: ClaudeService, mock_client: MagicMock, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message(
        "Partial", stop_reason="max_tokens"
    )

    answer = await claude_service.ask("Write a novel")

    assert answer.text == "Partial"
    assert answer.stop_reason == "max_tokens"


async def test_refusal_raises(
    claude_service: ClaudeService,
    mock_client: MagicMock,
    make_message,
    span_exporter: InMemorySpanExporter,
) -> None:
    mock_client.beta.messages.create.return_value = make_message(content=[], stop_reason="refusal")

    with pytest.raises(ClaudeRefusalError):
        await claude_service.ask("Something disallowed")

    (span,) = span_exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR


async def test_api_error_propagates_and_marks_span(
    claude_service: ClaudeService, mock_client: MagicMock, span_exporter: InMemorySpanExporter
) -> None:
    error = make_status_error(anthropic.InternalServerError, 500)
    mock_client.beta.messages.create.side_effect = error

    with pytest.raises(anthropic.InternalServerError):
        await claude_service.ask("Hello?")

    (span,) = span_exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.events[0].name == "exception"


async def test_span_records_gen_ai_attributes(
    claude_service: ClaudeService,
    mock_client: MagicMock,
    make_message,
    span_exporter: InMemorySpanExporter,
) -> None:
    mock_client.beta.messages.create.return_value = make_message()

    await claude_service.ask("Hello?")

    (span,) = span_exporter.get_finished_spans()
    assert span.name == "chat claude-opus-5-5"
    assert span.attributes["gen_ai.system"] == "anthropic"
    assert span.attributes["gen_ai.request.model"] == "claude-opus-5-5"
    assert span.attributes["gen_ai.response.id"] == "msg_test_123"
    assert span.attributes["gen_ai.usage.input_tokens"] == 12
    assert span.attributes["gen_ai.usage.output_tokens"] == 34
    assert span.attributes["gen_ai.response.finish_reasons"] == ("end_turn",)


async def test_missing_credentials_raise_not_configured(
    claude_service: ClaudeService, mock_client: MagicMock
) -> None:
    mock_client.beta.messages.create.side_effect = TypeError(
        '"Could not resolve authentication method. Expected one of api_key, ..."'
    )

    with pytest.raises(ClaudeNotConfiguredError):
        await claude_service.ask("Hello?")


async def test_unrelated_type_error_propagates(
    claude_service: ClaudeService, mock_client: MagicMock
) -> None:
    mock_client.beta.messages.create.side_effect = TypeError("unexpected keyword argument")

    with pytest.raises(TypeError, match="unexpected keyword"):
        await claude_service.ask("Hello?")
