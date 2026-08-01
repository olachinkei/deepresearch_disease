from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

REQUIRED_PRIVACY_ENV: dict[str, str] = {
    "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
    "ADK_TELEMETRY_IGNORE_RUN_CONFIG": "true",
}

_SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "app.user_hash",
        "app.turn_id",
        "app.conversation_id",
        "app.agent_version",
        "app.model_id",
        "app.prompt_version",
        "app.prompt_sha256",
        "app.corpus_version",
        "app.tool_count",
        "app.duplicate_query_count",
        "app.context_ratio",
        "app.finish_reason",
        "app.citation_count",
        "app.source_count",
        "app.flags_csv",
        "app.input_data_classification",
        "app.output_data_classification",
        "gen_ai.operation.name",
        "gen_ai.agent.name",
        "gen_ai.conversation.id",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "input.value",
        "output.value",
    }
)


class PrivacyConfigurationError(RuntimeError):
    """Unsafe ADK telemetry configuration was supplied."""


class TraceDataClassification(StrEnum):
    """Server-owned classification used to gate trace content."""

    PUBLIC = "public"
    SYNTHETIC = "synthetic"
    INTERNAL = "internal"
    RESEARCH_SENSITIVE = "research-sensitive"


def enforce_privacy_environment() -> None:
    """Fail closed, then set the three ADK 2.5 privacy controls before ADK import."""

    for key, expected in REQUIRED_PRIVACY_ENV.items():
        configured = os.getenv(key)
        if configured is not None and configured != expected:
            raise PrivacyConfigurationError(f"{key} must be exactly {expected!r}")
        os.environ[key] = expected


def pseudonymize_user(user_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()


def configure_otel() -> bool:
    """Configure the standard OTLP HTTP exporter only when its endpoint is present."""

    enforce_privacy_environment()
    if not os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return False
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return True
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return True


@dataclass(frozen=True, slots=True)
class TraceMetadata:
    user_hash: str
    turn_id: str
    conversation_id: str
    agent_version: str
    model_id: str
    prompt_version: str
    prompt_sha256: str
    corpus_version: str

    def attributes(self) -> dict[str, str]:
        return {
            "app.user_hash": self.user_hash,
            "app.turn_id": self.turn_id,
            "app.conversation_id": self.conversation_id,
            "app.agent_version": self.agent_version,
            "app.model_id": self.model_id,
            "app.prompt_version": self.prompt_version,
            "app.prompt_sha256": self.prompt_sha256,
            "app.corpus_version": self.corpus_version,
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "deepresearch_agent",
            "gen_ai.conversation.id": self.conversation_id,
        }


def set_safe_span_attributes(values: Mapping[str, Any]) -> None:
    unexpected = set(values) - _SAFE_ATTRIBUTE_KEYS
    if unexpected:
        raise PrivacyConfigurationError(
            f"trace attribute keys are not allow-listed: {sorted(unexpected)}"
        )
    span = trace.get_current_span()
    for key, value in values.items():
        if value is not None:
            span.set_attribute(key, value)


def trace_input_fingerprint(
    *,
    question: str,
    target_molecule: str | None,
    mechanism: str | None,
    disease: str,
    research_question: str,
) -> str:
    """Fingerprint the complete normalized request without exporting its content."""

    canonical = json.dumps(
        {
            "disease": disease,
            "mechanism": mechanism,
            "question": question,
            "research_question": research_question,
            "target_molecule": target_molecule,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def classify_trace_input(
    *,
    fingerprint: str,
    public_fingerprints: frozenset[str],
    synthetic_fingerprints: frozenset[str],
) -> TraceDataClassification:
    """Classify only exact server-approved inputs; unknown inputs stay sensitive."""

    if fingerprint in synthetic_fingerprints:
        return TraceDataClassification.SYNTHETIC
    if fingerprint in public_fingerprints:
        return TraceDataClassification.PUBLIC
    return TraceDataClassification.RESEARCH_SENSITIVE


def classify_trace_output(
    *,
    input_classification: TraceDataClassification,
    has_internal_evidence: bool,
) -> TraceDataClassification:
    """Prevent an otherwise allow-listed request from exporting internal evidence."""

    if has_internal_evidence:
        return TraceDataClassification.INTERNAL
    return input_classification


def trace_content_attributes(
    *,
    input_enabled: bool,
    output_enabled: bool,
    input_classification: TraceDataClassification,
    output_classification: TraceDataClassification,
    question: str,
    answer: str,
) -> dict[str, str]:
    exportable = {
        TraceDataClassification.PUBLIC,
        TraceDataClassification.SYNTHETIC,
    }
    attributes: dict[str, str] = {}
    if input_enabled and input_classification in exportable:
        attributes["input.value"] = trace_input_value(question)
        attributes["gen_ai.input.messages"] = trace_input_messages(question)
    if output_enabled and output_classification in exportable:
        attributes["output.value"] = trace_output_value(answer)
        attributes["gen_ai.output.messages"] = trace_output_messages(answer)
    return attributes


def trace_input_value(question: str) -> str:
    """Encode the approved question as the user-message shape W&B Agents expects."""

    return json.dumps(
        [{"role": "user", "content": question}],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def trace_output_value(answer: str) -> str:
    """Encode the approved final answer without exporting intermediate messages."""

    return json.dumps(
        {"content": answer},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def trace_input_messages(question: str) -> str:
    """Encode the approved question with the OTel GenAI message convention."""

    return json.dumps(
        [
            {
                "role": "user",
                "parts": [{"type": "text", "content": question}],
            }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def trace_output_messages(answer: str) -> str:
    """Encode only the approved final answer as an OTel GenAI message."""

    return json.dumps(
        [
            {
                "role": "assistant",
                "parts": [{"type": "text", "content": answer}],
            }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
