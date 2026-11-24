"""Search and chat endpoints. Available to every organization member; results
only ever include documents the caller's role may see."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_rate_limiter, get_session, get_settings_dep
from app.core.config import Settings
from app.core.rate_limit import Limit, RateLimiter
from app.llm.chat import ChatError
from app.llm.embeddings import EmbeddingError, EmbeddingProvider
from app.modules.auth.deps import require_org_member
from app.modules.auth.models import User
from app.modules.rag import service
from app.modules.rag.answer import Answerer
from app.modules.rag.models import Conversation
from app.modules.rag.retrieval import retrieve
from app.modules.rag.schemas import (
    AskRequest,
    AskResponse,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    PassageOut,
    SearchRequest,
    SearchResponse,
)

router = APIRouter(tags=["rag"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
Member = Annotated[User, Depends(require_org_member())]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_embedder(request: Request) -> EmbeddingProvider:
    return request.app.state.embedder


def get_answerer(request: Request) -> Answerer:
    return request.app.state.answerer


Embedder = Annotated[EmbeddingProvider, Depends(get_embedder)]
AnswererDep = Annotated[Answerer, Depends(get_answerer)]


async def _check_rate(limiter: RateLimiter, user: User, name: str, per_minute: int) -> None:
    decision = await limiter.hit(Limit(name, per_minute, 60), str(user.id))
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Please wait a moment.",
            headers={"Retry-After": str(decision.retry_after)},
        )


def _provider_error(exc: ChatError | EmbeddingError) -> HTTPException:
    # Details go to logs; users get a short, safe message.
    if exc.retryable:
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The AI service is temporarily unavailable. Please try again.",
        )
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI service rejected the request.")


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    caller: Member,
    session: Session,
    settings: SettingsDep,
    embedder: Embedder,
    limiter: Limiter,
) -> SearchResponse:
    await _check_rate(limiter, caller, "search", settings.chat_rate_limit_per_user * 2)
    try:
        result = await retrieve(
            session,
            user=caller,
            query=body.query,
            embedder=embedder,
            settings=settings,
            top_k=body.limit,
            document_ids=body.document_ids,
        )
    except EmbeddingError as exc:
        raise _provider_error(exc) from None
    return SearchResponse(
        query=body.query,
        candidates=result.candidates,
        results=[
            PassageOut(
                document_id=p.document_id,
                document_title=p.document_title,
                chunk_index=p.chunk_index,
                page_start=p.page_start,
                page_end=p.page_end,
                content=p.content,
                score=p.score,
                similarity=round(p.similarity, 4),
                keyword_match=p.keyword_rank is not None,
            )
            for p in result.passages
        ],
    )


@router.post("/chat/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    caller: Member,
    session: Session,
    settings: SettingsDep,
    embedder: Embedder,
    answerer: AnswererDep,
    limiter: Limiter,
) -> AskResponse:
    await _check_rate(limiter, caller, "ask", settings.chat_rate_limit_per_user)
    try:
        conversation, asked, reply = await service.ask(
            session,
            user=caller,
            question=body.question,
            conversation_id=body.conversation_id,
            embedder=embedder,
            answerer=answerer,
            settings=settings,
        )
    except service.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from None
    except (ChatError, EmbeddingError) as exc:
        await session.rollback()
        raise _provider_error(exc) from None
    question_out, answer_out = await service.present(session, caller, [asked, reply])
    return AskResponse(conversation_id=conversation.id, question=question_out, answer=answer_out)


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(caller: Member, session: Session) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(
            Conversation.user_id == caller.id,
            Conversation.organization_id == caller.organization_id,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .limit(100)
    )
    return list(rows)


async def _own(session: AsyncSession, conversation_id: uuid.UUID, user: User) -> Conversation:
    try:
        return await service.get_own_conversation(session, conversation_id, user)
    except service.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from None


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, caller: Member, session: Session
) -> ConversationDetail:
    conversation = await _own(session, conversation_id, caller)
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=await service.conversation_messages(session, caller, conversation),
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID, body: ConversationUpdate, caller: Member, session: Session
) -> Conversation:
    conversation = await _own(session, conversation_id, caller)
    conversation.title = body.title.strip()
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, caller: Member, session: Session
) -> Response:
    conversation = await _own(session, conversation_id, caller)
    await session.delete(conversation)  # messages cascade in the database
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
