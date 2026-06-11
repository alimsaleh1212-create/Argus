# Phase 1 — Data Model: Response & Remediation Agent (#10)

New pure Pydantic v2 types in `domain/response.py` (no outward imports — isolated like `domain/triage.py` /
`domain/enrichment.py`), one typed settings block in `infra/config.py`, and **two new Postgres tables**
(migration **0006**). The `incidents` table is **unchanged** (new disposition values are plain text).

---

## 1. Enums — `domain/response.py` (NEW)

```python
class ActionType(StrEnum):
    # auto-execute (low-risk, reversible) — default allowlist
    ADD_TO_WATCHLIST = "add_to_watchlist"
    OPEN_TICKET = "open_ticket"
    ENRICH_AND_TAG = "enrich_and_tag"
    # approval-required (destructive, irreversible)
    ISOLATE_HOST = "isolate_host"
    DISABLE_USER = "disable_user"
    BLOCK_IP = "block_ip"

class RiskClass(StrEnum):
    AUTO = "auto"                 # low-risk reversible → execute now
    APPROVAL_REQUIRED = "approval_required"  # destructive → HITL

class ActionStatus(StrEnum):
    APPLIED = "applied"           # executor dispatched successfully (NOT "threat eliminated")
    FAILED = "failed"             # executor failed
    NOT_EXECUTED = "not_executed" # rejected / expired / skipped

class VerificationVerdict(StrEnum):   # RESERVED for §v2c (T2) — unused in v1
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REGRESSED = "regressed"

class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
```

---

## 2. `RemediationAction` (model) — `domain/response.py` (NEW)

One executable unit; carries its policy classification and (after execution) its result.

| Field | Type | Rule | Notes |
|-------|------|------|-------|
| `type` | `ActionType` | required, in-catalog | unknown type → non-executable (FR-005). |
| `target` | `str` | required | host / user / IP / indicator (the real value; redacted only when surfaced). |
| `params` | `dict[str, Any]` | default `{}` | action parameters. |
| `risk` | `RiskClass` | required | set by the **pure** policy, not the LLM (FR-004). |
| `idempotency_key` | `str` | required | `f"{incident_id}:{plan_id}:{type}:{target}"` (RD6). |

---

## 3. `ActionResult` (model) — `domain/response.py` (NEW)

The executor outcome — the **execution result**, never an efficacy claim (FR-020).

| Field | Type | Rule | Notes |
|-------|------|------|-------|
| `type` | `ActionType` | required | which action. |
| `target` | `str` | required | the action target. |
| `status` | `ActionStatus` | required | `applied` / `failed` / `not_executed`. |
| `detail` | `str` | default `""` | executor message (redacted on surface). |
| `verification` | `VerificationVerdict \| None` | default `None` | **RESERVED §v2c** — always `None` in v1. |

---

## 4. `RemediationPlan` (model) — `domain/response.py` (NEW)

What the handler produces; mirrors `EnrichmentReport`'s validation posture (fail-closed on invalid → ESCALATE).

| Field | Type | Rule | Notes |
|-------|------|------|-------|
| `plan_id` | `str` | required | stable id (uuid4 hex) used in idempotency keys + the approval record. |
| `playbook_id` | `str` | required, in-catalog | the selected playbook. |
| `actions` | `list[RemediationAction]` | `min_length≥1` | ordered; each classified `auto`/`approval_required`. |
| `rationale` | `str` | `min_length=1` | evidence-cited selection rationale (FR-015). |
| `selected_by` | `Literal["deterministic","llm"]` | required | provenance of the selection (RD1; observability). |

```python
class RemediationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan_id: str
    playbook_id: str
    actions: list[RemediationAction] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    selected_by: Literal["deterministic", "llm"]
```

**Convenience:** `plan.has_approval_required -> bool` (any action `risk == APPROVAL_REQUIRED`) selects the
park-vs-resolve branch.

---

## 5. `ActionExecutor` Protocol — `domain/response.py` (NEW)

```python
class ActionExecutor(Protocol):
    async def execute(self, action: RemediationAction) -> ActionResult: ...
```

The mock registry (`infra/executors.py`) supplies one executor per `ActionType`. **Injected into the response
stage only** — the structural action-tool boundary (Constitution III / RD9). Real connectors are a drop-in.

---

## 6. `ResponseSettings` (settings) — `infra/config.py` (EXTEND)

Mirrors `EnrichmentSettings`; `extra="forbid"`; registered as the `"response"` section, added as
`Settings.response`, and `"response"` added to `_KNOWN_SENTINEL_SECTIONS`.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `auto_execute_actions` | `list[ActionType]` | `[add_to_watchlist, open_ticket, enrich_and_tag]` | the **allowlist**; everything else → approval-required (**default-deny**, FR-004). |
| `select_min_confidence` | `float ∈ [0,1]` | `0.6` | below → ESCALATE when the LLM path is taken (FR-013). |
| `approval_timeout_s` | `int > 0` | `1800` | pending-approval deadline (FR-009). |
| `sweep_interval_s` | `int > 0` | `60` | timeout-sweeper cadence (RD7). |
| `catalog_dir` | `str` | `backend/data/playbooks` | config-backed playbook catalog (RD10). |
| `max_output_tokens` | `int > 0` | `768` | output cap for the (ambiguous-only) LLM call. |
| `temperature` | `float ≥ 0` | `0.0` | deterministic reasoning. |
| `prompt_version` | `str` | `"v1"` | pins the system prompt. |

