# Public/synthetic demo release runbook

## Release

- Version: `v0.1.0`
- Agent package: `0.1.0`
- Deployment profile: `public_synthetic_demo`
- Completed plan: [MVP execution plan](../exec-plans/completed/mvp.md)

This runbook reproduces the technical demo only. It does not authorize confidential
data processing or claim scientific or clinical validity.

## Preconditions

- Node.js 24 or later, pnpm 10.32.1, Python 3.13, uv, and SQLite with FTS5.
- A clean checkout of the release tag.
- No real secret committed to `.env` or any tracked file.

## Install and configure

```bash
cp .env.example .env
make install
```

Keep the following deny-by-default values unchanged for the demo:

```dotenv
AGENT_RUNTIME_MODE=mock
AGENT_DEPLOYMENT_PROFILE=public_synthetic_demo
AGENT_ALLOW_TARGET_TO_EXA=false
AGENT_ALLOW_PUBLIC_CONTENT_TO_GEMINI=false
AGENT_ALLOW_PUBLIC_CONTENT_TO_GEMINI_EMBEDDINGS=false
AGENT_ALLOW_INTERNAL_CONTENT_TO_GEMINI=false
AGENT_TRACE_INPUT_CONTENT_ENABLED=false
AGENT_TRACE_OUTPUT_CONTENT_ENABLED=false
AGENT_INTERNAL_INGESTION_ENABLED=false
```

## Verify

```bash
make quality
make test
pnpm quality
pnpm test:e2e
cd services/agent && uv run run-offline-eval
```

The offline evaluation must report:

```text
technical_smoke_status=passed
scientific_release_status=ineligible
```

Also run the tracked-file audit before publishing a commit or tag:

```bash
git add <release files>
./scripts/audit-tracked-files.sh
```

## Run the demo

Start the services in separate terminals:

```bash
make agent
```

```bash
make web
```

Open `http://localhost:3000` and verify the bounded synthetic flow: initial research,
streaming, citations, follow-up, reload, feedback, cancellation, and sanitized errors.

## Release evidence

- [Weave Evaluation evidence](../../services/agent/docs/weave-pilot-evidence-2026-08-01.json)
- [W&B Agents Signals evidence](../../services/agent/docs/weave-signals-evidence-2026-08-01.json)
- [ADK / OTel compatibility record](../../services/agent/docs/ADK_OTEL_SPIKE.md)

Do not rerun paid or external live pilots as part of ordinary release verification.
Live canaries remain explicit opt-in jobs using only public or synthetic data.

## Stop conditions

Do not publish or demonstrate the build if any required check fails, scientific status
changes from `ineligible` without SME evidence, a sensitive feature becomes enabled,
or the tracked-file audit reports a secret or non-public artifact.
