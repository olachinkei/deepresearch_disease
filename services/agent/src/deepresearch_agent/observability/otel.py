from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
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
        "app.prompt_version",
        "app.corpus_version",
        "app.tool_count",
        "app.duplicate_query_count",
        "app.context_ratio",
        "app.finish_reason",
        "app.citation_count",
        "app.source_count",
        "app.flags_csv",
        "gen_ai.operation.name",
        "gen_ai.agent.name",
        "gen_ai.conversation.id",
        "input.value",
        "output.value",
    }
)


class PrivacyConfigurationError(RuntimeError):
    """Unsafe ADK telemetry configuration was supplied."""


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
    prompt_version: str
    corpus_version: str

    def attributes(self) -> dict[str, str]:
        return {
            "app.user_hash": self.user_hash,
            "app.turn_id": self.turn_id,
            "app.conversation_id": self.conversation_id,
            "app.agent_version": self.agent_version,
            "app.prompt_version": self.prompt_version,
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


def trace_content_attributes(
    *,
    enabled: bool,
    data_classification: str,
    question: str,
    answer: str,
) -> dict[str, str]:
    if not enabled or data_classification not in {"public", "synthetic"}:
        return {}
    return {"input.value": question, "output.value": answer}
