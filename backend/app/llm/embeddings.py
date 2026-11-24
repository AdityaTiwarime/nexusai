"""Embedding providers behind one interface.

Application code depends on `EmbeddingProvider`, never on a vendor SDK, so the
provider is a configuration choice (NEXUS_EMBEDDING_PROVIDER).

- HashingEmbedder: deterministic and offline. It maps words and word pairs into
  a fixed-size vector (the "hashing trick"). It is good enough for tests, CI and
  demos without an API key, and gives sensible *lexical* similarity, but it is
  NOT a semantic model: "car" and "automobile" are unrelated to it.
- OpenAIEmbedder: real semantic embeddings via the OpenAI REST API (or any
  OpenAI-compatible endpoint via NEXUS_OPENAI_BASE_URL).
"""

import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import Settings
from app.modules.documents.models import EMBEDDING_DIM

_TOKEN = re.compile(r"[a-z0-9]+")


class EmbeddingError(Exception):
    """Embedding failed. `retryable` tells the worker whether to try again later."""

    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    dimensions = EMBEDDING_DIM

    def __init__(self) -> None:
        self.model_name = f"hashing-v1-{self.dimensions}"

    def _vector(self, text: str) -> list[float]:
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # Empty/symbol-only text: a fixed unit vector keeps cosine distance defined.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model_name = model
        self.dimensions = EMBEDDING_DIM
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.post(
                "/embeddings",
                json={"model": self.model_name, "input": texts, "dimensions": self.dimensions},
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingError("Embedding provider timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise EmbeddingError("Could not reach the embedding provider", retryable=True) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise EmbeddingError(
                f"Embedding provider returned {response.status_code}", retryable=True
            )
        if response.status_code >= 400:
            # Bad key, bad model, input too long: retrying won't help.
            raise EmbeddingError(
                f"Embedding provider rejected the request ({response.status_code})",
                retryable=False,
            )
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in data]
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError("Embedding provider returned unexpected vectors", retryable=False)
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


def build_embedder(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai":
        assert settings.openai_api_key is not None  # enforced by Settings validation
        return OpenAIEmbedder(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.embedding_model,
            base_url=settings.openai_base_url,
            timeout=settings.embedding_timeout_seconds,
        )
    return HashingEmbedder()
