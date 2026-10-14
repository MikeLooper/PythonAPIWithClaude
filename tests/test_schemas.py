import pytest
from pydantic import ValidationError

from app.schemas import MAX_OUTPUT_TOKENS, MAX_QUESTION_LENGTH, QuestionRequest


def test_question_is_stripped() -> None:
    assert QuestionRequest(question="  Why is the sky blue?  ").question == "Why is the sky blue?"


@pytest.mark.parametrize("question", ["", "   ", "\n\t", "x" * (MAX_QUESTION_LENGTH + 1)])
def test_invalid_questions_rejected(question: str) -> None:
    with pytest.raises(ValidationError):
        QuestionRequest(question=question)


def test_question_at_max_length_accepted() -> None:
    assert len(QuestionRequest(question="x" * MAX_QUESTION_LENGTH).question) == MAX_QUESTION_LENGTH


def test_overrides_default_to_none() -> None:
    request = QuestionRequest(question="Hi")

    assert request.model is None
    assert request.max_tokens is None


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"])
def test_valid_model_accepted(model: str) -> None:
    assert QuestionRequest(question="Hi", model=model).model == model


@pytest.mark.parametrize("max_tokens", [1, MAX_OUTPUT_TOKENS])
def test_max_tokens_bounds_accepted(max_tokens: int) -> None:
    assert QuestionRequest(question="Hi", max_tokens=max_tokens).max_tokens == max_tokens


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": ""},
        {"model": "gpt-4"},
        {"model": "claude opus"},
        {"model": "claude-" + "x" * 100},
        {"max_tokens": 0},
        {"max_tokens": MAX_OUTPUT_TOKENS + 1},
    ],
)
def test_invalid_overrides_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        QuestionRequest(question="Hi", **overrides)
