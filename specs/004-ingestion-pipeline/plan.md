# Implementation Plan: Alert Ingestion Pipeline

**Branch**: `004-ingestion-pipeline` | **Date**: 2026-06-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-ingestion-pipeline/spec.md`

## Summary

Fill the front-door seams the platform foundation (#1) reserved as stubs — `infra/queue.py`,
`infra/cache.py`, `routers/ingest.py`, and `worker.py` — so a Wazuh-format alert flows
`source → webhook → queue → worker → Incident object`. The webhook is intentionally thin
(**validate → redact → dedup → persist → enqueue → `202`**); the async **worker** consumes the queue and
runs a deterministic **grounding** step that normalizes the alert into the Incident's structured evidence,
then hands off to a reserved downstream-pipeline seam (a stub until the supervisor, #7). This component
**owns the Incident schema** (`domain/incident.py`) — the single contract every later spec imports — and
nothing more: no triage, enrichment, response, or detection.

Durability lives in **Postgres** (the `incidents` table is the source of truth for status); **Redis** is
the dispatch + dedup substrate. Keeping with the user's "make it simple, don't overengineer" steer, the
queue is a **minimal reliable Redis-list pattern** (`BLMOVE` main→processing, `LREM` on done, drain
processing→main on worker startup) rather than a broker, dedup is a single **`SET NX EX`** on a content
fingerprint, and severity is a **deterministic level→band table** — no LLM anywhere in this component.

The component is "done" when unit + integration + e2e are green: a POSTed sample alert returns `202`, the
worker grounds it to `grounded`, a duplicate within the window collapses to one Incident, a planted secret
never appears unredacted, and a worker crash mid-job still drives the Incident to a terminal state. It
adds **one infra activation** already reserved in #1's compose: the **`redis` service** and the **`worker`
container** (both ship commented-out in `compose.yaml` today).

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`); managed with `uv`.

**Primary Dependencies**: **`redis>=5`** (`redis.asyncio`) — the one new dependency, confined to
`infra/cache.py` / `infra/queue.py` (no-bypass, mirroring how vendor SDKs are confined). Everything else is
reuse: FastAPI (`routers/ingest.py`), Pydantic v2 (the `WazuhAlert` and `Incident` boundary types), async
SQLAlchemy + Alembic (the `incidents` table), and the **#2 observability seam** (`span()`, `get_logger`,
the `Redactor` at the `SNAPSHOT`/`LOG` boundaries, correlation-id binding) for redaction and tracing.

