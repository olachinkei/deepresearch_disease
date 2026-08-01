from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from deepresearch_agent.evaluation.weave_pilot import (
    SIGNAL_SPECS,
    build_pilot_rows,
    export_synthetic_signal_turns,
    fetch_signal_aggregates,
    fetch_signal_correlations,
    fetch_signal_pilot_agent_trace,
    validate_pilot_rows,
)


def test_pilot_rows_are_bounded_synthetic_and_stroke_only() -> None:
    rows = build_pilot_rows()

    assert len(rows) == 4
    assert all(row["synthetic"] is True for row in rows)
    assert {row["disease"] for row in rows} == {"ischemic stroke"}
    assert all(set(row) == {"case_id", "query", "disease", "synthetic"} for row in rows)


@pytest.mark.parametrize(
    "change",
    [
        {"synthetic": False},
        {"disease": "other"},
        {"internal_body": "must not leave the service"},
    ],
)
def test_pilot_rows_fail_closed(change: dict[str, Any]) -> None:
    row = build_pilot_rows()[0] | change

    with pytest.raises(ValueError):
        validate_pilot_rows([row])


def test_signal_sampling_plan_matches_issue_acceptance() -> None:
    rates = {spec.name: spec.sample_rate for spec in SIGNAL_SPECS}

    assert rates == {
        "User Frustration": 1.0,
        "Low Quality Response": 0.2,
        "Medical Overclaim": 0.15,
        "Unsupported Citation": 0.15,
    }


def test_signal_export_rejects_nonstandard_pilot_size() -> None:
    with pytest.raises(ValueError, match="exactly 20"):
        export_synthetic_signal_turns(count=19)


def test_signal_pilot_agent_trace_uses_grouped_metadata_only() -> None:
    requests: list[Any] = []

    def query(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(
            groups=[
                SimpleNamespace(
                    group_keys={
                        "agent_name": "deepresearch_agent-signals-pilot",
                        "operation_name": "invoke_agent",
                    },
                    span_count=20,
                    invocation_count=20,
                    conversation_count=20,
                ),
                SimpleNamespace(
                    group_keys={
                        "agent_name": "Weave Signals",
                        "operation_name": "chat",
                    },
                    span_count=30,
                    invocation_count=0,
                    conversation_count=30,
                ),
            ]
        )

    client = SimpleNamespace(
        entity="entity",
        project="project",
        server=SimpleNamespace(agent_spans_query=query),
    )

    result = fetch_signal_pilot_agent_trace(
        client,
        start=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result.agent_name == "deepresearch_agent-signals-pilot"
    assert result.operation_name == "invoke_agent"
    assert result.span_count == 20
    assert result.invocation_count == 20
    assert result.conversation_count == 20
    assert len(requests) == 1
    assert [group.alias for group in requests[0].group_by] == [
        "agent_name",
        "operation_name",
    ]
    assert requests[0].include_details is False


def test_signal_aggregation_uses_grouped_span_query_only() -> None:
    requests: list[Any] = []

    def query(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(
            groups=[
                SimpleNamespace(group_keys={"turn_id": "signals-pilot-000"}),
                SimpleNamespace(group_keys={"turn_id": "signals-pilot-001"}),
            ]
        )

    client = SimpleNamespace(
        entity="entity",
        project="project",
        server=SimpleNamespace(agent_spans_query=query),
    )
    result = fetch_signal_aggregates(
        client,
        start=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result == {
        "user_frustration": 2,
        "low_quality": 2,
        "medical_overclaim": 2,
        "unsupported_citation": 2,
    }
    assert len(requests) == 4
    assert all(request.project_id == "entity/project" for request in requests)
    assert all(request.signal_filters is not None for request in requests)
    assert all(
        [group.alias for group in request.group_by] == ["conversation_id", "turn_id"]
        for request in requests
    )
    assert all(request.include_details is False for request in requests)


def test_signal_correlation_returns_only_aggregate_quality_metrics() -> None:
    requests: list[Any] = []

    def query(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(
            groups=[
                SimpleNamespace(group_keys={"turn_id": "signals-pilot-000"}),
                SimpleNamespace(group_keys={"turn_id": "signals-pilot-001"}),
            ]
        )

    client = SimpleNamespace(
        entity="entity",
        project="project",
        server=SimpleNamespace(agent_spans_query=query),
    )
    result = fetch_signal_correlations(
        client,
        start=datetime(2026, 8, 1, tzinfo=UTC),
    )

    frustration = result["user_frustration"]
    assert frustration.expected_positive == 5
    assert frustration.matched == 2
    assert frustration.true_positive == 1
    assert frustration.false_positive == 1
    assert frustration.observed_positive_precision == 0.5
    assert frustration.tagged_positive_capture == 0.2
    assert len(requests) == 4
    assert all(request.group_by[0].alias == "conversation_id" for request in requests)
    assert all(request.group_by[1].alias == "turn_id" for request in requests)
    assert all(request.signal_filters is not None for request in requests)
