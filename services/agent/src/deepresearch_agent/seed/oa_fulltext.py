from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as ET
from pydantic import BaseModel, ConfigDict

from deepresearch_agent.domain.models import Chunk, Document

EUROPE_PMC_BASE_URL = "https://www.ebi.ac.uk"
MAX_XML_BYTES = 10 * 1024 * 1024
_PMCID = re.compile(r"PMC[0-9]+", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

LICENSE_ALLOWLIST = (
    (
        re.compile(r"creativecommons\.org/licenses/by-sa/|\bcc(?:\s+|-)by(?:\s+|-)sa\b"),
        "CC-BY-SA-4.0",
    ),
    (
        re.compile(
            r"creativecommons\.org/licenses/by/|"
            r"\bcc(?:\s+|-)by\b(?![\s-]+(?:nc|nd|sa)\b)"
        ),
        "CC-BY-4.0",
    ),
    (re.compile(r"creativecommons\.org/publicdomain/zero/|\bcc0\b"), "CC0-1.0"),
    (re.compile(r"\bpublic domain\b"), "Public-Domain"),
)


class OaIngestionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    status: Literal["stored", "skipped", "failed"]
    reason: str | None = None
    source: Literal["europe_pmc_fulltext_xml"] = "europe_pmc_fulltext_xml"
    license: str | None = None
    acquired_at: datetime | None = None
    checksum_sha256: str | None = None
    chunk_count: int = 0


@dataclass(frozen=True, slots=True)
class OaIngestionResult:
    report: OaIngestionReport
    document: Document
    chunks: tuple[Chunk, ...] = ()


class EuropePmcOaFullTextClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = EUROPE_PMC_BASE_URL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=30,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._clock = clock or (lambda: datetime.now(UTC))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ingest(self, document: Document, *, snapshot_id: str) -> OaIngestionResult:
        pmcid = (document.pmcid or "").upper()
        if not document.is_oa:
            return self._skip(document, "not_open_access")
        if not _PMCID.fullmatch(pmcid):
            return self._skip(document, "pmcid_missing_or_invalid")
        try:
            response = await self._client.get(
                f"/europepmc/webservices/rest/{pmcid}/fullTextXML",
                headers={"accept": "application/xml, text/xml"},
            )
            if response.status_code == 404:
                return self._skip(document, "oa_full_text_not_found")
            response.raise_for_status()
        except httpx.HTTPError:
            return self._fail(document, "provider_request_failed")
        payload = response.content
        if len(payload) > MAX_XML_BYTES:
            return self._skip(document, "xml_size_limit_exceeded")
        if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
            return self._skip(document, "unsafe_xml_declaration")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return self._fail(document, "invalid_xml")

        license_id = _allowed_license(document.license, root)
        if license_id is None:
            return self._skip(document, "license_not_allowlisted")
        sections = _extract_sections(root)
        if not sections:
            return self._skip(document, "extractable_body_missing")
        acquired_at = self._clock()
        checksum = hashlib.sha256(payload).hexdigest()
        chunks = _chunk_sections(
            sections,
            document_id=document.id,
            snapshot_id=snapshot_id,
        )
        updated = document.model_copy(
            update={
                "license": license_id,
                "full_text_stored": True,
                "provenance": sorted(set([*document.provenance, "europe_pmc:fulltext_xml"])),
                "metadata": {
                    **document.metadata,
                    "full_text_source": (
                        f"{EUROPE_PMC_BASE_URL}/europepmc/webservices/rest/{pmcid}/fullTextXML"
                    ),
                    "full_text_acquired_at": acquired_at.isoformat(),
                    "full_text_checksum_sha256": checksum,
                },
            }
        )
        return OaIngestionResult(
            report=OaIngestionReport(
                document_id=document.id,
                status="stored",
                license=license_id,
                acquired_at=acquired_at,
                checksum_sha256=checksum,
                chunk_count=len(chunks),
            ),
            document=updated,
            chunks=chunks,
        )

    @staticmethod
    def _skip(document: Document, reason: str) -> OaIngestionResult:
        return OaIngestionResult(
            report=OaIngestionReport(
                document_id=document.id,
                status="skipped",
                reason=reason,
            ),
            document=document,
        )

    @staticmethod
    def _fail(document: Document, reason: str) -> OaIngestionResult:
        return OaIngestionResult(
            report=OaIngestionReport(
                document_id=document.id,
                status="failed",
                reason=reason,
            ),
            document=document,
        )


def _allowed_license(metadata_license: str | None, root: Element) -> str | None:
    candidates = [metadata_license or ""]
    for element in root.findall(".//license"):
        candidates.append(element.attrib.get("license-type", ""))
        candidates.append(" ".join(element.itertext()))
        candidates.extend(value for key, value in element.attrib.items() if key.endswith("href"))
    for candidate in candidates:
        normalized = " ".join(candidate.casefold().split())
        for pattern, license_id in LICENSE_ALLOWLIST:
            if pattern.search(normalized):
                return license_id
    return None


def _extract_sections(root: Element) -> list[tuple[str, str]]:
    body = root.find(".//body")
    if body is None:
        return []
    sections: list[tuple[str, str]] = []
    for section in body.findall(".//sec"):
        title_element = section.find("./title")
        title = (
            " ".join("".join(title_element.itertext()).split())
            if title_element is not None
            else "Body"
        )
        paragraphs = [
            " ".join("".join(paragraph.itertext()).split()) for paragraph in section.findall("./p")
        ]
        text = " ".join(paragraph for paragraph in paragraphs if paragraph)
        if text:
            sections.append((title, text))
    if sections:
        return sections
    paragraphs = [
        " ".join("".join(paragraph.itertext()).split()) for paragraph in body.findall("./p")
    ]
    text = " ".join(paragraph for paragraph in paragraphs if paragraph)
    return [("Body", text)] if text else []


def _chunk_sections(
    sections: Sequence[tuple[str, str]],
    *,
    document_id: str,
    snapshot_id: str,
    minimum_tokens: int = 350,
    maximum_tokens: int = 700,
) -> tuple[Chunk, ...]:
    chunks: list[Chunk] = []
    tagged_sentences = [
        (sentence, section)
        for section, text in sections
        for sentence in _sentences(text, maximum_tokens)
    ]
    groups = _sentence_groups(
        tagged_sentences,
        minimum_tokens=minimum_tokens,
        maximum_tokens=maximum_tokens,
    )
    for ordinal, group in enumerate(groups):
        section_names = list(dict.fromkeys(section for _, section in group))
        section = " / ".join(section_names)
        chunks.append(
            _chunk(
                document_id=document_id,
                snapshot_id=snapshot_id,
                ordinal=ordinal,
                section=section,
                sentences=[sentence for sentence, _ in group],
            )
        )
    return tuple(chunks)


def _sentence_groups(
    sentences: Sequence[tuple[str, str]],
    *,
    minimum_tokens: int,
    maximum_tokens: int,
) -> list[list[tuple[str, str]]]:
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_tokens = 0
    for sentence in sentences:
        sentence_tokens = len(sentence[0].split())
        if current and current_tokens + sentence_tokens > maximum_tokens:
            groups.append(current)
            current = [current[-1]]
            current_tokens = len(current[0][0].split())
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        groups.append(current)
    if len(groups) < 2 or _group_tokens(groups[-1]) >= minimum_tokens:
        return groups

    previous, final = groups[-2], groups[-1]
    combined = [*previous, *final[1:]] if final[0] == previous[-1] else [*previous, *final]
    for split in range(1, len(combined)):
        left = combined[:split]
        right = combined[split - 1 :]
        if (
            minimum_tokens <= _group_tokens(left) <= maximum_tokens
            and minimum_tokens <= _group_tokens(right) <= maximum_tokens
        ):
            groups[-2:] = [left, right]
            return groups
    if _group_tokens(combined) <= maximum_tokens:
        groups[-2:] = [combined]
    return groups


def _group_tokens(sentences: Sequence[tuple[str, str]]) -> int:
    return sum(len(sentence.split()) for sentence, _ in sentences)


def _sentences(text: str, maximum_tokens: int) -> list[str]:
    result: list[str] = []
    for sentence in _SENTENCE.split(text):
        words = sentence.split()
        if not words:
            continue
        result.extend(
            " ".join(words[offset : offset + maximum_tokens])
            for offset in range(0, len(words), maximum_tokens)
        )
    return result


def _chunk(
    *,
    document_id: str,
    snapshot_id: str,
    ordinal: int,
    section: str,
    sentences: Sequence[str],
) -> Chunk:
    text = " ".join(sentences)
    identity = f"{snapshot_id}:{document_id}:{ordinal}:{text}"
    return Chunk(
        id=hashlib.sha256(identity.encode()).hexdigest()[:24],
        document_id=document_id,
        ordinal=ordinal,
        text=text,
        section=section,
        token_count=max(1, len(text.split())),
    )
