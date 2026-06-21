"""Unit tests — T009: anomaly window building + featurize (SPEC-ml-anomaly-detector #17).

Covers:
- build_windows groups by entity and bins by event_time (no wall-clock).
- featurize produces stable feature order; missing → 0.0 / extra → dropped.
- Malformed replay records are skipped (FR-011).
"""

from __future__ import annotations

from datetime import datetime

from backend.domain.anomaly import EntityActivityWindow, RawLogEvent, parse_window
from backend.services.anomaly import build_windows, featurize, load_replay_events


def _event(entity_id: str, hour: int, fields: dict | None = None) -> RawLogEvent:
    return RawLogEvent(
        event_time=datetime(2024, 1, 1, hour, 0, 0),
        entity_id=entity_id,
        fields=fields or {"type": "logon", "pc": "pc001"},
    )


class TestBuildWindows:
    def test_groups_by_entity(self) -> None:
        events = [
            _event("alice", 9),
            _event("bob", 9),
            _event("alice", 10),
        ]
        windows = build_windows(events, parse_window("1d"))
        assert len(windows) == 2
        assert {w.entity_id for w in windows} == {"alice", "bob"}
        alice = [w for w in windows if w.entity_id == "alice"][0]
        assert alice.raw_event_count == 2

    def test_bins_by_event_time_not_wall_clock(self) -> None:
        events = [
            _event("alice", 9),
            _event("alice", 23),  # same day window if first event is 9am
            RawLogEvent(
                event_time=datetime(2024, 1, 2, 10, 0, 0),
                entity_id="alice",
                fields={"type": "logon", "pc": "pc001"},
            ),  # next day (>24h from first event)
        ]
        windows = build_windows(events, parse_window("1d"))
        assert len(windows) == 2
        assert windows[0].raw_event_count == 2
        assert windows[1].raw_event_count == 1

    def test_feature_counts(self) -> None:
        events = [
            RawLogEvent(
                event_time=datetime(2024, 1, 1, 8),
                entity_id="alice",
                fields={"type": "logon", "pc": "pc001"},
            ),
            RawLogEvent(
                event_time=datetime(2024, 1, 1, 9),
                entity_id="alice",
                fields={"type": "device", "pc": "pc001"},
            ),
            RawLogEvent(
                event_time=datetime(2024, 1, 1, 10),
                entity_id="alice",
                fields={"type": "file", "pc": "pc001", "to_removable": 1},
            ),
            RawLogEvent(
                event_time=datetime(2024, 1, 1, 11),
                entity_id="alice",
                fields={"type": "email", "pc": "pc001", "to_external": 1},
            ),
            RawLogEvent(
                event_time=datetime(2024, 1, 1, 12),
                entity_id="alice",
                fields={"type": "http", "pc": "pc001", "flagged": 1},
            ),
        ]
        windows = build_windows(events, parse_window("1d"))
        assert len(windows) == 1
        f = windows[0].features
        assert f["logon_count"] == 1.0
        assert f["device_count"] == 1.0
        assert f["file_count"] == 1.0
        assert f["email_count"] == 1.0
        assert f["http_count"] == 1.0
        assert f["distinct_pc"] == 1.0
        assert f["removable_copy_count"] == 1.0
        assert f["external_email_count"] == 1.0
        assert f["flagged_http_count"] == 1.0


class TestFeaturize:
    def test_feature_order_stability(self) -> None:
        window = EntityActivityWindow(
            entity_id="alice",
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 1, 2),
            features={"a": 1.0, "b": 2.0, "c": 3.0},
        )
        spec = ["c", "a", "b"]
        vec = featurize(window, spec)
        assert vec.values == [3.0, 1.0, 2.0]
        assert vec.entity_id == "alice"

    def test_missing_feature_becomes_zero(self) -> None:
        window = EntityActivityWindow(
            entity_id="alice",
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 1, 2),
            features={"a": 1.0},
        )
        vec = featurize(window, ["a", "missing"])
        assert vec.values == [1.0, 0.0]

    def test_extra_feature_dropped(self) -> None:
        window = EntityActivityWindow(
            entity_id="alice",
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 1, 2),
            features={"a": 1.0, "extra": 99.0},
        )
        vec = featurize(window, ["a"])
        assert vec.values == [1.0]


class TestLoadReplayEvents:
    def test_skip_malformed_jsonl_line(self, tmp_path) -> None:
        p = tmp_path / "replay.jsonl"
        p.write_text(
            '{"event_time": "2024-01-01T09:00:00", "entity_id": "alice", "fields": {}}\n'
            "not valid json\n"
            '{"event_time": "2024-01-01T10:00:00", "entity_id": "bob", "fields": {}}\n'
        )
        events = load_replay_events(p)
        assert len(events) == 2
        assert events[0].entity_id == "alice"
        assert events[1].entity_id == "bob"

    def test_skip_record_missing_entity_id(self, tmp_path) -> None:
        p = tmp_path / "replay.jsonl"
        p.write_text(
            '{"event_time": "2024-01-01T09:00:00", "entity_id": "alice", "fields": {}}\n'
            '{"event_time": "2024-01-01T10:00:00", "fields": {}}\n'
        )
        events = load_replay_events(p)
        assert len(events) == 1

    def test_skip_record_missing_event_time(self, tmp_path) -> None:
        p = tmp_path / "replay.jsonl"
        p.write_text(
            '{"event_time": "2024-01-01T09:00:00", "entity_id": "alice", "fields": {}}\n'
            '{"entity_id": "bob", "fields": {}}\n'
        )
        events = load_replay_events(p)
        assert len(events) == 1
