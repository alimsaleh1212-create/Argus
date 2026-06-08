# Contract — Incident Schema (the cross-spec seam)

**Owner**: #4 `SPEC-ingestion` · **Consumers**: supervisor #7, triage #8, enrichment #9, response #10,
dashboard #12, eval #13 · **Module**: `backend/domain/incident.py`

This is the **one schema defined once and imported by the rest** (the ingestion→state-machine seam rule).
Every downstream spec imports these types; **no later spec re-declares the Incident**. Full field tables
in [data-model.md](../data-model.md); this file fixes the *contract surface and its rules*.

---

## Exported types (`backend/domain/incident.py`)

```python
IncidentStatus(StrEnum)   # received / grounding / grounded / failed  (extended by #7/#10)
Severity(StrEnum)         # low / medium / high / critical
WazuhAlert / WazuhRule / WazuhAgent   # untrusted inbound shapes (extra="ignore")
NormalizedEvent           # the Wazuh adapter's normalized facts
Evidence                  # the grounded evidence packet (inputs only — no triage decision)
Incident                  # the canonical object
IngestResult              # the webhook response body
```

Pure domain module: **no imports outside `domain/`** (enforced by `import-linter`). Pydantic v2 models.

---

## Ownership rules (the no-gap seam)

- **#4 owns**: the type definitions; the `received/grounding/grounded/failed` statuses and their
  transitions; the `NormalizedEvent` and `Evidence` (inputs) shapes; `raw_alert`, `dedup_fingerprint`,
  `source`, `attempts`, timestamps.
- **#4 must NOT define**: triage decision fields (real/noise, confidence, rationale — #8), enrichment
  correlation output (#9), playbook/approval/audit fields (#10), or lifecycle states it does not drive.
- **Later specs extend, never fork**: #7/#8/#10 add `IncidentStatus` values and append their own output
  sub-objects (e.g. `triage: TriageResult | None`) to `Incident` via new optional fields + migrations —
  the existing fields and their meanings are frozen.

> **Field-slice rule (for #7→agents)**: each agent spec declares exactly which `Incident` fields it reads
> and writes; the union of writes across triage/enrich/respond must partition the agent-owned fields — no
> field written by two agents. #4 seeds this by keeping its inputs (`raw_alert`, `normalized_event`,
> `evidence`) distinct from any decision output.

## Invariants (hold for every persisted Incident)

1. `id` and `correlation_id` are stable for the incident's life; `correlation_id` defaults to `str(id)`.
2. `raw_alert` is **always the redacted form** — the repository rejects writing an un-redacted alert
   (FR-004). No credential/PII appears unredacted in any JSONB column (SC-005).
3. `dedup_fingerprint` is non-empty and computed over **redacted** content (no secret in a dedup key).
4. `status` advances only along the owned transition graph; `grounded` and `failed` are terminal for #4.
5. `evidence`/`normalized_event` are `None` until the worker grounds the Incident; non-`None` once
   `status == grounded` (SC-007).

## Repository surface (`backend/repositories/incidents.py`)

The **only** module that touches the `incidents` table (layering: `services → repositories → infra`).

| Method | Purpose |
|--------|---------|
| `create(incident) -> Incident` | insert at intake (`received`) |
| `get(incident_id) -> Incident \| None` | load by id (worker, dashboard) |
| `get_by_fingerprint(fp) -> Incident \| None` | dedup-hit lookup to return the existing id |
| `claim_for_grounding(id) -> bool` | guarded `received → grounding` (atomic; `False` if already claimed) |
| `set_grounded(id, normalized_event, evidence, severity)` | write grounding output, `→ grounded` |
| `bump_attempt(id) -> int` | increment + return `attempts` (retry accounting) |
| `mark_failed(id, reason)` | `→ failed` (terminal); `reason` is redaction-safe |
| `list_non_terminal() -> list[Incident]` | startup recovery scan (`received`/`grounding`) |
