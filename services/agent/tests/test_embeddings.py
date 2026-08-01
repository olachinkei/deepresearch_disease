from __future__ import annotations

import json
import math
from typing import Any, cast

import httpx
import pytest

from deepresearch_agent.infrastructure.embeddings import (
    EmbeddingAdapterError,
    EmbeddingDocument,
    EmbeddingErrorKind,
    GeminiEmbeddingProvider,
)
from deepresearch_agent.settings import Settings


def _vector() -> list[float]:
    return [1.0 / math.sqrt(768)] * 768


@pytest.mark.asyncio
async def test_gemini_embedding_uses_pinned_contract_and_asymmetric_prompts() -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1beta/models/gemini-embedding-2:batchEmbedContents")
        assert request.headers["x-goog-api-key"] == "test-key"
        payload = cast(dict[str, Any], json.loads(request.read()))
        payloads.append(payload)
        return httpx.Response(
            200,
            json={"embeddings": [{"values": _vector()} for _ in payload["requests"]]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gemini.test"
    ) as http_client:
        provider = GeminiEmbeddingProvider(api_key="test-key", client=http_client)
        query = await provider.embed_queries(["ischemic stroke NLRP3"])
        documents = await provider.embed_documents(
            [EmbeddingDocument(title="Synthetic paper", text="Synthetic abstract")]
        )

    query_request = payloads[0]["requests"][0]
    document_request = payloads[1]["requests"][0]
    assert query_request["model"] == "models/gemini-embedding-2"
    assert query_request["output_dimensionality"] == 768
    assert query_request["content"]["parts"][0]["text"] == (
        "task: search result | query: ischemic stroke NLRP3"
    )
    assert document_request["content"]["parts"][0]["text"] == (
        "title: Synthetic paper | text: Synthetic abstract"
    )
    assert len(query[0]) == len(documents[0]) == 768


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "kind", "retryable"),
    [
        (401, EmbeddingErrorKind.AUTH, False),
        (429, EmbeddingErrorKind.RATE_LIMIT, True),
        (503, EmbeddingErrorKind.UPSTREAM, True),
        (422, EmbeddingErrorKind.REQUEST, False),
    ],
)
async def test_gemini_embedding_errors_are_sanitized(
    status_code: int, kind: EmbeddingErrorKind, retryable: bool
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="raw-provider-secret")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gemini.test"
    ) as http_client:
        provider = GeminiEmbeddingProvider(api_key="secret-api-key", client=http_client)
        with pytest.raises(EmbeddingAdapterError) as caught:
            await provider.embed_queries(["sensitive-query"])

    assert caught.value.kind == kind
    assert caught.value.retryable is retryable
    assert "raw-provider-secret" not in str(caught.value)
    assert "sensitive-query" not in str(caught.value)
    assert "secret-api-key" not in str(caught.value)


def test_gemini_embedding_settings_are_fail_closed() -> None:
    with pytest.raises(ValueError, match="ALLOW_PUBLIC"):
        Settings(_env_file=None, embedding_provider="gemini", GOOGLE_API_KEY="test-key")
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        Settings(
            _env_file=None,
            embedding_provider="gemini",
            allow_public_content_to_gemini_embeddings=True,
        )

    settings = Settings(
        _env_file=None,
        embedding_provider="gemini",
        allow_public_content_to_gemini_embeddings=True,
        GOOGLE_API_KEY="test-key",
    )
    assert settings.embedding_model == "gemini-embedding-2"
    assert settings.embedding_dimension == 768

    with pytest.raises(ValueError, match="must be 768"):
        Settings(_env_file=None, embedding_dimension=1536)
