from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from deepresearch_agent.governance.approvals import log_approval_decisions
from deepresearch_agent.settings import Settings


def test_sensitive_flags_are_closed_without_registry() -> None:
    settings = Settings(_env_file=None)
    assert settings.sensitive_approval_decisions == ()

    with pytest.raises(ValueError, match="demo_profile_forbids_sensitive_features"):
        Settings(_env_file=None, allow_target_to_exa=True)

    with pytest.raises(ValueError, match="registry_missing"):
        Settings(
            _env_file=None,
            deployment_profile="approved_sensitive_pilot",
            allow_target_to_exa=True,
        )


@pytest.mark.parametrize(
    "enabled_flag",
    [
        "internal_ingestion_enabled",
        "allow_internal_content_to_gemini",
        "allow_research_hypothesis_to_gemini",
        "allow_target_to_exa",
        "feedback_comment_to_wandb_enabled",
    ],
)
def test_demo_profile_rejects_every_sensitive_feature_even_with_valid_registry(
    approval_registry_factory: Callable[..., Path],
    enabled_flag: str,
) -> None:
    registry = approval_registry_factory()

    with pytest.raises(ValueError, match="demo_profile_forbids_sensitive_features"):
        Settings(
            _env_file=None,
            sensitive_approval_registry_path=registry,
            **{enabled_flag: True},
        )


def test_demo_profile_allows_only_classified_public_or_synthetic_trace_content() -> None:
    settings = Settings(
        _env_file=None,
        trace_input_content_enabled=True,
        trace_output_content_enabled=True,
    )

    assert settings.sensitive_approval_decisions == ()
    assert settings.trace_public_input_fingerprints == frozenset()
    assert settings.trace_synthetic_input_fingerprints == frozenset()


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    [
        ({"environment": "pilot"}, "scope_mismatch"),
        ({"destination": "gemini"}, "scope_mismatch"),
        ({"data_class": "internal_document"}, "scope_mismatch"),
        ({"expires_on": "2026-01-02"}, "expired_or_inactive"),
        ({"approved_on": "2099-01-01"}, "expired_or_inactive"),
        ({"schema_version": "1"}, "registry_invalid"),
        ({"retention_stores": ("vendor",)}, "retention_scope_missing"),
        (
            {"evidence_stores": ("web_db", "vendor")},
            "deletion_evidence_missing",
        ),
        ({"approved_by": "other-manager-001"}, "data_manager_mismatch"),
        ({"pilot_verified_by": "other-manager-001"}, "pilot_verifier_mismatch"),
        (
            {"retention_owner_overrides": {"vendor": "wrong-owner-001"}},
            "deletion_owner_mismatch",
        ),
        (
            {"evidence_executor_overrides": {"vendor": "wrong-owner-001"}},
            "deletion_executor_mismatch",
        ),
        ({"approved_by": "   "}, "registry_invalid"),
        (
            {
                "stroke_sme_id": "same-sme-001",
                "drug_discovery_sme_id": "same-sme-001",
            },
            "registry_invalid",
        ),
    ],
)
def test_sensitive_approval_rejects_scope_and_date_mismatch(
    approval_registry_factory: Callable[..., Path],
    override: dict[str, object],
    expected_reason: str,
) -> None:
    registry = approval_registry_factory(**override)
    with pytest.raises(ValueError, match=expected_reason):
        Settings(
            _env_file=None,
            deployment_profile="approved_sensitive_pilot",
            allow_target_to_exa=True,
            sensitive_approval_registry_path=registry,
        )


def test_sensitive_approval_accepts_exact_scope_and_logs_only_safe_fields(
    approval_registry_factory: Callable[..., Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = approval_registry_factory()
    settings = Settings(
        _env_file=None,
        deployment_profile="approved_sensitive_pilot",
        allow_target_to_exa=True,
        sensitive_approval_registry_path=registry,
    )

    with caplog.at_level(
        logging.INFO,
        logger="deepresearch_agent.governance.approvals",
    ):
        log_approval_decisions(settings.sensitive_approval_decisions)

    assert len(settings.sensitive_approval_decisions) == 1
    assert "approval_id=synthetic-approval-001" in caplog.text
    assert "decision=approved" in caplog.text
    assert "test-data-manager-001" not in caplog.text
    assert "test-stroke-sme-001" not in caplog.text
    assert "test-vendor-owner-001" not in caplog.text
    assert "Synthetic test approval" not in caplog.text


def test_each_enabled_sensitive_feature_requires_its_own_record(
    approval_registry_factory: Callable[..., Path],
) -> None:
    exa_only = approval_registry_factory()
    with pytest.raises(
        ValueError,
        match="feature=internal_content_to_gemini",
    ):
        Settings(
            _env_file=None,
            deployment_profile="approved_sensitive_pilot",
            allow_target_to_exa=True,
            allow_internal_content_to_gemini=True,
            sensitive_approval_registry_path=exa_only,
        )
