"""Asking questions: conversation handling around retrieve -> answer.

Privacy rules:
- A conversation is visible only to the user who created it (404 for everyone else).
- Citations are re-checked every time a conversation is read. If a cited document
  was deleted, or its visibility was raised above the reader's role, the citation
  is shown as "no longer available" and its snippet is withheld.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.embeddings import EmbeddingProvider
from app.modules.auth.models import User
from app.modules.documents.models import Document
from app.modules.documents.service import visible_documents
from app.modules.rag.answer import Answerer
from app.modules.rag.models import AnswerStatus, Conversation, Message, MessageRole
from app.modules.rag.retrieval import retrieve
from app.modules.rag.schemas import Citation, MessageOut

SNIPPET_CHARS = 600
HISTORY_MESSAGES = 6


class NotFound(Exception):
    pass


async def get_own_conversation(
    session: AsyncSession, conversation_id: uuid.UUID, user: User
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.organization_id == user.organization_id,
        )
    )
    if conversation is None:
        raise NotFound
    return conversation


async def _messages(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(rows)


async def ask(
    session: AsyncSession,
    *,
    user: User,
    question: str,
    conversation_id: uuid.UUID | None,
    embedder: EmbeddingProvider,
    answerer: Answerer,
    settings: Settings,
) -> tuple[Conversation, Message, Message]:
    started = time.perf_counter()
    question = question.strip()
    if conversation_id:
        conversation = await get_own_conversation(session, conversation_id, user)
        history = [
            {"role": m.role, "content": m.content}
            for m in (await _messages(session, conversation.id))[-HISTORY_MESSAGES:]
        ]
    else:
        conversation = Conversation(
            id=uuid.uuid4(),
            organization_id=user.organization_id,
            user_id=user.id,
            title=question[:80] + ("…" if len(question) > 80 else ""),
        )
        session.add(conversation)
        history = []

    retrieval = await retrieve(
        session, user=user, query=question, embedder=embedder, settings=settings
    )
    answer = await answerer.answer(question, retrieval.passages, history)

    citations = []
    for number in answer.cited:
        passage = retrieval.passages[number - 1]
        citations.append(
            Citation(
                number=number,
                document_id=passage.document_id,
                document_title=passage.document_title,
                chunk_index=passage.chunk_index,
                page_start=passage.page_start,
                page_end=passage.page_end,
                snippet=passage.content[:SNIPPET_CHARS],
            ).model_dump(mode="json")
        )

    now = datetime.now(UTC)
    asked = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        organization_id=user.organization_id,
        role=MessageRole.USER,
        content=question,
        citations=[],
        created_at=now,
    )
    reply = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        organization_id=user.organization_id,
        role=MessageRole.ASSISTANT,
        content=answer.text,
        status=AnswerStatus.ANSWERED if answer.answered else AnswerStatus.NO_EVIDENCE,
        citations=citations,
        model=answer.model,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        latency_ms=round((time.perf_counter() - started) * 1000),
        created_at=now + timedelta(microseconds=1),  # keeps question-then-answer order
    )
    session.add_all([asked, reply])
    conversation.updated_at = now
    await session.commit()
    return conversation, asked, reply


async def present(session: AsyncSession, user: User, messages: list[Message]) -> list[MessageOut]:
    """Messages for display, with citations re-checked against current permissions."""
    cited_ids = {
        uuid.UUID(c["document_id"]) for m in messages for c in m.citations if c.get("document_id")
    }
    visible: set[uuid.UUID] = set()
    if cited_ids:
        visible = set(
            await session.scalars(
                visible_documents(user)
                .with_only_columns(Document.id)
                .where(Document.id.in_(cited_ids))
            )
        )
    out = []
    for m in messages:
        citations = []
        for raw in m.citations:
            citation = Citation.model_validate(raw)
            if citation.document_id not in visible:
                citation = Citation(number=citation.number, available=False)
            citations.append(citation)
        item = MessageOut.model_validate(m)
        item.citations = citations
        out.append(item)
    return out


async def conversation_messages(
    session: AsyncSession, user: User, conversation: Conversation
) -> list[MessageOut]:
    return await present(session, user, await _messages(session, conversation.id))
