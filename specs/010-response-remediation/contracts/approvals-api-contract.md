# Contract — Approvals API (`routers/approvals.py`)

The backend interface this component **owns** (clarification Q3): it receives the human approve/reject
decision, resolves the pending approval, and triggers the supervisor resume. The dashboard (#12) builds only
the React UI that calls these endpoints. Prefix `/approvals` (already reserved in #1). All responses redacted
(#2). v1 actor = single `admin` (auth layer is #12; until then a configured/stub admin identity supplies
`decided_by`).

---

## `GET /approvals`

List pending approvals for the operator queue (the data #12 renders).

**Query:** `status` (default `pending`), `limit` (default 50).

**200 →**
```json
{
  "approvals": [
    {
      "id": 42,
      "incident_id": "f1e2…",
      "plan_id": "9ab…",
      "pending_actions": [
        {"type": "isolate_host", "target": "<redacted-host>", "risk": "approval_required"}
      ],
      "rationale": "…evidence-cited…",
      "status": "pending",
      "deadline_at": "2026-06-10T12:30:00Z",
      "created_at": "2026-06-10T12:00:00Z"
    }
  ]
}
```

## `POST /approvals/{id}/decision`

Record a decision and drive the resume.

**Body:** `{"decision": "approve" | "reject", "note": "<optional>"}`

**Flow:**
1. Load the approval (`ApprovalRepository.get(id)`); `404` if missing.
2. If `status != pending` → `409 Conflict` (already decided/expired) — first decision wins (RD6); the late
   decision is a recorded no-op.
3. Resolve the approval (`status = approved|rejected`, `decided_by`, `decided_at`) — guarded
   `pending → …` update.
4. `supervisor.resume_incident(incident_id, decision, repo)`:
   - **approve** → `AWAITING_APPROVAL → RESPONDING`, then re-drive `run_incident` synchronously (the response
     stage executes the approved plan — RD3/RD4) → terminal `remediated`.
   - **reject** → `AWAITING_APPROVAL → RESOLVED` (`rejected_by_human`); write an `audit_log` row
     (`actor=<admin>`, `action=approval_rejected`, `outcome=not_executed`).
5. Return the resulting incident status + disposition.

**200 →**
```json
{"incident_id": "f1e2…", "decision": "approve", "status": "resolved", "disposition": "remediated"}
```

**Errors:** `404` (unknown approval), `409` (not pending — already approved/rejected/expired),
`422` (bad decision value).

## Idempotency & safety

- The `pending → approved|rejected` guard + the `advance_status(expected=AWAITING_APPROVAL)` guard make a
  duplicate POST a no-op (`409`), never a double execution (RD6 / SC-006).
- A decision arriving after the timeout sweeper already expired the approval → `409` (status `expired`); no
  action executes (FR-009).
- The endpoint never holds action tools itself — execution happens **only** inside the re-run response stage
  (Constitution III / RD3).

## Wiring

`approvals.py` depends on `get_supervisor`, `get_incident_repo`, and new `get_approval_repo` / `get_audit_repo`
FastAPI providers (session-scoped, mirroring `get_incident_repo`). Requires `SupervisorProvider` registered in
the API (RD4 — added to `main.py._bootstrap_providers`).
