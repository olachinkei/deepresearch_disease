from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class SourceKind(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"


class Mechanism(StrEnum):
    STABILIZATION = "stabilization"
    INHIBITION = "inhibition"
    DEGRADATION = "degradation"
    ACTIVATION = "activation"
    OTHER = "other"


class SupportLevel(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MIXED = "mixed"
    BACKGROUND = "background"
    UNKNOWN = "unknown"


class EvidenceStage(StrEnum):
    IN_VITRO = "in_vitro"
    ANIMAL = "animal"
    HUMAN_OBSERVATIONAL = "human_observational"
    CLINICAL = "clinical"
    REVIEW = "review"
    UNKNOWN = "unknown"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class PublicationStatus(StrEnum):
    CURRENT = "current"
    CORRECTED = "corrected"
    RETRACTED = "retracted"
    UNKNOWN = "unknown"


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    year: int | None = None
    journal: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    canonical_url: HttpUrl | None = None
    source_url: HttpUrl | None = None
    source_kind: SourceKind = SourceKind.PUBLIC
    internal_id: str | None = None
    access_class: str = "public"
    license: str | None = None
    is_oa: bool = False
    full_text_url: HttpUrl | None = None
    full_text_stored: bool = False
    retracted: bool = False
    provenance: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("doi")
    @classmethod
    def normalize_doi(cls, value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if normalized.startswith(prefix):
                normalized = normalized.removeprefix(prefix)
        return normalized or None


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    ordinal: int
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    token_count: int
    embedding: list[float] | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    source_kind: SourceKind
    title: str
    excerpt: str = Field(max_length=1200)
    canonical_url: HttpUrl | None = None
    doi: str | None = None
    pmid: str | None = None
    page: int | None = None
    section: str | None = None
    score: float = 0.0
    support_level: SupportLevel = SupportLevel.UNKNOWN
    evidence_stage: EvidenceStage = EvidenceStage.UNKNOWN
    retracted: bool = False
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    publication_status: PublicationStatus = PublicationStatus.UNKNOWN
    provenance: list[str] = Field(default_factory=list)


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_molecule: str | None = None
    mechanism: Mechanism | None = None
    disease: Literal["ischemic stroke"] = "ischemic stroke"
    research_question: str


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    support_level: SupportLevel


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    document_id: str
    title: str
    source_kind: SourceKind
    url: HttpUrl | None = None
    doi: str | None = None
    pmid: str | None = None
    evidence_stage: EvidenceStage = EvidenceStage.UNKNOWN
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    publication_status: PublicationStatus = PublicationStatus.UNKNOWN


class RunManifest(BaseModel):
    turn_id: str
    conversation_id: str
    agent_version: str
    model_id: str
    prompt_version: str
    prompt_sha256: str
    corpus_version: str
    runtime_mode: str
    tool_counts: dict[str, int]
    flags: list[str]
    citation_count: int
    source_count: int
    context_ratio: float = 0.0
    finish_reason: Literal["stop", "cancelled", "timeout", "error"] = "stop"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class ResearchResult(BaseModel):
    answer_markdown: str
    claims: list[Claim]
    sources: list[SourceReference]
    limitations: list[str]
    manifest: RunManifest


class WorkflowEvent(BaseModel):
    kind: Literal[
        "research_started",
        "search_progress",
        "answer_delta",
        "completed",
        "cancelled",
        "error",
    ]
    turn_id: str
    message: str | None = None
    delta: str | None = None
    result: ResearchResult | None = None
    details: dict[str, Any] = Field(default_factory=dict)
