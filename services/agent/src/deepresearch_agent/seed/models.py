from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from deepresearch_agent.domain.models import Document


class SourceCollectionStats(BaseModel):
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    enabled: bool = True


class SeedContentPolicy(BaseModel):
    metadata_only: bool = True
    article_text_downloaded: bool = False
    oa_full_text_handling: Literal[
        "url_and_license_only",
        "europe_pmc_allowlisted_xml",
    ] = "url_and_license_only"
    paywalled_text_stored: Literal[False] = False


class PublicSeedManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0", "2.0"] = "1.0"
    snapshot_id: str
    created_at: datetime
    query: str
    requested_limit: int
    document_count: int
    sources: dict[str, SourceCollectionStats]
    embedding: dict[str, Any] = Field(
        default_factory=lambda: {
            "provider": "local",
            "model": "local-hash-embedding-v1",
            "dimension": 768,
        }
    )
    content_policy: SeedContentPolicy = Field(default_factory=SeedContentPolicy)
    documents: list[Document]
