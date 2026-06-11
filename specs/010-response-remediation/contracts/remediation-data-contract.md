# Contract — Remediation Persistence (migration 0006 + repositories)

The two new tables this component owns, and the repository methods that are the **only** code touching them.
All SQL lives in `repositories/` (layering). The `incidents` table is unchanged; the supervisor remains its
single writer.

## Migration `0006_response_remediation.py`

```python
revision = "0006"
down_revision = "0005"
```

Creates `approval_requests` and `audit_log` (DDL in [data-model.md](../data-model.md) §8). Reversible:
`downgrade()` drops indexes then both tables. Idempotent constraints:
- `approval_requests`: partial unique on `incident_id WHERE status='pending'` (v1 parks once).
- `audit_log`: partial unique `uq_audit_applied_idem` on `idempotency_key WHERE outcome='applied'` (blocks
  double-execution — RD6).

## `ApprovalRepository` (`repositories/approvals.py`)

| Method | Signature | Contract |
|--------|-----------|----------|
| `create_pending` | `(incident_id, plan_id, pending_actions, rationale, deadline_at) -> int` | insert one `pending` row; returns approval id. Conflict on the partial-unique → the incident is already parked (idempotent). |
| `get` | `(approval_id) -> ApprovalRecord \| None` | read one. |
| `get_approved_pending_for` | `(incident_id) -> ApprovalRecord \| None` | the **Pass-B** discriminator (RD3): an `approved` (not yet consumed) record for this incident. |
| `resolve` | `(approval_id, *, to: ApprovalStatus, decided_by) -> bool` | guarded `pending → approved\|rejected\|expired`; `RETURNING` → `True` iff the guard held (first decision wins — RD6). |
| `list_pending_expired` | `(now) -> list[ApprovalRecord]` | `status='pending' AND deadline_at < now` (the timeout sweeper — RD7). |

## `AuditRepository` (`repositories/audit.py`)

| Method | Signature | Contract |
|--------|-----------|----------|
| `append` | `(incident_id, actor, action, target, outcome, idempotency_key=None) -> bool` | append-only insert; on the `applied` partial-unique conflict returns `False` (already executed — idempotent skip, RD6). Never updates/deletes. |
| `list_for_incident` | `(incident_id) -> list[AuditRow]` | the trail for the dashboard (#12), redacted on surface. |

## Invariants

- **Every** executed action (auto or approved) → exactly one `audit_log` row (SC-001).
- Non-executed terminal outcomes (rejected, expired) → an `audit_log` row (`outcome=not_executed`) so the trail
  is complete (FR-010).
- `audit_log` is append-only (no update/delete methods exist).
- Action tools (executors) and these repositories are injected into the **response stage only** + the approvals
  endpoint resume path; no other stage/route writes them.
