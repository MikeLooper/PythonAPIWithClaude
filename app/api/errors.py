"""Map Claude/Anthropic SDK failures to HTTP responses."""

import anthropic
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.services.claude_service import ClaudeNotConfiguredError, ClaudeRefusalError


def _error(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)


async def refusal_handler(_: Request, exc: Exception) -> JSONResponse:
    return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


async def rate_limit_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, anthropic.RateLimitError)
    retry_after = exc.response.headers.get("retry-after")
    headers = {"Retry-After": retry_after} if retry_after else None
    return _error(
        status.HTTP_429_TOO_MANY_REQUESTS, "Claude API rate limit reached; retry later.", headers
    )


async def invalid_request_handler(_: Request, exc: Exception) -> JSONResponse:
    # 400/404 from Claude are typically caused by a caller-supplied model or max_tokens.
    assert isinstance(exc, anthropic.BadRequestError | anthropic.NotFoundError)
    return _error(status.HTTP_400_BAD_REQUEST, f"Claude rejected the request: {exc.message}")


async def api_status_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, anthropic.APIStatusError)
    return _error(
        status.HTTP_502_BAD_GATEWAY, f"Claude API returned an error (status {exc.status_code})."
    )


async def not_configured_handler(_: Request, exc: Exception) -> JSONResponse:
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


async def connection_handler(_: Request, __: Exception) -> JSONResponse:
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "Claude API is currently unreachable.")


def register_exception_handlers(app: FastAPI) -> None:
    # Starlette resolves handlers by walking the exception's MRO, so the most specific wins.
    app.add_exception_handler(ClaudeRefusalError, refusal_handler)
    app.add_exception_handler(ClaudeNotConfiguredError, not_configured_handler)
    app.add_exception_handler(anthropic.RateLimitError, rate_limit_handler)
    app.add_exception_handler(anthropic.BadRequestError, invalid_request_handler)
    app.add_exception_handler(anthropic.NotFoundError, invalid_request_handler)
    app.add_exception_handler(anthropic.APIStatusError, api_status_handler)
    app.add_exception_handler(anthropic.APIConnectionError, connection_handler)
