"""Unit tests: dashboard DTO validation — extra='forbid', null token preserved."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.domain.dashboard import (
    IncidentSummary,
    KpiSnapshot,
    LoginRequest,
    MemoryHit,
    OperatorSession,
    QueuePage,
    SpanView,
    TokenResponse,
    VolumeBucket,
)


class TestLoginRequest:
    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            LoginRequest(username="admin", password="s3cret", extra_field="boom")

    def test_valid_parses(self) -> None:
        req = LoginRequest(username="admin", password="s3cret")
        assert req.username == "admin"
        assert req.password.get_secret_value() == "s3cret"


class TestSpanView:
    def test_null_tokens_preserved_as_null(self) -> None:
        span = SpanView(
            span_id="abc",
            name="triage",
            kind="agent_step",
            status="ok",
            tokens_in=None,
            tokens_out=None,
        )
        assert span.tokens_in is None
        assert span.tokens_out is None

    def test_zero_is_not_coerced(self) -> None:
        span = SpanView(
            span_id="abc",
            name="triage",
            kind="agent_step",
            status="ok",
            tokens_in=0,
            tokens_out=0,
        )
        assert span.tokens_in == 0
        assert span.tokens_out == 0


class TestMemoryHit:
    def test_rate_null_when_enriched_zero(self) -> None:
        hit = MemoryHit(enriched=0, hits=0, rate=None)
        assert hit.rate is None

    def test_rate_float_when_enriched_nonzero(self) -> None:
        hit = MemoryHit(enriched=40, hits=27, rate=0.675)
        assert hit.rate == pytest.approx(0.675)


class TestIncidentSummary:
    def test_is_awaiting_approval_derived(self) -> None:
        now = datetime.now(UTC)
        s = IncidentSummary(
            id=uuid.uuid4(),
            status="awaiting_approval",
            severity="high",
            source="wazuh",
            is_awaiting_approval=True,
            created_at=now,
            updated_at=now,
        )
        assert s.is_awaiting_approval is True


class TestQueuePage:
    def test_valid_page(self) -> None:
        now = datetime.now(UTC)
        page = QueuePage(
            items=[
                IncidentSummary(
                    id=uuid.uuid4(),
                    status="awaiting_approval",
                    severity="high",
                    source="wazuh",
                    is_awaiting_approval=True,
                    created_at=now,
                    updated_at=now,
                )
            ],
            total=1,
            limit=50,
            offset=0,
            view="active",
            applied_filters={"status": ["awaiting_approval"]},
        )
        assert page.total == 1
        assert page.view == "active"
