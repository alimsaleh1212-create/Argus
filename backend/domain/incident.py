"""Incident domain types — the single contract imported by #7/#8/#12.

Pure Pydantic v2; no outward imports (domain-isolation import-linter contract).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IncidentStatus(StrEnum):
    # --- from #4 (ingestion) ---
    RECEIVED = "received"
    GROUNDING = "grounding"
    GROUNDED = "grounded"
    FAILED = "failed"
    # --- added by #7 (supervisor) ---
    TRIAGING = "triaging"
    ENRICHING = "enriching"
    RESPONDING = "responding"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WazuhRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    level: int | None = None
    id: str | None = None
    description: str | None = None
    groups: list[str] = Field(default_factory=list)


class WazuhAgent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str | None = None
    ip: str | None = None


class WazuhAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    timestamp: str | None = None
    rule: WazuhRule
    agent: WazuhAgent | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    full_log: str | None = None


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rule_id: str | None = None
    rule_level: int | None = None
    rule_description: str | None = None
    rule_groups: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    agent_name: str | None = None
    agent_ip: str | None = None
    event_time: datetime | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: str
    severity: Severity
    normalized_event: NormalizedEvent
    summary: str
    retrieved_context: list[dict[str, Any]] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    feedback: dict[str, Any] | None = None


class Incident(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    status: IncidentStatus
    severity: Severity
    correlation_id: str
    dedup_fingerprint: str
    source: str
    raw_alert: dict[str, Any]
    normalized_event: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    disposition: str | None = None
    attempts: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None


class IngestResult(BaseModel):
    incident_id: uuid.UUID
    status: IncidentStatus
    deduplicated: bool
