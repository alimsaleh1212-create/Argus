# Sentinel — Spec-Driven Build Plan (v1 + layered v2)

**10 hours/day · ~12 days · solo.** The unit of work is the **component spec**, not the day. A spec is *done* when its tests (unit + integration + e2e) are green in CI and it's committed and pushed — not when a clock runs out. Time appears only as a **budget overlay** (to catch slippage early) and as **tier checkpoints** (the go/no-go gates that protect the layering contract). Each v2 layer is additive and independently shippable.

---

## Feasibility summary (read first)

- **v1 (full SOAR + dashboard): comfortable.** ~70h of work in a ~120h budget.
- **+ feedback loop (v2c): feasible.** Small, builds on memory already constructed. The realistic v2 target.
- **+ rule-based detector front-end: achievable.** Makes the system genuinely detect-and-respond end to end.
- **+ full ML anomaly layer (v2a): not advised.** It's a second project's shape (dataset/training/skew/baselining), not extra hours of the same work. Documented as v3.

Plan against ~95 *effective* hours, not 120 — late-day hours are lower-yield, and multi-agent + graph-DB debugging degrades with fatigue. Front-load hard specs into the high-energy block of each day; keep the tail for tests, docs, commits.

**The layering contract:** all T1 specs done & tagged by the day-9 checkpoint → feedback loop by day 10 → detector + polish days 11–12. You never trade v1 quality for a v2 layer.

---

## The four deliverable tiers

| Tier | What it adds | Checkpoint | If you stop here |
|------|-------------|-----------|------------------|
| **T1 — v1** | Full SOAR: ingest → triage → enrich → respond, seeded corpus, Graphiti memory, approval interrupt, React dashboard, evals + 3-tier tests in CI | End of day 9 | Complete, approved capstone |
| **T2 — v2c** | Feedback loop: incident memory tunes detection signals | End of day 10 | Complete cycle, "closes the SOC loop" |
| **T3 — detector** | Lightweight rule/threshold detector that *fires* alerts | Days 11–12 | True end-to-end detect-and-respond |
| **T4 — v2a (stretch)** | ML anomaly layer | Only if T1–T3 green with real surplus | Documented as v3 otherwise |

---

## How this plan works (read once)

- **Specs are the spine.** The component table below is the source of truth. You build spec by spec, in dependency order.
- **"Done" is defined per spec, not per day:** the spec's unit + integration + e2e tests are green in CI, and it's committed and pushed behind a focused PR (~400 lines). No spec is "done" while any test level is red.
- **Big specs carry internal milestones** so you never go dark for days inside one — commit at each milestone, not just at spec completion.
- **Budget, not calendar.** Each spec has a `~days` budget and a target window. If a spec blows its budget, that's your early-warning signal — re-check the tier checkpoint before continuing.
- **Tier checkpoints are dates** (day 9 / 10 / 12). They are the go/no-go gates. Hitting them is what the layering contract protects.

---

## Component specs (the source of truth) — spec-kit draft

Specs are scoped to **components** (a bounded capability with a stable contract), not to days or tiers. Run spec-kit one component at a time, in dependency order (start with the rows that depend on nothing). Version-specific behaviour (e.g. the v2c feedback loop) lives as a marked section *inside* the relevant spec, not a separate file.

**Gap rule:** every build noun in this plan and the proposal is owned by exactly one spec. The "Covers" column is the explicit anti-gap check; the seam rules below cover the contracts *between* specs.

