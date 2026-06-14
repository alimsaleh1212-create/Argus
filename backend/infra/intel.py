"""ThreatIntelClient — optional, config-gated, fail-closed on-demand intel lookup.

httpx is imported ONLY here (intel call confined to this module).
On-demand intel is off by default; missing credentials disable it rather than
failing boot. Any error/timeout yields verdict="unknown" (CD3/FR-008).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from backend.domain.corpus import IntelVerdict
from backend.domain.memory import EntityKind, EntityRef, TemporalFact
from backend.domain.redaction import Boundary
from backend.infra.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

_CACHE_PREFIX = "intel:"


class ThreatIntelClient:
    """Single-source threat-intel lookup with Redis caching and fail-closed behaviour."""

    def __init__(
        self,
        *,
        settings: Any,
        cache: Any,
        store: Any,
        redactor: Any,
    ) -> None:
        self._cfg = settings.intel
        self._cache = cache
        self._store = store
        self._redactor = redactor
        self._api_key: str | None = None  # resolved at first call or build time

    def set_api_key(self, key: str) -> None:
        self._api_key = key

    # ── public ───────────────────────────────────────────────────────────────

    async def lookup(self, indicator: str, kind: EntityKind = EntityKind.INDICATOR) -> IntelVerdict:
        """Return an IntelVerdict. Never raises into the caller (fail-closed)."""
        now = datetime.now(UTC)
        source = self._cfg.source_name
        try:
            return await self._lookup_inner(indicator, kind, now, source)
        except Exception as exc:
            logger.debug("intel_lookup_unhandled_error", error=str(exc))
            return IntelVerdict(
                indicator=indicator, verdict="unknown", source=source, observed_at=now
            )

    async def _lookup_inner(
        self, indicator: str, kind: EntityKind, now: datetime, source: str
    ) -> IntelVerdict:
        # Disabled fast-path
        if not self._cfg.enabled or not self._api_key:
            return IntelVerdict(
                indicator=indicator, verdict="unknown", source=source, observed_at=now
            )

        redacted_indicator = self._redactor.redact_text(indicator, Boundary.MEMORY_WRITE)
        cache_key = f"{_CACHE_PREFIX}{redacted_indicator}"

        # Cache read
        try:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return IntelVerdict(
                    indicator=redacted_indicator,
                    verdict=cached,
                    source=source,
                    observed_at=now,
                )
        except Exception as exc:
            logger.debug("intel_cache_read_error", error=str(exc))

        # External call
        verdict_str = await self._fetch_verdict(redacted_indicator)

        # Redact + guardrail response text (CD5)
        verdict_str = self._redactor.redact_text(verdict_str, Boundary.MEMORY_WRITE)
        verdict_str = await self._apply_guardrail(verdict_str)

        # Normalise to allowed values
        if verdict_str not in ("benign", "malicious", "suspicious"):
            verdict_str = "unknown"

        # Cache write (negative caching included)
        try:
            await self._cache.set(cache_key, verdict_str, ex=self._cfg.cache_ttl_s)
        except Exception as exc:
            logger.debug("intel_cache_write_error", error=str(exc))

        result = IntelVerdict(
            indicator=redacted_indicator,
            verdict=verdict_str,
            source=source,
            observed_at=now,
        )

        # Persist to memory as temporal fact (best-effort, CD2)
        if verdict_str != "unknown":
            await self._persist_fact(result, kind)

        return result

    # ── private ──────────────────────────────────────────────────────────────

    async def _fetch_verdict(self, indicator: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_s) as client:
                resp = await client.get(
                    self._cfg.base_url,
                    params={"indicator": indicator},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                return str(data.get("verdict", "unknown"))
        except Exception as exc:
            logger.debug("intel_fetch_unknown", error=str(exc))
            return "unknown"

    async def _apply_guardrail(self, text: str) -> str:
        """Route through the guardrail seam; no-op gracefully until #11 lands (CD5)."""
        try:
            from backend.infra.guardrails import get_guardrail

            guardrail = get_guardrail()
            await guardrail.check_input(text)
        except NotImplementedError:
            logger.debug("intel_guardrail_not_configured")
        except Exception as exc:
            logger.debug("intel_guardrail_error", error=str(exc))
        return text

    async def _persist_fact(self, verdict: IntelVerdict, kind: EntityKind) -> None:
        try:
            entity = EntityRef(kind=kind, value=verdict.indicator)
            fact = TemporalFact(
                entity=entity,
                fact_type="reputation",
                value=verdict.verdict,
                valid_from=verdict.observed_at,
            )
            await self._store.write_fact(fact)
        except Exception as exc:
            logger.debug("intel_write_fact_error", error=str(exc))


# ── Provider ─────────────────────────────────────────────────────────────────


class IntelProvider:
    """Lifespan singleton — yields ThreatIntelClient or a disabled stub."""

    name = "intel"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[ThreatIntelClient | None, None]:
        cfg = settings.intel
        if not cfg.enabled:
            logger.info("intel_disabled")
            yield None
            return

        # Resolve optional API key from Vault on demand (not a required boot path).
        api_key: str | None = None
        try:
            vault = settings._container.vault_client
            secret = await vault.fetch_secret(cfg.api_key_vault_path)
            api_key = secret.get("api_key") or None
        except Exception as exc:
            logger.warning("intel_api_key_unavailable", error=str(exc))

        if not api_key:
            logger.info("intel_no_api_key_disabled")
            yield None
            return

        container = getattr(settings, "_container", None)
        cache = getattr(container, "cache", None) if container else None
        store = getattr(container, "memory", None) if container else None
        redactor_obj = getattr(container, "observability", None) if container else None
        redactor = getattr(redactor_obj, "redactor", None) if redactor_obj else None

        if cache is None or redactor is None:
            logger.warning("intel_missing_dependencies")
            yield None
            return

        client = ThreatIntelClient(
            settings=settings,
            cache=cache,
            store=store,
            redactor=redactor,
        )
        client.set_api_key(api_key)
        logger.info("intel_provider_ready", source=cfg.source_name)
        yield client
