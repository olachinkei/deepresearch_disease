from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

SUITE_FILES: dict[str, str] = {
    "retrieval": "retrieval.jsonl",
    "synthesis": "synthesis.jsonl",
    "multi_turn_behavior": "multi_turn_behavior.jsonl",
    "frustration": "frustration.jsonl",
}
COUNT_RANGES: dict[str, tuple[int, int]] = {
    "retrieval": (30, 40),
    "synthesis": (20, 25),
    "multi_turn_behavior": (15, 20),
    "frustration": (100, 100),
}
LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "retrieval": ("relevant_document_ids",),
    "synthesis": ("evidence_ids", "expected_sections", "forbidden_behaviors"),
    "multi_turn_behavior": ("expected_state", "expected_flags"),
    "frustration": ("label", "hard_negative"),
}
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class Expertise(StrEnum):
    STROKE = "stroke"
    DRUG_DISCOVERY = "drug_discovery"


class CoverageTag(StrEnum):
    TARGET_MECHANISM = "target_mechanism"
    EVIDENCE_STAGE = "evidence_stage"
    NEGATIVE_EVIDENCE = "negative_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    RETRACTED = "retracted"
    OUT_OF_SCOPE = "out_of_scope"
    NOT_FOUND = "not_found"


class ReviewerAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
    expertise: Expertise
    assigned_on: date


class GoldDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"]
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
    disease: Literal["ischemic stroke"]
    rubric_version: str = Field(min_length=1)
    created_on: date
    artifact_visibility: Literal["public", "internal"]
    scientific_gold: bool
    release_gate_eligible: bool
    reviewers: tuple[ReviewerAssignment, ...] = Field(min_length=2)
    counts: dict[str, int]

    @field_validator("rubric_version")
    @classmethod
    def reject_blank_rubric_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rubric version cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_reviewer_assignments(self) -> GoldDatasetManifest:
        reviewer_ids = [reviewer.reviewer_id for reviewer in self.reviewers]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("reviewer IDs must be unique")
        expertise = {reviewer.expertise for reviewer in self.reviewers}
        if expertise != {Expertise.STROKE, Expertise.DRUG_DISCOVERY}:
            raise ValueError("stroke and drug-discovery SME assignments are required")
        if set(self.counts) != set(SUITE_FILES):
            raise ValueError("manifest counts must list every evaluation suite")
        return self


class IndependentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str
    reviewed_on: date
    case_sha256: str = Field(pattern=SHA256_PATTERN)
    label_sha256: str = Field(pattern=SHA256_PATTERN)


class Adjudication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adjudicator_id: str
    adjudicated_on: date
    case_sha256: str = Field(pattern=SHA256_PATTERN)
    final_label_sha256: str = Field(pattern=SHA256_PATTERN)
    resolution: Literal["consensus", "third_reviewer"]


class CaseGovernanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
    suite: Literal[
        "retrieval",
        "synthesis",
        "multi_turn_behavior",
        "frustration",
    ]
    coverage_tags: frozenset[CoverageTag]
    source_ids: tuple[str, ...]
    contains_internal_content: bool = False
    reviews: tuple[IndependentReview, ...] = Field(min_length=2, max_length=2)
    adjudication: Adjudication

    @model_validator(mode="after")
    def validate_independent_reviewers(self) -> CaseGovernanceRecord:
        reviewer_ids = [review.reviewer_id for review in self.reviews]
        if len(set(reviewer_ids)) != 2:
            raise ValueError("case reviews must come from two distinct reviewers")
        return self


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,255}$")
    source_type: Literal["public", "internal"]
    license: str = Field(min_length=1)
    acquired_on: date
    canonical_url: HttpUrl | None = None
    publication_status: Literal["active", "retracted", "corrected", "preprint"]
    evidence_stage: Literal[
        "in_vitro",
        "animal",
        "observational",
        "clinical",
        "mixed",
        "not_applicable",
    ]

    @field_validator("license")
    @classmethod
    def validate_license(cls, value: str) -> str:
        if value.strip().casefold() in {"", "unknown", "unverified"}:
            raise ValueError("source license must be verified")
        return value

    @model_validator(mode="after")
    def require_public_url(self) -> SourceProvenance:
        if self.source_type == "public" and self.canonical_url is None:
            raise ValueError("public source provenance requires a canonical URL")
        return self


class DatasetGovernanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    dataset_version: str
    case_counts: dict[str, int]
    coverage_counts: dict[str, int]
    reviewed_case_count: int
    agreement_count: int
    disagreement_count: int
    agreement_rate: float
    unresolved_label_count: int
    sme_reviewed: bool
    scientific_gold: bool
    release_gate_eligible: bool
    errors: list[str]


def canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def case_sha256(row: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(row))


def label_sha256(suite: str, row: Mapping[str, Any]) -> str:
    fields = LABEL_FIELDS[suite]
    missing = [field for field in fields if field not in row]
    if missing:
        raise ValueError(
            f"case {row.get('id', '<unknown>')} is missing label fields: {missing}"
        )
    return canonical_sha256({field: row[field] for field in fields})