| # | Spec file | Scope (one line) | Covers (build nouns) | ~Days | Target window | Tier | Depends on |
|---|-----------|------------------|----------------------|-------|---------------|------|------------|
| 1 | `SPEC-platform-infra.md` | Compose stack, secrets, blob, config, layered `app/` skeleton, lifespan singletons | Vault, MinIO, config, secrets, docker-compose, Alembic, pre-commit | 0.5 | Day 1 | T1 | — |
| 2 | `SPEC-observability.md` | Tracing + structured logging + redaction as one cross-cutting concern; spans carry tokens in/out, model, latency, redacted I/O; export off the synchronous path | tracing, span, token (metric), redaction, logging, no-latency NFR | 0.5 | Day 1 (+ verify d8) | T1 | 1 |
| 3 | `SPEC-llm-provider.md` | Provider-agnostic LLM adapter; env-selected primary; automatic fallback; evals must pass on both providers | LLM provider, Ollama, Gemini, fallback | 0.5 | Day 1 | T1 | 1 |
| 4 | `SPEC-ingestion.md` | Wazuh-format adapter, Pydantic webhook, Redis queue, async worker, incident object schema, grounding pipeline | adapter, webhook, queue, worker, grounding pipeline, incident schema | 1 | Day 2 | T1 | 1, 2 |
| 5 | `SPEC-knowledge-corpus.md` | Seeded reference corpus (MITRE/IOC/runbooks snapshot) at init; on-demand intel lookup (v1, optional); **§v2/v3: live ingestion** | reference corpus, on-demand intel, (live ingestion) | 0.5 | Day 2 | T1 / roadmap | 1, 6 |
| 6 | `SPEC-memory.md` *(big)* | Graphiti temporal incident graph (episode/entity schema), pgvector fallback, temporal-validity model; **§v2c: memory as a detection/triage input** | memory, Graphiti, pgvector, feedback loop (§v2c) | 1.5 | Days 4–5 | T1 + T2 | 1, 2, 3 |
| 7 | `SPEC-incident-state-machine.md` | Deterministic supervisor: states, transitions, routing (adaptive-depth investigation; determinism-first action), step/token cap | supervisor, state machine, routing, step/token cap | 1 | Day 5 | T1 | 4, 3 |
| 8 | `SPEC-triage-agent.md` | Triage tool contract (read-only), adaptive-depth rule, real/noise + severity + **confidence** + evidence-cited rationale I/O; judges over supplied evidence, never trained priors; abstains/escalates when unsure | triage agent | 1 | Day 3 | T1 | 4, 3, 5, 6 |
| 9 | `SPEC-enrichment-agent.md` | Enrichment tool contract (retrieval-only), both-directions (external intel + internal correlation), cross-correlation as output | enrichment agent | 1 | Day 5 | T1 | 6, 5, 3 |
| 10 | `SPEC-response-remediation.md` *(big)* | Response tool contract (only agent with action tools), playbook selection, config-backed auto/approval policy, interrupt/resume state machine, approval timeout, audit rows | response agent, playbook, auto-execute, approval interrupt, audit | 2 | Days 6–7 | T1 | 7, 3 |
| 11 | `SPEC-safety.md` | Guardrails sidecar (service-cred via Vault), injection/jailbreak rails over alert *and* feed text, red-team probe set, structural triage-has-no-action-tools boundary | guardrail, injection, red-team, safety boundary | 1 | Day 8 | T1 | 1, 2 |
| 12 | `SPEC-dashboard.md` | React operations console: single `admin` role (extensible auth), incident queue, trace/evidence inspector, approve/reject, KPI views; the API contract it consumes | React, dashboard, auth, role, KPIs | 1 | Day 8 | T1 | 10, 2, 3 |
| 13 | `SPEC-eval.md` *(big)* | All golden sets + committed thresholds + CI gates: triage (F1 vs labels), supervisor routing, enrichment/retrieval (hit@k/MRR), **temporal-memory** eval, rationale (LLM-judge validated by hand-labels), red-team, redaction, smoke; three-tier (unit/integration/e2e) discipline; runs on both LLM providers | eval, threshold, CI gate, temporal eval, 3-tier tests | spread | Days 1, 3, 4, 5, 8, 9 | T1 (+ extended each tier) | all of 2–12 |
| 14 | `SPEC-detector.md` | **T3:** lightweight rule/threshold detector that *fires* alerts from replayed events into the existing ingestion schema; tuned by §v2c feedback loop | detector (T3) | 1.5 | Days 11–12 | T3 / roadmap | 4 (schema), 6 (loop) |

**Coverage confirmation (no gaps):** every high-frequency build noun — memory, Graphiti, eval, dashboard, detector, supervisor, token, feedback-loop, threshold, guardrail, auth, redaction, injection, CI-gate, playbook, pgvector, audit, approval-interrupt, the three agents, reference-corpus, LLM-provider, Vault, MinIO, tracing, span, config, secrets, grounding-pipeline, webhook, queue, worker — appears in exactly one "Covers" cell. Cross-cutting concerns (observability, safety, eval, llm-provider) are their *own* specs because they change for independent reasons and are consumed by all components.

### Internal milestones for the three big specs (commit at each — don't go dark)

- **`SPEC-memory.md`:** (a) ingest path green — episodes write to Graphiti/Neo4j; (b) query path green — a similar incident retrieves a prior one; (c) temporal-validity green — a changed fact returns the correct time-valid state. *Day-1 Graphiti spike precedes (a): stand Neo4j up, run the quickstart, record go/no-go vs. pgvector fallback in `DECISIONS.md` before committing to the framework.*
- **`SPEC-response-remediation.md`:** (a) auto-path green — a low-risk incident auto-remediates with an audit row; (b) interrupt green — a destructive action parks in `awaiting_approval`; (c) resume green — approve and reject both resume correctly; timeout reaches its terminal state.
- **`SPEC-eval.md`:** seeded as placeholders on day 1 (so CI gates from the start), then each gate lands green as its component does — triage gate with #8, retrieval + temporal gate with #6, routing gate with #7, red-team + redaction with #11 — and the whole suite runs on both LLM providers at the day-9 freeze.

### No-gap seam rules (the contracts where two specs meet)

