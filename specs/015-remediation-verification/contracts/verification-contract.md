# Contract: Remediation Verification (#15, M1)

The internal contracts the verification tail exposes/consumes. No new HTTP endpoint (the response stage is
worker-internal; the dashboard reads verification via the existing read DTOs). Real connectors are a
drop-in behind the same protocol.

---

## C1 — Executor probe (read-only observation)

```python
# domain/response.py
class ActionExecutor(Protocol):
    async def execute(self, action: RemediationAction) -> ActionResult: ...
    async def probe(self,  action: RemediationAction) -> ProbeResult: ...
```

- **Read-only.** `probe()` MUST NOT mutate the environment — it observes post-state only (Constitution III:
  no new write authority).
- **Never raises into the caller.** Any error → `ProbeResult(state=INCONCLUSIVE, detail=<redacted>)`
  (fail-closed, mirroring `intel.lookup`).
- **Contract-real.** The mock returns `EXPECTED`; a real EDR/firewall/control-plane status query implements
  the same signature with no change to `decide_verdict`. The spec states this honestly (mock now).

## C2 — Indicator re-check (the real data path, reused from enrichment)

For each applied action target (`action.target` → `EntityRef`):

```python
verdict: IntelVerdict = await intel.lookup(target, kind)            # #5 — Redis-cached, fail-closed
state:   FactState    = await memory.query_fact(entity, "reputation", as_of=None)  # #6 — current time-valid
```

- Both calls are wrapped best-effort (`_safe(...)` pattern); a missing retriever or any error yields the
  absent/`unknown` signal — never an exception (Constitution VI graceful degradation).
- `query_fact(as_of=None)` MUST return the **current** time-valid reputation; a superseded fact
  (`is_current=False`) MUST NOT be treated as current (Constitution VI).

## C3 — Pure verdict function (deterministic)

```python
def decide_verdict(per_action: list[VerificationSignals], cfg) -> VerificationVerdict
```

- Pure, no I/O, total over its inputs. Worst-case aggregate `REGRESSED > UNVERIFIED > VERIFIED`.
- The **only** token-consuming path is the optional conflict-only LLM tiebreak (`verify_llm_tiebreak`,
  default `False`); with it off, the function is fully deterministic and the eval gate is
  provider-independent.

## C4 — Handler outcome mapping (response tail)

| Verdict | `StageResult.outcome` | Disposition (final) |
|---------|-----------------------|---------------------|
| `verified` | `RESOLVED` | `auto_remediated` (pass A) / `remediated` (pass B) — unchanged pass-through |
| `unverified` | `UNVERIFIED` | `remediation_unverified` (from the new FSM edge) |
| `regressed` | `UNVERIFIED` | `remediation_unverified` (escalates; verdict detail in the record) |

- Verification runs **only** when ≥1 action reached `ActionStatus.APPLIED` and `verify_remediation` is on.
  A plan whose actions all `failed`/`not_executed` is untouched (existing escalation path).
- Runs identically on the auto path (`_pass_a`) and the approved path (`_pass_b`) (FR-011).
- Idempotent: if `evidence["response"]["verification"]` already exists, the step is a no-op (no double
  probe, no duplicate audit row); terminal/parked incidents are already a supervisor no-op.

## C5 — Supervisor transition (single writer)

```python
(IncidentStatus.RESPONDING, StageOutcome.UNVERIFIED): (IncidentStatus.ESCALATED, DISP_REMEDIATION_UNVERIFIED)
```

The supervisor remains the single writer of status/disposition; the handler only *proposes* outcome +
evidence-patch (existing contract).

## C6 — Evidence + audit (egress, redacted)

- The `VerificationRecord` is merged under `evidence["response"]["verification"]` and each
  `ActionResult.verification` is set. All targets/details are redacted before egress (Constitution III;
  covered by the existing redaction gate boundary set — no new boundary).
- On `unverified`/`regressed`, one optional `audit_log` row (existing table): `actor="verifier"`,
  `action="verification"`, `outcome=<verdict>`.

## C7 — M2 (gated on #14 — reserved, not built)

- New `IncidentStatus.VERIFYING` (text status, no migration). Applied remediation parks here for
  `dwell_window_s`, **reusing** the `awaiting_approval` park/resume machinery (`advance_status` guarded
  edges + a dwell sweeper analogous to `expire_incident`). A #14 recurrence alert on the same entity reopens
  as `regressed`; clean expiry → `verified`. No new parking mechanism.