---

## 7. Disposition vocabulary (in `services/supervisor.py`) — map + delta

| Spec disposition (Q2) | Constant | Value | Status |
|-----------------------|----------|-------|--------|
| auto path | `DISP_AUTO_REMEDIATED` | `auto_remediated` | exists |
| approved | `DISP_REMEDIATED` | `remediated` | **NEW** |
| rejected | `DISP_REJECTED_BY_HUMAN` | `rejected_by_human` | exists |
| timeout | `DISP_APPROVAL_EXPIRED` | `approval_expired` | **NEW** |
| fail-closed | `DISP_ESCALATED_RESPONSE` | `escalated_response` | exists |
| (reserved §v2c) | `DISP_REMEDIATION_UNVERIFIED` | `remediation_unverified` | **NEW, unused in v1** |

**Edge change:** `(RESPONDING, StageOutcome.RESOLVED)` table disposition `auto_remediated` → `None` so the
handler-proposed disposition passes through (RD8). **Edge add:** `expire_incident` drives
`AWAITING_APPROVAL → ESCALATED` with `approval_expired` (RD7). The reject edge already exists in
`resume_incident` (`AWAITING_APPROVAL → RESOLVED`, `rejected_by_human`).

---

## 8. New tables — migration `0006_response_remediation.py` (revises `0005`)

### `approval_requests`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT` PK autoincr | approval id (the `{id}` in the API path). |
| `incident_id` | `UUID` FK → `incidents(id)` | the parked incident. |
| `plan_id` | `TEXT` | links to the `RemediationPlan`. |
| `pending_actions` | `JSONB` | the approval-required `RemediationAction`s. |
| `rationale` | `TEXT` | selection rationale. |
| `status` | `TEXT` | `pending`/`approved`/`rejected`/`expired` (guarded transitions). |
| `deadline_at` | `TIMESTAMPTZ` | `created_at + approval_timeout_s` (timeout sweeper queries this). |
| `decided_by` | `TEXT` null | actor on resolution (admin id / `timeout`). |
| `decided_at` | `TIMESTAMPTZ` null | resolution time. |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `server_default now()`. |

Indexes: `ix_approval_requests_incident_id`, `ix_approval_requests_status`, partial index on
`(status, deadline_at)` for the sweeper. One **pending** row per incident (v1 parks once — partial unique on
`incident_id WHERE status='pending'`).

### `audit_log` (append-only)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT` PK autoincr | audit row id. |
| `incident_id` | `UUID` FK → `incidents(id)` | the incident. |
| `actor` | `TEXT` | `response_agent` / `<admin id>` / `timeout`. |
| `action` | `TEXT` | `ActionType` (or `approval_rejected` / `approval_expired`). |
| `target` | `TEXT` null | the action target (redacted only when surfaced). |
| `outcome` | `TEXT` | `ActionStatus` (`applied`/`failed`/`not_executed`). |
| `idempotency_key` | `TEXT` null | unique with `outcome='applied'` → blocks double-execution (RD6). |
| `created_at` | `TIMESTAMPTZ` | `server_default now()`. |

Index: `ix_audit_log_incident_id`; **unique** `uq_audit_applied_idem` on `idempotency_key WHERE
outcome='applied'`. No `updated_at` (append-only).

---

## 9. `evidence_patch` shape (consumed by the supervisor, single writer)

The response handler returns a `StageResult` (contract owned by #7); the supervisor JSONB-merges
`evidence_patch` into `incidents.evidence` under `"response"` (same mechanism as `"triage"`/`"enrichment"`), so
the dashboard (#12) can render what was done.

```python
StageResult(
    stage=StageName.RESPONSE,
    outcome=<RESOLVED | NEEDS_APPROVAL | ESCALATE>,
    tokens_consumed=<int>,                      # 0 when selection was deterministic / on resume (SC-005)
    disposition=<"auto_remediated" | "remediated" | None>,  # passes through on the RESOLVED edge (RD8)
    evidence_patch={"response": {
        "plan": plan.model_dump(mode="json"),
        "results": [r.model_dump(mode="json") for r in results],   # ActionResults of executed/parked actions
        "approval_id": <int | None>,            # set when parked
    }},
    note="playbook=… selected_by=…: <rationale>"[:200],
)
```

On `NEEDS_APPROVAL` the handler has already written the `approval_requests` row (status `pending`) and any
auto-action `audit_log` rows before returning; the supervisor then performs the park transition (single writer
of `incidents`).

---

## 10. State / lifecycle

```
RESPONDING ──(auto-only plan)──────────────► RESOLVED            disposition=auto_remediated
RESPONDING ──(has destructive)─────────────► AWAITING_APPROVAL   disposition=awaiting_approval_destructive
AWAITING_APPROVAL ──approve──► RESPONDING ──(execute approved)──► RESOLVED   disposition=remediated
AWAITING_APPROVAL ──reject───────────────────────────────────────► RESOLVED   disposition=rejected_by_human
AWAITING_APPROVAL ──timeout (sweeper)────────────────────────────► ESCALATED  disposition=approval_expired
RESPONDING ──(no confident playbook / preconditions fail / fail-closed)──► ESCALATED  disposition=escalated_response
```

Approval-record lifecycle: `pending → approved | rejected | expired` (guarded; first decision wins). Audit rows
are written for **every** executed action (auto or approved) and for non-executed terminal outcomes
(rejected/expired) — completing the trail (FR-010).
