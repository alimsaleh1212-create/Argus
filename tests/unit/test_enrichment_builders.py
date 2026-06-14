"""Unit tests — build_reference_query and extract_entities (T008)."""

from __future__ import annotations

from backend.agents.enrichment import build_reference_query, extract_entities
from backend.domain.corpus import EntityKind


def _evidence(**overrides) -> dict:
    base = {
        "normalized_event": {
            "rule_id": "100001",
            "rule_description": "Possible T1059 execution detected on agent",
            "rule_groups": ["attack", "T1059.003", "execution"],
            "agent_name": "web-server-01",
            "agent_ip": "10.0.0.5",
            "fields": {
                "srcip": "192.168.1.10",
                "user": "svc-account",
                "md5": "d41d8cd98f00b204e9800998ecf8427e",
            },
        },
    }
    base.update(overrides)
    return base


class TestBuildReferenceQuery:
    def test_extracts_technique_ids_from_groups(self):
        q = build_reference_query(_evidence())
        assert "T1059.003" in q.technique_ids

    def test_extracts_technique_ids_from_description(self):
        ev = _evidence()
        ev["normalized_event"]["rule_description"] = "Alert: T1078 valid account abuse"
        ev["normalized_event"]["rule_groups"] = []
        q = build_reference_query(ev)
        assert "T1078" in q.technique_ids

    def test_terms_include_description(self):
        q = build_reference_query(_evidence())
        assert any("T1059" in t for t in q.terms)

    def test_terms_include_rule_groups(self):
        q = build_reference_query(_evidence())
        assert "attack" in q.terms or "execution" in q.terms

    def test_missing_normalized_event_yields_empty(self):
        q = build_reference_query({})
        assert q.technique_ids == []
        assert q.terms == []

    def test_no_technique_ids_when_no_patterns(self):
        ev = _evidence()
        ev["normalized_event"]["rule_groups"] = ["syslog", "authentication"]
        ev["normalized_event"]["rule_description"] = "Login failed"
        q = build_reference_query(ev)
        assert q.technique_ids == []
        assert "Login failed" in q.terms


class TestExtractEntities:
    def test_extracts_agent_ip_as_address(self):
        entities = extract_entities(_evidence())
        assert any(e.kind == EntityKind.ADDRESS and e.value == "10.0.0.5" for e in entities)

    def test_extracts_srcip_as_address(self):
        entities = extract_entities(_evidence())
        assert any(e.kind == EntityKind.ADDRESS and e.value == "192.168.1.10" for e in entities)

    def test_extracts_host_as_host(self):
        entities = extract_entities(_evidence())
        assert any(e.kind == EntityKind.HOST and e.value == "web-server-01" for e in entities)

    def test_extracts_user(self):
        entities = extract_entities(_evidence())
        assert any(e.kind == EntityKind.USER and e.value == "svc-account" for e in entities)

    def test_extracts_md5_as_indicator(self):
        entities = extract_entities(_evidence())
        assert any(e.kind == EntityKind.INDICATOR for e in entities)

    def test_deduplicates(self):
        ev = _evidence()
        ev["normalized_event"]["agent_ip"] = "10.0.0.5"
        ev["normalized_event"]["fields"]["srcip"] = "10.0.0.5"
        entities = extract_entities(ev)
        addrs = [e for e in entities if e.kind == EntityKind.ADDRESS and e.value == "10.0.0.5"]
        assert len(addrs) == 1

    def test_caps_at_max_indicators(self):
        ev = _evidence()
        ev["normalized_event"]["fields"].update(
            {
                "md5": "aaa",
                "sha1": "bbb",
                "sha256": "ccc",
                "hash": "ddd",
                "domain": "evil.com",
                "url": "http://evil.com/payload",
            }
        )
        entities = extract_entities(ev, max_indicators=3)
        assert len(entities) <= 3

    def test_missing_fields_yield_empty(self):
        entities = extract_entities({})
        assert entities == []

    def test_no_error_on_none_fields(self):
        ev = {"normalized_event": {"fields": None}}
        entities = extract_entities(ev)
        assert isinstance(entities, list)
