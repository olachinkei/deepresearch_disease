from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from deepresearch_agent import __version__
from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.evaluation.runner import (
    FIXTURE_DIRECTORY,
    SCORER_VERSION,
    _load_fixture_bundle,
    _seed_synthetic_corpus,
)
from deepresearch_agent.evaluation.weave_scaffolding import build_weave_evaluation
from deepresearch_agent.evaluation.workflow_adapter import (
    EvaluationTurn,
    WorkflowEvaluationAdapter,
)
from deepresearch_agent.infrastructure.corpus import CorpusRepository
from deepresearch_agent.infrastructure.embeddings import HashEmbeddingProvider
from deepresearch_agent.infrastructure.sessions import AdkSessionStateStore
from deepresearch_agent.observability.otel import configure_otel, set_safe_span_attributes
from deepresearch_agent.settings import Settings

PILOT_NAME = "ischemic-stroke-synthetic-pilot"
PILOT_DATASET_VERSION = "v1"
PILOT_MODEL_VERSION = "deterministic-synthesizer-v1"
PILOT_CASE_COUNT = 4
SIGNAL_PILOT_TURN_COUNT = 20


class SignalSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: str
    sample_rate: float = Field(ge=0.0, le=1.0)
    preset: bool
    purpose: str


SIGNAL_SPECS = (
    SignalSpec(
        name="User Frustration",
        kind="tag",
        sample_rate=1.0,
        preset=True,
        purpose="pilot correlation with the synthetic frustration labels",
    ),
    SignalSpec(
        name="User Satisfaction",
        kind="rating",
        sample_rate=1.0,
        preset=True,
        purpose="pilot correlation with explicit synthetic satisfaction cases",
    ),
    SignalSpec(
        name="Low Quality Response",
        kind="tag",
        sample_rate=0.2,
        preset=True,
        purpose="cost-controlled response-quality sampling",
    ),
    SignalSpec(
        name="Medical Overclaim",
        kind="tag",
        sample_rate=0.15,
        preset=False,
        purpose="detect medical or scientific claims stronger than the evidence",
    ),
    SignalSpec(
        name="Unsupported Citation",
        kind="tag",
        sample_rate=0.15,
        preset=False,
        purpose="detect claims whose cited source does not provide support",
    ),
)


class PilotEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    created_at: datetime
    project_url: str
    evaluation_name: str
    dataset_name: str
    evaluation_ref: str | None
    dataset_ref: str | None
    case_count: int
    versions: dict[str, str]
    signal_configuration: list[SignalSpec]
    aggregate_metric_names: list[str]
    scientific_release_eligible: bool = False
    data_classification: str = "synthetic"
    limitations: list[str]


