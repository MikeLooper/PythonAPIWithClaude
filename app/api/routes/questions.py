from fastapi import APIRouter, status

from app.api.dependencies import ClaudeServiceDep
from app.schemas import AnswerResponse, ErrorResponse, QuestionRequest, TokenUsage

router = APIRouter(prefix="/api/v1", tags=["questions"])


@router.post(
    "/questions",
    response_model=AnswerResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse, "description": "Claude rejected the model or parameters"},
        422: {"model": ErrorResponse, "description": "Invalid question, or Claude declined"},
        429: {"model": ErrorResponse, "description": "Claude API rate limit reached"},
        502: {"model": ErrorResponse, "description": "Claude API returned an error"},
        503: {"model": ErrorResponse, "description": "Claude API unreachable or not configured"},
    },
)
async def ask_question(payload: QuestionRequest, claude: ClaudeServiceDep) -> AnswerResponse:
    """Ask Claude a question and return its answer."""
    answer = await claude.ask(payload.question, model=payload.model, max_tokens=payload.max_tokens)
    return AnswerResponse(
        answer=answer.text,
        model=answer.model,
        stop_reason=answer.stop_reason,
        usage=TokenUsage(input_tokens=answer.input_tokens, output_tokens=answer.output_tokens),
    )
