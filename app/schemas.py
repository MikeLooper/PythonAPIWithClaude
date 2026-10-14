"""Request and response models for the public API."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_QUESTION_LENGTH = 10_000
# Largest output any current Claude model accepts; individual models may allow less.
MAX_OUTPUT_TOKENS = 128_000


class QuestionRequest(BaseModel):
    """A question submitted by a user, with optional per-request overrides."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"question": "What is the capital of France?"},
                {
                    "question": "Summarise the plot of Hamlet.",
                    "model": "claude-sonnet-5-5",
                    "max_tokens": 2000,
                },
            ]
        }
    )

    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    model: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^claude-[a-z0-9.-]+$",
        description="Claude model ID. Defaults to the server's CLAUDE_MODEL.",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_OUTPUT_TOKENS,
        description="Maximum answer length in tokens. Defaults to the server's CLAUDE_MAX_TOKENS.",
    )

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class AnswerResponse(BaseModel):
    """Claude's answer to a question."""

    answer: str
    model: str
    stop_reason: str | None
    usage: TokenUsage


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    version: str
