from unittest.mock import MagicMock

import anthropic
import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings
from tests.conftest import make_connection_error, make_status_error, make_timeout_error

QUESTIONS_URL = "/api/v1/questions"


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ask_question_success(client: TestClient, mock_client: MagicMock, make_message) -> None:
    mock_client.beta.messages.create.return_value = make_message("Paris.")

    response = client.post(QUESTIONS_URL, json={"question": "What is the capital of France?"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Paris.",
        "model": "claude-opus-5-5",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 34},
    }
    sent = mock_client.beta.messages.create.call_args.kwargs["messages"]
    assert sent == [{"role": "user", "content": "What is the capital of France?"}]


def test_ask_question_passes_overrides(
    client: TestClient, mock_client: MagicMock, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message(model="claude-sonnet-5-5")

    response = client.post(
        QUESTIONS_URL,
        json={"question": "Hi", "model": "claude-sonnet-5-5", "max_tokens": 1000},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "claude-sonnet-5-5"
    kwargs = mock_client.beta.messages.create.call_args.kwargs
    assert (kwargs["model"], kwargs["max_tokens"]) == ("claude-sonnet-5-5", 1000)


def test_ask_question_null_overrides_use_defaults(
    client: TestClient, mock_client: MagicMock, settings: Settings, make_message
) -> None:
    mock_client.beta.messages.create.return_value = make_message()

    response = client.post(
        QUESTIONS_URL, json={"question": "Hi", "model": None, "max_tokens": None}
    )

    assert response.status_code == 200
    kwargs = mock_client.beta.messages.create.call_args.kwargs
    assert (kwargs["model"], kwargs["max_tokens"]) == (
        settings.claude_model,
        settings.claude_max_tokens,
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": 42},
        {"question": "Hi", "model": ""},
        {"question": "Hi", "model": "gpt-4"},
        {"question": "Hi", "model": "Claude Opus"},
        {"question": "Hi", "model": "claude-" + "x" * 100},
        {"question": "Hi", "max_tokens": 0},
        {"question": "Hi", "max_tokens": -5},
        {"question": "Hi", "max_tokens": 128_001},
        {"question": "Hi", "max_tokens": "lots"},
    ],
)
def test_ask_question_validation_error(
    client: TestClient, mock_client: MagicMock, body: dict
) -> None:
    response = client.post(QUESTIONS_URL, json=body)

    assert response.status_code == 422
    mock_client.beta.messages.create.assert_not_awaited()


def test_refusal_maps_to_422(client: TestClient, mock_client: MagicMock, make_message) -> None:
    mock_client.beta.messages.create.return_value = make_message(content=[], stop_reason="refusal")

    response = client.post(QUESTIONS_URL, json={"question": "Something disallowed"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Claude declined to answer this question."}


def test_rate_limit_maps_to_429_with_retry_after(
    client: TestClient, mock_client: MagicMock
) -> None:
    mock_client.beta.messages.create.side_effect = make_status_error(
        anthropic.RateLimitError, 429, headers={"retry-after": "30"}
    )

    response = client.post(QUESTIONS_URL, json={"question": "Hi"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


def test_rate_limit_without_retry_after(client: TestClient, mock_client: MagicMock) -> None:
    mock_client.beta.messages.create.side_effect = make_status_error(anthropic.RateLimitError, 429)

    response = client.post(QUESTIONS_URL, json={"question": "Hi"})

    assert response.status_code == 429
    assert "retry-after" not in response.headers


@pytest.mark.parametrize(
    ("error_cls", "status_code"),
    [(anthropic.BadRequestError, 400), (anthropic.NotFoundError, 404)],
)
def test_claude_invalid_request_errors_map_to_400(
    client: TestClient,
    mock_client: MagicMock,
    error_cls: type[anthropic.APIStatusError],
    status_code: int,
) -> None:
    mock_client.beta.messages.create.side_effect = make_status_error(error_cls, status_code)

    response = client.post(QUESTIONS_URL, json={"question": "Hi", "model": "claude-nope"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Claude rejected the request: error"}


@pytest.mark.parametrize(
    ("error_cls", "status_code"),
    [
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.InternalServerError, 500),
    ],
)
def test_api_status_errors_map_to_502(
    client: TestClient,
    mock_client: MagicMock,
    error_cls: type[anthropic.APIStatusError],
    status_code: int,
) -> None:
    mock_client.beta.messages.create.side_effect = make_status_error(error_cls, status_code)

    response = client.post(QUESTIONS_URL, json={"question": "Hi"})

    assert response.status_code == 502
    assert response.json() == {"detail": f"Claude API returned an error (status {status_code})."}


@pytest.mark.parametrize("make_error", [make_connection_error, make_timeout_error])
def test_connection_errors_map_to_503(
    client: TestClient, mock_client: MagicMock, make_error
) -> None:
    mock_client.beta.messages.create.side_effect = make_error()

    response = client.post(QUESTIONS_URL, json={"question": "Hi"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Claude API is currently unreachable."}


def test_dependency_returns_service_from_app_state() -> None:
    from types import SimpleNamespace

    from app.api.dependencies import get_claude_service

    service = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(claude_service=service)))

    assert get_claude_service(request) is service


def test_missing_credentials_map_to_503(client: TestClient, mock_client: MagicMock) -> None:
    mock_client.beta.messages.create.side_effect = TypeError(
        "Could not resolve authentication method."
    )

    response = client.post(QUESTIONS_URL, json={"question": "Hi"})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]
