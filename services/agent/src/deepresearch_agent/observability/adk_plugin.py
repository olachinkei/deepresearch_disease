from __future__ import annotations

from typing import Any

from deepresearch_agent.observability.otel import (
    enforce_privacy_environment,
    set_safe_span_attributes,
)

enforce_privacy_environment()

from google.adk.plugins import BasePlugin  # noqa: E402


class SafeTraceMetadataPlugin(BasePlugin):
    """Adds only pre-sanitized, scalar metadata to the current ADK root span."""

    def __init__(self) -> None:
        super().__init__(name="safe_trace_metadata")

    async def before_run_callback(self, *, invocation_context: Any) -> None:
        run_config = getattr(invocation_context, "run_config", None)
        metadata = getattr(run_config, "custom_metadata", None) or {}
        safe = {key: value for key, value in metadata.items() if key.startswith("app.")}
        set_safe_span_attributes(safe)
