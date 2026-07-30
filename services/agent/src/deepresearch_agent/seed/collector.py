from __future__ import annotations

import asyncio
import hashlib
import math
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import HttpUrl, TypeAdapter

from deepresearch_agent.application.dedupe import deduplicate_documents
from deepresearch_agent.domain.models import Document, SourceKind
from deepresearch_agent.seed.models import PublicSeedManifest, SourceCollectionStats

DEFAULT_QUERY = (
    '(TITLE_ABS:"ischemic stroke" OR TITLE_ABS:"cerebral infarction") '
    "AND (drug OR therapeutic OR target OR inhibitor OR neuroprotection)"
)
DEFAULT_QUERY_BUCKETS = (
    f"{DEFAULT_QUERY} AND FIRST_PDATE:[1900-01-01 TO 2010-12-31] sort_cited:y",
    f"{DEFAULT_QUERY} AND FIRST_PDATE:[2011-01-01 TO 2017-12-31] sort_cited:y",
    f"{DEFAULT_QUERY} AND FIRST_PDATE:[2018-01-01 TO 2022-12-31] sort_cited:y",
    f"{DEFAULT_QUERY} AND FIRST_PDATE:[2023-01-01 TO 2030-12-31] sort_date:y",
)
_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


class PublicSeedCollector:
    """Metadata-only collector. No method in this class downloads article bodies."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        crossref_mailto: str | None = None,
        unpaywall_email: str | None = None,
        concurrency: int = 2,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._owns_client = client is None
        self._crossref_mailto = crossref_mailto
        self._unpaywall_email = unpaywall_email
        self._semaphore = asyncio.Semaphore(concurrency)
        self._crossref_lock = asyncio.Lock()
        self._last_crossref_request = 0.0
        self._crossref_interval_seconds = 0.1 if crossref_mailto else 0.2

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def collect(self, *, query: str = DEFAULT_QUERY, limit: int = 200) -> PublicSeedManifest:
        stats = {
            "europe_pmc": SourceCollectionStats(),
            "crossref": SourceCollectionStats(),
            "unpaywall": SourceCollectionStats(enabled=bool(self._unpaywall_email)),
        }
        if query == DEFAULT_QUERY:
            bucket_count = min(len(DEFAULT_QUERY_BUCKETS), limit)
            per_bucket = math.ceil(limit * 1.35 / bucket_count)
            bucket_documents: list[list[Document]] = []
            for bucket_query in DEFAULT_QUERY_BUCKETS[:bucket_count]:
                bucket_documents.append(
                    await self._collect_europe_pmc(
                        query=bucket_query,
                        limit=per_bucket,
                        stats=stats,
                    )
                )
            documents = _round_robin(bucket_documents)
            manifest_query = " || ".join(DEFAULT_QUERY_BUCKETS[:bucket_count])
        else:
            documents = await self._collect_europe_pmc(
                query=query,
                limit=limit,
                stats=stats,
            )
            manifest_query = query
        deduped = deduplicate_documents(documents)[:limit]
        await asyncio.gather(
            *(self._enrich_document(document, stats) for document in deduped if document.doi)
        )
        return PublicSeedManifest(
            snapshot_id=f"public-seed-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:8]}",
            created_at=datetime.now(UTC),
            query=manifest_query,
            requested_limit=limit,
            document_count=len(deduped),
            sources=stats,
            documents=deduped,
        )

    async def _collect_europe_pmc(
        self,
        *,
        query: str,
        limit: int,
        stats: dict[str, SourceCollectionStats],
    ) -> list[Document]:
        documents: list[Document] = []
        cursor = "*"
        while len(documents) < limit:
            page_size = min(100, limit - len(documents))
            stats["europe_pmc"].attempted += 1
            payload = await self._europe_pmc_page(
                query=query,
                page_size=page_size,
                cursor=cursor,
            )
            if payload is None:
                stats["europe_pmc"].failed += 1
                break
            results = payload.get("resultList", {}).get("result", [])
            stats["europe_pmc"].succeeded += 1
            if not results:
                break
            documents.extend(self._from_europe_pmc(item) for item in results)
            next_cursor = payload.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return documents[:limit]

    async def _europe_pmc_page(
        self,
        *,
        query: str,
        page_size: int,
        cursor: str,
    ) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                response = await self._client.get(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={
                        "query": query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": page_size,
                        "cursorMark": cursor,
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else None
            except (httpx.HTTPError, TypeError, ValueError):
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
        return None

    def _from_europe_pmc(self, item: dict[str, Any]) -> Document:
        pmid = _string_or_none(item.get("pmid"))
        pmcid = _string_or_none(item.get("pmcid"))
        doi = _string_or_none(item.get("doi"))
        europe_id = _string_or_none(item.get("id")) or pmid or pmcid or uuid4().hex
        title = _string_or_none(item.get("title")) or "Untitled publication"
        full_text_urls = item.get("fullTextUrlList", {}).get("fullTextUrl", [])
        oa_url = _preferred_oa_url(full_text_urls)
        is_oa = bool(item.get("isOpenAccess") == "Y" or oa_url)
        canonical_url = _http_url(
            f"https://doi.org/{doi}"
            if doi
            else f"https://europepmc.org/article/{item.get('source', 'MED')}/{europe_id}"
        )
        source_url = _http_url(
            f"https://europepmc.org/article/{item.get('source', 'MED')}/{europe_id}"
        )
        authors = [
            author.strip()
            for author in (_string_or_none(item.get("authorString")) or "").split(",")
            if author.strip()
        ]
        return Document(
            id=f"epmc:{europe_id}",
            title=title,
            abstract=_string_or_none(item.get("abstractText")),
            authors=authors,
            publication_date=_string_or_none(item.get("firstPublicationDate")),
            year=_int_or_none(item.get("pubYear")),
            journal=_string_or_none(item.get("journalTitle")),
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            canonical_url=canonical_url,
            source_url=source_url,
            source_kind=SourceKind.PUBLIC,
            access_class="public",
            is_oa=is_oa,
            full_text_url=oa_url,
            full_text_stored=False,
            retracted="RETRACTED" in set(item.get("pubTypeList", {}).get("pubType", [])),
            provenance=["europe_pmc"],
            metadata={"europe_pmc_source": item.get("source")},
        )

    async def _enrich_document(
        self,
        document: Document,
        stats: dict[str, SourceCollectionStats],
    ) -> None:
        assert document.doi is not None
        async with self._semaphore:
            crossref = await self._crossref(document.doi, stats["crossref"])
            if crossref:
                updates: dict[str, Any] = {
                    "license": document.license or crossref.get("license"),
                    "journal": document.journal or crossref.get("journal"),
                    "provenance": sorted(set([*document.provenance, "crossref"])),
                }
                if not document.authors and crossref.get("authors"):
                    updates["authors"] = crossref["authors"]
                document.__dict__.update(document.model_copy(update=updates).__dict__)
            if self._unpaywall_email:
                unpaywall = await self._unpaywall(document.doi, stats["unpaywall"])
                if unpaywall:
                    updates = {
                        "is_oa": document.is_oa or bool(unpaywall.get("is_oa")),
                        "license": document.license or unpaywall.get("license"),
                        "full_text_url": document.full_text_url or unpaywall.get("url"),
                        "provenance": sorted(set([*document.provenance, "unpaywall"])),
                    }
                    document.__dict__.update(document.model_copy(update=updates).__dict__)

    async def _crossref(
        self, doi: str, stats: SourceCollectionStats
    ) -> dict[str, Any] | None:
        stats.attempted += 1
        params = {"mailto": self._crossref_mailto} if self._crossref_mailto else None
        for attempt in range(3):
            try:
                async with self._crossref_lock:
                    wait_seconds = (
                        self._crossref_interval_seconds
                        - (monotonic() - self._last_crossref_request)
                    )
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                    response = await self._client.get(
                        f"https://api.crossref.org/works/{quote(doi, safe='')}",
                        params=params,
                        headers={
                            "User-Agent": (
                                "deepresearch-disease-agent/0.1 "
                                f"(mailto:{self._crossref_mailto or 'not-configured'})"
                            )
                        },
                    )
                    self._last_crossref_request = monotonic()
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        retry_after = response.headers.get("retry-after")
                        delay = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 0.5 * (2**attempt)
                        )
                        await asyncio.sleep(min(delay, 5.0))
                        continue
                response.raise_for_status()
                message = response.json().get("message", {})
                stats.succeeded += 1
                licenses = message.get("license", [])
                authors = [
                    " ".join(
                        part
                        for part in (author.get("given"), author.get("family"))
                        if part
                    )
                    for author in message.get("author", [])
                ]
                containers = message.get("container-title", [])
                return {
                    "license": licenses[0].get("URL") if licenses else None,
                    "authors": [author for author in authors if author],
                    "journal": containers[0] if containers else None,
                }
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
        stats.failed += 1
        return None

    async def _unpaywall(
        self, doi: str, stats: SourceCollectionStats
    ) -> dict[str, Any] | None:
        stats.attempted += 1
        try:
            response = await self._client.get(
                f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                params={"email": self._unpaywall_email},
            )
            response.raise_for_status()
            payload = response.json()
            location = payload.get("best_oa_location") or {}
            stats.succeeded += 1
            return {
                "is_oa": bool(payload.get("is_oa")),
                "license": location.get("license"),
                "url": location.get("url_for_pdf") or location.get("url"),
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            stats.failed += 1
            return None


def _preferred_oa_url(values: list[dict[str, Any]]) -> HttpUrl | None:
    for value in values:
        if value.get("availability") == "Open access" and value.get("url"):
            return _http_url(str(value["url"]))
    return None


def _http_url(value: str) -> HttpUrl:
    return _HTTP_URL_ADAPTER.validate_python(value)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round_robin(groups: list[list[Document]]) -> list[Document]:
    result: list[Document] = []
    max_length = max((len(group) for group in groups), default=0)
    for index in range(max_length):
        result.extend(group[index] for group in groups if index < len(group))
    return result


def abstract_chunk_id(document: Document) -> str:
    identity = f"{document.id}:{document.abstract or document.title}"
    return hashlib.sha256(identity.encode()).hexdigest()[:24]
