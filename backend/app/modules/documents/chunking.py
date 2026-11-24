"""Split extracted text into overlapping chunks for retrieval.

Strategy (structure first, then size):
1. Split on paragraph boundaries (blank lines, markdown headings).
2. Split paragraphs that are still too long on sentence boundaries.
3. Hard-split anything left over (e.g. a 5,000-character line with no punctuation).
4. Pack the pieces greedily into chunks of at most `chunk_size` characters, and
   start each new chunk with the last ~`overlap` characters of the previous one,
   so a fact that straddles a boundary is still retrievable from one chunk.

Each chunk remembers the PDF pages it came from, so answers can cite "page 4".
Sizes are in characters (roughly 4 characters per token for English).
"""

import re
from dataclasses import dataclass

from app.modules.documents.parsing import Segment

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n|\n(?=#{1,6}\s)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"[ \t]+")


@dataclass
class Chunk:
    index: int
    text: str
    page_start: int | None
    page_end: int | None


@dataclass
class _Piece:
    text: str
    page: int | None


def _normalise(text: str) -> str:
    # PostgreSQL text can't hold NUL bytes; some PDFs produce them.
    text = text.replace("\x00", "")
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if len(sentence) <= limit:
            parts.append(sentence)
        else:
            parts.extend(sentence[i : i + limit] for i in range(0, len(sentence), limit))
    return [p for p in parts if p.strip()]


def _pieces(segments: list[Segment], limit: int) -> list[_Piece]:
    pieces: list[_Piece] = []
    for segment in segments:
        for paragraph in _PARAGRAPH_BREAK.split(segment.text):
            paragraph = _normalise(paragraph)
            if paragraph:
                pieces.extend(_Piece(p, segment.page) for p in _split_long(paragraph, limit))
    return pieces


def chunk_segments(segments: list[Segment], chunk_size: int, overlap: int) -> list[Chunk]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    # Leave room for the overlap carried into each chunk.
    pieces = _pieces(segments, max(1, chunk_size - overlap))
    chunks: list[Chunk] = []
    current: list[_Piece] = []

    def size(items: list[_Piece]) -> int:
        return sum(len(p.text) for p in items) + 2 * max(0, len(items) - 1)

    def flush() -> None:
        pages = [p.page for p in current if p.page is not None]
        chunks.append(
            Chunk(
                index=len(chunks),
                text="\n\n".join(p.text for p in current),
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
            )
        )

    for piece in pieces:
        if current and size([*current, piece]) > chunk_size:
            flush()
            # Carry over trailing pieces (up to `overlap` characters) as context.
            carried: list[_Piece] = []
            for previous in reversed(current):
                if size([previous, *carried]) > overlap:
                    break
                carried.insert(0, previous)
            current = carried
            # If the carried context plus the new piece still doesn't fit, drop the context.
            if size([*current, piece]) > chunk_size:
                current = []
        current.append(piece)

    if current:
        flush()
    return chunks
