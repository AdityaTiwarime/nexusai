"""Embedding providers. The OpenAI provider is tested against a mocked HTTP transport."""

import json
import math

import httpx
import pytest

from app.llm.embeddings import EmbeddingError, HashingEmbedder, OpenAIEmbedder, build_embedder
from tests.conftest import make_settings


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingEmbedder()
    first, again, empty = await embedder.embed(["refund policy", "refund policy", "!!!"])
    assert first == again
    assert len(first) == 1536
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-9)
    assert math.isclose(math.sqrt(sum(v * v for v in empty)), 1.0, rel_tol=1e-9)


async def test_hashing_embedder_reflects_word_overlap():
    a, b, c = await HashingEmbedder().embed(
        ["customer refund policy", "refund policy for customers", "quarterly holiday calendar"]
    )
    assert _cos(a, b) > _cos(a, c)


def _openai(handler) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        api_key="sk-test",
        model="text-embedding-3-small",
        base_url="https://api.example.com/v1/",
        timeout=5,
        transport=httpx.MockTransport(handler),
    )


async def test_openai_embedder_sends_request_and_orders_results():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        # Returned out of order on purpose: the client must sort by index.
        return httpx.Response(
            200,
            json={"data": [
                {"index": 1, "embedding": [0.2] * 1536},
                {"index": 0, "embedding": [0.1] * 1536},
            ]},
        )  # fmt: skip

    vectors = await _openai(handler).embed(["a", "b"])
    assert seen["url"] == "https://api.example.com/v1/embeddings"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"] == {
        "model": "text-embedding-3-small",
        "input": ["a", "b"],
        "dimensions": 1536,
    }
    assert vectors[0][0] == 0.1 and vectors[1][0] == 0.2


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (500, True), (503, True), (400, False), (401, False)]
)
async def test_openai_errors_are_classified(status, retryable):
    embedder = _openai(lambda request: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(EmbeddingError) as info:
        await embedder.embed(["a"])
    assert info.value.retryable is retryable


async def test_openai_timeout_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(EmbeddingError) as info:
        await _openai(handler).embed(["a"])
    assert info.value.retryable


async def test_openai_wrong_dimensions_are_rejected():
    embedder = _openai(
        lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 10}]})
    )
    with pytest.raises(EmbeddingError) as info:
        await embedder.embed(["a"])
    assert not info.value.retryable


def test_provider_is_chosen_by_configuration():
    assert isinstance(build_embedder(make_settings()), HashingEmbedder)
    openai = build_embedder(make_settings(embedding_provider="openai", openai_api_key="sk-x"))
    assert isinstance(openai, OpenAIEmbedder)


def test_openai_provider_requires_a_key():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        make_settings(embedding_provider="openai")
