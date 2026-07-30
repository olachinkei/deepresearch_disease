from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from deepresearch_agent.infrastructure.feedback import (
    FeedbackRecord,
    FeedbackSynchronizer,
    WeaveFeedbackBackend,
)
from deepresearch_agent.observability.flag_analysis import fetch_flagged_rows
from deepresearch_agent.observability.otel import (
    PrivacyConfigurationError,
    pseudonymize_user,
    set_safe_span_attributes,
    trace_content_attributes,
)


class FakeFeedbackBackend:
    def __init__(self, *, trace_id: str | None) -> None:
        self.trace_id = trace_id
        self.added: list[tuple[str, FeedbackRecord, bool]] = []
        self.existing: set[str] = set()

    def has_feedback(self, feedback_id: str) -> bool:
        return feedback_id in self.existing

    def find_turn_trace_id(self, turn_id: str) -> str | None:
        return self.trace_id

    def add_feedback(
        self, trace_id: str, feedback: FeedbackRecord, *, include_comment: bool
    ) -> str:
        self.added.append((trace_id, feedback, include_comment))
        self.existing.add(feedback.feedback_id)
        return feedback.feedback_id


def test_feedback_stays_pending_then_syncs_idempotently() -> None:
    feedback = FeedbackRecord(
        feedback_id="feedback-1",
        turn_id="turn-1",
        rating="down",
        reason="citation_error",
        comment="Synthetic comment",
    )
    pending_backend = FakeFeedbackBackend(trace_id=None)
    assert FeedbackSynchronizer(pending_backend).sync(feedback).status == "pending"

    backend = FakeFeedbackBackend(trace_id="trace-1")
    synchronizer = FeedbackSynchronizer(backend, include_comment=False)
    first = synchronizer.sync(feedback)
    second = synchronizer.sync(feedback)
    assert first.status == "synced"
    assert first.feedback_id == "feedback-1"
    assert first.trace_id == "trace-1"
    assert second.status == "synced"
    assert second.feedback_id == "feedback-1"
    assert len(backend.added) == 1
    assert backend.added[0][2] is False


def test_weave_feedback_backend_uses_agent_span_and_turn_feedback_api() -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.span_request = None
            self.feedback_request = None

        def agent_spans_query(self, request):
            self.span_request = request
            return SimpleNamespace(
                spans=[
                    SimpleNamespace(
                        operation_name="invoke_agent",
                        trace_id="trace-1",
                    )
                ]
            )

        def feedback_create(self, request):
            self.feedback_request = request
            return SimpleNamespace(id=request.id)

    server = FakeServer()
    client = SimpleNamespace(
        entity="entity",
        project="project",
        server=server,
    )
    backend = WeaveFeedbackBackend("entity/project")
    backend._client = client
    trace_id = backend.find_turn_trace_id("turn-1")
    feedback = FeedbackRecord(
        feedback_id="018f1f2a-9c2b-7d3e-b5a1-8c9d2e4f6a7b",
        turn_id="turn-1",
        rating="down",
        reason="citation_error",
        comment="Must remain local.",
    )

    feedback_id = backend.add_feedback(
        trace_id or "",
        feedback,
        include_comment=False,
    )

    assert trace_id == "trace-1"
    assert server.span_request.project_id == "entity/project"
    assert server.span_request.limit == 10
    assert feedback_id == feedback.feedback_id
    assert server.feedback_request.feedback_type == "wandb.agent_user_feedback"
    assert server.feedback_request.span_trace_id == ""
    assert server.feedback_request.payload == {
        "feedback_id": feedback.feedback_id,
        "rating": "down",
        "reaction": "👎",
        "reason": "citation_error",
    }
    assert server.feedback_request.weave_ref.endswith("/agent_turn/trace-1")


def test_flag_analysis_queries_agent_spans_without_content() -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.request = None

        def agent_spans_query(self, request):
            self.request = request
            return SimpleNamespace(
                spans=[
                    SimpleNamespace(
                        trace_id="trace-flagged",
                        started_at="2026-07-30T00:00:00Z",
                        custom_attrs_float={"app.context_ratio": 0.81},
                        custom_attrs_int={
                            "app.tool_count": 6,
                            "app.duplicate_query_count": 2,
                        },
                        custom_attrs_string={"app.flags_csv": "tool_loop"},
                    )
                ]
            )

    server = FakeServer()
    client = SimpleNamespace(
        entity="entity",
        project="project",
        server=server,
    )

    rows = fetch_flagged_rows(client, limit=25)

    assert server.request.project_id == "entity/project"
    assert server.request.include_details is False
    assert server.request.include_costs is False
    assert server.request.limit == 25
    assert rows == [
        {
            "trace_id": "trace-flagged",
            "started_at": "2026-07-30T00:00:00Z",
            "context_ratio": 0.81,
            "tool_count": 6,
            "duplicate_query_count": 2,
            "flags": "tool_loop",
        }
    ]


def test_exported_span_contains_only_allowlisted_non_content_metadata() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("privacy-test")
    secret = "not-exported-secret"
    with tracer.start_as_current_span("invoke_agent"):
        set_safe_span_attributes(
            {
                "app.user_hash": pseudonymize_user("Display Name", secret),
                "app.turn_id": "turn-1",
                "app.source_count": 2,
            }
        )

    payload = json.dumps(
        dict(exporter.get_finished_spans()[0].attributes or {}),
        ensure_ascii=False,
    )
    assert "Display Name" not in payload
    assert secret not in payload
    assert "internal excerpt" not in payload
    with pytest.raises(PrivacyConfigurationError):
        set_safe_span_attributes({"app.display_name": "Display Name"})


def test_trace_content_is_public_only_and_feature_flagged() -> None:
    assert (
        trace_content_attributes(
            enabled=False,
            data_classification="public",
            question="q",
            answer="a",
        )
        == {}
    )
    assert (
        trace_content_attributes(
            enabled=True,
            data_classification="internal",
            question="internal question",
            answer="internal answer",
        )
        == {}
    )
    assert trace_content_attributes(
        enabled=True,
        data_classification="synthetic",
        question="synthetic q",
        answer="synthetic a",
    ) == {"input.value": "synthetic q", "output.value": "synthetic a"}


def test_otlp_http_exporter_sends_protobuf_without_sensitive_content() -> None:
    received: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["content-length"])
            received.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        exporter = OTLPSpanExporter(
            endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces"
        )
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("otlp-integration-test")
        with tracer.start_as_current_span("invoke_agent synthetic") as span:
            span.set_attribute("app.turn_id", "turn-synthetic")
            span.set_attribute("app.user_hash", pseudonymize_user("Display Name", "secret"))
        provider.shutdown()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(received) == 1
    request = ExportTraceServiceRequest()
    request.ParseFromString(received[0])
    serialized = str(request)
    assert "turn-synthetic" in serialized
    assert "Display Name" not in serialized
    assert "secret" not in serialized
    assert "tool raw response" not in serialized
