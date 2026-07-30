from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Any


class ToolKind(StrEnum):
    INTERNAL_SEARCH = "internal_search"
    EXA_SEARCH = "exa_search"
    CONTENTS = "contents"
    METADATA = "metadata"


DEFAULT_LIMITS: dict[ToolKind, int] = {
    ToolKind.INTERNAL_SEARCH: 2,
    ToolKind.EXA_SEARCH: 2,
    ToolKind.CONTENTS: 1,
    ToolKind.METADATA: 1,
}


class BudgetExceeded(RuntimeError):
    """A deterministic tool, time, or context budget was exceeded."""


@dataclass(slots=True)
class ResearchBudget:
    limits: dict[ToolKind, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    timeout_seconds: float = 180.0
    max_evidence: int = 12
    max_excerpt_chars: int = 1200
    max_excerpts_per_document: int = 2
    max_evidence_tokens: int = 10_000
    started_at: float = field(default_factory=monotonic)
    counts: dict[ToolKind, int] = field(default_factory=dict)
    query_counts: dict[str, int] = field(default_factory=dict)
    consecutive_no_progress: int = 0
    flags: set[str] = field(default_factory=set)

    def consume(self, tool: ToolKind, arguments: dict[str, Any]) -> None:
        self.check_time()
        count = self.counts.get(tool, 0) + 1
        self.counts[tool] = count
        if count > self.limits[tool]:
            self.flags.add("search_budget_exceeded")
            raise BudgetExceeded(f"{tool.value} call budget exceeded")

        fingerprint = self._fingerprint(tool, arguments)
        query_count = self.query_counts.get(fingerprint, 0) + 1
        self.query_counts[fingerprint] = query_count
        if query_count >= 3:
            self.flags.add("duplicate_query_loop")
            raise BudgetExceeded("same tool arguments attempted three times")

    def record_progress(self, new_source_count: int) -> None:
        self.consecutive_no_progress = (
            self.consecutive_no_progress + 1 if new_source_count == 0 else 0
        )
        if self.consecutive_no_progress >= 2:
            self.flags.add("no_progress")
            raise BudgetExceeded("two consecutive retrieval rounds produced no new sources")

    def record_context_ratio(self, ratio: float) -> None:
        if ratio >= 0.95:
            self.flags.add("context_critical")
        elif ratio >= 0.80:
            self.flags.add("context_high")

    def check_time(self) -> None:
        if monotonic() - self.started_at > self.timeout_seconds:
            self.flags.add("timeout")
            raise BudgetExceeded("turn exceeded 180 seconds")

    @property
    def total_calls(self) -> int:
        return sum(self.counts.values())

    @property
    def duplicate_query_count(self) -> int:
        return sum(max(count - 1, 0) for count in self.query_counts.values())

    @staticmethod
    def _fingerprint(tool: ToolKind, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(f"{tool.value}:{canonical}".encode()).hexdigest()


def pack_evidence_text(
    evidence_by_document: list[tuple[str, str]],
    budget: ResearchBudget,
) -> list[tuple[str, str]]:
    """Apply per-document, total excerpt, character, and approximate token limits."""

    result: list[tuple[str, str]] = []
    document_counts: dict[str, int] = {}
    approximate_tokens = 0
    for document_id, excerpt in evidence_by_document:
        if len(result) >= budget.max_evidence:
            break
        if document_counts.get(document_id, 0) >= budget.max_excerpts_per_document:
            continue
        clipped = excerpt[: budget.max_excerpt_chars]
        token_count = max(1, len(clipped) // 4)
        if approximate_tokens + token_count > budget.max_evidence_tokens:
            break
        result.append((document_id, clipped))
        document_counts[document_id] = document_counts.get(document_id, 0) + 1
        approximate_tokens += token_count
    return result
