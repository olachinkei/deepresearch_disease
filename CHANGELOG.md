# Changelog

## v0.1.0 - 2026-08-01

First completed public/synthetic demo release of the ischemic-stroke drug-discovery
Deep Research Agent.

### Included

- Local React Router BFF/UI and separate Google ADK agent service.
- Ischemic-stroke-only research workflow with bounded retrieval, evidence packing,
  structured synthesis, citation verification, cancellation, and recovery.
- License-gated Europe PMC OA ingestion, immutable corpus/embedding snapshots,
  FTS5/BM25 plus RRF retrieval, and synthetic retrieval regression evaluation.
- Raw OTel export to W&B Agents, idempotent feedback synchronization, offline Weave
  Evaluation, and a bounded W&B Inference Signals pilot.
- Reloadable conversation/source/feedback state and accessibility-tested streaming UI.
- Fail-closed controls for internal data and all externally transmitted content.

### Validation

- Technical release gate: passed.
- Scientific release: `ineligible` because the demo dataset is synthetic and not SME
  reviewed.
- Required unit, integration, contract, privacy, E2E, type, lint, duplication, and
  tracked-file safety checks: passed.

### Scope

This release is a research-support demo. It must not be used for diagnosis, treatment,
patient-specific advice, or scientific/clinical release decisions.
