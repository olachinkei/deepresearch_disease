from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from deepresearch_agent.governance.approvals import (
    ApprovalDecision,
    DataClass,
    sensitive_requirements,
    validate_sensitive_approvals,
)


class Settings(BaseSettings):
    """Process configuration with external-data gates defaulting to closed."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "deepresearch-disease-agent"
    host: str = "127.0.0.1"
    port: int = 8001
    runtime_mode: Literal["mock", "live"] = "mock"
    database_path: Path = Path("data/corpus.sqlite")
    session_database_path: Path = Path("data/sessions.sqlite")
    model: str = "gemini-3.6-flash"
    prompt_version: str = "v1"
    corpus_version: str = "public-seed-20260730-c8457953"
    hmac_secret: SecretStr = SecretStr("local-development-only-change-me")
    environment: Literal["local", "pilot"] = "local"
    deployment_profile: Literal[
        "public_synthetic_demo",
        "approved_sensitive_pilot",
    ] = "public_synthetic_demo"
    sensitive_approval_registry_path: Path | None = None
    sensitive_approval_decisions: tuple[ApprovalDecision, ...] = Field(
        default_factory=tuple,
        exclude=True,
        repr=False,
    )

    allow_target_to_exa: bool = False
    allow_public_content_to_gemini: bool = False
    allow_internal_content_to_gemini: bool = False
    allow_research_hypothesis_to_gemini: bool = False
    trace_input_content_enabled: bool = False
    trace_output_content_enabled: bool = False
    trace_public_input_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    trace_synthetic_input_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    legacy_trace_content_enabled: bool | None = Field(
        default=None,
        validation_alias="AGENT_TRACE_CONTENT_ENABLED",
        exclude=True,
        repr=False,
    )
    legacy_trace_research_hypotheses_enabled: bool | None = Field(
        default=None,
        validation_alias="AGENT_TRACE_RESEARCH_HYPOTHESES_ENABLED",
        exclude=True,
        repr=False,
    )
    feedback_comment_to_wandb_enabled: bool = False
    internal_ingestion_enabled: bool = False
    exa_retry_backoff_seconds: float = Field(default=0.25, ge=0.0, le=5.0)
    turn_deadline_seconds: float = Field(default=180.0, gt=0.0, le=180.0)

    exa_api_key: SecretStr | None = Field(default=None, validation_alias="EXA_API_KEY")
    google_api_key: SecretStr | None = Field(default=None, validation_alias="GOOGLE_API_KEY")
    wandb_entity: str | None = Field(default=None, validation_alias="WANDB_ENTITY")
    wandb_project: str | None = Field(default=None, validation_alias="WANDB_PROJECT")

    @field_validator("hmac_secret")
    @classmethod
    def validate_hmac_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 24:
            raise ValueError("AGENT_HMAC_SECRET must contain at least 24 characters")
        return value

    @field_validator(
        "trace_public_input_fingerprints",
        "trace_synthetic_input_fingerprints",
    )
    @classmethod
    def validate_trace_input_fingerprints(
        cls, values: frozenset[str]
    ) -> frozenset[str]:
        if any(
            len(value) != 64
            or value != value.lower()
            or any(character not in "0123456789abcdef" for character in value)
            for value in values
        ):
            raise ValueError("trace input fingerprints must be lowercase SHA-256 values")
        return values

    @model_validator(mode="after")
    def validate_trace_content_policy(self) -> Settings:
        if self.legacy_trace_content_enabled:
            raise ValueError(
                "AGENT_TRACE_CONTENT_ENABLED was removed; configure independent "
                "input/output trace flags"
            )
        if self.legacy_trace_research_hypotheses_enabled:
            raise ValueError(
                "AGENT_TRACE_RESEARCH_HYPOTHESES_ENABLED was removed; use exact "
                "server-owned trace input fingerprints"
            )
        overlap = (
            self.trace_public_input_fingerprints
            & self.trace_synthetic_input_fingerprints
        )
        if overlap:
            raise ValueError(
                "trace input fingerprints cannot be both public and synthetic"
            )
        requirements = sensitive_requirements(
            internal_ingestion_enabled=self.internal_ingestion_enabled,
            allow_internal_content_to_gemini=self.allow_internal_content_to_gemini,
            allow_research_hypothesis_to_gemini=(
                self.allow_research_hypothesis_to_gemini
            ),
            allow_target_to_exa=self.allow_target_to_exa,
            trace_input_content_enabled=self.trace_input_content_enabled,
            trace_output_content_enabled=self.trace_output_content_enabled,
            feedback_comment_to_wandb_enabled=(
                self.feedback_comment_to_wandb_enabled
            ),
        )
        approval_requirements = requirements
        if self.deployment_profile == "public_synthetic_demo":
            forbidden_requirements = tuple(
                requirement
                for requirement in requirements
                if requirement.data_class != DataClass.PUBLIC_OR_SYNTHETIC
            )
            if forbidden_requirements:
                raise ValueError(
                    "sensitive feature approval denied "
                    "(reason=demo_profile_forbids_sensitive_features)"
                )
            approval_requirements = ()
        decisions = validate_sensitive_approvals(
            registry_path=self.sensitive_approval_registry_path,
            environment=self.environment,
            requirements=approval_requirements,
        )
        object.__setattr__(self, "sensitive_approval_decisions", decisions)
        return self

    @property
    def live_exa_enabled(self) -> bool:
        return (
            self.runtime_mode == "live"
            and self.allow_target_to_exa
            and self.exa_api_key is not None
        )

    @property
    def live_gemini_enabled(self) -> bool:
        return self.runtime_mode == "live" and self.google_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
