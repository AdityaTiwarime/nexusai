"""Upload validation and text extraction.

Validation happens twice on purpose:
- at upload time (`validate_upload`): cheap checks so obviously bad files are
  rejected immediately with a clear message;
- in the worker (`parse_document`): full parsing, which can still fail on files
  that look valid but are corrupt. Those become a "failed" document with a reason.

The file extension alone is never trusted: content must match the claimed type.
"""

import csv
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from docx import Document as DocxDocument
from pypdf import PdfReader
from pypdf.errors import PdfReadError

ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".csv": "csv", ".md": "md"}
MAX_DOCX_UNCOMPRESSED = 100 * 1024 * 1024  # zip-bomb guard
CSV_ROWS_PER_SEGMENT = 25


class UploadRejected(ValueError):
    """The upload is invalid. The message is safe to show to the user."""


class ParseError(Exception):
    """The file couldn't be turned into text. Not worth retrying."""


@dataclass
class Segment:
    text: str
    page: int | None = None  # 1-based page number for PDFs


@dataclass
class ParsedDocument:
    segments: list[Segment]
    metadata: dict[str, Any] = field(default_factory=dict)
    page_count: int | None = None

    @property
    def text_length(self) -> int:
        return sum(len(s.text) for s in self.segments)


def file_type_for(filename: str) -> str:
    suffix = PurePath(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadRejected(f"Unsupported file type '{suffix or 'none'}'. Allowed: {allowed}")
    return ALLOWED_EXTENSIONS[suffix]


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise UploadRejected("This looks like a binary file, not text")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UploadRejected("Could not read the file as text (unsupported encoding)")


def validate_upload(filename: str, data: bytes, max_bytes: int) -> str:
    """Return the normalised file type, or raise UploadRejected."""
    file_type = file_type_for(filename)
    if not data:
        raise UploadRejected("The file is empty")
    if len(data) > max_bytes:
        raise UploadRejected(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit")

    if file_type == "pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadRejected("The file has a .pdf name but is not a PDF")
    elif file_type == "docx":
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise UploadRejected("The file has a .docx name but is not a Word document")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise UploadRejected("The file has a .docx name but is not a Word document")
            if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_UNCOMPRESSED:
                raise UploadRejected("The Word document is too large when uncompressed")
    else:
        _decode_text(data)
    return file_type


# --- parsers ------------------------------------------------------------------------


def _parse_pdf(data: bytes) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("The PDF is password-protected")
        segments = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                segments.append(Segment(text=text, page=number))
        info = reader.metadata or {}
        metadata = {
            key: str(info.get(f"/{key.capitalize()}"))
            for key in ("title", "author")
            if info.get(f"/{key.capitalize()}")
        }
        return ParsedDocument(segments, metadata, page_count=len(reader.pages))
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ParseError("The PDF is corrupt or unreadable") from exc


def _parse_docx(data: bytes) -> ParsedDocument:
    try:
        doc = DocxDocument(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of errors for bad files
        raise ParseError("The Word document is corrupt or unreadable") from exc
    blocks = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    props = doc.core_properties
    metadata = {k: v for k, v in {"title": props.title, "author": props.author}.items() if v}
    return ParsedDocument([Segment("\n\n".join(blocks))] if blocks else [], metadata)


def _parse_csv(text: str) -> ParsedDocument:
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ParseError("The CSV file is malformed") from exc
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return ParsedDocument([], {"rows": 0})
    header, body = rows[0], rows[1:]
    # Each row becomes "column: value; ..." so a chunk is self-explanatory without the header.
    lines = [
        "; ".join(
            f"{h.strip() or f'col{i + 1}'}: {v.strip()}"
            for i, (h, v) in enumerate(zip(header, row, strict=False))
        )
        for row in body
    ]
    segments = [
        Segment("\n".join(lines[i : i + CSV_ROWS_PER_SEGMENT]))
        for i in range(0, len(lines), CSV_ROWS_PER_SEGMENT)
    ]
    return ParsedDocument(segments, {"rows": len(body), "columns": header})


def parse_document(file_type: str, data: bytes) -> ParsedDocument:
    if file_type == "pdf":
        parsed = _parse_pdf(data)
    elif file_type == "docx":
        parsed = _parse_docx(data)
    else:
        try:
            text = _decode_text(data)
        except UploadRejected as exc:
            raise ParseError(str(exc)) from exc
        if file_type == "csv":
            parsed = _parse_csv(text)
        else:  # txt, md: kept as-is; markdown headings help the chunker find boundaries
            parsed = ParsedDocument([Segment(text.strip())] if text.strip() else [])

    if parsed.text_length == 0:
        hint = (
            " (it may be a scanned image; OCR is not supported yet)" if file_type == "pdf" else ""
        )
        raise ParseError(f"No text could be extracted{hint}")
    return parsed
