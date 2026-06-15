"""Typed settings — single source of truth for all configuration.

Rules enforced here:
- ``extra="forbid"``: any unknown key → ValidationError → refuse boot.
- ``SecretStr`` for all sensitive fields; ``__repr__`` never emits values.
- Frozen after construction (immutable once loaded at startup).
- Env var naming: ``ARGUS__<SECTION>__<FIELD>``.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.domain.llm import ProviderId

_KNOWN_ARGUS_SECTIONS = frozenset(
    {
        "app",
        "vault",
        "postgres",
        "minio",
        "startup",
        "observability",
        "llm",
        "redis",
        "ingest",
        "supervisor",
        "triage",
        "memory",
        "corpus",
        "intel",
        "enrichment",
        "response",
        "dashboard",
    }
)
_ARGUS_PREFIX = "ARGUS__"


def _check_unknown_env_sections() -> None:
    """Raise ValueError if any ARGUS__ env var has an unknown section.

    pydantic-settings v2 silently ignores env vars not matching known fields;
    ``extra="forbid"`` alone cannot catch unknown section keys (FR-002).
    Calling this *before* Settings() ensures the error is a plain ValueError
    with only key names — never secret values — in the message.
    """
    unknown = []
    for key in os.environ:
        if key.upper().startswith(_ARGUS_PREFIX):
            section = key[len(_ARGUS_PREFIX) :].split("__")[0].lower()
            if section and section not in _KNOWN_ARGUS_SECTIONS:
                unknown.append(key)
    if unknown:
        raise ValueError(
            f"Unknown ARGUS__ environment variable(s): {sorted(unknown)}. "
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

    dsn: SecretStr = SecretStr("postgresql+asyncpg://argus:argus@postgres:5432/argus")


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

    fallback_order: list[ProviderId] = Field(
        default_factory=lambda: [ProviderId.GEMINI, ProviderId.OLLAMA]
    )

    @property
    def primary(self) -> ProviderId:
        return self.fallback_order[0]

    request_timeout_s: Annotated[float, Field(gt=0)] = 30.0
    max_retries: Annotated[int, Field(ge=0)] = 2
    gemini_model: str = "gemini-2.5-flash"
    gemini_vault_path: str = "secret/llm"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "gemma4:31b-cloud"

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


class EnrichmentSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    advance_min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.6
    resolve_min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.7
    corpus_k: Annotated[int, Field(gt=0)] = 5
    memory_k: Annotated[int, Field(gt=0)] = 5
    consult_intel: bool = True
    max_indicators: Annotated[int, Field(gt=0)] = 5
    max_output_tokens: Annotated[int, Field(gt=0)] = 768
    temperature: Annotated[float, Field(ge=0.0)] = 0.0
    prompt_version: str = "v1"

    @model_validator(mode="after")
    def _advance_le_resolve(self) -> EnrichmentSettings:
        if self.advance_min_confidence > self.resolve_min_confidence:
            raise ValueError(
                f"advance_min_confidence ({self.advance_min_confidence}) must be "
                f"<= resolve_min_confidence ({self.resolve_min_confidence})"
            )
        return self


class CorpusSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    enabled: bool = True
    data_dir: str = "backend/data/corpus"
    retrieval_k: Annotated[int, Field(gt=0)] = 5


class IntelSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    enabled: bool = False
    source_name: str = "demo-intel"
    base_url: str = ""
    api_key_vault_path: str = "secret/intel"
    timeout_s: Annotated[float, Field(gt=0)] = 5.0
    cache_ttl_s: Annotated[int, Field(gt=0)] = 3600


class MemorySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    enabled: bool = True
    backend: Literal["graphiti", "pgvector"] = "graphiti"
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_vault_path: str = "secret/memory"
    retrieval_k: Annotated[int, Field(gt=0)] = 5
    retrieval_timeout_s: Annotated[float, Field(gt=0)] = 5.0
    # Writes (add_episode) run LLM entity/edge extraction + embedding + graph writes,
    # so they need far longer than the read timeout — especially on a cloud LLM.
    write_timeout_s: Annotated[float, Field(gt=0)] = 60.0

    # Embedder — chosen once at deploy time; do NOT change after data is written
    # (vectors from different models are incompatible; switching mid-stream corrupts search).
    embedder_provider: Literal["gemini", "ollama"] = "gemini"
    # Gemini embedder (embedder_provider="gemini")
    gemini_embedding_model: str = "text-embedding-004"
    # Ollama embedder (embedder_provider="ollama") — uses OpenAI-compatible /v1/embeddings
    ollama_embedder_base_url: str = "http://ollama:11434"
    ollama_embedder_model: str = "nomic-embed-text"
    ollama_embedder_dim: Annotated[int, Field(gt=0)] = 768

    # Cross-encoder / reranker — an ordered fallback chain (like llm.fallback_order),
    # INDEPENDENT of embedder_provider (reranking only scores search relevance, so it
    # never affects vector compatibility) and replacing Graphiti's default
    # OpenAIRerankerClient (which would demand a real OPENAI_API_KEY). Only invoked at
    # search time — writes (seeding / episode writes) never use it.
    #   gemini — GeminiRerankerClient (direct 0-100 scoring; reuses the secret/llm key).
    #   ollama — OpenAIRerankerClient at ollama's /v1, reusing llm.ollama_model (no
    #            dedicated pull). Logprob-limited, so best-effort — a last-resort
    #            fallback for when gemini is unavailable.
    # Default is gemini-only; set ["gemini","ollama"] to fall back to ollama.
    cross_encoder_order: list[Literal["gemini", "ollama"]] = Field(
        default_factory=lambda: ["gemini"]
    )

    @field_validator("cross_encoder_order", mode="before")
    @classmethod
    def _parse_cross_encoder_order(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("cross_encoder_order")
    @classmethod
    def _validate_cross_encoder_order(cls, v: list) -> list:
        if not v:
            raise ValueError("cross_encoder_order must not be empty")
        return v


class DashboardSettings(BaseSettings):
    """Settings for the operations dashboard (#12)."""

    model_config = SettingsConfigDict(extra="forbid")

    admin_username: str = "admin"
    vault_path_admin: str = "secret/dashboard"
    token_ttl_minutes: Annotated[int, Field(gt=0)] = 60
    algorithm: str = "HS256"
    stream_poll_seconds: Annotated[float, Field(gt=0)] = 2.0


class ResponseSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    auto_execute_actions: list[str] = Field(
        default_factory=lambda: ["add_to_watchlist", "open_ticket", "enrich_and_tag"]
    )
    select_min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.6
    approval_timeout_s: Annotated[int, Field(gt=0)] = 1800
    sweep_interval_s: Annotated[int, Field(gt=0)] = 60
    catalog_dir: str = "backend/data/playbooks"
    max_output_tokens: Annotated[int, Field(gt=0)] = 768
    temperature: Annotated[float, Field(ge=0.0)] = 0.0
    prompt_version: str = "v1"


class Settings(BaseSettings):
    """Root settings object — built once at startup, frozen thereafter.

    Sensitive fields use ``SecretStr``; their values are never rendered in
    ``__repr__``, logs, or error messages.
    """

    model_config = SettingsConfigDict(
        extra="forbid",
        env_prefix="ARGUS__",
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
    enrichment: EnrichmentSettings = Field(default_factory=EnrichmentSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    corpus: CorpusSettings = Field(default_factory=CorpusSettings)
    intel: IntelSettings = Field(default_factory=IntelSettings)
    response: ResponseSettings = Field(default_factory=ResponseSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)

    @model_validator(mode="after")
    def _ensure_dashboard_vault_path_required(self) -> Settings:
        """Guarantee admin Vault path is in vault.required_paths (fail boot if unseeded)."""
        dash_path = self.dashboard.vault_path_admin
        if dash_path not in self.vault.required_paths:
            object.__setattr__(
                self.vault,
                "required_paths",
                list(self.vault.required_paths) + [dash_path],
            )
        return self

    @model_validator(mode="after")
    def _ensure_memory_vault_path_required(self) -> Settings:
        """Guarantee Neo4j Vault path is in vault.required_paths (fail-boot if unseeded)."""
        if not self.memory.enabled:
            return self
        mem_path = self.memory.neo4j_vault_path
        if mem_path not in self.vault.required_paths:
            object.__setattr__(
                self.vault,
                "required_paths",
                list(self.vault.required_paths) + [mem_path],
            )
        return self

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

