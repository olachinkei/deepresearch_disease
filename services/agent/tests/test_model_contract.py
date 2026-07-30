from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from deepresearch_agent.model_contract import (
    GENERATION_MODEL_ID,
    SYNTHESIS_PROMPT_CONTRACT,
    SYNTHESIS_PROMPT_SHA256,
    SYNTHESIS_PROMPT_VERSION,
)
from deepresearch_agent.observability.otel import TraceMetadata
from deepresearch_agent.settings import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _env_assignments(path: Path) -> list[tuple[str, str]]:
    assignments: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        assignments.append((key, value))
    return assignments


@pytest.mark.parametrize(
    "model_id",
    ["gemini-flash-latest", "gemini-3.6-flash-preview", "gemini-2.5-flash"],
)
def test_settings_rejects_unpinned_model_ids(model_id: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, model=model_id)  # type: ignore[arg-type]


def test_settings_rejects_stale_prompt_version_or_hash() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, prompt_version="1.0.1")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="does not match the pinned prompt"):
        Settings(_env_file=None, prompt_sha256="0" * 64)


def test_model_and_prompt_contract_defaults_are_exact() -> None:
    settings = Settings(_env_file=None)

    assert settings.model == GENERATION_MODEL_ID == "gemini-3.6-flash"
    assert settings.prompt_version == SYNTHESIS_PROMPT_VERSION == "1.0.0"
    assert settings.prompt_sha256 == SYNTHESIS_PROMPT_SHA256
    assert SYNTHESIS_PROMPT_SHA256 == hashlib.sha256(
        SYNTHESIS_PROMPT_CONTRACT.encode("utf-8")
    ).hexdigest()


def test_example_env_files_have_one_exact_assignment_per_contract_key() -> None:
    expected = {
        "AGENT_MODEL": GENERATION_MODEL_ID,
        "AGENT_PROMPT_VERSION": SYNTHESIS_PROMPT_VERSION,
        "AGENT_PROMPT_SHA256": SYNTHESIS_PROMPT_SHA256,
    }
    for relative_path in (".env.example", "services/agent/.env.example"):
        assignments = _env_assignments(REPOSITORY_ROOT / relative_path)
        keys = [key for key, _ in assignments]
        assert len(keys) == len(set(keys)), f"duplicate env key in {relative_path}"
        values = dict(assignments)
        assert {key: values[key] for key in expected} == expected


def test_trace_metadata_contains_versions_but_not_prompt_content() -> None:
    attributes = TraceMetadata(
        user_hash="a" * 64,
        turn_id="turn-1",
        conversation_id="conversation-1",
        agent_version="test",
        model_id=GENERATION_MODEL_ID,
        prompt_version=SYNTHESIS_PROMPT_VERSION,
        prompt_sha256=SYNTHESIS_PROMPT_SHA256,
        corpus_version="test-corpus",
    ).attributes()

    assert attributes["app.model_id"] == GENERATION_MODEL_ID
    assert attributes["app.prompt_version"] == SYNTHESIS_PROMPT_VERSION
    assert attributes["app.prompt_sha256"] == SYNTHESIS_PROMPT_SHA256
    assert SYNTHESIS_PROMPT_CONTRACT not in attributes.values()
