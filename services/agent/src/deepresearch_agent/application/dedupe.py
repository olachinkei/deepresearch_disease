from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from deepresearch_agent.domain.models import Document, Evidence

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return _NON_ALNUM.sub(" ", ascii_title.casefold()).strip()


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", "")
    )


def document_identity(document: Document) -> str:
    if document.doi:
        return f"doi:{document.doi.casefold()}"
    if document.pmid:
        return f"pmid:{document.pmid}"
    if document.canonical_url:
        return f"url:{canonicalize_url(str(document.canonical_url))}"
    return f"title:{normalize_title(document.title)}"


def deduplicate_documents(documents: Iterable[Document]) -> list[Document]:
    by_identity: dict[str, Document] = {}
    for document in documents:
        key = document_identity(document)
        if key not in by_identity:
            by_identity[key] = document
            continue
        existing = by_identity[key]
        by_identity[key] = existing.model_copy(
            update={
                "abstract": existing.abstract or document.abstract,
                "authors": existing.authors or document.authors,
                "doi": existing.doi or document.doi,
                "pmid": existing.pmid or document.pmid,
                "canonical_url": existing.canonical_url or document.canonical_url,
                "license": existing.license or document.license,
                "is_oa": existing.is_oa or document.is_oa,
                "full_text_url": existing.full_text_url or document.full_text_url,
                "provenance": sorted(set(existing.provenance + document.provenance)),
            }
        )
    return list(by_identity.values())


def deduplicate_evidence(evidence: Iterable[Evidence]) -> list[Evidence]:
    best: dict[tuple[str, str], Evidence] = {}
    for item in evidence:
        excerpt_key = " ".join(item.excerpt.casefold().split())[:240]
        key = (item.document_id, excerpt_key)
        if key not in best or item.score > best[key].score:
            best[key] = item
    return sorted(best.values(), key=lambda item: item.score, reverse=True)