class SignalCorrelation(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_positive: int
    matched: int
    true_positive: int
    false_positive: int
    observed_positive_precision: float | None
    tagged_positive_capture: float


class SignalAnalysisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    window_start: datetime
    window_end: datetime
    project_url: str
    counts: dict[str, int]
    correlations: dict[str, SignalCorrelation]
    content_retrieved: bool = False
    scientific_release_eligible: bool = False


def build_pilot_rows(fixtures: Path = FIXTURE_DIRECTORY) -> list[dict[str, Any]]:
    """Return a small, public/synthetic-only dataset suitable for live Weave."""

    manifest, datasets = _load_fixture_bundle(fixtures)
    if manifest.get("scientific_gold") or manifest.get("sme_reviewed"):
        raise ValueError("the technical pilot requires unreviewed synthetic fixtures")
    rows = [
        {
            "case_id": str(row["id"]),
            "query": str(row["query"]),
            "disease": "ischemic stroke",
            "synthetic": True,
        }
        for row in datasets["retrieval"][:PILOT_CASE_COUNT]
    ]
    validate_pilot_rows(rows)
    return rows


def validate_pilot_rows(rows: list[dict[str, Any]]) -> None:
    allowed = {"case_id", "query", "disease", "synthetic"}
    if not rows:
        raise ValueError("pilot dataset is empty")
    for row in rows:
        if set(row) != allowed:
            raise ValueError("pilot rows contain fields outside the public schema")
        if row["synthetic"] is not True:
            raise ValueError("pilot rows must be explicitly synthetic")
        if row["disease"] != "ischemic stroke":
            raise ValueError("pilot disease must be ischemic stroke")


def _versions(settings: Settings, embedding_model: str) -> dict[str, str]:
    return {
        "agent": __version__,
        "prompt": settings.prompt_version,
        "prompt_sha256": settings.prompt_sha256,
        "model": PILOT_MODEL_VERSION,
        "corpus": settings.corpus_version,
        "embedding": embedding_model,
        "dataset": PILOT_DATASET_VERSION,
        "scorer": SCORER_VERSION,
    }


@asynccontextmanager
async def _pilot_model(
    rows: list[dict[str, Any]],
) -> AsyncIterator[tuple[Callable[..., Any], Settings, str]]:
    _, fixture_datasets = _load_fixture_bundle(FIXTURE_DIRECTORY)
    temporary = TemporaryDirectory(prefix="deepresearch-weave-pilot-")
    root = Path(temporary.name)
    settings = Settings(
        database_path=root / "corpus.sqlite",
        session_database_path=root / "sessions.sqlite",
        corpus_version=f"synthetic-weave-{PILOT_DATASET_VERSION}",
        runtime_mode="mock",
    )
    corpus = CorpusRepository(settings.database_path)
    corpus.initialize()
    embeddings = HashEmbeddingProvider()
    await _seed_synthetic_corpus(
        corpus=corpus,
        embeddings=embeddings,
        retrieval_rows=fixture_datasets["retrieval"][: len(rows)],
        snapshot_id=settings.corpus_version,
    )
    sessions = AdkSessionStateStore(settings.session_database_path)
    workflow = ResearchWorkflow(
        settings=settings,
        corpus=corpus,
        embeddings=embeddings,
        sessions=sessions,
    )
    adapter = WorkflowEvaluationAdapter(workflow=workflow, sessions=sessions)

    import weave

    @weave.op(name="deepresearch_synthetic_pilot_model")
    async def predict(
        case_id: str,
        query: str,
        disease: str,
        synthetic: bool,
    ) -> dict[str, Any]:
        validate_pilot_rows(
            [
                {
                    "case_id": case_id,
                    "query": query,
                    "disease": disease,
                    "synthetic": synthetic,
                }
            ]
        )
        observation = await adapter.run_case(
            case_id=case_id,
            turns=[EvaluationTurn(text=query, research_question=query)],
        )
        output = observation.result.model_dump(mode="json")
        output["evidence"] = [item.model_dump(mode="json") for item in observation.packed_evidence]
        return output

    try:
        yield predict, settings, embeddings.model_name
    finally:
        await workflow.close()
        temporary.cleanup()


def _ref_uri(value: object) -> str | None:
    ref = getattr(value, "ref", None)
    if ref is None:
        return None
    uri = getattr(ref, "uri", None)
    return str(uri()) if callable(uri) else str(ref)


def export_synthetic_signal_turns(*, count: int = SIGNAL_PILOT_TURN_COUNT) -> int:
    """Export bounded synthetic turns for Signals; never used by runtime startup."""

    if count != SIGNAL_PILOT_TURN_COUNT:
        raise ValueError("the controlled signal pilot requires exactly 20 turns")
    if not configure_otel():
        raise RuntimeError("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is required")

    from opentelemetry import trace

    tracer = trace.get_tracer("deepresearch.weave-signals-pilot")
    conversation_id = f"synthetic-signals-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    for index in range(count):
        frustrated = index % 4 == 0
        unsafe = index % 5 == 0
        question = (
            "This is the third time: where is the supporting citation?"
            if frustrated
            else "Summarize the synthetic ischemic stroke evidence limitations."
        )
        answer = (
            "This proves the treatment works for every patient [E404]."
            if unsafe
            else (
                "The synthetic evidence is not sufficient for diagnosis or treatment; "
                "no patient-specific conclusion can be made [E1]."
            )
        )
        with tracer.start_as_current_span("invoke_agent deepresearch_agent"):
            set_safe_span_attributes(
                {
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": "deepresearch_agent-signals-pilot",
                    "gen_ai.conversation.id": conversation_id,
                    "app.turn_id": f"signals-pilot-{index:03d}",
                    "app.conversation_id": conversation_id,
                    "app.agent_version": __version__,
                    "app.model_id": PILOT_MODEL_VERSION,
                    "app.prompt_version": "signals-pilot-v1",
                    "app.prompt_sha256": "0" * 64,
                    "app.corpus_version": "synthetic-signals-v1",
                    "app.input_data_classification": "synthetic",
                    "app.output_data_classification": "synthetic",
                    "input.value": question,
                    "output.value": answer,
                }
            )
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if callable(force_flush) and not force_flush(timeout_millis=30_000):
        raise RuntimeError("timed out exporting synthetic signal turns")
    return count


def _signal_filters() -> dict[str, Any]:
    from weave.trace_server.agents.types import AgentSignalFilter, RatingCondition

    return {
        "user_frustration": AgentSignalFilter(tags=["user-frustration"]),
        "user_satisfaction_low": AgentSignalFilter(
            ratings=[
                RatingCondition(
                    scorer_key="user-satisfaction",
                    op="lt",
                    value=0.5,
                )
            ]
        ),
        "low_quality": AgentSignalFilter(tags=["low-quality-response"]),
        "medical_overclaim": AgentSignalFilter(tags=["medical-overclaim"]),
        "unsupported_citation": AgentSignalFilter(tags=["unsupported-citation"]),
    }


def _trace_count_metric() -> Any:
    from weave.trace_server.agents.types import AgentSpanStatsMetricSpec, AgentSpanValueRef

    return AgentSpanStatsMetricSpec(
        alias="turns",
        value_type="string",
        aggregations=["count_distinct"],
        value=AgentSpanValueRef(source="field", key="trace_id"),
    )


def _expected_signal_turn_ids() -> dict[str, frozenset[str]]:
    frustrated_ids = frozenset(
        f"signals-pilot-{index:03d}" for index in range(SIGNAL_PILOT_TURN_COUNT) if index % 4 == 0
    )
    unsafe_ids = frozenset(
        f"signals-pilot-{index:03d}" for index in range(SIGNAL_PILOT_TURN_COUNT) if index % 5 == 0
    )
    return {
        "user_frustration": frustrated_ids,
        "user_satisfaction_low": frustrated_ids,
        "low_quality": unsafe_ids,
        "medical_overclaim": unsafe_ids,
        "unsupported_citation": unsafe_ids,
    }


def fetch_signal_aggregates(
    client: Any,
    *,
    start: datetime,
    end: datetime | None = None,
) -> dict[str, int]:
    """Count signal matches server-side without retrieving trace content."""

    from weave.trace_server.agents.types import AgentSpanStatsReq

    filters = _signal_filters()
    metric = _trace_count_metric()
    counts: dict[str, int] = {}
    for name, signal_filter in filters.items():
        response = client.server.agent_spans_stats(
            AgentSpanStatsReq(
                project_id=f"{client.entity}/{client.project}",
                start=start,
                end=end,
                metrics=[metric],
                signal_filters=signal_filter,
            )
        )
        values = [
            value
            for row in response.rows
            for key, value in row.items()
            if key.startswith("turns") and isinstance(value, (int, float))
        ]
        counts[name] = int(sum(values))
    return counts


def fetch_signal_correlations(
    client: Any,
    *,
    start: datetime,
    end: datetime | None = None,
) -> dict[str, SignalCorrelation]:
    """Correlate bounded synthetic turn IDs without reading messages or outputs."""

    from weave.trace_server.agents.types import AgentGroupByRef, AgentSpanStatsReq

    filters = _signal_filters()
    expected = _expected_signal_turn_ids()
    metric = _trace_count_metric()
    group = AgentGroupByRef(
        source="custom_attrs_string",
        key="app.turn_id",
        alias="turn_id",
    )
    correlations: dict[str, SignalCorrelation] = {}
    for name, signal_filter in filters.items():
        response = client.server.agent_spans_stats(
            AgentSpanStatsReq(
                project_id=f"{client.entity}/{client.project}",
                start=start,
                end=end,
                group_by=[group],
                metrics=[metric],
                signal_filters=signal_filter,
            )
        )
        matched = {str(row["turn_id"]) for row in response.rows if row.get("turn_id") is not None}
        expected_ids = expected[name]
        true_positives = matched & expected_ids
        precision = len(true_positives) / len(matched) if matched else None
        correlations[name] = SignalCorrelation(
            expected_positive=len(expected_ids),
            matched=len(matched),
            true_positive=len(true_positives),
            false_positive=len(matched - expected_ids),
            observed_positive_precision=precision,
            tagged_positive_capture=len(true_positives) / len(expected_ids),
        )
    return correlations


@contextmanager
def _weave_session(project_path: str) -> Iterator[Any]:
    import weave

    client = weave.init(
        project_path,
        settings={
            "implicitly_patch_integrations": False,
            "capture_code": False,
            "capture_system_info": False,
            "print_call_link": False,
        },
    )
    try:
        yield client
    finally:
        finish = getattr(weave, "finish", None)
        if callable(finish):
            finish()


async def run_live_pilot(project_path: str, output_path: Path) -> PilotEvidence:
    rows = build_pilot_rows()
    with _weave_session(project_path):
        async with _pilot_model(rows) as (model, settings, embedding_model):
            versions = _versions(settings, embedding_model)
            evaluation = build_weave_evaluation(
                name=PILOT_NAME,
                rows=rows,
                metadata={"versions": versions},
            )
            await evaluation.evaluate(model)  # type: ignore[attr-defined]
            entity, project = project_path.split("/", maxsplit=1)
            evidence = PilotEvidence(
                status="technical_pilot_completed",
                created_at=datetime.now(UTC),
                project_url=f"https://wandb.ai/{entity}/{project}",
                evaluation_name=PILOT_NAME,
                dataset_name=f"{PILOT_NAME}-dataset",
                evaluation_ref=_ref_uri(evaluation),
                dataset_ref=_ref_uri(evaluation.dataset),  # type: ignore[attr-defined]
                case_count=len(rows),
                versions=versions,
                signal_configuration=list(SIGNAL_SPECS),
                aggregate_metric_names=[
                    "user_frustration",
                    "user_satisfaction_low",
                    "low_quality",
                    "medical_overclaim",
                    "unsupported_citation",
                ],
                limitations=[
                    "synthetic and not SME reviewed",
                    "not evidence for scientific or clinical release",
                    "Signals are post-hoc monitors, not safety guardrails",
                ],
            )
            await asyncio.to_thread(
                _write_json,
                output_path,
                evidence.model_dump(mode="json"),
            )
            return evidence


def run_signal_analysis(
    project_path: str,
    *,
    start: datetime,
    end: datetime,
    output_path: Path,
) -> SignalAnalysisEvidence:
    if end <= start:
        raise ValueError("signal analysis end must be after start")
    with _weave_session(project_path) as client:
        entity, project = project_path.split("/", maxsplit=1)
        evidence = SignalAnalysisEvidence(
            created_at=datetime.now(UTC),
            window_start=start,
            window_end=end,
            project_url=f"https://wandb.ai/{entity}/{project}",
            counts=fetch_signal_aggregates(client, start=start, end=end),
            correlations=fetch_signal_correlations(client, start=start, end=end),
        )
        _write_json(output_path, evidence.model_dump(mode="json"))
        return evidence


def _write_json(output_path: Path, value: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
    )


async def _async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the public/synthetic-only Weave Evaluation pilot."
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--project",
        default=f"{os.getenv('WANDB_ENTITY', '')}/{os.getenv('WANDB_PROJECT', '')}",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("--live is required; the pilot is deny-by-default")
    if "/" not in args.project or args.project.startswith("/"):
        raise SystemExit("--project entity/project is required")
    if args.output is None:
        raise SystemExit("--output is required for the sanitized pilot evidence")
    evidence = await run_live_pilot(args.project, args.output)
    print(
        json.dumps(
            {
                "status": evidence.status,
                "case_count": evidence.case_count,
                "project_url": evidence.project_url,
                "evidence_path": str(args.output),
            }
        )
    )


def main() -> None:
    asyncio.run(_async_main())


def signal_export_main() -> None:
    parser = argparse.ArgumentParser(
        description="Export bounded synthetic Agent turns for the Signals pilot."
    )
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("--live is required; signal export is deny-by-default")
    count = export_synthetic_signal_turns()
    print(json.dumps({"status": "synthetic_signal_turns_exported", "count": count}))


def signal_analysis_main() -> None:
    parser = argparse.ArgumentParser(
        description="Server-aggregate the bounded Signals pilot without trace content."
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--project",
        default=f"{os.getenv('WANDB_ENTITY', '')}/{os.getenv('WANDB_PROJECT', '')}",
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("--live is required; signal analysis is deny-by-default")
    if "/" not in args.project or args.project.startswith("/"):
        raise SystemExit("--project entity/project is required")
    try:
        start = _parse_iso_datetime(args.start)
        end = _parse_iso_datetime(args.end) if args.end else datetime.now(UTC)
    except ValueError as exc:
        raise SystemExit("--start/--end must be ISO-8601 datetimes") from exc
    evidence = run_signal_analysis(
        args.project,
        start=start,
        end=end,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "status": "signal_analysis_completed",
                "counts": evidence.counts,
                "content_retrieved": evidence.content_retrieved,
                "evidence_path": str(args.output),
            }
        )
    )


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed.astimezone(UTC)
