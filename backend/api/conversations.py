from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from backend.api.dependencies import get_services
from backend.conversations import (
    ConversationMessage,
    ConversationParticipantsAdd,
    ConversationPost,
    ConversationPostResult,
    ConversationSession,
    ConversationSessionCreate,
    ConversationSummary,
)
from backend.services import ApplicationServices


router = APIRouter(tags=["conversations"])


@router.get(
    "/conversations/{conversation_id}", response_model=ConversationSummary
)
async def get_conversation(
    conversation_id: str,
    services: ApplicationServices = Depends(get_services),
) -> ConversationSummary:
    return services.conversation_summary(conversation_id)


@router.post(
    "/conversations/{conversation_id}/sessions",
    response_model=ConversationSession,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation_session(
    conversation_id: str,
    request: ConversationSessionCreate,
    services: ApplicationServices = Depends(get_services),
) -> ConversationSession:
    return await services.create_conversation_session(conversation_id, request)


@router.post(
    "/conversations/{conversation_id}/sessions/{session_id}/participants",
    response_model=ConversationSession,
)
async def add_conversation_session_participants(
    conversation_id: str,
    session_id: str,
    request: ConversationParticipantsAdd,
    services: ApplicationServices = Depends(get_services),
) -> ConversationSession:
    return await services.add_conversation_session_participants(
        conversation_id, session_id, request
    )


@router.delete(
    "/conversations/{conversation_id}/sessions/{session_id}/participants/{agent_id}",
    response_model=ConversationSession,
)
async def remove_conversation_session_participant(
    conversation_id: str,
    session_id: str,
    agent_id: str,
    services: ApplicationServices = Depends(get_services),
) -> ConversationSession:
    return await services.remove_conversation_session_participant(
        conversation_id, session_id, agent_id
    )


@router.delete(
    "/conversations/{conversation_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation_session(
    conversation_id: str,
    session_id: str,
    services: ApplicationServices = Depends(get_services),
) -> None:
    await services.delete_conversation_session(conversation_id, session_id)


@router.get(
    "/conversations/{conversation_id}/sessions/{session_id}/messages",
    response_model=list[ConversationMessage],
)
async def list_conversation_messages(
    conversation_id: str,
    session_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    services: ApplicationServices = Depends(get_services),
) -> list[ConversationMessage]:
    return services.list_conversation_messages(
        conversation_id, session_id, limit=limit
    )


@router.post(
    "/conversations/{conversation_id}/sessions/{session_id}/messages",
    response_model=ConversationPostResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_conversation_message(
    conversation_id: str,
    session_id: str,
    request: ConversationPost,
    services: ApplicationServices = Depends(get_services),
) -> ConversationPostResult:
    return await services.post_conversation_message(
        conversation_id, session_id, request
    )


@router.get(
    "/agents/{agent_id}/conversation-sessions",
    response_model=list[ConversationSession],
)
async def list_agent_conversation_sessions(
    agent_id: str,
    services: ApplicationServices = Depends(get_services),
) -> list[ConversationSession]:
    return services.list_agent_conversation_sessions(agent_id)
