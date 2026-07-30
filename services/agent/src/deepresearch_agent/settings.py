from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    allow_target_to_exa: bool = False
    allow_public_content_to_gemini: bool = False
    allow_internal_content_to_gemini: bool = False
    allow_research_hypothesis_to_gemini: bool = False
    trace_content_enabled: bool = False
    trace_research_hypotheses_enabled: bool = False
    feedback_comment_to_wandb_enabled: bool = False
    internal_ingestion_enabled: bool = False

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
