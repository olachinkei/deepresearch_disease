from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from deepresearch_agent.embedding_contract import EMBEDDING_DIMENSION, EMBEDDING_MODEL_ID

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


@dataclass(frozen=True, slots=True)
class EmbeddingDocument:
    text: str
    title: str | None = None


class EmbeddingErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    UPSTREAM = "upstream"
    SCHEMA = "schema"
    REQUEST = "request"


class EmbeddingAdapterError(RuntimeError):
    """Sanitized provider failure that never includes content or credentials."""

    def __init__(self, kind: EmbeddingErrorKind, *, retryable: bool) -> None:
        super().__init__(f"Embedding request failed ({kind.value})")
        self.kind = kind
        self.retryable = retryable


class _EmbeddingValues(BaseModel):
    model_config = ConfigDict(extra="ignore")

    values: list[float]


class _BatchEmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    embeddings: list[_EmbeddingValues]


class EmbeddingProvider(Protocol):
    dimension: int
    model_name: str
    external: bool

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_documents(
        self, documents: Sequence[EmbeddingDocument]
    ) -> list[list[float]]: ...

    async def close(self) -> None: ...


class HashEmbeddingProvider:
    """Stable local embedding for tests and retrieval plumbing; not a scientific model."""

    dimension = EMBEDDING_DIMENSION
    model_name = "local-hash-embedding-v1"
    external = False

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    async def embed_documents(self, documents: Sequence[EmbeddingDocument]) -> list[list[float]]:
        return [self._embed_one(document.text) for document in documents]

    async def close(self) -> None:
        return None

    def _embed_one(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        for token in _TOKEN.findall(text.casefold()):
            digest = hashlib.blake2b(token.encode(), digest_size=16).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            values[index] += sign
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else values


class GeminiEmbeddingProvider:
    """Gemini Embedding 2 adapter for explicitly approved external text."""

    dimension = EMBEDDING_DIMENSION
    model_name = EMBEDDING_MODEL_ID
    external = True
    _batch_size = 100

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=30)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        prepared = [f"task: search result | query: {text}" for text in texts]
        return await self._embed_prepared(prepared)

    async def embed_documents(self, documents: Sequence[EmbeddingDocument]) -> list[list[float]]:
        prepared = [
            f"title: {document.title or 'none'} | text: {document.text}" for document in documents
        ]
        return await self._embed_prepared(prepared)

    async def _embed_prepared(self, texts: Sequence[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for offset in range(0, len(texts), self._batch_size):
            result.extend(await self._embed_batch(texts[offset : offset + self._batch_size]))
        return result

    async def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model_resource = f"models/{self.model_name}"
        requests: list[dict[str, Any]] = [
            {
                "model": model_resource,
                "content": {"parts": [{"text": text}]},
                "output_dimensionality": self.dimension,
            }
            for text in texts
        ]
        try:
            response = await self._client.post(
                f"/v1beta/{model_resource}:batchEmbedContents",
                headers={
                    "x-goog-api-key": self._api_key,
                    "content-type": "application/json",
                },
                json={"requests": requests},
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            del exc
            raise EmbeddingAdapterError(EmbeddingErrorKind.TIMEOUT, retryable=True) from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                kind, retryable = EmbeddingErrorKind.AUTH, False
            elif status == 429:
                kind, retryable = EmbeddingErrorKind.RATE_LIMIT, True
            elif 500 <= status < 600:
                kind, retryable = EmbeddingErrorKind.UPSTREAM, True
            else:
                kind, retryable = EmbeddingErrorKind.REQUEST, False
            del exc
            raise EmbeddingAdapterError(kind, retryable=retryable) from None
        except httpx.RequestError as exc:
            del exc
            raise EmbeddingAdapterError(EmbeddingErrorKind.REQUEST, retryable=True) from None
        try:
            parsed = _BatchEmbeddingResponse.model_validate(response.json())
            vectors = [embedding.values for embedding in parsed.embeddings]
            if len(vectors) != len(texts) or any(
                len(vector) != self.dimension or any(not math.isfinite(value) for value in vector)
                for vector in vectors
            ):
                raise ValueError
        except (ValueError, ValidationError) as exc:
            del exc
            raise EmbeddingAdapterError(EmbeddingErrorKind.SCHEMA, retryable=False) from None
        return vectors


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions must match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
