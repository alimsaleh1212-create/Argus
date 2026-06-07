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

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_KNOWN_SENTINEL_SECTIONS = frozenset(
    {"app", "vault", "postgres", "minio", "startup", "observability"}
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
