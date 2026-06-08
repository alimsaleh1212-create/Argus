"""Typed settings — single source of truth for all configuration.

Rules enforced here:
- ``extra="forbid"``: any unknown key → ValidationError → refuse boot.
- ``SecretStr`` for all sensitive fields; ``__repr__`` never emits values.
- Frozen after construction (immutable once loaded at startup).
- Env var naming: ``SENTINEL__<SECTION>__<FIELD>``.
"""

from __future__ import annotations

import json
import os
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.domain.llm import ProviderId

_KNOWN_SENTINEL_SECTIONS = frozenset(
    {
        "app", "vault", "postgres", "minio", "startup", "observability",
        "llm", "redis", "ingest", "supervisor", "triage",
    }
)
_SENTINEL_PREFIX = "SENTINEL__"


def _check_unknown_env_sections() -> None:
    """Raise ValueError if any SENTINEL__ env var has an unknown section.

    pydantic-settings v2 silently ignores env vars not matching known fields;
    ``extra="forbid"`` alone cannot catch unknown section keys (FR-002).
    Calling this *before* Settings() ensures the error is a plain ValueError
    with only key names — never secret values — in the message.
    """
    unknown = []
    for key in os.environ:
        if key.upper().startswith(_SENTINEL_PREFIX):
            section = key[len(_SENTINEL_PREFIX) :].split("__")[0].lower()
            if section and section not in _KNOWN_SENTINEL_SECTIONS:
                unknown.append(key)
    if unknown:
        raise ValueError(
            f"Unknown SENTINEL__ environment variable(s): {sorted(unknown)}. "
            "Remove unrecognised keys or check for typos (Settings extra='forbid')."
        )


def load_settings() -> Settings:
    """Build and return the application Settings with unknown-key validation.

    Use this instead of calling ``Settings()`` directly so that the
    unknown-section check runs *before* pydantic wraps anything in a
    ValidationError (which can include input_value and expose raw strings).
    """
    _check_unknown_env_sections()
    return Settings()


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    env: str = "local"
    log_level: str = "INFO"


class VaultSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    addr: str = "http://vault:8200"
    token: SecretStr = SecretStr("dev-root-token")
    kv_mount: str = "secret"
    required_paths: list[str] = Field(default_factory=list)

    @field_validator("required_paths", mode="before")
    @classmethod
    def parse_json_list(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v


class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    dsn: SecretStr = SecretStr("postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel")


class MinioSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    endpoint_url: str = "http://minio:9000"
    buckets: list[str] = Field(default_factory=lambda: ["eval-reports", "incident-snapshots"])

    @field_validator("buckets", mode="before")
    @classmethod
    def parse_json_list(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v


class StartupSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    dependency_timeout_s: Annotated[float, Field(gt=0)] = 5.0
    connect_retries: Annotated[int, Field(ge=1)] = 5


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    presidio_enabled: bool = True
    spacy_model: str = "en_core_web_sm"
    entropy_threshold: Annotated[float, Field(gt=0)] = 4.0
    span_attr_max_bytes: Annotated[int, Field(gt=0)] = 8192
    export_batch_size: Annotated[int, Field(gt=0)] = 512
    export_interval_ms: Annotated[int, Field(gt=0)] = 2000
    trace_to_stdout: bool = False


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    url: str = "redis://redis:6379/0"
    queue_key: str = "queue:incidents"
    processing_key: str = "queue:processing"
    dedup_prefix: str = "dedup:"
    dequeue_block_s: float = 5.0


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    webhook_vault_path: str = "secret/ingest"
    max_alert_bytes: int = 262_144
    dedup_window_s: int = 300
    max_attempts: int = 3


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    primary: ProviderId = ProviderId.GEMINI
    fallback_order: list[ProviderId] = Field(
        default_factory=lambda: [ProviderId.GEMINI, ProviderId.OLLAMA]
    )
    request_timeout_s: Annotated[float, Field(gt=0)] = 30.0
    max_retries: Annotated[int, Field(ge=0)] = 2
    gemini_model: str = "gemini-1.5-flash"
    gemini_vault_path: str = "secret/llm"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2:0.5b"

    @field_validator("fallback_order", mode="before")
    @classmethod
    def parse_json_list(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("fallback_order")
    @classmethod
    def validate_fallback_order(cls, v: list) -> list:
        if not v:
            raise ValueError("fallback_order must not be empty")
        return v


class SupervisorSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    max_steps: Annotated[int, Field(gt=0)] = 8
    max_tokens: Annotated[int, Field(gt=0)] = 40_000
    max_stage_retries: Annotated[int, Field(ge=0)] = 2
    fast_path_autoclose_severities: list[str] = Field(default_factory=lambda: ["low"])
    fast_path_critical_severities: list[str] = Field(default_factory=lambda: ["critical"])


class TriageSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    advance_min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.6
    resolve_min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.7
    max_output_tokens: Annotated[int, Field(gt=0)] = 512
    temperature: Annotated[float, Field(ge=0.0)] = 0.0
    prompt_version: str = "v1"

    @model_validator(mode="after")
    def _advance_le_resolve(self) -> TriageSettings:
        if self.advance_min_confidence > self.resolve_min_confidence:
            raise ValueError(
                f"advance_min_confidence ({self.advance_min_confidence}) must be "
                f"<= resolve_min_confidence ({self.resolve_min_confidence})"
            )
        return self


class Settings(BaseSettings):
    """Root settings object — built once at startup, frozen thereafter.

    Sensitive fields use ``SecretStr``; their values are never rendered in
    ``__repr__``, logs, or error messages.
    """

    model_config = SettingsConfigDict(
        extra="forbid",
        env_prefix="SENTINEL__",
        env_nested_delimiter="__",
        env_file=".env",
        frozen=True,
    )

    app: AppSettings = Field(default_factory=AppSettings)
    vault: VaultSettings = Field(default_factory=VaultSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    minio: MinioSettings = Field(default_factory=MinioSettings)
    startup: StartupSettings = Field(default_factory=StartupSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    supervisor: SupervisorSettings = Field(default_factory=SupervisorSettings)
    triage: TriageSettings = Field(default_factory=TriageSettings)

    @model_validator(mode="after")
    def _ensure_ingest_vault_path_required(self) -> Settings:
        """Guarantee ingest webhook Vault path is in vault.required_paths (fail boot if absent)."""
        ingest_path = self.ingest.webhook_vault_path
        if ingest_path not in self.vault.required_paths:
            object.__setattr__(
                self.vault,
                "required_paths",
                list(self.vault.required_paths) + [ingest_path],
            )
        return self

    @model_validator(mode="after")
    def _ensure_llm_vault_path_required(self) -> Settings:
        """Guarantee the Gemini Vault path is in vault.required_paths (fail-boot if absent)."""
        llm_path = self.llm.gemini_vault_path
        if llm_path not in self.vault.required_paths:
            object.__setattr__(
                self.vault,
                "required_paths",
                list(self.vault.required_paths) + [llm_path],
            )
        return self

    @model_validator(mode="after")
    def _validate_fallback_primary_consistency(self) -> Settings:
        """Ensure fallback_order[0] == primary (data-model.md validation)."""
        if self.llm.fallback_order and self.llm.fallback_order[0] != self.llm.primary:
            raise ValueError(
                f"llm.fallback_order[0] must equal llm.primary "
                f"(got {self.llm.fallback_order[0]!r} != {self.llm.primary!r})"
            )
        return self
