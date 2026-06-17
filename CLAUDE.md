<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `017-ml-anomaly-detector` (Component #17 — `SPEC-ml-anomaly-detector`; the **ML
anomaly detection layer**, UEBA-style — built **after** the rule detector #14, per the 2026-06-16
*Detection Strategy Update*). A **decoupled, complementary** ML source that reads **replayed SIEM logs**,
scores **per-entity time windows** for behavioral deviation, and **fires** alerts into the **existing `#4`
ingestion contract** with **zero downstream change**. It **complements, does not replace** #14 (signature +
anomaly cover each other's blind spots). XDR correlation rolls to **#18/v3**; `011` remains the safety gap
(v3b/VD1).
- Plan: `specs/017-ml-anomaly-detector/plan.md`
- Spec: `specs/017-ml-anomaly-detector/spec.md`
- Design: `specs/017-ml-anomaly-detector/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component, #17): a **backend-only** extension that **mirrors #14** (one-shot commands + pure
`services` core + closure-factory DI; pure domain types in a new `domain/anomaly.py`; **zero migration**).
An **offline** `python -m backend.anomaly_train` reads the **CERT Insider Threat (r6.2)** dataset (NOT
committed), builds per-user-day features, fits an **Isolation Forest** (pinned seed), and saves a small
**committed** artifact (`backend/data/anomaly/model.joblib`). A replay one-shot `python -m
backend.anomaly_detector` (`make_anomaly_runner` DI) loads the artifact (behind the pure **`AnomalyModel`
Protocol**, `infra/anomaly_model.py`, faked in tests), builds **entity-windows** from replayed logs, scores
them, maps **`score → severity` via config-backed bands** + a `fire_threshold`, and emits each over-threshold
window as `AnomalyFinding → WazuhAlert` **in-process via the existing `services/intake.accept(...,
source="anomaly-detector")`** seam — whose `source` param **already exists** (from #14), so **`intake` is
reused unchanged** (no migration, no schema/FSM/agent change). New **`AnomalySettings`** (`extra="forbid"`)
+ new deterministic, provider-independent, **blocking** **`anomaly_detection`** eval gate (precision/recall +
FP ceiling, scored against the committed artifact; yaml block **+** registry runner **+** `__main__` import
added together — orphan check is a hard error in #13). **NEW runtime deps**: `scikit-learn` + `numpy`
(inference); `pandas` dev-only (training). **Constitution note**: this is ML at the **detection** layer — an
explicit **recorded exception** to Principle IV (response path stays deterministic; detector is decoupled —
no second writer, no FSM edge), requiring a `DECISIONS.md` entry + constitution note **before** implementation
(M-0 precondition). Ships **M-a** (train + features + model/infra) → **M-b** (runner + emit + integration/e2e)
→ **M-c** (`anomaly_detection` gate + labeled fixture), each ≤~400 lines. **Out of scope**: live feeds (v3c),
drift/retraining, feed-to-detector tuning of the model, XDR (#18).

Prior #14 (`014-detector`, done) — a **deterministic** rule/threshold detector (Constitution IV, no LLM/ML):
one-shot `python -m backend.detector` loads a config-backed rule set (`backend/data/detector/rules.yaml` —
`match` + `threshold` rules) + a replayed event set, runs pure `services/detector.evaluate()`, maps
`FiredAlert → WazuhAlert`, emits via `services/intake.accept(..., source="detector")` (it **added** the
backward-compatible `source` param #17 now reuses). `DetectorSettings` + deterministic **`detection`** gate.
Zero downstream change; supervisor stays single writer. The **real, Constitution-clean** shipping detector
that #17 complements. Plan: `specs/014-detector/plan.md`.

Prior #16 (`016-memory-feedback-loop`, done) — build detail: a **backend-only** extension (mirrors #9/#15 — zero migration, closure-factory DI, pure
domain types in a new `domain/feedback.py`). **M1** (buildable now) **writes** each verification verdict (from
#15, `evidence["response"]["verification"]`) back to temporal memory as a **time-valid `TemporalFact`**
(`fact_type="remediation_outcome"`, `value=<verdict>`) keyed **identically to the reputation fact** so
write-key == read-key (the load-bearing invariant), via the **existing off-path worker→`services/memory`
post-terminal seam** (`MemoryStore.write_fact`, invalidate-not-delete) — **no new write authority over incident
state** (Constitution III). It then **consumes** the fact through a deterministic read-only
`services/feedback.gather_feedback` (`query_fact(as_of=None)`, current-only) at the **grounded boundary**, which
tunes two config-backed **inputs**: a **severity/routing escalation bias** (raise effective severity → existing
`route_grounded` escalates sooner; **no new FSM edge**) and a **stronger-playbook preference** (config-backed
catalog `strength`) in `selection.py`. Deterministic, **no LLM** on the feedback path (Constitution IV); the
**supervisor stays the single writer** of status/disposition (feedback tunes inputs only). New **`FeedbackSettings`**
section (`extra="forbid"`). New deterministic **`feedback`** eval gate (yaml block **+** registry runner added
together — the declared⇔registered orphan check is a hard error in #13), fixtures under `tests/fixtures/feedback/`
(baseline-vs-repeat); **extends** the temporal-memory (`remediation_outcome_flip` time-validity) + supervisor_routing
(`prior_regressed_escalates`) + redaction (outcome fact + KPI view) gates rather than duplicating. Best-effort +
graceful degradation: memory outage → no bias (baseline v1) + no write, never a block. Ships **3 M1 milestone PRs**
(Constitution I): M1-a write-back → M1-b consumption/bias → M1-c eval gate + read-only dashboard KPI surface.
**M2** (feed memory-derived intel → detector, the "closes the detection↔response loop" headline) is
**designed-but-deferred, gated on the detector #14** — a defined export contract, detector still emits the
existing ingestion schema. **Layering-contract watch-item**: v2 *design* proceeds ahead of the T1 tag
(additive, roadmap §6.1); implementation code lands after the #12/#13 freeze or under a recorded `DECISIONS.md`
entry.

Prior components (done): `015-remediation-verification` (#15, first v2/T2) — closed the **action-applied →
threat-eliminated** gap: a **deterministic** verification step at the **tail of the response stage** computes a
`VerificationVerdict` (`verified`/`unverified`/`regressed`); `verified` keeps `auto_remediated`/`remediated`,
`unverified`/`regressed` activates the reserved **`remediation_unverified`** disposition and **escalates**.
Combines a real **indicator re-check** (`ThreatIntelClient.lookup` + `MemoryStore.query_fact(as_of=None)`) with
an **executor status probe** (new read-only `probe()` on `ActionExecutor`) into a **pure `decide_verdict`**
(worst-case; no LLM common-path, optional config-gated conflict-only tiebreak). New `StageOutcome.UNVERIFIED` +
one edge `(RESPONDING, UNVERIFIED) → (ESCALATED, remediation_unverified)`; verdict rides `incidents.evidence`
JSONB; verification fields on **`ResponseSettings`**; deterministic **`verification`** eval gate. **Read-only
re-check, no new write authority** — memory write-back of the verdict is **#16's** job (this component). M2
(`verifying` dwell-window monitoring loop) deferred, gated on #14. Plan: `specs/015-remediation-verification/plan.md`.

`013-eval-harness` — the **consolidated evaluation harness** (T1, the day-9 freeze
spec; depends on #2–#12). Backend-only `backend/eval/` entrypoint (`python -m backend.eval`) reads
`config/eval_thresholds.yaml` via a **registry** (declared⇔registered **orphan/stale = hard error**) → pure
`EvalReport` (`domain/eval.py`). Seven seeded gates consumed unchanged; **CI wiring** is the core gap closed
(required per-PR `eval` job on **Ollama only**; `eval-freeze.yml` runs **both providers** + the pinned-judge
**rationale** gate, uploads to the **`eval-reports`** MinIO bucket). `EvalSettings` (`extra="forbid"`);
`pyyaml` a direct dep; memory-safe `scripts/run-evals.sh`. **Red-team gate stays deferred to #11/v3b (VD1).**
Plan: `specs/013-eval-harness/plan.md`.

`012-dashboard` — the **React operations dashboard** (the human surface, graded
showcase). Separate-image React SPA (`frontend/`, Node 20) over **read-side** endpoints; **read-only
except approve/reject** (reuses #10's `/approvals/{id}/decision`; supervisor stays single writer —
Constitution III). Filled the reserved `routers/incidents.py` (queue/detail/audit/trace/kpis/stream) +
**admin auth** (username+password in Vault → HS256 JWT, `services/auth.py`/`get_current_operator`,
PyJWT + stdlib PBKDF2); registered the `incidents`+`approvals` routers. **No migration** (reads existing
tables); pure read DTOs in `domain/dashboard.py`; **SSE** push from an API-side 2s snapshot poll. Extends
the **redaction** gate with a dashboard-view check — no new gate. Plan: `specs/012-dashboard/plan.md`.
`010-response-remediation` — the only **acting** stage + the HITL approval
interrupt. Determinism-first playbook select (catalog match, **no** LLM; ambiguous tail = **one**
`LlmClient` call); **config-backed default-deny** policy → **auto** allowlist executes via mock executors
(`infra/executors.py`, `ActionExecutor`) + **audit row**; **destructive** → `AWAITING_APPROVAL` +
`pending` row. Approve re-enters `RESPONDING` (re-runs to execute, no LLM on resume) → `remediated`;
reject → `rejected_by_human`; worker **timeout sweeper** → `approval_expired`; all **idempotent**.
**Owns `GET /approvals` + `POST /approvals/{id}/decision`** (drives `supervisor.resume_incident`; API
registers `SupervisorProvider`). New persistence `approval_requests` + `audit_log` (migration **0006**)
via `repositories/approvals.py`/`audit.py`; **`incidents` table unchanged**. Pure `domain/response.py`;
`ResponseSettings`. v1 records *applied*; `verification` reserved for **§v2c** (T2). Extends
**`supervisor-routing`** gate — no new gate. Plan: `specs/010-response-remediation/plan.md`.
`009-enrichment-agent` — **second LLM stage**: retrieval-only cross-correlation.
A **bounded retrieval fan-out (`asyncio.gather`) + exactly one** `LlmClient` call reads **both directions**
(external `CorpusRetriever`/`IntelVerdict` #5; internal `MemoryStore.search_similar` + `query_fact(as_of=…)`
#6) → validated `EnrichmentReport` (`domain/enrichment.py`) → pure `decide_outcome` → ADVANCE/RESOLVED/ESCALATE.
Closure-factory DI `make_enrichment_handler(...)`; **retrieval-only, no action tools, no write**; best-effort
retrieval + fail-closed reasoning; zero change to `supervisor.py`/`repositories/`/schema (#7 wired `ENRICHING`).
Extends the **`retrieval`** gate — no new gate. Plan: `specs/009-enrichment-agent/plan.md`.
`008-knowledge-corpus` — **seeded reference corpus + optional on-demand intel**
(Constitution VI cold-start): static MITRE technique→mitigation + runbooks → Postgres `reference_corpus`
(`0006`, deterministic keyed/lexical, embeddings reserved); temporal reputation (seed IOC + intel verdicts) →
`TemporalFact`s in #6 via the anticipated `MemoryStore.write_fact` (intel is a *fact*, not an episode — keeps
`search_similar` clean). Pure `domain/corpus.py` (`CorpusRetriever` Protocol, consumed by #9);
`infra/intel.py` `ThreatIntelClient` optional/config-gated/fail-closed (missing creds → disabled not
fail-boot; outage → `unknown`). Idempotent one-shot `seed-corpus`. Extends the **`retrieval`** gate with
corpus fixtures — no new gate. Plan: `specs/008-knowledge-corpus/plan.md`.
`007-incident-memory` — **temporal incident-memory layer** (Constitution VI):
**Graphiti on Neo4j 5.26** behind the `MemoryStore` Protocol (`domain/memory.py`:
`write_episode`/`search_similar`/`query_fact`), decided pgvector fallback (MD9). The **worker** writes one
redacted, idempotent `IncidentEpisode` per incident after terminal — off-path, best-effort (memory outage
never blocks disposition); supervisor stays pure. `query_fact(as_of=…)` → time-valid `FactState` via
invalidate-not-delete. Graphiti's native Gemini LLM+embedder is the one documented VII deviation.
`MemoryProvider` degrades to `NullMemory`. Lands **retrieval** (hit@k/MRR) + **temporal_memory** gates
(provider-independent). Plan: `specs/007-incident-memory/plan.md`.
`006-triage-agent` — **first LLM stage**: replaces the triage stub with **one**
structured-output `LlmClient` call → validated `TriageJudgment` (`domain/triage.py`: real/noise/uncertain +
confidence + evidence-cited rationale) → pure config-gated `decide_outcome` → ADVANCE/RESOLVED/ESCALATE;
**fail-closed** (bad output → escalate, worker never crashes); **no tools / no write** (closure-factory DI
preserves the frozen `StageHandler`); supervisor JSONB-merges `evidence_patch`; **triage F1** gate on both
providers. Plan: `specs/006-triage-agent/plan.md`.
`005-incident-state-machine` — **deterministic supervisor** (`services/supervisor.py`,
plain async state machine, no LLM/LangGraph); config-backed fast-path routing + adaptive depth; hard
step+token cap → `escalated`; graceful degradation; **single-writer** over pure stage handlers; pure types
`domain/pipeline.py` (`StageName`/`StageOutcome`/`StageResult`/`ToolError`); extends `IncidentStatus` (text,
no migration) + nullable `disposition` (`0004`); guarded `advance_status` (idempotent/resumable);
`awaiting_approval` park + resume edges (#10 owns mechanism/timeout/audit); **supervisor-routing** eval gate.
Plan: `specs/005-incident-state-machine/plan.md`.
`004-ingestion-pipeline` — Wazuh **webhook → queue → worker → Incident**; thin
`POST /ingest/wazuh` (**validate → redact → dedup → persist → enqueue → `202`**); async **worker** grounds
(`services/grounding.py`, no LLM) then hands to `services/pipeline.py` (the seam #7 now fills). **Postgres
`incidents` source of truth** (migration `0003`); **Redis transient** (reliable-list queue + `SET NX EX`
dedup). Owns the **Incident schema** `domain/incident.py`. Plan: `specs/004-ingestion-pipeline/plan.md`.
`003-llm-provider` — provider-agnostic async `LlmClient` (`Depends(get_llm)`),
Gemini primary + Ollama fallback behind SDKs confined to `infra/llm_drivers.py`, fail-closed contract,
`domain/llm.py`, `ollama` compose service. Plan: `specs/003-llm-provider/plan.md`.
`002-observability-redaction` — `structlog` redaction + correlation-id,
**OpenTelemetry** tracing → Postgres `trace_spans` (off-path `BatchSpanProcessor`), **Presidio + secret
scrubber** redaction; the unified `infra/observability.py` seam (`span()`, `record_llm_usage`,
`Redactor`) #3 consumes. Plan: `specs/002-observability-redaction/plan.md`.
`001-platform-infra` — compose stack, Vault, MinIO, async SQLAlchemy/Alembic, typed `pydantic-settings`
(`extra="forbid"`, `SecretStr`), layered `backend/` with `import-linter`, lifespan singletons via the
provider seam in `backend/infra/container.py`. Plan: `specs/001-platform-infra/plan.md`.
<!-- SPECKIT END -->
