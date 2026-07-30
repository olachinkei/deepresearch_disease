from __future__ import annotations

import json
from pathlib import Path

ROOT = (
    Path(__file__).parents[1]
    / "src"
    / "deepresearch_agent"
    / "evaluation"
    / "fixtures"
    / "v1"
)


def write_jsonl(name: str, rows: list[dict[str, object]]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / name).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "v1",
        "synthetic": True,
        "sme_reviewed": False,
        "scientific_gold": False,
        "release_gate_eligible": False,
        "purpose": "Pipeline and scorer regression only until stroke/drug-discovery SME review.",
        "counts": {
            "retrieval": 36,
            "synthesis": 24,
            "multi_turn_behavior": 18,
            "frustration": 100,
        },
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    topics = [
        "reperfusion",
        "neuroprotection",
        "inflammation",
        "blood-brain barrier",
        "ferroptosis",
        "repair",
    ]
    retrieval = [
        {
            "id": f"ret-{index:03d}",
            "synthetic": True,
            "sme_reviewed": False,
            "query": f"ischemic stroke {topics[index % len(topics)]} target evidence",
            "relevant_document_ids": [f"D{index:03d}", f"D{(index + 1):03d}"],
            "retrieved_document_ids": [
                f"D{index:03d}",
                f"D{(index + 1):03d}",
                f"N{index:03d}",
            ],
        }
        for index in range(36)
    ]
    write_jsonl("retrieval.jsonl", retrieval)
    synthesis = [
        {
            "id": f"syn-{index:03d}",
            "synthetic": True,
            "sme_reviewed": False,
            "disease": "ischemic stroke",
            "evidence_ids": [f"E{index * 2 + 1}", f"E{index * 2 + 2}"],
            "expected_sections": [
                "結論",
                "Mechanistic rationale",
                "Evidence table",
                "臨床移行段階",
                "矛盾・negative evidence",
                "限界",
                "References",
            ],
            "forbidden_behaviors": [
                "fabricated_citation",
                "patient_specific_advice",
                "positive_retracted_use",
            ],
        }
        for index in range(24)
    ]
    write_jsonl("synthesis.jsonl", synthesis)
    multi_turn = [
        {
            "id": f"multi-{index:03d}",
            "synthetic": True,
            "sme_reviewed": False,
            "turns": [
                {
                    "text": "Assess MMP9 inhibition.",
                    "target_molecule": "MMP9",
                    "mechanism": "inhibition",
                },
                {"text": "What about clinical translation?"},
                {"text": "Show contradictory evidence."},
                {"text": "Summarize limitations."},
            ],
            "expected_state": {
                "target_molecule": "MMP9",
                "mechanism": "inhibition",
                "disease": "ischemic stroke",
            },
            "expected_flags": [],
        }
        for index in range(18)
    ]
    write_jsonl("multi_turn_behavior.jsonl", multi_turn)
    positive_templates = [
        "You ignored my question again.",
        "This is the third time the search looped.",
        "The answer still has no supporting citation.",
        "Why do I keep getting the same irrelevant sources?",
        "This is not what I asked for.",
    ]
    negative_templates = [
        "Please expand the mechanism section.",
        "Can you compare the two cited studies?",
        "Thanks, now show the limitations.",
        "Which evidence is from animal studies?",
        "Please narrow this to the target.",
    ]
    frustration = [
        {
            "id": f"fr-pos-{index:03d}",
            "synthetic": True,
            "sme_reviewed": False,
            "text": positive_templates[index % len(positive_templates)],
            "label": True,
            "hard_negative": False,
        }
        for index in range(50)
    ] + [
        {
            "id": f"fr-neg-{index:03d}",
            "synthetic": True,
            "sme_reviewed": False,
            "text": negative_templates[index % len(negative_templates)],
            "label": False,
            "hard_negative": True,
        }
        for index in range(50)
    ]
    write_jsonl("frustration.jsonl", frustration)


if __name__ == "__main__":
    main()
