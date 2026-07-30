from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def approval_registry_factory(
    tmp_path: Path,
) -> Callable[..., Path]:
    def create(
        *,
        feature: str = "research_hypothesis_to_exa",
        destination: str = "exa",
        environment: str = "local",
        data_class: str = "research_sensitive",
        approved_on: str = "2026-01-01",
        expires_on: str = "2099-12-31",
        approval_id: str = "synthetic-approval-001",
        schema_version: str = "2",
        data_manager_id: str = "test-data-manager-001",
        approved_by: str | None = None,
        stroke_sme_id: str = "test-stroke-sme-001",
        drug_discovery_sme_id: str = "test-drug-sme-001",
        retention_stores: tuple[str, ...] = (
            "web_db",
            "adk_session_db",
            "vendor",
        ),
        evidence_stores: tuple[str, ...] | None = None,
        retention_owner_overrides: dict[str, str] | None = None,
        evidence_executor_overrides: dict[str, str] | None = None,
        pilot_verified_by: str | None = None,
    ) -> Path:
        deletion_owners = {
            "web_db": "test-web-owner-001",
            "adk_session_db": "test-session-owner-001",
            "corpus_db": "test-corpus-owner-001",
            "wandb": "test-wandb-owner-001",
            "vendor": "test-vendor-owner-001",
        }
        retention_owner_overrides = retention_owner_overrides or {}
        evidence_executor_overrides = evidence_executor_overrides or {}
        actual_evidence_stores = evidence_stores or retention_stores
        payload: dict[str, Any] = {
            "schema_version": schema_version,
            "roles": {
                "data_manager_id": data_manager_id,
                "service_owner_id": "test-service-owner-001",
                "stroke_sme_id": stroke_sme_id,
                "drug_discovery_sme_id": drug_discovery_sme_id,
                "deletion_owners": deletion_owners,
            },
            "approvals": [
                {
                    "approval_id": approval_id,
                    "feature": feature,
                    "destination": destination,
                    "environment": environment,
                    "data_class": data_class,
                    "purpose": "Synthetic test approval",
                    "approved_by": approved_by or data_manager_id,
                    "approved_on": approved_on,
                    "expires_on": expires_on,
                    "constraints": ["synthetic test data only"],
                    "retention": [
                        {
                            "store": store,
                            "retention_days": 0,
                            "deletion_owner": retention_owner_overrides.get(
                                store,
                                deletion_owners[store],
                            ),
                            "backup_policy": "none",
                            "deletion_verification": "synthetic receipt check",
                        }
                        for store in retention_stores
                    ],
                    "pilot_verification": {
                        "public_synthetic_pilot_on": "2025-12-30",
                        "verified_by": pilot_verified_by or data_manager_id,
                        "deletion_evidence": [
                            {
                                "store": store,
                                "dry_run_on": "2025-12-31",
                                "executed_by": evidence_executor_overrides.get(
                                    store,
                                    deletion_owners[store],
                                ),
                                "matched_record_count": 1,
                                "backup_status": "not_applicable",
                                "verification_status": "verified",
                                "evidence_reference": f"test-ticket/{store}",
                            }
                            for store in actual_evidence_stores
                        ],
                    },
                }
            ],
        }
        path = tmp_path / f"{approval_id}.json"
        path.write_text(json.dumps(payload))
        return path

    return create
