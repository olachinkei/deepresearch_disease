# Evaluation policy and runbook

## Dataset status

`src/deepresearch_agent/evaluation/fixtures/v1` contains:

- 36 retrieval cases;
- 24 synthesis specifications;
- 18 multi-turn/behavior cases;
- 100 frustration cases (50 positive and 50 hard negative).

All are synthetic, unreviewed fixtures. They test pipeline and scorer behavior but
are not scientific gold, are not release-gate eligible, and must not be promoted to
a challenge dataset without stroke/drug-discovery SME review.

## Local deterministic checks

```bash
uv run pytest
uv run run-offline-eval
```

`run-offline-eval` builds a versioned synthetic SQLite corpus, runs the actual
`CorpusRepository` and `ResearchWorkflow`, executes the multi-turn session path, and
scores the observed outputs. The `retrieved_document_ids` stored in the fixture remain
fixture-integrity metadata and are not used as the actual retrieval output.

The command exits non-zero when the technical smoke fails. Add
`--require-scientific-release` when a release job must also require an eligible,
SME-reviewed dataset and an explicit passing human review. LLM-judge metrics are
advisory and cannot satisfy that requirement.

The typed summary reports independent metrics for schema, scope, tool policy,
Recall@10, nDCG@10, citation resolution/coverage, retrieved-before-cited,
claim-evidence entailment, source status, stage calibration, conflict handling,
multi-turn retention, context p95, truncation, and frustration precision/recall.
Missing required metrics or incident counters always fail the technical gate.
`context_ratio_p95` uses nearest-rank `ceil(0.95 * n)` and fails at `0.80`.

The current synthetic v1 run intentionally reports:

- `technical_smoke_status=failed`, because actual nDCG@10 is approximately `0.4643`;
- `scientific_release_status=ineligible`, because the dataset is synthetic,
  unreviewed, and not release-gate eligible.

The nDCG failure exposes repeated identical queries mapped to different expected
document IDs in v1. Do not tune the threshold or substitute the recorded rankings.
Issue #10 must replace these fixtures with SME-reviewed, adjudicated labels.

Schema-v2 candidate and gold bundles use
[`GOLD_DATASET_LABELING.md`](GOLD_DATASET_LABELING.md). Run
`validate-gold-dataset --fixtures <path>` before evaluation. The validator binds two
independent SME reviews and adjudication to the current case and label hashes, checks
required coverage/counts, rejects repeated case templates and missing
provenance/licenses, reports exact reviewer agreement and unresolved labels, and
prevents internal material from entering a public artifact.

Manifest booleans are not sufficient evidence. Only a clean schema-v2 governance
report can set `sme_reviewed`, scientific-gold eligibility, and the human-review input
used by the scientific release gate. Legacy manifests cannot self-declare those
statuses.

## Weave evaluation

`build_weave_evaluation` creates a versioned Weave Dataset and Evaluation with
autopatching disabled by the caller. Run it only against a configured W&B project
and public/synthetic rows. LLM-as-judge outputs are advisory until SME review and
must not replace deterministic citation, scope, loop, truncation, or retraction
checks.

The release thresholds are defined in `evaluation/gates.py`. A release requires
zero fabricated citations, citation-registry mismatches, unsupported claims,
positive retracted-source uses, scope violations, tool loops, and truncations,
retrieved-before-cited violations, plus all configured quality thresholds.
Synthetic fixture success alone cannot satisfy the release gate.

## Trace analysis and Signals

Use `analyze-flagged-traces --project entity/project` to apply a server-side filter
against the Agents span API and export only trace IDs, timestamps, ratios, counts,
and flags. Do not send raw trace
content in bulk to an LLM.

Signals are post-hoc monitoring:

- User Frustration and User Satisfaction: 100% in a controlled pilot;
- Low Quality Response: 20%;
- medical overclaim / unsupported citation: 10–20%.

Sampling and thresholds must be confirmed in the real project compatibility spike.
Signals never replace application safety checks.
