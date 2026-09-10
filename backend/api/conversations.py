from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from urllib.parse import quote

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
from backend.conversations.models import ConversationMessagePage, ConversationSessionRename


router = APIRouter(tags=["conversations"])


@router.post('/conversations/{conversation_id}/sessions/{session_id}/attachments', status_code=201)
async def upload_attachment(conversation_id: str, session_id: str, filename: str, request: Request,
                            services: ApplicationServices = Depends(get_services)):
    from backend.conversations.models import ConversationAttachmentRef
    from backend.conversations.attachments import resolve
    record = await services.resources.artifacts.upload(services, conversation_id, session_id, filename, request.stream())
    return resolve(services, conversation_id, session_id,
                   [ConversationAttachmentRef(version_id=record['version_id'], path=filename)])[0]


@router.get('/conversations/{conversation_id}/sessions/{session_id}/attachments/{version_id}')
async def attachment_content(conversation_id: str, session_id: str, version_id: str, path: str,
                             preview: bool = False, services: ApplicationServices = Depends(get_services)):
    from backend.conversations.models import ConversationAttachmentRef
    from backend.conversations.attachments import resolve
    attachment = resolve(services, conversation_id, session_id, [ConversationAttachmentRef(version_id=version_id, path=path)])[0]
    inline = preview and attachment.media_type in {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
    stream = services.resources.artifacts.read(services, conversation_id, version_id, path)
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        first = b''
    async def body():
        try:
            yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()
    return StreamingResponse(body(), media_type=attachment.media_type if inline else 'application/octet-stream',
        headers={'Content-Disposition': f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(attachment.name, safe='')}",
                 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox"})


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


@router.patch("/conversations/{conversation_id}/sessions/{session_id}", response_model=ConversationSession)
async def rename_conversation_session(conversation_id: str, session_id: str, request: ConversationSessionRename,
                                      services: ApplicationServices = Depends(get_services)) -> ConversationSession:
    return await services.rename_conversation_session(conversation_id, session_id, request.title)


@router.get("/conversations/{conversation_id}/sessions/{session_id}/timeline", response_model=ConversationMessagePage)
async def conversation_timeline(conversation_id: str, session_id: str,
                                before: Annotated[int | None, Query(ge=1)] = None,
                                after: Annotated[int | None, Query(ge=0)] = None,
                                limit: Annotated[int, Query(ge=1, le=100)] = 50,
                                services: ApplicationServices = Depends(get_services)) -> ConversationMessagePage:
    # The store validates the full conversation/session pair, including deleted sessions.
    return services.conversations.page_messages(conversation_id, session_id, before=before, after=after, limit=limit)
