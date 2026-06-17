<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 2.0.0  (MAJOR — redefinition of a NON-NEGOTIABLE principle)

Rationale for MAJOR: Principle III is re-tiered. The injection/jailbreak guardrails and the
  red-team CI gate were v1 (T1-freeze) non-negotiables; they are now deferred to v3b (SPEC-safety
  #11), mandatory before v3c live-feed ingestion. A v1 build without the red-team gate was previously
  non-compliant and is now compliant — a backward-incompatible change to the v1 contract. The
  load-bearing structural boundary (DI: triage holds no action tools) and the redaction layer remain
  v1 non-negotiables, unchanged. Authorized by DECISIONS.md VD1.

Modified principles:
  - III. Security Boundaries Are Structural, Not Prompted — split into (a) v1 non-negotiables
      (structural DI boundary + redaction) and (b) v3b-deferred guardrails rails + red-team CI gate.
  - VI. Temporal Memory & Graceful Degradation — the "same guardrails as alert text" clause now
      cross-references III's tiering (guardrails land by v3b, before v3c live feeds).

Added sections: none.
Removed sections: none.

Templates requiring updates:
  - .specify/templates/plan-template.md ............ ✅ updated (III gate reflects the v3b deferral)
  - .specify/templates/tasks-template.md ........... ✅ no change needed (no red-team-specific tasks)
  - .specify/templates/spec-template.md ............ ✅ no change needed (no constitution-coupled text)

Follow-up TODOs: none. RATIFICATION_DATE unchanged (2026-06-06); LAST_AMENDED_DATE = 2026-06-15.
-->

# Argus Constitution

Argus is an AI-driven SOAR (Security Orchestration, Automation & Response) platform that
ingests Wazuh-format alerts and processes each through a supervisor-coordinated pipeline of three
agents — triage → enrichment → response — backed by a temporal incident-memory layer. This
constitution defines the non-negotiable principles that govern how Argus is built. It
supersedes ad-hoc convenience; where a principle and a shortcut conflict, the principle wins.

## Core Principles

### I. Spec-Driven Delivery — "Done" Is Tests-Green-and-Pushed

The unit of work is the component spec, never the day. Every component begins with a `SPEC.md`
before any implementation code. A spec is **done** only when its unit, integration, and e2e tests
are green in CI **and** the work is committed and pushed behind a focused PR (≤ ~400 lines). No
spec is "done" while any test level is red. Big specs MUST commit at each declared internal
milestone so work never goes dark, and no spec may depend on a later spec to be in a valid state.

**Rationale**: defining "done" per spec — not per clock — keeps the system in a continuously
valid, demonstrable state and removes all-or-nothing risk.

### II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)

Three test tiers MUST be green every day: **unit** (schemas and tool logic with the LLM mocked),
**integration** (each agent against its real backing service — Redis, Postgres, Neo4j/Graphiti,
guardrails sidecar), and **e2e** (one full incident with only external/remediation targets
mocked). Coverage MUST be ≥80% on new code and higher on remediation and the safety boundary.
Eval thresholds live in a committed `eval_thresholds.yaml`, are seeded as placeholders on day 1 so
CI gates from the start, and every eval MUST pass on **both** configured LLM providers before a
tier is frozen.

**Rationale**: tests and evals are the contract that lets the system ship daily; regressions fail
CI, not the demo.

### III. Security Boundaries Are Structural, Not Prompted

Two safety guarantees are **v1 non-negotiables**:

1. **Structural capability boundary.** The triage agent structurally holds **no** action tools; the
   response agent is the **only** agent with action tools, and this separation is enforced via
   dependency injection, not prompt text. This boundary is **library-independent**: a prompt-injected
   alert that hijacks triage still cannot execute a remediation, because the capability is absent by
   construction rather than discouraged by instruction.
2. **Redaction before egress.** A redaction layer MUST run before anything leaves the service: every
   log line, trace span, memory write, and dashboard view; a redaction eval proves no secret ever
   appears unredacted.

The **guardrails library, the mandatory injection/jailbreak rails over alert-derived and
feed-derived text, and the red-team CI gate** are **deferred to v3b** (`SPEC-safety`, #11) per
`DECISIONS.md` **VD1** — they are **not** part of the v1 (T1) freeze. They become mandatory **by
v3b**, and there is a **hard ordering constraint**: **#11 MUST land before any untrusted live-feed
ingestion (v3c)**. Until #11 lands, **no live or otherwise untrusted feed may be ingested**, because
the "same guardrails as alert text" invariant (Principle VI) is unenforceable without it. No v1
evaluation, report, or claim may imply injection/jailbreak coverage exists.

**Rationale**: the structural separation (capability absent by construction) plus redaction are the
load-bearing safety properties and ship in v1 regardless of guardrails-library choice. The rails and
red-team gate are genuine but additive safety infrastructure whose value begins when untrusted feed
text exists; tiering them to land before that point (v3b, ahead of v3c) is honest sequencing, not a
weakening of the boundary — which remains absolute in v1.

### IV. Determinism First; Agents Only for the Ambiguous Long Tail

The supervisor is a **deterministic state machine** with explicit transitions — never an LLM
freelancing about orchestration. Obvious false positives and obvious criticals MUST resolve on a
deterministic fast-path with no LLM call. The supervisor MUST enforce a hard cap on total steps
and tokens per incident (a cost and safety control). Agents are reserved for the ambiguous cases
where "detected → known playbook" breaks down. Every agent reasons **only over supplied evidence**
(the verdict and severity, structured event fields, retrieved context, and policy) — never trained
priors — emits a plain-language, evidence-cited rationale, and abstains or escalates when unsure.

**Rationale**: using AI where determinism suffices is overengineering; reserving agents for runtime
judgment, with an auditable rationale, is where they earn their place.

### V. Human-in-the-Loop for Consequential Action

Destructive or irreversible actions (isolate host, disable user, block IP) MUST raise a human
approval interrupt: the incident parks in `awaiting_approval` and resumes only on an explicit
approve/reject decision. The auto-execute vs. approval boundary is a **config-backed policy**,
defended in `DECISIONS.md`, never hardcoded in agent logic. Pending approvals MUST have a defined
timeout with an explicit terminal state. Every executed action — auto or approved — MUST write an
audit row recording actor (agent or human), action, target, and timestamp.

**Rationale**: consequential automation must be reversible-by-default, accountable, and ultimately
answerable to a human.

### VI. Temporal Memory & Graceful Degradation

Institutional memory is **queryable, not retrained**: incidents and analyst dispositions
accumulate as time-stamped episodes. Time-validity MUST be preserved — on conflict the prior edge
is invalidated, not deleted, so the system answers "benign as of the seed, malicious as of the
feed update," not merely "what is true now." A seeded reference corpus MUST make the agent
competent on the very first incident (cold-start closed). The Graphiti/Neo4j memory layer MUST
have a decided pgvector + relational fallback (temporal validity modeled as `valid_from`/`valid_to`),
chosen at the day-1 integration spike; the triage→enrichment→response spine and the approval
interrupt never move when the slice shrinks. All feed- and knowledge-sourced text MUST pass the
same guardrails as alert text — a clause governed by **Principle III's tiering**: those guardrails
land by **v3b** and MUST precede **v3c** live-feed ingestion (until then, no untrusted feed is
ingested).

**Rationale**: the "gets smarter over time" capability must be real and defensible, yet never a
single point of failure that can sink v1.

### VII. Production Engineering Standards

Argus is async all the way down (`httpx`, async SQLAlchemy, async LLM SDK; `asyncio.gather`
where enrichment fans out). Dependency injection supplies tool sets, DB sessions, LLM and
guardrails clients, and retrievers — which is also what enforces Principle III and mocks the LLM in
tests. Lifespan singletons are built once on startup and disposed on shutdown. Pydantic validates
every boundary (incident state, each tool's I/O, the Wazuh payload, remediation requests).
Structured logging carries a trace ID on every line. Observability MUST add negligible latency —
span export and eval logging run off the synchronous incident path. Configuration is one typed
`pydantic-settings` object with `extra="forbid"`; required secrets fail at startup (Vault refuses
to boot if unreachable). Use `uv` for venv and dependencies; pinned deps; `ruff` + formatter +
`gitleaks` in pre-commit; Conventional Commits; `feature/` branches.

**Rationale**: the capstone earns its grade on engineering quality; these standards are the bar,
applied uniformly so every component is mockable, observable, and safe by default.

## Scope Discipline & Delivery Tiers

Argus sits **deliberately downstream of detection**: a SOAR consumes alerts and orchestrates the
response; it is not a detector. The following are explicitly **out of scope for v1** and MUST NOT
be built unless a tier checkpoint is met with genuine surplus: an ML anomaly detector (roadmap
v2a/v3), multi-tenancy, an embeddable widget, live network capture, an LLM-driven supervisor, and
any fourth agent (supervisor + triage/enrichment/response only).

> **Exception**: Component #17 (SPEC-ml-anomaly-detector) is an ML anomaly detector at the
detection layer, authorized by the 2026-06-16 *Detection Strategy Update* as a bounded, decoupled
complement to the deterministic rule detector (#14). The exception is recorded in `DECISIONS.md`
**AD2** and in the Amendment log below; it does not generalize to the other out-of-scope items.

Delivery is **layered and independently shippable**. Each tier is a complete, honest deliverable on
its own:

| Tier | Adds | Checkpoint |
|------|------|-----------|
| **T1 — v1** | Full SOAR: ingest → triage → enrich → respond, seeded corpus, Graphiti memory, approval interrupt, React dashboard, evals + 3-tier tests in CI | End of day 9 — tag `v1` |
| **T2 — v2c** | Feedback loop: incident memory tunes future triage/severity | End of day 10 |
| **T3 — detector** | Lightweight rule/threshold detector that fires alerts into the existing ingestion schema | Days 11–12 |
| **T4 — v2a** | ML anomaly layer (stretch) | Only if T1–T3 green with real surplus; otherwise documented as v3 |

Beyond the four tiers above, the **v3 roadmap** sequences `v3a ML anomaly → v3b safety/guardrails
(#11) → v3c live feeds → v3d XDR`. The **safety-before-live-feeds** ordering (v3b before v3c) is a
**binding constraint** (Principle III): live/untrusted feed ingestion MUST NOT ship before the
guardrails rails and red-team gate land.

**The layering contract is binding**: all T1 specs MUST be done and tagged by the day-9 checkpoint
before any v2 layer begins, and v1 quality is **never** traded for a later layer. Budget slippage
is a signal to re-check the next tier checkpoint, not a license to cut v1 quality. Fatigue is itself
the signal to shed T3/T4 — never the safety, testing, or memory guarantees above.

## Development Workflow & Quality Gates

- **Spec-first, dependency-ordered.** Build component by component in dependency order; the
  component spec table is the source of truth. Every build noun is owned by exactly one spec; the
  no-gap seam rules define the contracts where two specs meet (one schema defined once and imported).
- **Budget, not calendar.** Each spec carries a `~days` budget and target window; overrunning it
  triggers a tier-checkpoint re-check, not a quiet slip.
- **CI gates from day 1.** The v1 eval suite (triage F1, supervisor routing, retrieval hit@k/MRR,
  temporal-memory, rationale, redaction, smoke) gates merges; gates land green as their component
  does and the full suite runs on both LLM providers at the day-9 freeze. The **red-team gate is
  deferred to v3b** (`SPEC-safety` #11, per VD1) and is **not** part of the v1 gate set.
- **Reproducibility is a deliverable.** A fresh-clone `docker-compose up` MUST come up clean; the
  final submission is a public repo with a clean stack and the `v1.0.0-capstone` tag.
- **Defensible by design.** Every non-obvious architectural choice (auto/approval allowlist, the
  Graphiti go/no-go, fallback boundaries, the #11 deferral) is recorded and defended in `DECISIONS.md`.

## Governance

This constitution supersedes other working practices for Argus. Compliance is verified at every
PR and tier checkpoint: a change that violates a principle MUST be rejected or accompanied by a
justified, time-bound exception recorded in `DECISIONS.md`. Complexity MUST be justified against a
simpler rejected alternative. The deferral of the injection/jailbreak rails and red-team gate to
v3b is recorded as exception **VD1** in `DECISIONS.md`, with the binding v3b-before-v3c constraint.

Amendments are made by editing this file with a clear rationale and propagating the change to all
dependent templates (`plan-template.md`, `spec-template.md`, `tasks-template.md`) in the same
change. Versioning follows semantic versioning:

- **MAJOR** — backward-incompatible governance changes or removal/redefinition of a principle.
- **MINOR** — a new principle or section, or materially expanded guidance.
- **PATCH** — clarifications, wording, and non-semantic refinements.

For day-to-day runtime guidance (technologies, structure, shell commands), developers and agents
read the current plan and the relevant component `SPEC.md`, as directed by `CLAUDE.md`.

**Version**: 2.0.1 | **Ratified**: 2026-06-06 | **Last Amended**: 2026-06-16

## Amendment log

### 2026-06-16 — Detection-layer ML exception for SPEC-ml-anomaly-detector (#17)

**Scope**: Principle IV / Scope-Discipline.

**Change**: Records a justified, time-bound exception for component #17 (UEBA-style ML anomaly
detection). The exception is bounded: the response path remains deterministic; the detector is
decoupled (no second writer, no new FSM edge); and it complements (does not replace) the existing
deterministic rule detector (#14). Full rationale, mitigations, and rejected alternatives are
recorded in `DECISIONS.md` **AD2**.

**Impact**: The v1-out-of-scope / T4-stretch line for "an ML anomaly detector" is explicitly
overridden for #17 by the 2026-06-16 *Detection Strategy Update*. No other scope-discipline items
are affected. No template changes required.
