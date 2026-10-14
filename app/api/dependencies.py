"""FastAPI dependency providers."""

from typing import Annotated

from fastapi import Depends, Request

from app.services.claude_service import ClaudeService


def get_claude_service(request: Request) -> ClaudeService:
    """Return the ClaudeService created during application startup."""
    return request.app.state.claude_service


ClaudeServiceDep = Annotated[ClaudeService, Depends(get_claude_service)]
