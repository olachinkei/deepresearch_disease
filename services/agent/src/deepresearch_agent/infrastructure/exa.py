from __future__ import annotations

import hashlib
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from deepresearch_agent.domain.models import Evidence, SourceKind


class ExaResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None
    url: HttpUrl
    published_date: str | None = Field(default=None, alias="publishedDate")
    author: str | None = None
    highlights: list[str] = Field(default_factory=list)


class ExaSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[ExaResult]


class ExaSearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.exa.ai",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=30)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search_publications(self, query: str, *, num_results: int = 10) -> list[Evidence]:
        payload: dict[str, Any] = {
            "query": query,
            "type": "auto",
            "category": "publication",
            "numResults": min(num_results, 10),
            "contents": {
                "highlights": {
                    "maxCharacters": 1200,
                    "numSentences": 5,
                }
            },
        }
        response = await self._client.post(
            "/search",
            headers={"x-api-key": self._api_key, "content-type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        parsed = ExaSearchResponse.model_validate(response.json())
        evidence: list[Evidence] = []
        for result in parsed.results:
            for index, highlight in enumerate(result.highlights[:2], start=1):
                stable = hashlib.sha256(f"{result.id}:{index}".encode()).hexdigest()[:12]
                evidence.append(
                    Evidence(
                        id=f"EXA-{stable}",
                        document_id=f"exa:{result.id}",
                        source_kind=SourceKind.PUBLIC,
                        title=result.title or "Untitled publication",
                        excerpt=highlight[:1200],
                        canonical_url=result.url,
                        score=1.0 / index,
                    )
                )
        return evidence
