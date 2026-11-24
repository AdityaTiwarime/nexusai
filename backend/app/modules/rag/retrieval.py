"""Hybrid retrieval: vector search + keyword search, fused, filtered, reranked.

1. Vector search: nearest chunks by cosine distance (pgvector HNSW index).
2. Keyword search: PostgreSQL full-text search over the generated `tsv` column.
   Any question word may match (OR), ranked by `ts_rank_cd`, so natural questions
   like "how many days of leave do we get?" still find "24 days of paid leave".
3. Reciprocal Rank Fusion (RRF) merges the two ranked lists without having to
   compare their incompatible scores.
4. Evidence filter: a passage counts only if it matched keywords or is
   semantically close enough (`retrieval_min_similarity`).
5. Rerank by fused score plus how much of the question the passage covers.

Permissions are applied INSIDE both SQL queries (organization, the caller's
role vs. the document's `min_role`, and only `ready` documents), never after
the fact, so a hidden document can't influence results or leak through them.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.embeddings import EmbeddingProvider
from app.modules.auth.models import User
from app.modules.documents.service import visible_roles
from app.modules.rag.text import coverage, terms

RRF_K = 60  # standard constant from the RRF paper; dampens the weight of top ranks

_FILTERS = """
    c.organization_id = :org
    AND d.organization_id = :org
    AND d.status = 'ready'
    AND d.min_role = ANY(:roles)
    AND (CAST(:doc_ids AS uuid[]) IS NULL OR d.id = ANY(CAST(:doc_ids AS uuid[])))
"""

_VECTOR_SQL = text(
    f"""
    SELECT c.id, 1 - (c.embedding <=> CAST(:qvec AS vector)) AS similarity
    FROM document_chunks c JOIN documents d ON d.id = c.document_id
    WHERE {_FILTERS}
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :k
    """
)

# The question's lexemes OR-ed together. They come from the same 'english'
# configuration as the stored tsv; 'simple' then avoids stemming them twice.
_KEYWORD_SQL = text(
    f"""
    WITH q AS (
        SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
        FROM unnest(tsvector_to_array(to_tsvector('english', :q))) AS lexeme
    )
    SELECT c.id, ts_rank_cd(c.tsv, q.query) AS rank
    FROM q, document_chunks c JOIN documents d ON d.id = c.document_id
    WHERE q.query IS NOT NULL AND c.tsv @@ q.query AND {_FILTERS}
    ORDER BY rank DESC, c.id
    LIMIT :k
    """
)

_DETAILS_SQL = text(
    """
    SELECT c.id, c.document_id, d.title, c.chunk_index, c.page_start, c.page_end, c.content,
           1 - (c.embedding <=> CAST(:qvec AS vector)) AS similarity
    FROM document_chunks c JOIN documents d ON d.id = c.document_id
    WHERE c.id = ANY(:ids) AND c.organization_id = :org
    """
)


@dataclass
class Passage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    chunk_index: int
    page_start: int | None
    page_end: int | None
    content: str
    similarity: float
    keyword_rank: float | None  # None = no keyword match
    fused_score: float
    score: float = 0.0  # after reranking, 0..1


@dataclass
class RetrievalResult:
    passages: list[Passage]
    candidates: int  # chunks considered before the evidence filter
    query_terms: set[str] = field(default_factory=set)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"


async def retrieve(
    session: AsyncSession,
    *,
    user: User,
    query: str,
    embedder: EmbeddingProvider,
    settings: Settings,
    top_k: int | None = None,
    document_ids: list[uuid.UUID] | None = None,
) -> RetrievalResult:
    top_k = top_k or settings.retrieval_top_k
    [query_vector] = await embedder.embed([query])
    params = {
        "org": user.organization_id,
        "roles": visible_roles(user),
        "doc_ids": [str(d) for d in document_ids] if document_ids else None,
        "qvec": _vector_literal(query_vector),
        "q": query,
        "k": settings.retrieval_candidates,
    }

    # HNSW applies the WHERE filters after the index scan; searching a wider
    # candidate list keeps enough rows per tenant after filtering.
    await session.execute(text("SET LOCAL hnsw.ef_search = 200"))
    vector_rows = (await session.execute(_VECTOR_SQL, params)).all()
    keyword_rows = (await session.execute(_KEYWORD_SQL, params)).all()

    fused: dict[uuid.UUID, float] = {}
    for ranked in (vector_rows, keyword_rows):
        for position, row in enumerate(ranked):
            fused[row.id] = fused.get(row.id, 0.0) + 1.0 / (RRF_K + position + 1)
    keyword_rank = {row.id: float(row.rank) for row in keyword_rows}
    if not fused:
        return RetrievalResult([], 0, terms(query))

    details = (
        await session.execute(
            _DETAILS_SQL, {"ids": list(fused), "org": user.organization_id, "qvec": params["qvec"]}
        )
    ).all()
    query_terms = terms(query)
    passages = [
        Passage(
            chunk_id=row.id,
            document_id=row.document_id,
            document_title=row.title,
            chunk_index=row.chunk_index,
            page_start=row.page_start,
            page_end=row.page_end,
            content=row.content,
            similarity=float(row.similarity),
            keyword_rank=keyword_rank.get(row.id),
            fused_score=fused[row.id],
        )
        for row in details
    ]
    evidence = [
        p
        for p in passages
        if p.keyword_rank is not None or p.similarity >= settings.retrieval_min_similarity
    ]
    return RetrievalResult(rerank(evidence, query_terms)[:top_k], len(passages), query_terms)


def rerank(passages: list[Passage], query_terms: set[str]) -> list[Passage]:
    """Heuristic reranker: fused rank position blended with question coverage.

    A cross-encoder model would rerank better; this keeps the default setup
    dependency-free and deterministic. The interface (list in, list out) lets a
    model-based reranker replace it without touching callers.
    """
    if not passages:
        return []
    best = max(p.fused_score for p in passages)
    for p in passages:
        p.score = round(0.5 * (p.fused_score / best) + 0.5 * coverage(query_terms, p.content), 4)
    return sorted(passages, key=lambda p: (-p.score, p.document_title, p.chunk_index))
