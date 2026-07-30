from __future__ import annotations

import logging
import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)
OWNER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$"


class SensitiveFeature(StrEnum):
    INTERNAL_PDF_INGESTION = "internal_pdf_ingestion"
    INTERNAL_CONTENT_TO_GEMINI = "internal_content_to_gemini"
    RESEARCH_HYPOTHESIS_TO_GEMINI = "research_hypothesis_to_gemini"
    RESEARCH_HYPOTHESIS_TO_EXA = "research_hypothesis_to_exa"
    WANDB_TRACE_INPUT_CONTENT = "wandb_trace_input_content"
    WANDB_TRACE_OUTPUT_CONTENT = "wandb_trace_output_content"
    WANDB_FEEDBACK_COMMENT = "wandb_feedback_comment"


class Destination(StrEnum):
    LOCAL_CORPUS = "local_corpus"
    GEMINI = "gemini"
    EXA = "exa"
    WANDB = "wandb"


class DataClass(StrEnum):
    INTERNAL_DOCUMENT = "internal_document"
    RESEARCH_SENSITIVE = "research_sensitive"
    PUBLIC_OR_SYNTHETIC = "public_or_synthetic"
    USER_LOCAL = "user_local"


class RetentionStore(StrEnum):
    WEB_DB = "web_db"
    ADK_SESSION_DB = "adk_session_db"
    CORPUS_DB = "corpus_db"
    WANDB = "wandb"
    VENDOR = "vendor"


class RetentionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: RetentionStore
    retention_days: int = Field(ge=0)
    deletion_owner: str = Field(min_length=1)
    backup_policy: str = Field(min_length=1)
    deletion_verification: str = Field(min_length=1)

    @field_validator(
        "deletion_owner",
        "backup_policy",
        "deletion_verification",
    )
    @classmethod
    def reject_blank_retention_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("retention fields cannot be blank")
        return value


class GovernanceRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_manager_id: str = Field(pattern=OWNER_ID_PATTERN)
    service_owner_id: str = Field(pattern=OWNER_ID_PATTERN)
    stroke_sme_id: str = Field(pattern=OWNER_ID_PATTERN)
    drug_discovery_sme_id: str = Field(pattern=OWNER_ID_PATTERN)
    deletion_owners: dict[RetentionStore, str]

    @field_validator("deletion_owners")
    @classmethod
    def validate_deletion_owners(
        cls,
        values: dict[RetentionStore, str],
    ) -> dict[RetentionStore, str]:
        if set(values) != set(RetentionStore):
            raise ValueError("every retention store requires a deletion owner")
        if any(re.fullmatch(OWNER_ID_PATTERN, value) is None for value in values.values()):
            raise ValueError("deletion owners require stable non-blank IDs")
        return values

    @model_validator(mode="after")
    def validate_sme_independence(self) -> GovernanceRoles:
        if self.stroke_sme_id == self.drug_discovery_sme_id:
            raise ValueError("stroke and drug-discovery SME IDs must be distinct")
        return self


class StoreDeletionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: RetentionStore
    dry_run_on: date
    executed_by: str = Field(pattern=OWNER_ID_PATTERN)
    matched_record_count: int = Field(ge=0)
    backup_status: Literal[
        "not_applicable",
        "verified_expiry",
        "verified_purge",
    ]
    verification_status: Literal["verified"]
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$")


class PilotVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_synthetic_pilot_on: date
    verified_by: str = Field(pattern=OWNER_ID_PATTERN)
    deletion_evidence: tuple[StoreDeletionEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_stores(self) -> PilotVerification:
        stores = [evidence.store for evidence in self.deletion_evidence]
        if len(stores) != len(set(stores)):
            raise ValueError("pilot deletion evidence stores must be unique")
        return self


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
    feature: SensitiveFeature
    destination: Destination
    environment: Literal["local", "pilot"]
    data_class: DataClass
    purpose: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_on: date
    expires_on: date
    constraints: tuple[str, ...] = Field(min_length=1)
    retention: tuple[RetentionRule, ...] = Field(min_length=1)
    pilot_verification: PilotVerification

    @field_validator("purpose", "approved_by")
    @classmethod
    def reject_blank_record_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approval fields cannot be blank")
        return value

    @field_validator("constraints")
    @classmethod
    def reject_blank_constraints(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("approval constraints cannot be blank")
        return values

    @model_validator(mode="after")
    def validate_dates_and_retention(self) -> ApprovalRecord:
        if self.expires_on < self.approved_on:
            raise ValueError("approval expiry cannot precede approval date")
        if self.pilot_verification.public_synthetic_pilot_on > self.approved_on:
            raise ValueError("public/synthetic pilot must precede approval")
        if any(
            evidence.dry_run_on > self.approved_on
            for evidence in self.pilot_verification.deletion_evidence
        ):
            raise ValueError("deletion dry-run evidence must precede approval")
        stores = [rule.store for rule in self.retention]
        if len(stores) != len(set(stores)):
            raise ValueError("approval retention stores must be unique")
        return self


class ApprovalRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"]
    roles: GovernanceRoles
    approvals: tuple[ApprovalRecord, ...]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ApprovalRegistry:
        approval_ids = [record.approval_id for record in self.approvals]
        if len(approval_ids) != len(set(approval_ids)):
            raise ValueError("approval IDs must be unique")
        return self


class ApprovalRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature: SensitiveFeature
    destination: Destination
    data_class: DataClass


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    feature: SensitiveFeature
    destination: Destination
    environment: Literal["local", "pilot"]
    decision: Literal["approved"] = "approved"


class ApprovalConfigurationError(ValueError):
    """Sanitized failure raised before a sensitive feature can start."""


FEATURE_RETENTION_STORES: dict[SensitiveFeature, frozenset[RetentionStore]] = {
    SensitiveFeature.INTERNAL_PDF_INGESTION: frozenset({RetentionStore.CORPUS_DB}),
    SensitiveFeature.INTERNAL_CONTENT_TO_GEMINI: frozenset(
        {
            RetentionStore.WEB_DB,
            RetentionStore.ADK_SESSION_DB,
            RetentionStore.CORPUS_DB,
            RetentionStore.VENDOR,
        }
    ),
    SensitiveFeature.RESEARCH_HYPOTHESIS_TO_GEMINI: frozenset(
        {
            RetentionStore.WEB_DB,
            RetentionStore.ADK_SESSION_DB,
            RetentionStore.VENDOR,
        }
    ),
    SensitiveFeature.RESEARCH_HYPOTHESIS_TO_EXA: frozenset(
        {
            RetentionStore.WEB_DB,
            RetentionStore.ADK_SESSION_DB,
            RetentionStore.VENDOR,
        }
    ),
    SensitiveFeature.WANDB_TRACE_INPUT_CONTENT: frozenset(
        {
            RetentionStore.WEB_DB,
            RetentionStore.ADK_SESSION_DB,
            RetentionStore.WANDB,
        }
    ),
    SensitiveFeature.WANDB_TRACE_OUTPUT_CONTENT: frozenset(
        {
            RetentionStore.WEB_DB,
            RetentionStore.ADK_SESSION_DB,
            RetentionStore.WANDB,
        }
    ),
    SensitiveFeature.WANDB_FEEDBACK_COMMENT: frozenset(
        {RetentionStore.WEB_DB, RetentionStore.WANDB}
    ),
}


def sensitive_requirements(
    *,
    internal_ingestion_enabled: bool,
    allow_internal_content_to_gemini: bool,
    allow_research_hypothesis_to_gemini: bool,
    allow_target_to_exa: bool,
    trace_input_content_enabled: bool,
    trace_output_content_enabled: bool,
    feedback_comment_to_wandb_enabled: bool,
) -> tuple[ApprovalRequirement, ...]:
    configured = (
        (
            internal_ingestion_enabled,
            SensitiveFeature.INTERNAL_PDF_INGESTION,
            Destination.LOCAL_CORPUS,
            DataClass.INTERNAL_DOCUMENT,
        ),
        (
            allow_internal_content_to_gemini,
            SensitiveFeature.INTERNAL_CONTENT_TO_GEMINI,
            Destination.GEMINI,
            DataClass.INTERNAL_DOCUMENT,
        ),
        (
            allow_research_hypothesis_to_gemini,
            SensitiveFeature.RESEARCH_HYPOTHESIS_TO_GEMINI,
            Destination.GEMINI,
            DataClass.RESEARCH_SENSITIVE,
        ),
        (
            allow_target_to_exa,
            SensitiveFeature.RESEARCH_HYPOTHESIS_TO_EXA,
            Destination.EXA,
            DataClass.RESEARCH_SENSITIVE,
        ),
        (
            trace_input_content_enabled,
            SensitiveFeature.WANDB_TRACE_INPUT_CONTENT,
            Destination.WANDB,
            DataClass.PUBLIC_OR_SYNTHETIC,
        ),
        (
            trace_output_content_enabled,
            SensitiveFeature.WANDB_TRACE_OUTPUT_CONTENT,
            Destination.WANDB,
            DataClass.PUBLIC_OR_SYNTHETIC,
        ),
        (
            feedback_comment_to_wandb_enabled,
            SensitiveFeature.WANDB_FEEDBACK_COMMENT,
            Destination.WANDB,
            DataClass.USER_LOCAL,
        ),
    )
    return tuple(
        ApprovalRequirement(
            feature=feature,
            destination=destination,
            data_class=data_class,
        )
        for enabled, feature, destination, data_class in configured
        if enabled
    )


def validate_sensitive_approvals(
    *,
    registry_path: Path | None,
    environment: Literal["local", "pilot"],
    requirements: tuple[ApprovalRequirement, ...],
    today: date | None = None,
) -> tuple[ApprovalDecision, ...]:
    if not requirements:
        return ()
    if registry_path is None or not registry_path.is_file():
        raise ApprovalConfigurationError(
            "sensitive feature approval denied (reason=registry_missing)"
        )
    try:
        registry = ApprovalRegistry.model_validate_json(registry_path.read_text())
    except (OSError, ValueError) as exc:
        raise ApprovalConfigurationError(
            "sensitive feature approval denied (reason=registry_invalid)"
        ) from exc

    effective_date = today or date.today()
    decisions: list[ApprovalDecision] = []
    for requirement in requirements:
        matching = [
            record
            for record in registry.approvals
            if record.feature == requirement.feature
            and record.destination == requirement.destination
            and record.environment == environment
            and record.data_class == requirement.data_class
        ]
        active = [
            record
            for record in matching
            if record.approved_on <= effective_date <= record.expires_on
        ]
        if not active:
            reason = "expired_or_inactive" if matching else "scope_mismatch"
            raise ApprovalConfigurationError(
                "sensitive feature approval denied "
                f"(feature={requirement.feature.value}, reason={reason})"
            )
        required_stores = FEATURE_RETENTION_STORES[requirement.feature]
        valid: list[ApprovalRecord] = []
        failure_reasons: list[str] = []
        for candidate in sorted(
            active,
            key=lambda item: (item.expires_on, item.approval_id),
        ):
            readiness_reason = _record_readiness_failure(
                record=candidate,
                roles=registry.roles,
                required_stores=required_stores,
                effective_date=effective_date,
            )
            if readiness_reason is None:
                valid.append(candidate)
            else:
                failure_reasons.append(readiness_reason)
        if not valid:
            reason = failure_reasons[0] if failure_reasons else "scope_mismatch"
            raise ApprovalConfigurationError(
                "sensitive feature approval denied "
                f"(feature={requirement.feature.value}, reason={reason})"
            )
        record = valid[0]
        decisions.append(
            ApprovalDecision(
                approval_id=record.approval_id,
                feature=record.feature,
                destination=record.destination,
                environment=record.environment,
            )
        )
    return tuple(decisions)


def _record_readiness_failure(
    *,
    record: ApprovalRecord,
    roles: GovernanceRoles,
    required_stores: frozenset[RetentionStore],
    effective_date: date,
) -> str | None:
    if record.approved_by != roles.data_manager_id:
        return "data_manager_mismatch"
    retention_by_store = {rule.store: rule for rule in record.retention}
    if not required_stores.issubset(retention_by_store):
        return "retention_scope_missing"
    if any(
        retention_by_store[store].deletion_owner != roles.deletion_owners[store]
        for store in required_stores
    ):
        return "deletion_owner_mismatch"
    verification = record.pilot_verification
    if verification.verified_by != roles.data_manager_id:
        return "pilot_verifier_mismatch"
    evidence_by_store = {evidence.store: evidence for evidence in verification.deletion_evidence}
    if not required_stores.issubset(evidence_by_store):
        return "deletion_evidence_missing"
    if any(
        evidence_by_store[store].executed_by != roles.deletion_owners[store]
        for store in required_stores
    ):
        return "deletion_executor_mismatch"
    if verification.public_synthetic_pilot_on > effective_date or any(
        evidence_by_store[store].dry_run_on > effective_date for store in required_stores
    ):
        return "pilot_evidence_in_future"
    return None


def log_approval_decisions(decisions: tuple[ApprovalDecision, ...]) -> None:
    for decision in decisions:
        logger.info(
            "sensitive_approval_decision approval_id=%s feature=%s "
            "destination=%s environment=%s decision=%s",
            decision.approval_id,
            decision.feature.value,
            decision.destination.value,
            decision.environment,
            decision.decision,
        )
