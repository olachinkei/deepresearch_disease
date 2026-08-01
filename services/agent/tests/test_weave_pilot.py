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
        "User Satisfaction": 1.0,
        "Low Quality Response": 0.2,
        "Medical Overclaim": 0.15,
        "Unsupported Citation": 0.15,
    }


def test_signal_export_rejects_nonstandard_pilot_size() -> None:
    with pytest.raises(ValueError, match="exactly 20"):
        export_synthetic_signal_turns(count=19)


def test_signal_aggregation_uses_stats_endpoint_only() -> None:
    requests: list[Any] = []

    def stats(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(rows=[{"turns.count_distinct": 2}])

    client = SimpleNamespace(
        entity="entity",
        project="project",
        server=SimpleNamespace(agent_spans_stats=stats),
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
        "user_satisfaction_low": 2,
    }
    assert len(requests) == 5
    assert all(request.project_id == "entity/project" for request in requests)
    assert all(request.signal_filters is not None for request in requests)
    assert all(request.metrics[0].value.key == "trace_id" for request in requests)


def test_signal_correlation_returns_only_aggregate_quality_metrics() -> None:
    requests: list[Any] = []

    def stats(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(
            rows=[
                {"turn_id": "signals-pilot-000", "turns.count_distinct": 1},
                {"turn_id": "signals-pilot-001", "turns.count_distinct": 1},
            ]
        )

    client = SimpleNamespace(
        entity="entity",
        project="project",
        server=SimpleNamespace(agent_spans_stats=stats),
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
    assert len(requests) == 5
    assert all(request.group_by[0].alias == "turn_id" for request in requests)
    assert all(request.signal_filters is not None for request in requests)
