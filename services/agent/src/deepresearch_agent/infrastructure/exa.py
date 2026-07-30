from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from deepresearch_agent.domain.models import Evidence, SourceKind

DOI_URL_PATTERN = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
PMID_URL_PATTERN = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov|europepmc\.org/article/MED)/(\d+)")


class ExaErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    UPSTREAM = "upstream"
    SCHEMA = "schema"
    REQUEST = "request"


class ExaAdapterError(RuntimeError):
    """Sanitized Exa failure safe for logs, traces, and user-visible flags."""

    def __init__(self, kind: ExaErrorKind, *, retryable: bool) -> None:
        super().__init__(f"Exa request failed ({kind.value})")
        self.kind = kind
        self.retryable = retryable


class ExaResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None
    url: HttpUrl
    published_date: str | None = Field(default=None, alias="publishedDate")
    author: str | None = None
    highlights: list[str] = Field(default_factory=list)
    doi: str | None = None
    pmid: str | None = None


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
        try:
            response = await self._client.post(
                "/search",
                headers={"x-api-key": self._api_key, "content-type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            del exc
            raise ExaAdapterError(ExaErrorKind.TIMEOUT, retryable=True) from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                kind, retryable = ExaErrorKind.AUTH, False
            elif status == 429:
                kind, retryable = ExaErrorKind.RATE_LIMIT, True
            elif 500 <= status < 600:
                kind, retryable = ExaErrorKind.UPSTREAM, True
            else:
                kind, retryable = ExaErrorKind.REQUEST, False
            del exc
            raise ExaAdapterError(kind, retryable=retryable) from None
        except httpx.RequestError as exc:
            del exc
            raise ExaAdapterError(ExaErrorKind.REQUEST, retryable=True) from None
        try:
            parsed = ExaSearchResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            del exc
            raise ExaAdapterError(ExaErrorKind.SCHEMA, retryable=False) from None
        evidence: list[Evidence] = []
        for result in parsed.results:
            doi, pmid = _identifiers_from_result(result)
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
                        doi=doi,
                        pmid=pmid,
                        score=1.0 / index,
                        provenance=["exa:search"],
                    )
                )
        return evidence


def _identifiers_from_result(result: ExaResult) -> tuple[str | None, str | None]:
    url = unquote(str(result.url))
    doi_match = DOI_URL_PATTERN.search(url)
    pmid_match = PMID_URL_PATTERN.search(url)
    doi = result.doi or (doi_match.group(1).rstrip(".,;") if doi_match else None)
    pmid = result.pmid or (pmid_match.group(1) if pmid_match else None)
    if doi and doi.casefold().startswith(("http://doi.org/", "https://doi.org/", "doi:")):
        doi = urlparse(doi).path.lstrip("/") if "://" in doi else doi[4:]
    return doi.casefold() if doi else None, pmid
