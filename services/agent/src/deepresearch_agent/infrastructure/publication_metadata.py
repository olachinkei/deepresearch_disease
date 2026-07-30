from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from deepresearch_agent.domain.models import (
    Evidence,
    EvidenceStage,
    PublicationStatus,
    VerificationStatus,
)

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
PMID_PATTERN = re.compile(r"\d{1,12}")


class MetadataVerificationError(RuntimeError):
    """Sanitized metadata-provider failure."""

    def __init__(self) -> None:
        super().__init__("Publication metadata verification failed")


class PublicationMetadataVerifier(Protocol):
    async def verify(self, evidence: Sequence[Evidence]) -> list[Evidence]: ...

    async def close(self) -> None: ...


class EuropePmcResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pmid: str | None = None
    doi: str | None = None
    pub_type_list: list[str] = Field(default_factory=list, alias="pubTypeList")
    is_retracted: str | bool | None = Field(default=None, alias="isRetracted")

    @field_validator("pub_type_list", mode="before")
    @classmethod
    def flatten_publication_types(cls, value: object) -> object:
        if isinstance(value, dict):
            return value.get("pubType", [])
        return value


class EuropePmcResultList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: list[EuropePmcResult] = Field(default_factory=list)


class EuropePmcResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result_list: EuropePmcResultList = Field(alias="resultList")


class EuropePmcMetadataVerifier:
    """Verify all DOI/PMID identifiers in one metadata-only Europe PMC request."""

    def __init__(
        self,
        *,
        base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=20)
        self._owns_client = client is None
        self._cache: dict[str, EuropePmcResult | None] = {}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def verify(self, evidence: Sequence[Evidence]) -> list[Evidence]:
        entries = _identifier_entries(evidence)
        if not entries:
            return list(evidence)
        missing = [(key, query) for key, query in entries if key not in self._cache]
        if missing:
            try:
                response = await self._client.get(
                    "/search",
                    params={
                        "query": " OR ".join(query for _, query in missing),
                        "format": "json",
                        "pageSize": min(len(missing), 100),
                        "resultType": "core",
                    },
                )
                response.raise_for_status()
                parsed = EuropePmcResponse.model_validate(response.json())
            except (
                httpx.HTTPError,
                ValueError,
                ValidationError,
            ) as exc:
                del exc
                raise MetadataVerificationError from None
            for key, _ in missing:
                self._cache[key] = None
            for result in parsed.result_list.result:
                if result.doi:
                    self._cache[f"doi:{result.doi.casefold()}"] = result
                if result.pmid:
                    self._cache[f"pmid:{result.pmid}"] = result
            while len(self._cache) > 2048:
                self._cache.pop(next(iter(self._cache)))

        verified: list[Evidence] = []
        for item in evidence:
            item_key = _identifier_key(item)
            cached_result = self._cache.get(item_key) if item_key else None
            if cached_result is None:
                status = (
                    VerificationStatus.NOT_FOUND
                    if item.doi or item.pmid
                    else VerificationStatus.UNVERIFIED
                )
                verified.append(
                    item.model_copy(
                        update={
                            "verification_status": status,
                            "provenance": [*item.provenance, "europe_pmc:not_found"]
                            if status == VerificationStatus.NOT_FOUND
                            else item.provenance,
                        }
                    )
                )
                continue
            publication_status = _publication_status(cached_result)
            verified.append(
                item.model_copy(
                    update={
                        "doi": cached_result.doi or item.doi,
                        "pmid": cached_result.pmid or item.pmid,
                        "evidence_stage": _evidence_stage(cached_result.pub_type_list),
                        "retracted": publication_status == PublicationStatus.RETRACTED,
                        "verification_status": VerificationStatus.VERIFIED,
                        "publication_status": publication_status,
                        "provenance": [*item.provenance, "europe_pmc:verified"],
                    }
                )
            )
        return verified


def _identifier_entries(evidence: Sequence[Evidence]) -> list[tuple[str, str]]:
    identifiers: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in evidence:
        entry = (
            (f"doi:{item.doi.casefold()}", f'DOI:"{item.doi}"')
            if item.doi and DOI_PATTERN.fullmatch(item.doi)
            else (
                (f"pmid:{item.pmid}", f'EXT_ID:"{item.pmid}"')
                if item.pmid and PMID_PATTERN.fullmatch(item.pmid)
                else None
            )
        )
        if entry and entry[0] not in seen:
            identifiers.append(entry)
            seen.add(entry[0])
    return identifiers


def _identifier_key(item: Evidence) -> str | None:
    entries = _identifier_entries([item])
    return entries[0][0] if entries else None


def _publication_status(result: EuropePmcResult) -> PublicationStatus:
    types = {value.casefold() for value in result.pub_type_list}
    is_retracted = result.is_retracted is True or (
        isinstance(result.is_retracted, str)
        and result.is_retracted.casefold() in {"y", "yes", "true"}
    )
    if is_retracted or any("retract" in value for value in types):
        return PublicationStatus.RETRACTED
    if any("correct" in value or "erratum" in value for value in types):
        return PublicationStatus.CORRECTED
    return PublicationStatus.CURRENT


def _evidence_stage(publication_types: Sequence[str]) -> EvidenceStage:
    types = " ".join(publication_types).casefold()
    if "clinical trial" in types:
        return EvidenceStage.CLINICAL
    if "observational" in types or "cohort" in types or "case-control" in types:
        return EvidenceStage.HUMAN_OBSERVATIONAL
    if "review" in types or "meta-analysis" in types:
        return EvidenceStage.REVIEW
    if "animal" in types:
        return EvidenceStage.ANIMAL
    if "in vitro" in types:
        return EvidenceStage.IN_VITRO
    return EvidenceStage.UNKNOWN
