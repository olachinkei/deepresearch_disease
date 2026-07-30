from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from deepresearch_agent.evaluation.dataset_governance import (
    CoverageTag,
    case_sha256,
    label_sha256,
    validate_gold_dataset_bundle,
)
from deepresearch_agent.evaluation.runner import (
    FIXTURE_DIRECTORY,
    _load_fixture_bundle,
)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _build_reviewed_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "reviewed-v2"
    bundle.mkdir()
    datasets: dict[str, list[dict[str, Any]]] = {
        "retrieval": [
            {
                "id": f"ret-{index:03d}",
                "synthetic": False,
                "query": f"ischemic stroke target evidence case {index}",
                "relevant_document_ids": ["SRC-1"],
                "retrieved_document_ids": ["SRC-1"],
            }
            for index in range(36)
        ],
        "synthesis": [
            {
                "id": f"syn-{index:03d}",
                "synthetic": False,
                "disease": "ischemic stroke",
                "evidence_ids": ["SRC-1"],
                "expected_sections": ["結論", f"Evidence case {index}", "限界"],
                "forbidden_behaviors": [
                    "fabricated_citation",
                    f"unsupported_case_{index}",
                ],
            }
            for index in range(24)
        ],
        "multi_turn_behavior": [
            {
                "id": f"multi-{index:03d}",
                "synthetic": True,
                "turns": [
                    {
                        "text": f"Assess synthetic target {index}.",
                        "target_molecule": f"TARGET-{index}",
                        "mechanism": "inhibition",
                    },
                    {"text": "Show conflicting evidence."},
                ],
                "expected_state": {
                    "target_molecule": f"TARGET-{index}",
                    "mechanism": "inhibition",
                    "disease": "ischemic stroke",
                },
                "expected_flags": [],
            }
            for index in range(18)
        ],
        "frustration": [
            {
                "id": f"fr-pos-{index:03d}",
                "synthetic": True,
                "text": f"Synthetic frustration example {index}.",
                "label": True,
                "hard_negative": False,
            }
            for index in range(50)
        ]
        + [
            {
                "id": f"fr-neg-{index:03d}",
                "synthetic": True,
                "text": f"Synthetic neutral follow-up {index}.",
                "label": False,
                "hard_negative": True,
            }
            for index in range(50)
        ],
    }
    filenames = {
        "retrieval": "retrieval.jsonl",
        "synthesis": "synthesis.jsonl",
        "multi_turn_behavior": "multi_turn_behavior.jsonl",
        "frustration": "frustration.jsonl",
    }
    for suite, rows in datasets.items():
        _write_jsonl(bundle / filenames[suite], rows)

    required_tags = list(CoverageTag)
    reviews: list[dict[str, Any]] = []
    ordinal = 0
    for suite, rows in datasets.items():
        for row in rows:
            current_case_hash = case_sha256(row)
            current_label_hash = label_sha256(suite, row)
            second_label_hash = (
                "f" * 64 if ordinal == 0 else current_label_hash
            )
            coverage_tags = (
                [required_tags[ordinal].value]
                if ordinal < len(required_tags)
                else ["target_mechanism"]
            )
            source_ids = [] if suite == "frustration" else ["SRC-1"]
            if coverage_tags == ["retracted"]:
                source_ids.append("SRC-RETRACTED")
            reviews.append(
                {
                    "case_id": row["id"],
                    "suite": suite,
                    "coverage_tags": coverage_tags,
                    "source_ids": source_ids,
                    "contains_internal_content": False,
                    "reviews": [
                        {
                            "reviewer_id": "stroke-sme-001",
                            "reviewed_on": "2026-07-30",
                            "case_sha256": current_case_hash,
                            "label_sha256": current_label_hash,
                        },
                        {
                            "reviewer_id": "drug-sme-001",
                            "reviewed_on": "2026-07-30",
                            "case_sha256": current_case_hash,
                            "label_sha256": second_label_hash,
                        },
                    ],
                    "adjudication": {
                        "adjudicator_id": "stroke-sme-001",
                        "adjudicated_on": "2026-07-30",
                        "case_sha256": current_case_hash,
                        "final_label_sha256": current_label_hash,
                        "resolution": "consensus",
                    },
                }
            )
            ordinal += 1
    _write_jsonl(bundle / "reviews.jsonl", reviews)
    _write_jsonl(
        bundle / "provenance.jsonl",
        [
            {
                "source_id": "SRC-1",
                "source_type": "public",
                "license": "CC-BY-4.0",
                "acquired_on": "2026-07-30",
                "canonical_url": "https://example.org/public-source",
                "publication_status": "active",
                "evidence_stage": "clinical",
            },
            {
                "source_id": "SRC-RETRACTED",
                "source_type": "public",
                "license": "CC-BY-4.0",
                "acquired_on": "2026-07-30",
                "canonical_url": "https://example.org/retracted-source",
                "publication_status": "retracted",
                "evidence_stage": "animal",
            },
        ],
    )
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2",
                "version": "reviewed-v2",
                "disease": "ischemic stroke",
                "rubric_version": "v1.0.0",
                "created_on": "2026-07-30",
                "artifact_visibility": "public",
                "scientific_gold": True,
                "release_gate_eligible": True,
                "reviewers": [
                    {
                        "reviewer_id": "stroke-sme-001",
                        "expertise": "stroke",
                        "assigned_on": "2026-07-30",
                    },
                    {
                        "reviewer_id": "drug-sme-001",
                        "expertise": "drug_discovery",
                        "assigned_on": "2026-07-30",
                    },
                ],
                "counts": {suite: len(rows) for suite, rows in datasets.items()},
            }
        ),
        encoding="utf-8",
    )
    return bundle


