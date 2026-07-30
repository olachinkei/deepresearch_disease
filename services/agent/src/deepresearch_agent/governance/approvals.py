from __future__ import annotations

import logging
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


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
        stores = [rule.store for rule in self.retention]
        if len(stores) != len(set(stores)):
            raise ValueError("approval retention stores must be unique")
        return self


class ApprovalRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
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
        record = sorted(active, key=lambda item: (item.expires_on, item.approval_id))[0]
        required_retention_store = {
            Destination.LOCAL_CORPUS: RetentionStore.CORPUS_DB,
            Destination.GEMINI: RetentionStore.VENDOR,
            Destination.EXA: RetentionStore.VENDOR,
            Destination.WANDB: RetentionStore.WANDB,
        }[requirement.destination]
        if required_retention_store not in {
            rule.store for rule in record.retention
        }:
            raise ApprovalConfigurationError(
                "sensitive feature approval denied "
                f"(feature={requirement.feature.value}, reason=retention_scope_missing)"
            )
        decisions.append(
            ApprovalDecision(
                approval_id=record.approval_id,
                feature=record.feature,
                destination=record.destination,
                environment=record.environment,
            )
        )
    return tuple(decisions)


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
