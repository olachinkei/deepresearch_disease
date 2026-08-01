# ADK / Weave raw OTel compatibility spike

## Status

As of 2026-08-01, raw OTel ingestion, ADK/Gemini spans, conversation grouping,
token usage, privacy checks, Agent turn lookup, feedback, and flagged-trace
analysis pass against the configured W&B project. The Agents endpoint stores data
in the Agents span model: the legacy Calls API correctly reports zero Calls for
these exports and must not be used to assess Agents ingestion.

The versioned Weave Evaluation technical pilot also passes with four synthetic
workflow cases. The controlled Signals pilot passes with 20 synthetic Agent turns,
four W&B Inference tag signals, and server-side aggregate analysis. Signals remain
post-hoc monitors and are not a release safety guardrail.

Runtime tracing uses the standard OTLP HTTP exporter only. It does not initialize
the Weave SDK and must not fall back to `weave.init()` if export or mapping fails.
The Weave SDK remains limited to offline evaluation, feedback synchronization, and
filtered analysis.

## Local evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Root agent span | Pass in real project | `invoke_agent deepresearch_agent` with `gen_ai.operation.name=invoke_agent` |
| Retrieval and LLM child spans | Pass in real project | internal retrieval and Gemini 3.6 Flash spans share the turn trace |
| Conversation grouping | Pass in real project | two turn traces share the opaque `gen_ai.conversation.id` |
| Token usage | Pass in real project | Gemini input and output tokens are populated |
| Safe custom metadata | Pass in real project | `app.turn_id` and scalar budget/version fields are queryable through Agents custom attributes |
| Content capture disabled | Pass in live privacy scan | nine spans contained no canary question, display/user identifier, API key, or raw tool content |
| Optional input/output | Pass in unit and live Signals tests | emitted only when enabled and classification is `public` or `synthetic`; OTel GenAI message copies contain only the same question and final answer |
| Feedback retry/idempotency | Pass in real project | `app.turn_id` resolves an Agent turn trace; repeat sync keeps one `wandb.agent_user_feedback` row |
| Real W&B OTLP acceptance | Pass | direct public/synthetic protobuf export returned `SUCCESS` |
| Agent span lookup | Pass | Agents span API returned the exported spans; legacy Calls API is not applicable |
| Flag analysis | Pass in real project | server-side Agents filter returned one synthetic `tool_loop` row without content |
| Agents Signals | Pass in real project | 20 `deepresearch_agent-signals-pilot` turns reached the Agents model; W&B Inference read the GenAI messages and emitted tag results |

The final bounded pilot window was `2026-08-01T09:19:51.740340Z` through
`2026-08-01T09:20:00.523591Z`. The server-side Agents aggregate reported 20
`invoke_agent` spans, 20 invocations, and 20 conversations for
`deepresearch_agent-signals-pilot`. W&B Inference produced 5 User Frustration,
0 Low Quality Response, 1 Medical Overclaim, and 2 Unsupported Citation matches.
The sampled signal counts are a connectivity check, not a recall estimate. The
sanitized aggregate is stored in `weave-signals-evidence-2026-08-01.json` and records
`content_retrieved=false`.

The current Signals UI exposes tag signals but no rating creation flow, so the
verified set is User Frustration, Low Quality Response, Medical Overclaim, and
Unsupported Citation. User Satisfaction is not claimed as configured.

The Exa key passed a public publication-search smoke. The updated Google key
passed an actual ADK `/run_sse` canary using `gemini-3.6-flash`: the turn completed
with 11 sources and 5 citations. `gemini-2.5-flash` was replaced because Google
no longer makes that model available to new users.

## Real-project smoke procedure

Use public or synthetic input only.

```bash
export WANDB_API_KEY=...
export WANDB_ENTITY=...
export WANDB_PROJECT=...
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://trace.wandb.ai/agents/otel/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_HEADERS=wandb-api-key=$WANDB_API_KEY
export OTEL_RESOURCE_ATTRIBUTES=service.name=deepresearch-disease-agent,wandb.entity=$WANDB_ENTITY,wandb.project=$WANDB_PROJECT
export ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false
export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT
export ADK_TELEMETRY_IGNORE_RUN_CONFIG=true
```

Run one synthetic turn, then verify in the project:

1. one root Agent turn span and retrieval/LLM child spans are linked;
2. `gen_ai.conversation.id` groups two turns;
3. token usage is present for a synthetic Gemini canary;
4. only allow-listed `app.*` attributes appear;
5. no display name, secret, raw tool response, or internal excerpt appears;
6. `app.turn_id` resolves the Agent turn `trace_id` and feedback can be added once;
7. Signals can read the Agent turn without becoming an application guardrail.

Any missing mapping fails the spike. Record the actual project, run time, ADK
version, exporter version, and sanitized screenshots/results here before marking it
complete.
