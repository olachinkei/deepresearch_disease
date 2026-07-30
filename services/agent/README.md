# Deep Research Disease Agent

Python 3.13 service for the local ischemic-stroke drug-discovery MVP. It exposes an
ADK-compatible `POST /run_sse` boundary while keeping retrieval, evidence packing,
privacy policy, and deterministic checks outside the model.

## Local run

```bash
uv sync --extra dev
uv run deepresearch-agent
curl http://127.0.0.1:8001/healthz
```

`AGENT_RUNTIME_MODE=mock` is the default. It requires no external API key and emits
the same SSE contract as a live run. Live Exa or Gemini calls require both a key and
the corresponding explicit data-policy flag; no provider is silently enabled.

## Internal API contract

`POST /run_sse` accepts an ADK `RunAgentRequest`-compatible body:

```json
{
  "app_name": "deepresearch_agent",
  "user_id": "web-user-uuid",
  "session_id": "conversation-uuid",
  "new_message": {"role": "user", "parts": [{"text": "Assess the target."}]},
  "streaming": true,
  "custom_metadata": {
    "turn_id": "turn-uuid",
    "conversation_id": "conversation-uuid",
    "target_molecule": "MMP9",
    "mechanism": "inhibition",
    "disease": "ischemic stroke",
    "research_question": "What evidence supports this target?"
  }
}
```

Each SSE `data:` value is an ADK-shaped event. The browser-facing BFF must allow-list
`customMetadata.kind` and `content.parts[].text`; raw tool responses are never emitted.
Cancellation is `POST /runs/{turn_id}/cancel`.

## Public seed corpus

```bash
uv run collect-public-seed \
  --output data/public-seed-v1.json \
  --database data/corpus.sqlite \
  --limit 200
```

The collector queries Europe PMC, enriches DOI records through Crossref, and calls
Unpaywall only when `UNPAYWALL_EMAIL` is set. It stores metadata and OA URL/license
information only. The default query is split into four date buckets and interleaved so
foundational and recent publications are both represented. It does not download or store
article text.

The checked-in `public-seed-v1` snapshot contains 220 unique records spanning 1989–2026:
54 through 2010, 53 from 2011–2017, 58 from 2018–2022, and 55 from 2023 onward.
Its exact runtime snapshot ID is `public-seed-20260730-c8457953`.

## Data policy

- The disease scope is fixed to `ischemic stroke`.
- Internal ingestion is disabled until `AGENT_INTERNAL_INGESTION_ENABLED=true`.
- Internal excerpts are never sent to Exa.
- Gemini receives public or internal evidence only when the matching approval flag is true.
- ADK message/tool content capture is forced off before ADK is imported.
- Trace identity is an HMAC pseudonym; display names are not accepted as trace attributes.
- The UI must display: `創薬仮説探索用であり、臨床判断や患者個別助言には使用できません。`

See [`docs/ADK_OTEL_SPIKE.md`](docs/ADK_OTEL_SPIKE.md) for the compatibility-spike
checklist and [`docs/EVALUATION.md`](docs/EVALUATION.md) for release gates and the
synthetic, non-gold fixture policy.