def test_reviewed_bundle_reports_coverage_iaa_and_release_eligibility(
    tmp_path: Path,
) -> None:
    bundle = _build_reviewed_bundle(tmp_path)
    report = validate_gold_dataset_bundle(bundle)

    assert report.errors == []
    assert report.reviewed_case_count == 178
    assert report.agreement_count == 177
    assert report.disagreement_count == 1
    assert report.agreement_rate == pytest.approx(177 / 178)
    assert report.unresolved_label_count == 0
    assert all(count >= 1 for count in report.coverage_counts.values())
    assert report.sme_reviewed
    assert report.scientific_gold
    assert report.release_gate_eligible
    serialized_report = report.model_dump_json()
    assert "Synthetic frustration example" not in serialized_report
    assert "stroke-sme-001" not in serialized_report
    assert "drug-sme-001" not in serialized_report

    manifest, _ = _load_fixture_bundle(bundle)
    assert manifest["sme_reviewed"] is True
    assert manifest["_human_review_passed"] is True


def test_changed_case_invalidates_reviews_and_adjudication(tmp_path: Path) -> None:
    bundle = _build_reviewed_bundle(tmp_path)
    rows = [
        json.loads(line)
        for line in (bundle / "retrieval.jsonl").read_text().splitlines()
    ]
    rows[0]["relevant_document_ids"] = ["SRC-CHANGED"]
    _write_jsonl(bundle / "retrieval.jsonl", rows)

    report = validate_gold_dataset_bundle(bundle)

    assert "ret-000:stale_review" in report.errors
    assert "ret-000:unresolved_or_stale_adjudication" in report.errors
    assert not report.release_gate_eligible


def test_public_bundle_rejects_internal_source_and_missing_coverage(
    tmp_path: Path,
) -> None:
    bundle = _build_reviewed_bundle(tmp_path)
    provenance = [
        {
            "source_id": "SRC-1",
            "source_type": "internal",
            "license": "internal-approved",
            "acquired_on": "2026-07-30",
            "publication_status": "active",
            "evidence_stage": "clinical",
        }
    ]
    _write_jsonl(bundle / "provenance.jsonl", provenance)
    review_rows = [
        json.loads(line)
        for line in (bundle / "reviews.jsonl").read_text().splitlines()
    ]
    for row in review_rows:
        row["coverage_tags"] = ["target_mechanism"]
    _write_jsonl(bundle / "reviews.jsonl", review_rows)

    report = validate_gold_dataset_bundle(bundle)

    assert any(
        error.endswith(":internal_source_in_public_artifact")
        for error in report.errors
    )
    assert any(error.startswith("coverage_missing:") for error in report.errors)
    assert not report.release_gate_eligible


def test_legacy_manifest_cannot_self_declare_sme_review(tmp_path: Path) -> None:
    bundle = tmp_path / "legacy"
    shutil.copytree(FIXTURE_DIRECTORY, bundle)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest.update(
        {
            "sme_reviewed": True,
            "scientific_gold": True,
            "release_gate_eligible": True,
        }
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(
        ValueError,
        match="Legacy fixture manifests cannot claim",
    ):
        _load_fixture_bundle(bundle)
