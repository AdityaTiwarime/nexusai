"""Unit tests: upload validation, text extraction and chunking (no database)."""

import io
import zipfile

import pytest

from app.modules.documents.chunking import chunk_segments
from app.modules.documents.parsing import (
    ParseError,
    Segment,
    UploadRejected,
    parse_document,
    validate_upload,
)
from tests.factories import make_blank_pdf, make_docx, make_pdf

MB = 1024 * 1024


# --- validation ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["report.exe", "notes", "image.png", "archive.zip"])
def test_rejects_unsupported_extensions(name):
    with pytest.raises(UploadRejected, match="Unsupported file type"):
        validate_upload(name, b"hello", MB)


def test_extension_is_case_insensitive():
    assert validate_upload("NOTES.TXT", b"hello", MB) == "txt"


def test_rejects_empty_and_oversized_files():
    with pytest.raises(UploadRejected, match="empty"):
        validate_upload("a.txt", b"", MB)
    with pytest.raises(UploadRejected, match="larger than"):
        validate_upload("a.txt", b"x" * (MB + 1), MB)


def test_rejects_content_that_does_not_match_the_extension():
    with pytest.raises(UploadRejected, match="not a PDF"):
        validate_upload("fake.pdf", b"just some text", MB)
    with pytest.raises(UploadRejected, match="not a Word document"):
        validate_upload("fake.docx", b"just some text", MB)
    with pytest.raises(UploadRejected, match="binary"):
        validate_upload("fake.txt", make_pdf(["x"])[:20] + b"\x00\x01\x02", MB)


def test_rejects_zip_that_is_not_a_word_document():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(UploadRejected, match="not a Word document"):
        validate_upload("x.docx", buffer.getvalue(), MB)


def test_rejects_docx_zip_bomb():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w/>")
        archive.writestr("word/padding.bin", b"\0" * (101 * MB))  # compresses to ~100 KB
    data = buffer.getvalue()
    assert len(data) < MB
    with pytest.raises(UploadRejected, match="too large when uncompressed"):
        validate_upload("bomb.docx", data, 20 * MB)


# --- parsing -------------------------------------------------------------------------


def test_pdf_text_keeps_page_numbers_and_metadata():
    data = make_pdf(["Refund policy page one", "Holiday calendar page two"], title="Handbook")
    parsed = parse_document("pdf", data)
    assert parsed.page_count == 2
    assert [s.page for s in parsed.segments] == [1, 2]
    assert "Refund policy" in parsed.segments[0].text
    assert parsed.metadata["title"] == "Handbook"


def test_pdf_without_text_fails_with_a_clear_reason():
    with pytest.raises(ParseError, match="scanned image"):
        parse_document("pdf", make_blank_pdf())


def test_corrupt_pdf_fails_cleanly():
    with pytest.raises(ParseError, match="corrupt"):
        parse_document("pdf", b"%PDF-1.7\nthis is not really a pdf")


def test_docx_paragraphs_and_tables():
    data = make_docx(
        ["Expense policy", "Meals up to 50 EUR"], table=[["Item", "Limit"], ["Taxi", "30"]]
    )
    parsed = parse_document("docx", data)
    text = parsed.segments[0].text
    assert "Expense policy" in text and "Meals up to 50 EUR" in text
    assert "Taxi | 30" in text
    assert parsed.metadata["title"] == "Test document"


def test_csv_rows_become_self_describing_lines():
    data = b"name,region,revenue\nAcme,EU,100\nGlobex,US,200\n"
    parsed = parse_document("csv", data)
    assert parsed.metadata["rows"] == 2
    assert "name: Acme; region: EU; revenue: 100" in parsed.segments[0].text


def test_text_handles_bom_and_windows_encoding():
    assert parse_document("txt", "﻿hello".encode()).segments[0].text == "hello"
    assert "café" in parse_document("md", "café".encode("cp1252")).segments[0].text


def test_whitespace_only_text_fails():
    with pytest.raises(ParseError, match="No text"):
        parse_document("txt", b"   \n\n  ")


# --- chunking ------------------------------------------------------------------------


def test_short_text_is_one_chunk():
    chunks = chunk_segments([Segment("Just one short paragraph.")], 1200, 200)
    assert len(chunks) == 1
    assert chunks[0].text == "Just one short paragraph."


def test_chunks_respect_size_and_overlap():
    paragraphs = [f"Paragraph {i}: " + "word " * 60 for i in range(30)]
    chunks = chunk_segments([Segment("\n\n".join(paragraphs))], 1000, 350)
    assert len(chunks) > 3
    assert all(len(c.text) <= 1000 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # Overlap: each chunk starts with the last paragraph of the one before.
    for previous, current in zip(chunks, chunks[1:], strict=False):
        last_paragraph = previous.text.split("\n\n")[-1]
        assert current.text.startswith(last_paragraph)


def test_no_text_is_lost():
    paragraphs = [f"Fact number {i} is unique." for i in range(200)]
    chunks = chunk_segments([Segment("\n\n".join(paragraphs))], 300, 0)
    joined = "\n\n".join(c.text for c in chunks)
    for paragraph in paragraphs:
        assert paragraph in joined


def test_very_long_line_is_hard_split():
    chunks = chunk_segments([Segment("x" * 5000)], 1000, 100)
    assert all(len(c.text) <= 1000 for c in chunks)
    assert sum(len(c.text) for c in chunks) >= 5000


def test_chunks_track_page_ranges():
    segments = [Segment("A " * 300, page=1), Segment("B " * 300, page=2), Segment("C", page=3)]
    chunks = chunk_segments(segments, 700, 0)
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 3
    assert all(c.page_start <= c.page_end for c in chunks)


def test_nul_bytes_are_removed():
    chunks = chunk_segments([Segment("before\x00after")], 1200, 200)
    assert chunks[0].text == "beforeafter"


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_segments([Segment("x")], 500, 500)
