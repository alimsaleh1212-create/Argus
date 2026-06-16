# Phase 1 — Data Model: Remediation Verification (#15)

All new types are **pure Pydantic v2** in `domain/response.py` (extending the reserved §v2c surface) and one
new member in `domain/pipeline.py`. **No new table, no migration for M1.** The verdict rides the existing
`incidents.evidence` JSONB via the single-writer evidence-patch. `extra="forbid"` on every model; all text
fields are already redacted before construction (Constitution III).

---

## 1. Enums

### `domain/response.py` — activate the reserved verdict + add probe state

```python
class VerificationVerdict(StrEnum):   # reserved in #10 → ACTIVATED here
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REGRESSED = "regressed"

class ProbeState(StrEnum):            # NEW — observed executor post-state
    EXPECTED = "expected"             # control reports the intended post-state
    UNEXPECTED = "unexpected"         # control reports the threat persists / action not in effect
    INCONCLUSIVE = "inconclusive"     # control could not be read (→ fail-closed unverified)
```

Worst-case ordering used by aggregation: `REGRESSED > UNVERIFIED > VERIFIED`.

### `domain/pipeline.py` — new stage outcome

```python
class StageOutcome(StrEnum):
    RESOLVED = "resolved"
    ADVANCE = "advance"
    NEEDS_APPROVAL = "needs_approval"
    ESCALATE = "escalate"
    UNVERIFIED = "unverified"          # NEW — remediation applied but not confirmed → escalate
```

---

## 2. `ProbeResult` — `domain/response.py` (NEW)

One executor's observed post-state for one action (read-only; never an efficacy *claim*).

| Field | Type | Rule | Notes |
|-------|------|------|-------|
| `type` | `ActionType` | required | which action was probed. |
| `target` | `str` | required | the probed target (redacted only when surfaced). |
| `state` | `ProbeState` | required | `expected` / `unexpected` / `inconclusive`. |
| `detail` | `str` | default `""` | probe message (redacted on surface). |

---

## 3. `ActionExecutor` protocol — `domain/response.py` (EXTEND)

```python
class ActionExecutor(Protocol):
    async def execute(self, action: RemediationAction) -> ActionResult: ...
    async def probe(self, action: RemediationAction) -> ProbeResult: ...   # NEW (read-only)
```

Mock executors (`infra/executors.py`) implement `probe()` returning `ProbeState.EXPECTED`; a
`build_regressed_executors(*types)` / `build_inconclusive_executors(*types)` test helper returns
`UNEXPECTED` / `INCONCLUSIVE`. Real EDR/firewall connectors implement the same method — a drop-in (RD9
parity with `execute()`).

---

## 4. `IndicatorRecheck` — `domain/response.py` (NEW)

The current time-valid reputation for one applied target (the "real data path").

| Field | Type | Rule | Notes |
|-------|------|------|-------|
| `target` | `str` | required | the re-checked indicator/entity. |
| `intel_verdict` | `Literal["benign","malicious","suspicious","unknown"]` | required | from `ThreatIntelClient.lookup` (`unknown` when disabled/failed). |
| `fact_value` | `str \| None` | default `None` | current reputation fact value from `MemoryStore.query_fact(...).fact.value`. |
| `fact_is_current` | `bool` | default `False` | `FactState.is_current` — distinguishes current vs superseded (Constitution VI). |

---

## 5. `VerificationSignals` & `VerificationRecord` — `domain/response.py` (NEW)

```python
class VerificationSignals(BaseModel):     # the inputs to the pure verdict fn, per action
    model_config = ConfigDict(extra="forbid", frozen=True)
    probe: ProbeResult
    recheck: IndicatorRecheck | None = None    # None when target has no re-checkable indicator

class VerificationRecord(BaseModel):      # what rides evidence["response"]["verification"]
    model_config = ConfigDict(extra="forbid", frozen=True)
    verdict: VerificationVerdict                # incident-level worst-case
    per_action: list[ActionResult]              # each ActionResult.verification now populated
    signals: list[VerificationSignals]          # redacted evidence considered (auditability, FR-010)
    used_llm_tiebreak: bool = False             # provenance (Constitution IV)
    rationale: str = Field(min_length=1)        # plain-language, evidence-cited
```

