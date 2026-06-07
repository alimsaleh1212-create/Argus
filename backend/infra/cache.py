"""Cache seam (Redis) — reserved.

RESERVED SEAM. Implemented in SPEC-ingestion (#4), which adds the Redis service
to compose and a CacheProvider into the registry.

Consumers and intent:
  - enrichment IOC cache  — memoize slow/rate-limited threat-intel lookups (TTL)
  - alert dedup           — collapse repeat Wazuh alerts by fingerprint
  - outbound rate-limiting — token-bucket for third-party APIs
  - (optional) LLM response cache — identical prompt → cached completion (#3)
"""

from __future__ import annotations

from typing import Any


class CacheProvider:
    """Redis connection-pool provider. Implemented in SPEC-ingestion (#4)."""

    name = "cache"

    def build(self, settings: Any) -> Any:
        raise NotImplementedError(
            "Cache (Redis) is a reserved seam; implemented in SPEC-ingestion (#4)."
        )
