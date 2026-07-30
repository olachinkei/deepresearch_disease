from __future__ import annotations

import re
from typing import Literal

from deepresearch_agent.domain.models import Mechanism, ResearchInput

CANONICAL_DISEASE: Literal["ischemic stroke"] = "ischemic stroke"
DISEASE_ALIASES: dict[str, Literal["ischemic stroke"]] = {
    "ischemic stroke": CANONICAL_DISEASE,
    "ischaemic stroke": CANONICAL_DISEASE,
    "cerebral infarction": CANONICAL_DISEASE,
    "brain infarction": CANONICAL_DISEASE,
    "脳梗塞": CANONICAL_DISEASE,
}
DEFAULT_RESEARCH_QUESTION = (
    "Assess target validity, mechanistic evidence, contradictory findings, "
    "and clinical translatability for ischemic stroke."
)
_ENGLISH_BIOMEDICAL_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:/()'-]{0,119}$")


class ScopeError(ValueError):
    """Raised when a request is outside the MVP disease scope."""


def normalize_disease(value: str | None) -> Literal["ischemic stroke"]:
    key = (value or CANONICAL_DISEASE).strip().casefold()
    try:
        return DISEASE_ALIASES[key]
    except KeyError as exc:
        raise ScopeError(
            f"Disease scope is fixed to {CANONICAL_DISEASE!r}; received {value!r}"
        ) from exc


def normalize_optional_biomedical_term(value: str | None, *, field_name: str) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = " ".join(value.split())
    if not _ENGLISH_BIOMEDICAL_TERM.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an English biomedical term")
    return normalized


def normalize_research_input(
    *,
    target_molecule: str | None,
    mechanism: str | None,
    disease: str | None,
    research_question: str | None,
) -> ResearchInput:
    target = normalize_optional_biomedical_term(target_molecule, field_name="target_molecule")
    normalized_mechanism = mechanism.strip().casefold() if mechanism else None
    mechanism_value = Mechanism(normalized_mechanism) if normalized_mechanism else None
    question = " ".join((research_question or "").split()) or DEFAULT_RESEARCH_QUESTION
    if len(question) > 2000:
        raise ValueError("research_question must contain at most 2000 characters")
    return ResearchInput(
        target_molecule=target,
        mechanism=mechanism_value,
        disease=normalize_disease(disease),
        research_question=question,
    )


def build_search_queries(research_input: ResearchInput) -> list[str]:
    terms: list[str] = [research_input.disease]
    if research_input.target_molecule:
        terms.append(research_input.target_molecule)
    if research_input.mechanism:
        terms.append(research_input.mechanism.value)
    question = research_input.research_question[:400]
    primary = " ".join([*terms, question])
    secondary = (
        f"{' '.join(terms)} mechanism therapeutic target contradictory evidence {question}"
    )
    return [primary, secondary]