`ActionResult.verification` (reserved `None` in #10) is **populated** here with the per-action verdict.

---

## 6. Pure verdict function — `domain/response.py` (NEW)

```python
def decide_action_verdict(signals: VerificationSignals, cfg: VerdictRules) -> VerificationVerdict: ...
def decide_verdict(per_action: list[VerificationSignals], cfg: VerdictRules) -> VerificationVerdict:
    """Worst-case aggregate: REGRESSED > UNVERIFIED > VERIFIED."""
```

Rules (config-backed `verify_regressed_verdicts`, default `{"malicious","suspicious"}`):

| Probe `state` | Indicator (intel/fact current) | Per-action verdict |
|---------------|-------------------------------|--------------------|
| `expected` | clean/benign (or no indicator) | `verified` |
| `unexpected` | any | `regressed` |
| any | intel ∈ regressed-set **or** current fact ∈ regressed-set | `regressed` |
| `inconclusive` | unknown/absent | `unverified` |
| `expected` | `malicious`/`suspicious` (**genuine conflict**) | `regressed` (deterministic worse-case); LLM tiebreak only if `verify_llm_tiebreak` and conflict |

Pure, no I/O, fully unit-testable; the LLM tiebreak (if enabled) is the **only** path that consumes tokens.

---

## 7. `ResponseSettings` (settings) — `infra/config.py` (EXTEND)

Reuses the existing `"response"` section (no new section registration).

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `verify_remediation` | `bool` | `True` | master switch for the verification tail (off → legacy behaviour). |
| `verify_regressed_verdicts` | `list[str]` | `["malicious","suspicious"]` | intel/fact values that mean `regressed`. |
| `verify_llm_tiebreak` | `bool` | `False` | allow a conflict-only LLM tiebreak (Constitution IV; default deterministic). |
| `dwell_window_s` | `int > 0` | `900` | **M2-reserved** — monitoring-loop dwell window (gated on #14). |

---

## 8. FSM delta — `services/supervisor.py`

Add one transition (the `DISP_REMEDIATION_UNVERIFIED` constant already exists):

```python
(IncidentStatus.RESPONDING, StageOutcome.UNVERIFIED): (
    IncidentStatus.ESCALATED, DISP_REMEDIATION_UNVERIFIED,
),
```

`verified` keeps the existing `(RESPONDING, RESOLVED) → (RESOLVED, None)` edge (handler disposition
`auto_remediated`/`remediated` passes through, RD8). **M2-reserved** (not built now): an
`IncidentStatus.VERIFYING` text status + park/resume edges reusing the `awaiting_approval` machinery + a
dwell sweeper analogous to `expire_incident`.

---

## 9. `evidence_patch` shape (consumed by the supervisor, single writer)

The response handler returns its existing `StageResult`, now with the verification record merged under the
`"response"` slice and the outcome chosen by the verdict:

```python
StageResult(
    stage=StageName.RESPONSE,
    outcome=<RESOLVED if verified else UNVERIFIED>,
    tokens_consumed=<0 unless the conflict-only tiebreak ran>,
    disposition=<"auto_remediated" | "remediated" | None>,   # None on UNVERIFIED → table supplies it
    evidence_patch={"response": {
        "plan": ...,                                   # (pass A) unchanged
        "results": [r.model_dump() for r in results],  # each ActionResult.verification now set
        "approval_id": ...,
        "verification": verification_record.model_dump(mode="json"),   # NEW
    }},
    note="playbook=… verdict=<verified|unverified|regressed>: <rationale>"[:200],
)
```

On `UNVERIFIED` the handler MAY append one `audit_log` row (existing table) before returning:
`actor="verifier"`, `action="verification"`, `target=<indicator|None>`, `outcome=<verdict>`,
`idempotency_key=None` — completing the audit trail without a new table.

---

## 10. State / lifecycle (M1)

```
RESPONDING ─(auto-only plan, verdict=verified)────────► RESOLVED    disposition=auto_remediated
RESPONDING ─(approved plan executed, verdict=verified)► RESOLVED    disposition=remediated
RESPONDING ─(verdict=unverified | regressed)──────────► ESCALATED   disposition=remediation_unverified
RESPONDING ─(no confident playbook / fail-closed)─────► ESCALATED   disposition=escalated_response   (unchanged)
```

Read-side (#12, read-only): `remediation_unverified` already surfaces via `IncidentSummary.disposition`
(queue) and `IncidentDetailView.evidence` (trace renders the verification record). A coarse
`verified/unverified/regressed` KPI derives from the disposition split for M1; a finer per-verdict KPI is an
optional enhancement.