def _read_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value: object = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def validate_gold_dataset_bundle(fixtures: Path) -> DatasetGovernanceReport:
    manifest = GoldDatasetManifest.model_validate(_read_json(fixtures / "manifest.json"))
    datasets = {
        suite: _read_jsonl(fixtures / filename)
        for suite, filename in SUITE_FILES.items()
    }
    governance_rows = [
        CaseGovernanceRecord.model_validate(row)
        for row in _read_jsonl(fixtures / "reviews.jsonl")
    ]
    provenance_rows = [
        SourceProvenance.model_validate(row)
        for row in _read_jsonl(fixtures / "provenance.jsonl")
    ]

    errors: list[str] = []
    today = date.today()
    if manifest.created_on > today:
        errors.append("manifest_created_in_future")
    if any(reviewer.assigned_on > today for reviewer in manifest.reviewers):
        errors.append("reviewer_assignment_in_future")
    counts = {suite: len(rows) for suite, rows in datasets.items()}
    if counts != manifest.counts:
        errors.append("manifest_count_mismatch")
    for suite, count in counts.items():
        minimum, maximum = COUNT_RANGES[suite]
        if not minimum <= count <= maximum:
            errors.append(f"{suite}:count_out_of_range")

    cases: dict[str, tuple[str, dict[str, Any]]] = {}
    content_hashes: set[str] = set()
    duplicate_content = 0
    for suite, rows in datasets.items():
        for row in rows:
            case_id = row.get("id")
            if not isinstance(case_id, str) or not case_id:
                errors.append(f"{suite}:case_id_missing")
                continue
            if case_id in cases:
                errors.append(f"{case_id}:duplicate_case_id")
            cases[case_id] = (suite, row)
            content = {
                key: value
                for key, value in row.items()
                if key not in {"id", "sme_reviewed"}
            }
            content_hash = canonical_sha256(content)
            if content_hash in content_hashes:
                duplicate_content += 1
            content_hashes.add(content_hash)
    if duplicate_content:
        errors.append("duplicate_case_content")

    governance_by_id: dict[str, CaseGovernanceRecord] = {}
    for record in governance_rows:
        if record.case_id in governance_by_id:
            errors.append(f"{record.case_id}:duplicate_governance_record")
        governance_by_id[record.case_id] = record
    missing_governance = sorted(cases.keys() - governance_by_id.keys())
    extra_governance = sorted(governance_by_id.keys() - cases.keys())
    if missing_governance:
        errors.append(f"missing_governance_records:{len(missing_governance)}")
    if extra_governance:
        errors.append(f"unknown_governance_records:{len(extra_governance)}")

    provenance_by_id = {record.source_id: record for record in provenance_rows}
    if len(provenance_by_id) != len(provenance_rows):
        errors.append("duplicate_provenance_source_id")

    reviewers = {reviewer.reviewer_id: reviewer for reviewer in manifest.reviewers}
    agreement_count = 0
    disagreement_count = 0
    unresolved = 0
    coverage_counts = {tag.value: 0 for tag in CoverageTag}
    reviewed_case_count = 0
    for case_id, (suite, row) in cases.items():
        governance_record = governance_by_id.get(case_id)
        if governance_record is None:
            unresolved += 1
            continue
        if governance_record.suite != suite:
            errors.append(f"{case_id}:suite_mismatch")
        for tag in governance_record.coverage_tags:
            coverage_counts[tag.value] += 1
        if (
            manifest.artifact_visibility == "public"
            and governance_record.contains_internal_content
        ):
            errors.append(f"{case_id}:internal_content_in_public_artifact")

        current_case_hash = case_sha256(row)
        try:
            current_label_hash = label_sha256(suite, row)
        except ValueError:
            errors.append(f"{case_id}:label_fields_missing")
            unresolved += 1
            continue
        review_ids = {
            review.reviewer_id for review in governance_record.reviews
        }
        assigned = [reviewers.get(reviewer_id) for reviewer_id in review_ids]
        if any(reviewer is None for reviewer in assigned):
            errors.append(f"{case_id}:unassigned_reviewer")
        elif {reviewer.expertise for reviewer in assigned if reviewer} != {
            Expertise.STROKE,
            Expertise.DRUG_DISCOVERY,
        }:
            errors.append(f"{case_id}:required_expertise_missing")
        if any(
            review.case_sha256 != current_case_hash
            for review in governance_record.reviews
        ):
            errors.append(f"{case_id}:stale_review")
        if any(review.reviewed_on > today for review in governance_record.reviews):
            errors.append(f"{case_id}:review_in_future")
        if any(
            reviewer is not None
            and review.reviewed_on < reviewer.assigned_on
            for review, reviewer in (
                (review, reviewers.get(review.reviewer_id))
                for review in governance_record.reviews
            )
        ):
            errors.append(f"{case_id}:review_precedes_assignment")
        review_label_hashes = {
            review.label_sha256 for review in governance_record.reviews
        }
        if len(review_label_hashes) == 1:
            agreement_count += 1
        else:
            disagreement_count += 1
        adjudication = governance_record.adjudication
        adjudicator = reviewers.get(adjudication.adjudicator_id)
        if adjudicator is None:
            errors.append(f"{case_id}:unassigned_adjudicator")
        elif adjudication.adjudicated_on < adjudicator.assigned_on:
            errors.append(f"{case_id}:adjudication_precedes_assignment")
        if (
            adjudication.adjudicated_on > today
            or adjudication.adjudicated_on
            < max(review.reviewed_on for review in governance_record.reviews)
        ):
            errors.append(f"{case_id}:invalid_adjudication_date")
        if (
            adjudication.resolution == "third_reviewer"
            and adjudication.adjudicator_id in review_ids
        ):
            errors.append(f"{case_id}:third_reviewer_not_independent")
        if (
            adjudication.case_sha256 != current_case_hash
            or adjudication.final_label_sha256 != current_label_hash
        ):
            errors.append(f"{case_id}:unresolved_or_stale_adjudication")
            unresolved += 1
        else:
            reviewed_case_count += 1

        if (
            not governance_record.source_ids
            and CoverageTag.OUT_OF_SCOPE not in governance_record.coverage_tags
            and CoverageTag.NOT_FOUND not in governance_record.coverage_tags
            and suite != "frustration"
        ):
            errors.append(f"{case_id}:source_provenance_missing")
        for source_id in governance_record.source_ids:
            provenance = provenance_by_id.get(source_id)
            if provenance is None:
                errors.append(f"{case_id}:unknown_source_id")
            elif (
                manifest.artifact_visibility == "public"
                and provenance.source_type == "internal"
            ):
                errors.append(f"{case_id}:internal_source_in_public_artifact")
        label_source_ids = {
            str(source_id)
            for field in ("relevant_document_ids", "evidence_ids")
            for source_id in row.get(field, [])
        }
        if not label_source_ids.issubset(set(governance_record.source_ids)):
            errors.append(f"{case_id}:label_source_provenance_missing")
        referenced_provenance = [
            provenance_by_id[source_id]
            for source_id in governance_record.source_ids
            if source_id in provenance_by_id
        ]
        if (
            CoverageTag.RETRACTED in governance_record.coverage_tags
            and not any(
                source.publication_status == "retracted"
                for source in referenced_provenance
            )
        ):
            errors.append(f"{case_id}:retracted_coverage_unproven")
        if (
            CoverageTag.EVIDENCE_STAGE in governance_record.coverage_tags
            and not any(
                source.evidence_stage != "not_applicable"
                for source in referenced_provenance
            )
        ):
            errors.append(f"{case_id}:evidence_stage_coverage_unproven")

    missing_coverage = [
        tag for tag, count in coverage_counts.items() if count == 0
    ]
    if missing_coverage:
        errors.append(f"coverage_missing:{','.join(sorted(missing_coverage))}")

    frustration_rows = datasets["frustration"]
    positive_count = sum(row.get("label") is True for row in frustration_rows)
    hard_negative_count = sum(
        row.get("label") is False and row.get("hard_negative") is True
        for row in frustration_rows
    )
    if positive_count != 50:
        errors.append("frustration_positive_count_mismatch")
    if hard_negative_count != 50:
        errors.append("frustration_hard_negative_count_mismatch")
    if any(
        not (
            (row.get("label") is True and row.get("hard_negative") is False)
            or (row.get("label") is False and row.get("hard_negative") is True)
        )
        for row in frustration_rows
    ):
        errors.append("frustration_label_contract_violation")

    total_reviewed = agreement_count + disagreement_count
    agreement_rate = (
        agreement_count / total_reviewed if total_reviewed else 0.0
    )
    unique_errors = sorted(set(errors))
    review_complete = (
        not unique_errors
        and reviewed_case_count == len(cases)
        and unresolved == 0
    )
    release_eligible = (
        review_complete
        and manifest.scientific_gold
        and manifest.release_gate_eligible
    )
    return DatasetGovernanceReport(
        schema_version=manifest.schema_version,
        dataset_version=manifest.version,
        case_counts=counts,
        coverage_counts=coverage_counts,
        reviewed_case_count=reviewed_case_count,
        agreement_count=agreement_count,
        disagreement_count=disagreement_count,
        agreement_rate=agreement_rate,
        unresolved_label_count=unresolved,
        sme_reviewed=review_complete,
        scientific_gold=review_complete and manifest.scientific_gold,
        release_gate_eligible=release_eligible,
        errors=unique_errors,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate SME review and adjudication for a gold dataset bundle."
    )
    parser.add_argument("--fixtures", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = validate_gold_dataset_bundle(args.fixtures)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"gold dataset validation failed: {type(exc).__name__}") from exc
    print(json.dumps(report.model_dump(mode="json"), indent=2))
    if not report.release_gate_eligible:
        raise SystemExit(1)
