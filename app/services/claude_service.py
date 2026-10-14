"""Thin wrapper around the Anthropic SDK used to answer questions."""

import logging
import time
from dataclasses import dataclass

from anthropic import AsyncAnthropic, Omit, omit
from opentelemetry import metrics, trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from app.config import Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

token_usage_histogram = meter.create_histogram(
    "gen_ai.client.token.usage",
    unit="{token}",
    description="Number of tokens used per Claude request.",
)
operation_duration_histogram = meter.create_histogram(
    "gen_ai.client.operation.duration",
    unit="s",
    description="Duration of Claude requests.",
)

# Server-side refusal fallback: if Claude's safety classifiers decline a request, the API
# re-runs it on Anthropic's recommended fallback model within the same call.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept `fallbacks="default"`; other models are called without it.
REFUSAL_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)


class ClaudeRefusalError(Exception):
    """Raised when Claude declines to answer a question."""


class ClaudeNotConfiguredError(Exception):
    """Raised when no Anthropic credentials (e.g. ANTHROPIC_API_KEY) are available."""


@dataclass(frozen=True, slots=True)
class ClaudeAnswer:
    text: str
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int


class ClaudeService:
    """Sends a single question to Claude and returns the text answer."""

    def __init__(self, client: AsyncAnthropic, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def ask(
        self, question: str, *, model: str | None = None, max_tokens: int | None = None
    ) -> ClaudeAnswer:
        """Ask Claude a question; ``model`` and ``max_tokens`` override the configured defaults."""
        settings = self._settings
        model = model or settings.claude_model
        max_tokens = max_tokens or settings.claude_max_tokens
        base_attributes = {
            "gen_ai.system": "anthropic",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
            "gen_ai.request.max_tokens": max_tokens,
        }
        use_fallback = settings.claude_refusal_fallback and model in REFUSAL_FALLBACK_MODELS
        fallbacks: str | Omit = "default" if use_fallback else omit
        betas: list[str] | Omit = [REFUSAL_FALLBACK_BETA] if use_fallback else omit

        with tracer.start_as_current_span(
            f"chat {model}", kind=SpanKind.CLIENT, attributes=base_attributes
        ) as span:
            start = time.perf_counter()
            try:
                response = await self._client.beta.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=settings.claude_system_prompt,
                    messages=[{"role": "user", "content": question}],
                    output_config={"effort": settings.claude_effort},
                    betas=betas,
                    fallbacks=fallbacks,
                )
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                operation_duration_histogram.record(
                    time.perf_counter() - start,
                    {**base_attributes, "error.type": type(exc).__name__},
                )
                logger.exception("Claude request failed")
                # The SDK raises a TypeError at request time when it finds no credentials.
                if isinstance(exc, TypeError) and "authentication method" in str(exc):
                    raise ClaudeNotConfiguredError(
                        "Anthropic credentials are not configured; set ANTHROPIC_API_KEY."
                    ) from exc
                raise

            duration = time.perf_counter() - start
            response_attributes = {**base_attributes, "gen_ai.response.model": response.model}
            operation_duration_histogram.record(duration, response_attributes)
            token_usage_histogram.record(
                response.usage.input_tokens, {**response_attributes, "gen_ai.token.type": "input"}
            )
            token_usage_histogram.record(
                response.usage.output_tokens,
                {**response_attributes, "gen_ai.token.type": "output"},
            )
            span.set_attributes(
                {
                    "gen_ai.response.id": response.id,
                    "gen_ai.response.model": response.model,
                    "gen_ai.response.finish_reasons": [str(response.stop_reason)],
                    "gen_ai.usage.input_tokens": response.usage.input_tokens,
                    "gen_ai.usage.output_tokens": response.usage.output_tokens,
                }
            )

            if response.stop_reason == "refusal":
                span.set_status(Status(StatusCode.ERROR, "refusal"))
                logger.warning("Claude declined to answer (model=%s)", response.model)
                raise ClaudeRefusalError("Claude declined to answer this question.")

            text = "".join(block.text for block in response.content if block.type == "text")
            if response.stop_reason == "max_tokens":
                logger.warning("Claude answer truncated at max_tokens=%s", max_tokens)

            logger.info(
                "Claude answered (model=%s, input_tokens=%s, output_tokens=%s, duration=%.2fs)",
                response.model,
                response.usage.input_tokens,
                response.usage.output_tokens,
                duration,
            )
            return ClaudeAnswer(
                text=text,
                model=response.model,
                stop_reason=response.stop_reason,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
