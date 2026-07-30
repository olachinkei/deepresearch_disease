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
        approved_by: str = "Synthetic Test Data Manager",
        retention_store: str = "vendor",
    ) -> Path:
        payload: dict[str, Any] = {
            "schema_version": "1",
            "approvals": [
                {
                    "approval_id": approval_id,
                    "feature": feature,
                    "destination": destination,
                    "environment": environment,
                    "data_class": data_class,
                    "purpose": "Synthetic test approval",
                    "approved_by": approved_by,
                    "approved_on": approved_on,
                    "expires_on": expires_on,
                    "constraints": ["synthetic test data only"],
                    "retention": [
                        {
                            "store": retention_store,
                            "retention_days": 0,
                            "deletion_owner": "Synthetic Test Operator",
                            "backup_policy": "none",
                            "deletion_verification": "synthetic receipt check",
                        }
                    ],
                }
            ],
        }
        path = tmp_path / f"{approval_id}.json"
        path.write_text(json.dumps(payload))
        return path

    return create
