"""Deterministic query builders — pure functions over already-redacted incident evidence."""

from __future__ import annotations

import re
from typing import Any

from backend.domain.corpus import EntityKind, EntityRef, ReferenceQuery

_TECHNIQUE_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b")


def build_reference_query(evidence: dict[str, Any]) -> ReferenceQuery:
    """Build a ReferenceQuery from the incident's already-redacted evidence dict.

    technique_ids: extracted from rule_groups and rule_id (MITRE Txxxx pattern).
    terms: rule_description (if present) + rule_groups.
    Missing fields yield empty — never an error.
    """
    ne_raw = evidence.get("normalized_event") or {}
    rule_description: str = ne_raw.get("rule_description") or ""
    rule_groups: list[str] = ne_raw.get("rule_groups") or []
    rule_id: str | None = ne_raw.get("rule_id")

    technique_ids: list[str] = []
    seen_ids: set[str] = set()

    # Extract technique IDs from rule_groups
    for group in rule_groups:
        for match in _TECHNIQUE_RE.findall(group):
            if match not in seen_ids:
                technique_ids.append(match)
                seen_ids.add(match)

    # Extract from rule_description
    for match in _TECHNIQUE_RE.findall(rule_description):
        if match not in seen_ids:
            technique_ids.append(match)
            seen_ids.add(match)

    # rule_id itself if it looks like a technique
    if rule_id and _TECHNIQUE_RE.match(rule_id) and rule_id not in seen_ids:
        technique_ids.append(rule_id)

    terms: list[str] = []
    seen_terms: set[str] = set()
    if rule_description:
        t = rule_description.strip()
        if t and t not in seen_terms:
            terms.append(t)
            seen_terms.add(t)
    for group in rule_groups:
        g = group.strip()
        if g and g not in seen_terms:
            terms.append(g)
            seen_terms.add(g)

    return ReferenceQuery(technique_ids=technique_ids, terms=terms)


def extract_entities(evidence: dict[str, Any], max_indicators: int = 5) -> list[EntityRef]:
    """Extract entity refs from the incident's already-redacted evidence dict.

    Covers ADDRESS (agent_ip, srcip/dstip), HOST (agent_name), USER, and INDICATOR (hashes/domains).
    De-duplicated; capped at max_indicators.
    """
    ne_raw = evidence.get("normalized_event") or {}
    fields: dict[str, Any] = ne_raw.get("fields") or {}

    refs: list[EntityRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: EntityKind, value: str) -> None:
        if not value:
            return
        key = (kind.value, value)
        if key not in seen:
            seen.add(key)
            refs.append(EntityRef(kind=kind, value=value))

    # ADDRESS — agent IP
    if ne_raw.get("agent_ip"):
        _add(EntityKind.ADDRESS, ne_raw["agent_ip"])

    # ADDRESS — src/dst IPs from fields
    for field_key in ("srcip", "dstip", "src_ip", "dst_ip"):
        raw = fields.get(field_key)
        if isinstance(raw, str) and raw:
            _add(EntityKind.ADDRESS, raw)

    # HOST — agent name
    if ne_raw.get("agent_name"):
        _add(EntityKind.HOST, ne_raw["agent_name"])

    # USER — various field names
    for field_key in ("user", "srcuser", "dstuser"):
        raw = fields.get(field_key)
        if isinstance(raw, str) and raw:
            _add(EntityKind.USER, raw)

    # INDICATOR — hashes and domains/urls (bounded by max_indicators)
    indicator_count = 0
    for field_key in ("md5", "sha1", "sha256", "hash", "domain", "url"):
        if indicator_count >= max_indicators:
            break
        raw = fields.get(field_key)
        if isinstance(raw, str) and raw:
            _add(EntityKind.INDICATOR, raw)
            indicator_count += 1

    return refs[:max_indicators]