- **Ingestion → state machine:** the incident object schema (owned by `SPEC-ingestion`) is the input contract for `SPEC-incident-state-machine`. One schema, defined once, imported by the rest.
- **State machine → agents:** the supervisor passes each agent a bounded slice of incident state; each agent spec declares exactly which fields it reads/writes. The union of those slices must equal the incident schema — no field unowned, none written by two agents.
- **Agents → LLM provider:** every agent calls the LLM only through `SPEC-llm-provider`'s adapter — no agent talks to Ollama/Gemini directly.
- **Everything → observability:** every span/log/memory-write passes through `SPEC-observability`'s redaction before leaving the service. No component logs raw.
- **Detector → ingestion (T3):** the detector emits the *same* incident schema as the Wazuh adapter, so it plugs in with no downstream change.
- **Feedback-loop seam (v2c):** lives as a section in *both* `SPEC-memory` (the signal written back) and `SPEC-response-remediation`/`SPEC-incident-state-machine` (how that signal changes future scoring), cross-referenced so the loop isn't half-specified.

---

## Build order & target windows (the time overlay)

Dependency-ordered, with rough windows. This is a *budget*, not a rigid schedule — slipping a window is your signal to check the next tier checkpoint, not a failure in itself.

| Window | Specs in flight | Notes |
|--------|-----------------|-------|
| **Day 1** | 1 → 2 → 3, eval placeholders, **Graphiti spike** | Foundations + cross-cutting. End on green empty pipeline (all 3 test levels wired). |
| **Day 2** | 4, 5 | Alert flows source → queue → worker → incident object; corpus queryable. |
| **Day 3** | 8 (triage) | Adaptive-depth triage; triage F1 gate green. |
| **Days 4–5** | 6 (memory), 7 (supervisor), 9 (enrichment) | Hardest stretch. Memory milestones a→b→c; supervisor routes triage→enrich; enrichment cross-correlation visible. |
| **Days 6–7** | 10 (response + remediation) | Auto-path, then interrupt, then resume. Day 7 is the trickiest state-machine work — its own buffer. |
| **Day 8** | 11 (safety), 12 (dashboard) | Heaviest day (two specs). Dashboard deep-polish may borrow from days 9 & 12. Verify observability adds no latency. |
| **Day 9 — T1 CHECKPOINT** | 13 finalized | Full e2e, failure-path recovery (incl. LLM fallback), all gates green on *both* providers, `eval_report.json` to MinIO. READMEs. **Tag v1.** *Complete capstone even if days 10–12 produce nothing.* |
| **Day 10 — T2 CHECKPOINT** | 6 §v2c | Feedback loop: memory tunes future triage/severity. Small eval proving behaviour changes. Surface in dashboard KPIs. **Tag.** |
| **Days 11–12 — T3** | 14 (detector) | Rule/threshold detector fires alerts into the existing schema; feedback loop tunes *it*. **Day 12 H1–6 decision gate:** if T1–T3 green with genuine surplus and you're not on fumes, a *minimal* anomaly score (T4) — time-boxed, abandoned the moment it threatens stability; otherwise skip and document v3. **Day 12 tail:** fresh-clone `docker-compose up` green, rehearse demo moments, final READMEs, **final tag.** |

---

## Demo moments (by tier)

1. **(T1)** An incident flows the full pipeline; trace tree shows triage → enrichment → response with one error path handled.
2. **(T1)** A destructive remediation parks on the approval interrupt; you approve live and the pipeline resumes.
3. **(T1)** A second incident resembling an earlier one: enrichment surfaces the prior incident and its disposition from temporal memory.
4. **(T1)** The dashboard: live queue, per-agent tokens/latency in a trace drill-down, and the KPI view.
5. **(T2)** The same alert handled differently *after* memory accumulates — the feedback loop visibly closing the SOC cycle.
6. **(T3)** A raw replayed event with no pre-made alert is *detected* by your rules and runs end to end.
7. **(T1)** An injection payload in alert text is refused; the red-team CI gate blocks the regression.

---

## Risk controls

- **Every *spec* ends green (all three test levels) and pushed.** No spec depends on the next to be in a valid state. Big specs commit at each internal milestone.
- **The layering contract is the safety net:** stop at any tier checkpoint (day 9, 10, or 11) and you have a complete, honest deliverable.
- **Graphiti fallback** is decided at the day-1 spike, not discovered at the memory spec.
- **`SPEC-response-remediation` (the interrupt)** is the hardest state-machine work — it has its own two-day window and sits before the T1 freeze.
- **Dashboard is in T1, not polish** — it's a graded showcase surface — but its deep polish can borrow from days 9 and 12.
- **T4 is conditional, never assumed.** If you're tired, that *is* the signal to skip it. Protect v1 and the feedback loop; shed T3/T4, not quality.
- **Budget slippage is a signal, not a failure:** if a spec overruns its `~days`, re-check the next tier checkpoint before adding anything.

---

## What to tell your mentor

> *"I build spec by spec — each component is done only when its unit, integration, and e2e tests are green and it's pushed, so the system is always in a valid state. Time is a budget, not the structure: I complete v1 — the full triage→enrichment→response pipeline with seeded knowledge, Graphiti temporal memory, the approval interrupt, and a polished operations dashboard — and tag it at the day-9 checkpoint. I then close the detection↔response feedback loop by day 10 and add a working rule-based detector by day 12, so the system genuinely detects and responds end to end. The full ML anomaly layer is a second project's worth of dataset and training work; I've scoped it as v3 with the integration boundary already in place. Every tier is independently complete, so there's no all-or-nothing risk."*
