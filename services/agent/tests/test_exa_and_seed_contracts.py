from __future__ import annotations

import httpx
import pytest

from deepresearch_agent.infrastructure.exa import ExaSearchClient
from deepresearch_agent.seed.collector import PublicSeedCollector


@pytest.mark.asyncio
async def test_exa_request_uses_publication_highlights_contract() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read().decode()
        captured["api_key"] = request.headers["x-api-key"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "paper-1",
                        "title": "Synthetic stroke publication",
                        "url": "https://example.org/paper-1",
                        "highlights": ["Relevant extractive evidence."],
                        "summary": "This generated summary must not be used.",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.exa.test"
    ) as http_client:
        client = ExaSearchClient(api_key="test-key", client=http_client)
        evidence = await client.search_publications("ischemic stroke NLRP3")

    payload = str(captured["payload"])
    assert '"type":"auto"' in payload
    assert '"category":"publication"' in payload
    assert '"highlights"' in payload
    assert '"context"' not in payload
    assert captured["api_key"] == "test-key"
    assert evidence[0].excerpt == "Relevant extractive evidence."
    assert "generated summary" not in evidence[0].excerpt


@pytest.mark.asyncio
async def test_seed_collector_is_metadata_only_and_enriches_crossref() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if "europepmc" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "resultList": {
                        "result": [
                            {
                                "id": "1",
                                "source": "MED",
                                "pmid": "1",
                                "doi": "10.1000/test",
                                "title": "Synthetic ischemic stroke study",
                                "abstractText": "Synthetic abstract.",
                                "pubYear": "2015",
                                "isOpenAccess": "N",
                            }
                        ]
                    }
                },
            )
        if "crossref" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "container-title": ["Synthetic Journal"],
                        "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
                    }
                },
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector = PublicSeedCollector(client=client)
        manifest = await collector.collect(limit=1)

    assert manifest.document_count == 1
    assert manifest.content_policy.metadata_only
    assert not manifest.content_policy.article_text_downloaded
    assert not manifest.documents[0].full_text_stored
    assert manifest.documents[0].journal == "Synthetic Journal"
    assert manifest.sources["crossref"].succeeded == 1
