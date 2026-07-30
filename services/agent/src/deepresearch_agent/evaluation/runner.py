from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from deepresearch_agent.evaluation.scorers import ndcg_at_k, recall_at_k

FIXTURE_DIRECTORY = Path(__file__).with_name("fixtures") / "v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def evaluate_fixtures(fixtures: Path) -> dict[str, Any]:
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("sme_reviewed"):
        raise ValueError("Synthetic fixture manifest must not claim SME review")
    datasets = {
        name: load_jsonl(fixtures / filename)
        for name, filename in {
            "retrieval": "retrieval.jsonl",
            "synthesis": "synthesis.jsonl",
            "multi_turn_behavior": "multi_turn_behavior.jsonl",
            "frustration": "frustration.jsonl",
        }.items()
    }
    expected_counts = manifest.get("counts", {})
    actual_counts = {name: len(rows) for name, rows in datasets.items()}
    if actual_counts != expected_counts:
        raise ValueError(
            f"Fixture counts do not match manifest: expected={expected_counts}, "
            f"actual={actual_counts}"
        )
    retrieval = datasets["retrieval"]
    recalls = [
        recall_at_k(row["retrieved_document_ids"], row["relevant_document_ids"], k=10)
        for row in retrieval
    ]
    ndcgs = [
        ndcg_at_k(
            row["retrieved_document_ids"],
            {document_id: 1.0 for document_id in row["relevant_document_ids"]},
            k=10,
        )
        for row in retrieval
    ]
    frustration = datasets["frustration"]
    return {
        "fixture_version": manifest["version"],
        "scientific_gold": False,
        "sme_reviewed": False,
        "release_gate_eligible": False,
        "counts": actual_counts,
        "mean_recall_at_10": sum(recalls) / max(len(recalls), 1),
        "mean_ndcg_at_10": sum(ndcgs) / max(len(ndcgs), 1),
        "frustration_positive": sum(bool(row["label"]) for row in frustration),
        "frustration_hard_negative": sum(
            bool(row["hard_negative"]) for row in frustration
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic offline fixture checks.")
    parser.add_argument("--fixtures", type=Path, default=FIXTURE_DIRECTORY)
    args = parser.parse_args()
    try:
        summary = evaluate_fixtures(args.fixtures)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, indent=2))