**Storage**: **Postgres** `incidents` table (new Alembic migration `0003_incidents`) — the durable source
of truth for incident status; flexible sub-objects (redacted raw alert, normalized event, evidence) are
JSONB validated by Pydantic at the boundary. **Redis** holds only transient dispatch state (the queue +
processing list) and dedup keys (TTL'd) — no durable data. No MinIO use here.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). **Unit** = Wazuh→normalized mapping,
severity banding, dedup-fingerprint determinism, grounding evidence assembly, and the intake state machine
with Redis/DB faked. **Integration** = real **Redis** (enqueue/reliable-dequeue/recover, `SET NX` dedup)
and real **Postgres** (incident persistence + migration round-trip), worker reliable-recovery under a
simulated crash. **e2e** = POST a sample alert via the app → `202` → run the worker → Incident reaches
`grounded` with evidence populated; duplicate collapses; planted-secret redaction; fault-injected crash
reaches a terminal state.

**Target Platform**: Linux containers under Docker Compose v2 on a single host (dev/CI). Activates the
reserved **`redis`** service and the **`worker`** container (one image, different command —
`python -m backend.worker`); `vault-seed` gains a `secret/ingest` entry (the webhook shared-secret token).

**Project Type**: Backend feature inside the existing modular-monolith `backend/` package — it *fills*
reserved #1 seams and adds the Incident domain type, a thin ingestion service layer, one repository, and
one migration. No restructuring.

**Performance Goals**: the webhook does only validate/redact/dedup/persist/enqueue — all cheap, bounded
work — so it acknowledges in **< 300 ms p95** (SC-001) independent of downstream processing. Redaction is
the only non-trivial on-path cost and is already #2's measured-overhead concern. Worker throughput is
demo-scale (replayed alerts); a single worker is sufficient and assumed.

**Constraints**: **fail-closed redaction** (raw alert never persisted/logged/enqueued if redaction
raises); **accept-and-enqueue is atomic** (enqueue failure ⇒ `503`, no orphan Incident committed);
**at-least-once** delivery with **bounded retry → `failed`** (no lost or stuck Incident, SC-006);
**idempotent grounding** (re-run on a `grounded` Incident is a no-op); single typed `redis` + `ingest`
settings sections (`extra="forbid"`); webhook shared-secret resolved from Vault, **required → fail boot**;
async all the way down; alert text is **untrusted input** (redacted now; injection rails are #11's seam).

**Scale/Scope**: single-SOC, single-worker, replayed-alert demo scale. **In scope**: queue + dedup +
worker + grounding + Incident schema. **Out of scope** (seams only): the IOC/intel cache and outbound
rate-limiting (#9), the full lifecycle state machine (#7), triage/enrich/respond (#8–#10), and a detector
that *fires* alerts (#14).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing.*

Derived from `.specify/memory/constitution.md` (v1.0.0).

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green and
      pushed. Internal milestones keep PRs ≤ ~400 lines: **(a)** Incident schema + `incidents` migration +
      repository + intake service + Wazuh adapter + `POST /ingest/wazuh` returning `202` + unit (the MVP —
      US1); **(b)** `redis` cache/queue seams + worker loop + grounding + downstream-handoff stub +
      integration (US2); **(c)** dedup (`SET NX`) + reliable-recovery + readiness `check_redis` + e2e +
      fault injection (US3).
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: three tiers planned, green daily,
      **≥80% on new code**. **No LLM in this component**, so the both-providers gate is **N/A** here; the
      existing **smoke** gate is strengthened (the stack now includes `redis` + `worker`) and the existing
      **redaction** gate is exercised through the new ingest path (alert payloads with planted secrets).
      No new eval gate is invented — the triage/routing/retrieval gates land with their own components.
- [x] **III. Security Boundaries Are Structural, Not Prompted**: alert text is **untrusted, attacker-
      influenceable input** — it is **redacted before it is persisted, logged, or enqueued** (`SNAPSHOT`/
      `LOG` boundaries via #2), failing closed on redaction error. Triage-has-no-action-tools is N/A (no
      agents here). The webhook carries a **minimal shared-secret guard** (full auth/roles are #12);
      injection/jailbreak rails over this same alert text are #11's seam (the redaction boundary is the
      part owed now).
- [x] **IV. Determinism First**: the entire component is **deterministic plumbing** — Wazuh mapping,
      severity banding, dedup fingerprint, and grounding are pure functions with **no LLM call**. This is
      exactly "use determinism where it suffices"; the grounding step *assembles* the evidence the triage
      agent (#8) will later reason over, it does not reason itself.
- [x] **V. Human-in-the-Loop**: N/A — no remediation actions. The Incident `status` enum is defined here
      with only the minimal `received → grounding → grounded | failed` values; the `awaiting_approval`
      and disposition states are added by their owning specs (#7/#10) extending this enum.
- [x] **VI. Temporal Memory & Graceful Degradation**: memory N/A. **Graceful degradation is central** —
      Redis down ⇒ `/ready` reports not-ready (`check_redis`) and ingest returns `503` with **no orphan
      Incident**; a worker crash mid-job is recovered (processing-list drain on startup) so the Incident
      reaches a terminal state. No knowledge/feed text is ingested here (that's #5).
- [x] **VII. Production Engineering Standards**: async (`redis.asyncio` + async SQLAlchemy); **DI**
      (`Depends` supplies the cache, queue, repository, and intake service; substitutable in tests);
      **lifespan singletons** (`CacheProvider`, `QueueProvider` via the existing provider seam — Redis
      pool built once); **Pydantic** at every boundary (`WazuhAlert`, `Incident`, evidence); structured
      logging + correlation-id and off-path span export via #2; typed `redis`/`ingest` settings
      (`extra="forbid"`) with the webhook secret failing boot; `uv`-pinned `redis` dep.
- [x] **Scope & Tiers**: strictly v1 / T1; no ML detector / multi-tenancy / widget / live capture / LLM
      supervisor / 4th agent; respects inward-only layering (`routers → services → repositories → infra`,
      `domain` isolated). **One infra activation** (the `redis` service + `worker` container) — see
      Complexity Tracking; both were pre-reserved in #1's compose, so this is planned, not a surprise.

**Result: PASS — one tracked, pre-reserved infra activation (the `redis` service + `worker` container).**
The single new dependency (`redis`) is the official async client for the queue/dedup substrate the brief
and #1 already committed to; it is confined to `infra/` (no-bypass) and is not a constitution deviation.

## Project Structure

### Documentation (this feature)

```text
specs/004-ingestion-pipeline/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 — decisions & rationale (ID1–ID11)
├── data-model.md        # Phase 1 — the Incident schema + WazuhAlert + evidence + settings shapes
├── quickstart.md        # Phase 1 — POST an alert; watch it ground; verify dedup / redaction / recovery
├── contracts/           # Phase 1 — the outward contracts later specs consume
│   ├── ingest-webhook-api.md   # POST /ingest/wazuh: request/response, status codes, auth, size/dedup
│   ├── incident-schema.md      # the Incident domain object — the single import contract (seam to #7/#8/#12)
│   └── queue-and-worker.md     # queue seam (enqueue/reliable-dequeue/recover) + worker grounding + handoff stub
├── checklists/
│   └── requirements.md  # (created by /speckit-specify)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

> Fills reserved #1 seams and adds the minimum new files; **no restructuring**. New files marked `+`.

```text
backend/
├── domain/
│   └── incident.py     + # NEW: pure types — Incident, IncidentStatus, Severity, NormalizedEvent,
│                          #      Evidence, IngestResult; the single downstream contract (FR-012)
├── routers/
│   └── ingest.py         # FILL: POST /ingest/wazuh — auth guard → size check → validate → intake service
├── services/
│   ├── wazuh.py        + # NEW: Wazuh adapter — parse raw alert → NormalizedEvent + deterministic severity
│   ├── intake.py       + # NEW: accept() — validate → redact → dedup → persist → enqueue → IngestResult
│   ├── grounding.py    + # NEW: ground() — NormalizedEvent → Evidence (the grounding pipeline)
│   └── pipeline.py     + # NEW: dispatch_to_pipeline() — downstream handoff seam (logging stub; filled by #7)
├── repositories/
│   └── incidents.py    + # NEW: IncidentRepository — create / get / update_status / claim-for-grounding
├── infra/
│   ├── cache.py          # FILL: CacheProvider — redis.asyncio pool (lifespan singleton); dedup helpers
│   ├── queue.py          # FILL: QueueProvider + RedisTaskQueue — enqueue / BLMOVE-dequeue / ack / recover
│   ├── config.py         # EDIT: add RedisSettings + IngestSettings; register on Settings; extend
│   │                     #       _KNOWN_SENTINEL_SECTIONS with "redis","ingest"; require secret/ingest path
│   └── health.py         # EDIT: add check_redis() — PING the pool
├── worker.py             # FILL: recover() → consume loop: dequeue → load → ground → handoff → grounded
├── dependencies.py        # EDIT: add get_cache(), get_queue(), get_incident_repo(), get_intake_service()
├── main.py                # EDIT: register CacheProvider + QueueProvider after observability
└── routers/__init__.py    # EDIT: include ingest.router in api_router

backend/db/migrations/versions/
└── 0003_incidents.py    + # NEW: create the incidents table (+ indexes on status, dedup_fingerprint)

config/
└── eval_thresholds.yaml   # EDIT (light): note the smoke gate now covers redis + worker; redaction gate
                           #               extended to the ingest path. No new gate invented.

compose.yaml               # EDIT: activate the reserved `redis` service + `worker` container; add redis to
                           #       api/worker depends_on; vault-seed writes secret/ingest
deploy/                    # (unchanged) worker reuses deploy/api/Dockerfile — one image, different command
.env.example               # EDIT: ingest webhook token placeholder + redis url + ingest tunables
pyproject.toml             # EDIT: add redis>=5; drop worker.py, routers/ingest.py, infra/cache.py,
                           #       infra/queue.py from coverage omit (now measured)

tests/
├── unit/                  # wazuh mapping, severity banding, dedup fingerprint, grounding, intake FSM (faked I/O)
├── integration/          # real redis (queue/dedup/recover), real postgres (incidents migration + repo)
└── e2e/                   # POST alert → 202 → worker grounds; dedup; redaction; crash-recovery
tests/fixtures/wazuh_alerts/*.json  # a handful of representative sample alerts (drive tests + the demo)
```

**Structure Decision**: Stay inside the established modular-monolith `backend/` and **fill the reserved
#1 seams** rather than restructure. The Incident contract goes in `domain/incident.py` (no outward deps,
satisfying the domain-isolation `import-linter` contract) so #7/#8/#12 import one schema defined once. The
**Redis client is confined to `infra/cache.py` / `infra/queue.py`** (the no-bypass boundary). Ingestion
logic lives in the **`services/` layer** (Wazuh adapter, intake, grounding, the handoff stub) with one
**`repositories/incidents.py`** owning all `incidents` table access — honoring the inward-only layering
(`routers → services → repositories → infra`, `domain` isolated). The cache and queue are **lifespan
singletons via the existing provider seam** (`container.py`); routers and the worker obtain them only via
DI. Every persist/log/enqueue passes through the **#2 redaction boundary** — this component re-implements
no tracing or redaction. The one infra activation is the pre-reserved **`redis` service + `worker`
container**.

## Complexity Tracking

> One tracked activation of infrastructure that #1 already reserved (commented-out) in `compose.yaml`;
> justified below and recorded in `DECISIONS.md`.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Activate the `redis` compose service + `worker` container | The brief and #1 fixed the push-webhook→queue→worker shape; the queue, dedup, and at-least-once recovery cannot be real without Redis, and the asynchronous "grounding off the request path" cannot be demonstrated without a worker container. Both ship pre-reserved (commented) in #1's `compose.yaml`. | "Process inline in the request handler" rejected: violates the thin-`202`-then-async contract (FR-005/FR-006), couples acknowledgment latency to grounding work, and removes the queue the supervisor (#7) will consume. "In-memory queue" rejected: loses at-least-once across a worker crash (SC-006) and isn't shared between the API and worker containers. |
| New `redis>=5` dependency | Official async client (`redis.asyncio`) for the queue + dedup substrate; confined to `infra/`. | Hand-rolled RESP-over-`httpx`/sockets rejected: re-implements connection pooling, `BLMOVE`, and `SET NX EX` that the maintained client already gets right, for no benefit. (Dependency-weight note, not a constitution deviation.) |
