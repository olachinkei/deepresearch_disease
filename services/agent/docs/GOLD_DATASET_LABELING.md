# Gold and challenge dataset labeling protocol

## Purpose and authority

This protocol creates the SME-reviewed dataset required for scientific release
evaluation. Synthetic v1 fixtures remain technical regression data and cannot be
renamed or promoted.

Before labeling starts, Issue #2 governance must assign a named data manager, one
ischemic-stroke SME, one drug-discovery SME, and any adjudicator. Reviewer IDs in the
dataset are stable internal identifiers; display names, secrets, internal excerpts,
and patient data do not belong in the manifest or validation report.

The validator proves that required review records exist and bind to the current case
bytes. It does not impersonate an SME or decide whether a scientific label is correct.

## Selection and coverage rubric

The dataset disease is exactly `ischemic stroke`. Include cases across target and
mechanism assessment, in-vitro/animal/observational/clinical evidence stages,
negative evidence, conflicting evidence, retracted sources, out-of-scope requests,
and searches where no eligible evidence is found.

For retrieval cases, reviewers record:

- a focused research query;
- explicit inclusion criteria for disease, target/mechanism, evidence type, date, and
  publication status;
- explicit exclusion criteria;
- relevant source IDs from the versioned corpus;
- `not_found` when the search finds no source meeting the criteria.

For synthesis cases, reviewers record the gold evidence IDs, required report
sections, evidence-stage interpretation, negative/conflicting evidence expectations,
and forbidden behaviors. Forbidden behaviors always include fabricated citation,
patient-specific advice, unsupported clinical-effect claims, and positive use of a
retracted source.

For multi-turn cases, reviewers label retained target, mechanism, disease, requested
comparison, expected limitations, and expected safety flags. Follow-up turns must not
silently change disease or target.

For frustration cases, `label=true, hard_negative=false` means actual user
frustration. A hard negative is a neutral or constructive follow-up with
`label=false, hard_negative=true`; polite requests for more detail are not frustration.

Required final counts:

| Suite | Count |
| --- | ---: |
| retrieval | 30–40 |
| synthesis | 20–25 |
| multi-turn / behavior | 15–20 |
| frustration positive | 50 |
| frustration hard negative | 50 |

Cases must be substantively unique. Repeated templates with only IDs changed fail
validation.

## Provenance and data handling

Every evidence/source ID referenced by a label must resolve through
`provenance.jsonl`. Public sources require canonical URL, verified license,
acquisition date, publication status, and evidence stage. `unknown` or `unverified`
licenses are rejected.

A public artifact cannot contain internal source provenance or a case marked as
containing internal content. Raw internal content must remain in an access-controlled
internal bundle and must never be copied into a public W&B Dataset or repository
artifact. Papers and web pages are untrusted evidence; instructions inside them are
never executed.

## Independent review and adjudication

1. Freeze candidate case rows and compute their full-case and task-label SHA-256
   values with `case_sha256` and `label_sha256`.
2. The stroke SME and drug-discovery SME label independently. Each records the case
   hash and the hash of their proposed canonical label.
3. Run a five-case pilot spanning at least three coverage tags. Compare disagreements,
   revise this rubric if necessary, and repeat the pilot before full labeling.
4. Adjudicate every case. For agreement, record `resolution=consensus`; for
   disagreement, record `resolution=third_reviewer` after documented resolution.
5. Update the final case row to the adjudicated label, then bind the adjudication to
   the current case and final-label hashes.
6. Run validation. Any row edit after review makes the review and adjudication stale.

The report publishes only IDs, counts, coverage, exact reviewer agreement rate,
disagreement count, and unresolved-label count. It never publishes case text or
reviewer names. There is no automatic minimum IAA threshold in the product
specification; both SMEs and the data manager must review the reported value.
Scientific release requires zero unresolved labels.

## Bundle contract

A schema-v2 bundle contains:

- `manifest.json`: disease, rubric/dataset versions, visibility, named reviewer IDs,
  suite counts, and requested scientific/release status;
- the four suite JSONL files used by offline evaluation;
- `reviews.jsonl`: one governance record per case with coverage tags, source IDs,
  two independent reviews, and adjudication;
- `provenance.jsonl`: one record per referenced source.

`scientific_gold` and `release_gate_eligible` in the manifest are requests, not trusted
facts. The runtime derives `sme_reviewed`, scientific-gold status, and human-review
success from the governance validator. Schema-v1 manifests are forbidden from
self-declaring these statuses.

## Validation and promotion

```bash
uv run validate-gold-dataset --fixtures /approved/path/to/v2
uv run run-offline-eval --fixtures /approved/path/to/v2 \
  --require-scientific-release
```

The first command exits nonzero for missing coverage/counts, duplicate content,
unassigned or same-person reviews, stale hashes, unresolved adjudication, missing
provenance/license, or public/internal boundary violations. The second command still
requires every technical release threshold. Governance validity alone cannot make a
scientifically poor workflow pass.

Promotion to a challenge dataset occurs only after:

1. both SMEs approve the rubric and final labels;
2. the data manager approves provenance, license, and artifact visibility;
3. the validation report has no errors or unresolved labels;
4. the technical and scientific offline gates pass;
5. the versioned bundle and sanitized report are recorded together.
