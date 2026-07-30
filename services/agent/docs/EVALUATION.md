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

The test suite separately covers schema, disease scope, tool policy, Recall@10,
nDCG@10, citation resolution, Markdown/claim/source-registry integrity, coverage,
claim-evidence support/lexical entailment, context/truncation, evidence-stage
calibration, contradiction handling, multi-turn retention, frustration metrics,
and release-gate aggregation.

## Weave evaluation

`build_weave_evaluation` creates a versioned Weave Dataset and Evaluation with
autopatching disabled by the caller. Run it only against a configured W&B project
and public/synthetic rows. LLM-as-judge outputs are advisory until SME review and
must not replace deterministic citation, scope, loop, truncation, or retraction
checks.

The release thresholds are defined in `evaluation/scorers.py`. A release requires
zero fabricated citations, citation-registry mismatches, unsupported claims,
positive retracted-source uses, scope violations, tool loops, and truncations,
plus all configured quality thresholds. Synthetic fixture success alone cannot
satisfy the release gate.

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
