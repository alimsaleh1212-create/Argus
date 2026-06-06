<!--
SYNC IMPACT REPORT
==================
Version change: (unratified template) → 1.0.0
Ratification: initial adoption of the Sentinel constitution, derived from
  docs/resources/SOAR_brief.md and docs/resources/SOAR_Plan.md.

Modified principles: none (initial definition — all 7 principles created from
  template placeholders [PRINCIPLE_1..5]; project required 7, not 5).

Added sections:
  - Core Principles (7 principles)
  - Scope Discipline & Delivery Tiers (Section 2)
  - Development Workflow & Quality Gates (Section 3)
  - Governance

Removed sections: none.

Templates requiring updates:
  - .specify/templates/plan-template.md ............ ✅ updated (Constitution
      Check replaced with concrete gates derived from the 7 principles)
  - .specify/templates/tasks-template.md ........... ✅ updated (testing note:
      three-tier tests + eval gates are REQUIRED for Sentinel, not optional)
  - .specify/templates/spec-template.md ............ ✅ aligned (no change needed)
  - .specify/templates/checklist-template.md ....... ✅ aligned (no change needed)

Follow-up TODOs: none. RATIFICATION_DATE set to project setup date (2026-06-06).
-->

# Sentinel Constitution

Sentinel is an AI-driven SOAR (Security Orchestration, Automation & Response) platform that
ingests Wazuh-format alerts and processes each through a supervisor-coordinated pipeline of three
agents — triage → enrichment → response — backed by a temporal incident-memory layer. This
constitution defines the non-negotiable principles that govern how Sentinel is built. It
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

The triage agent structurally holds **no** action tools; the response agent is the **only** agent
with action tools, and this separation is enforced via dependency injection, not prompt text.
Mandatory injection/jailbreak guardrails MUST run over alert-derived **and** feed-derived text —
both are attacker-influenceable — and a red-team CI gate MUST block any regression. A redaction
layer MUST run before anything leaves the service: every log line, trace span, memory write, and
dashboard view; a redaction eval proves no secret ever appears unredacted.

**Rationale**: a prompt-injected alert that hijacks triage still cannot execute a remediation,
because the capability is absent by construction rather than discouraged by instruction.

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
interrupt never move when the slice shrinks. All feed- and knowledge-sourced text passes the same
guardrails as alert text.

**Rationale**: the "gets smarter over time" capability must be real and defensible, yet never a
single point of failure that can sink v1.

### VII. Production Engineering Standards

Sentinel is async all the way down (`httpx`, async SQLAlchemy, async LLM SDK; `asyncio.gather`
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

Sentinel sits **deliberately downstream of detection**: a SOAR consumes alerts and orchestrates the
response; it is not a detector. The following are explicitly **out of scope for v1** and MUST NOT
be built unless a tier checkpoint is met with genuine surplus: an ML anomaly detector (roadmap
v2a/v3), multi-tenancy, an embeddable widget, live network capture, an LLM-driven supervisor, and
any fourth agent (supervisor + triage/enrichment/response only).

Delivery is **layered and independently shippable**. Each tier is a complete, honest deliverable on
its own:

| Tier | Adds | Checkpoint |
|------|------|-----------|
| **T1 — v1** | Full SOAR: ingest → triage → enrich → respond, seeded corpus, Graphiti memory, approval interrupt, React dashboard, evals + 3-tier tests in CI | End of day 9 — tag `v1` |
| **T2 — v2c** | Feedback loop: incident memory tunes future triage/severity | End of day 10 |
| **T3 — detector** | Lightweight rule/threshold detector that fires alerts into the existing ingestion schema | Days 11–12 |
| **T4 — v2a** | ML anomaly layer (stretch) | Only if T1–T3 green with real surplus; otherwise documented as v3 |

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
- **CI gates from day 1.** The eval suite (triage F1, supervisor routing, retrieval hit@k/MRR,
  temporal-memory, red-team, redaction, smoke) gates merges; gates land green as their component
  does and the full suite runs on both LLM providers at the day-9 freeze.
- **Reproducibility is a deliverable.** A fresh-clone `docker-compose up` MUST come up clean; the
  final submission is a public repo with a clean stack and the `v1.0.0-capstone` tag.
- **Defensible by design.** Every non-obvious architectural choice (auto/approval allowlist, the
  Graphiti go/no-go, fallback boundaries) is recorded and defended in `DECISIONS.md`.

## Governance

This constitution supersedes other working practices for Sentinel. Compliance is verified at every
PR and tier checkpoint: a change that violates a principle MUST be rejected or accompanied by a
justified, time-bound exception recorded in `DECISIONS.md`. Complexity MUST be justified against a
simpler rejected alternative.

Amendments are made by editing this file with a clear rationale and propagating the change to all
dependent templates (`plan-template.md`, `spec-template.md`, `tasks-template.md`) in the same
change. Versioning follows semantic versioning:

- **MAJOR** — backward-incompatible governance changes or removal/redefinition of a principle.
- **MINOR** — a new principle or section, or materially expanded guidance.
- **PATCH** — clarifications, wording, and non-semantic refinements.

For day-to-day runtime guidance (technologies, structure, shell commands), developers and agents
read the current plan and the relevant component `SPEC.md`, as directed by `CLAUDE.md`.

**Version**: 1.0.0 | **Ratified**: 2026-06-06 | **Last Amended**: 2026-06-06
