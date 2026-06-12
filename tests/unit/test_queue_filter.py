"""Unit tests for IncidentRepository queue filter/sort/paginate logic.

Tests the _build_queue_where helper and the _ALLOWED_SORTS / _ACTIVE_STATUSES
constants without touching the database.
"""

from __future__ import annotations

import pytest

from backend.repositories.incidents import (
    _ACTIVE_STATUSES,
    _ALLOWED_SORTS,
    _TERMINAL_STATUSES,
    _build_queue_where,
)


def _extract_in_values(where: str, params: dict, prefix: str) -> set[str]:
    """Extract all values whose parameter key starts with prefix."""
    return {v for k, v in params.items() if k.startswith(prefix)}


class TestViewFilter:
    def test_active_view_excludes_terminal_statuses(self) -> None:
        where, params = _build_queue_where(view="active", statuses=None, severities=None)
        assert "WHERE" in where
        values = _extract_in_values(where, params, "vs")
        assert values == _ACTIVE_STATUSES
        assert not values.intersection(_TERMINAL_STATUSES)

    def test_resolved_view_includes_only_terminal(self) -> None:
        where, params = _build_queue_where(view="resolved", statuses=None, severities=None)
        assert "WHERE" in where
        values = _extract_in_values(where, params, "vt")
        assert values == _TERMINAL_STATUSES

    def test_all_view_has_no_status_clause(self) -> None:
        where, params = _build_queue_where(view="all", statuses=None, severities=None)
        assert where == ""
        assert params == {}

    def test_active_statuses_include_awaiting_approval(self) -> None:
        assert "awaiting_approval" in _ACTIVE_STATUSES

    def test_active_statuses_exclude_resolved_escalated_failed(self) -> None:
        for terminal in ("resolved", "escalated", "failed"):
            assert terminal not in _ACTIVE_STATUSES


class TestStatusFilter:
    def test_statuses_filter_added_as_extra_clause(self) -> None:
        where, params = _build_queue_where(
            view="all", statuses=["awaiting_approval"], severities=None
        )
        assert "WHERE" in where
        status_values = _extract_in_values(where, params, "st")
        assert "awaiting_approval" in status_values

    def test_multiple_statuses(self) -> None:
        where, params = _build_queue_where(
            view="all", statuses=["triaging", "enriching"], severities=None
        )
        status_values = _extract_in_values(where, params, "st")
        assert status_values == {"triaging", "enriching"}

    def test_none_statuses_adds_no_extra_clause(self) -> None:
        where_all, params_all = _build_queue_where(view="all", statuses=None, severities=None)
        where_filtered, _ = _build_queue_where(
            view="all", statuses=["awaiting_approval"], severities=None
        )
        assert "st" not in str(params_all)
        assert "st0" in str(dict(
            _build_queue_where(view="all", statuses=["awaiting_approval"], severities=None)[1]
        ))


class TestSeverityFilter:
    def test_severity_filter(self) -> None:
        where, params = _build_queue_where(
            view="all", statuses=None, severities=["high", "critical"]
        )
        sev_values = _extract_in_values(where, params, "sv")
        assert sev_values == {"high", "critical"}

    def test_none_severities_no_clause(self) -> None:
        _, params = _build_queue_where(view="all", statuses=None, severities=None)
        assert not any(k.startswith("sv") for k in params)


class TestSortMap:
    def test_default_sort_descending(self) -> None:
        assert _ALLOWED_SORTS["-updated_at"] == "updated_at DESC"

    def test_ascending_sort(self) -> None:
        assert _ALLOWED_SORTS["updated_at"] == "updated_at ASC"

    def test_created_at_sort(self) -> None:
        assert "created_at DESC" in _ALLOWED_SORTS.values()
        assert "created_at ASC" in _ALLOWED_SORTS.values()

    def test_unknown_sort_key_not_present(self) -> None:
        assert "bad_field" not in _ALLOWED_SORTS


class TestCombinedFilters:
    def test_active_with_status_and_severity(self) -> None:
        where, params = _build_queue_where(
            view="active", statuses=["awaiting_approval"], severities=["high"]
        )
        assert where.count("AND") >= 2
        assert _extract_in_values(where, params, "vs")  # active statuses
        assert _extract_in_values(where, params, "st")  # status filter
        assert _extract_in_values(where, params, "sv")  # severity filter
