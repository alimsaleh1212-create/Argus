"""Pure DTOs for the evaluation harness (SPEC-eval #13).

Domain-isolated: no I/O, no infra imports. Pydantic v2, extra="forbid".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GateProviderDim(StrEnum):
    provider_independent = "provider_independent"
    per_provider = "per_provider"


class GateKind(StrEnum):
    required = "required"
    reported_only = "reported_only"


class RunMode(StrEnum):
    per_pr = "per_pr"
    nightly = "nightly"
    freeze = "freeze"


class FreezeVerdict(StrEnum):
    certifiable = "certifiable"
    not_certifiable = "not_certifiable"
    incomplete = "incomplete"


class RationaleLabel(StrEnum):
    grounded = "grounded"
    partially_grounded = "partially_grounded"
    ungrounded = "ungrounded"


class GateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    kind: GateKind
    provider_dim: GateProviderDim
    threshold: dict[str, Any]
    providers: list[str] = Field(default_factory=list)


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: str
    kind: GateKind
    provider: str | None = None
    score: float | dict[str, float]
    threshold: dict[str, Any]
    passed: bool | None
    blocking: bool
    evidence: str


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    gates: list[GateResult]


class RationaleScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    producer_provider: str
    grounded_rate: float
    judge_human_agreement: float
    n: int


class RationaleJudgeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_context: str
    rationale_text: str
    human_label: RationaleLabel
    cites_supplied_evidence: bool


class EvalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    run_id: str
    run_mode: RunMode
    commit_sha: str
    git_tag: str | None = None
    created_at: datetime
    providers: list[str]
    gate_results: list[GateResult]
    rationale: list[RationaleScore] | None = None
    verdict: FreezeVerdict
    summary: dict[str, int]
