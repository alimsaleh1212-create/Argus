# Phase 0 — Research & Decisions: Alert Ingestion Pipeline

**Component**: #4 `SPEC-ingestion` · **Date**: 2026-06-08

The spec left **zero `[NEEDS CLARIFICATION]` markers** — the brief and #1's `DECISIONS.md` already fixed
the push-webhook→queue→worker shape. This file records the design decisions that turn that shape into an
implementation, each chosen under the user's explicit steer: **make it simple, don't overengineer**.
Decisions are numbered `ID1…` (Ingestion Decision) and carried into `DECISIONS.md`.

Format per item — **Decision / Rationale / Alternatives rejected**.

---

## ID1 — Postgres is the source of truth; Redis is only transient dispatch

**Decision**: The `incidents` table in Postgres is the durable record of every Incident and its `status`.
Redis holds only the dispatch queue, a per-worker processing list, and TTL'd dedup keys — nothing whose
loss is unrecoverable.

**Rationale**: We already run async SQLAlchemy + Alembic (#1). Anchoring durability in Postgres means a
flushed Redis only costs in-flight dispatch (recoverable by re-enqueuing non-terminal incidents), never
the incident record itself. It also gives the dashboard (#12) and supervisor (#7) one obvious place to
read incident state. This keeps the queue dumb and the reliability story simple.

**Alternatives rejected**: *Redis as the system of record* (Streams holding incident state) — couples
durability to an in-memory store, duplicates state the relational DB models better, and complicates the
#12 read side. *Two sources of truth* — invites drift.

---

## ID2 — Queue = minimal reliable Redis-list pattern (`BLMOVE` + processing list), not a broker

**Decision**: Implement `RedisTaskQueue` over Redis lists:
- **enqueue**: `LPUSH queue:incidents <incident_id>`.
- **dequeue (worker)**: `BLMOVE queue:incidents queue:processing RIGHT LEFT <block_timeout>` — atomically
  moves the id to a processing list so an in-flight job is never lost.
- **ack (on success)**: `LREM queue:processing 1 <incident_id>`.
- **recover (worker startup)**: drain everything left in `queue:processing` back to `queue:incidents`
  (anything there is a previous worker's in-flight job that crashed before ack).

**Rationale**: This is the textbook ~10-line reliable-queue pattern. It delivers **at-least-once** and
**crash recovery** (SC-006) with no broker, no new heavy dependency, and no consumer-group bookkeeping —
the right amount of machinery for a single-worker, demo-scale SOC. Idempotent grounding (ID7) makes the
"at-least-once ⇒ possible re-delivery" harmless.

**Alternatives rejected**: *Redis Streams + consumer groups* — correct but more API surface (XADD/XACK/
XAUTOCLAIM, pending-entries handling) than a single-worker demo needs; revisit only if multi-worker
scaling lands. *Plain `BRPOP`* — loses the job if the worker dies between pop and completion (no
at-least-once). *A task framework (Celery/arq/dramatiq/saq)* — a large dependency and operational model
for what is one queue; overengineering.

---

## ID3 — Dedup = `SET <fingerprint> <id> NX EX <window>` on a content fingerprint

**Decision**: At intake, compute a stable fingerprint and attempt
`SET dedup:<fingerprint> <incident_id> NX EX <dedup_window_s>`. If it returns "not set" (key already
exists), the alert is a duplicate: read the stored incident id and return it (no new Incident, no enqueue).
Fingerprint = SHA-256 of the **redacted** normalized triplet `(rule_id, agent_id, content_signature)`,
where `content_signature` is a hash of the salient event fields (not the volatile timestamp).

**Rationale**: One atomic Redis op gives both the "seen recently?" test and the TTL window in a single
round-trip — no race, no separate sweep. Fingerprinting on rule+agent+content (not the timestamp) collapses
detector retries and replayed batches while letting a genuinely new occurrence after the window through
(per US3 acceptance #2). Computing it over the **redacted** form keeps secrets out of Redis keys too.

**Alternatives rejected**: *A unique DB constraint on the fingerprint* — would reject the legitimate
re-occurrence after the window (no TTL semantics) and turns a dedup into a hard error. *Bloom filter /
`SETBIT`* — premature optimization at demo scale; loses the "return the existing id" affordance.

---

## ID4 — Incident schema is one Pydantic model in `domain/incident.py` with JSONB sub-objects

**Decision**: Define `Incident` (and `IncidentStatus`, `Severity`, `NormalizedEvent`, `Evidence`,
`IngestResult`) as pure Pydantic v2 types in `domain/incident.py` — no outward imports, satisfying the
domain-isolation `import-linter` contract. The `incidents` table stores scalar columns for the queryable
fields (`id`, `status`, `severity`, `dedup_fingerprint`, `correlation_id`, `attempts`, timestamps) and
**JSONB** for the three flexible sub-objects (`raw_alert` redacted, `normalized_event`, `evidence`).

**Rationale**: One schema "defined once and imported by the rest" is the seam rule for ingestion→state
machine. Scalars-for-query + JSONB-for-shape is the same pattern #2 used for `trace_spans.attributes`;
it avoids over-modeling the evidence into a wide relational schema we'd reshape every time an agent spec
adds a field. Pydantic validates the JSONB shape at the boundary so it stays typed in code.

**Alternatives rejected**: *Fully normalized relational model* (separate tables for event/evidence) —
heavy, premature, and churned by every downstream spec. *Schema in `services/` or `repositories/`* —
would let infrastructure leak into the contract every later layer imports; the domain layer is the
correct, dependency-free home.

---

## ID5 — Minimal `IncidentStatus` enum now; later specs extend it

**Decision**: This component defines only `received → grounding → grounded` (happy path) and `failed`
(terminal). The downstream lifecycle values (`triaging`, `enriching`, `awaiting_approval`, `resolved`,
`escalated`, …) are **added by their owning specs** (#7/#8/#10) extending the same enum.

**Rationale**: The seam rule gives ingestion the *schema*; the supervisor (#7) owns the *transitions*.
Defining only what this component drives keeps the contract honest and avoids inventing states whose
semantics another spec owns. `StrEnum` (matching #2's `SpanKind`/`SpanStatus`) makes extension a one-line
addition with no migration churn (status is stored as text).

**Alternatives rejected**: *Enumerate the whole lifecycle now* — guesses at #7/#10 semantics and risks a
rename later. *Free-form string status* — loses validation and invites typos across specs.

---

## ID6 — Wazuh adapter accepts a tolerant subset; severity is a deterministic level→band table

**Decision**: `services/wazuh.py` parses the documented Wazuh alert fields it needs (`rule.level`,
`rule.id`, `rule.description`, `rule.groups`, `agent.*`, `data.*`, `full_log`, `timestamp`,
`id`/`@timestamp`) into a `NormalizedEvent`, **tolerating and ignoring unknown fields**
(`model_config = ConfigDict(extra="ignore")`). Severity is a fixed mapping of Wazuh `rule.level` (0–15):
`0–3 → low`, `4–7 → medium`, `8–11 → high`, `12–15 → critical`. A missing/unparseable level yields
`Severity.MEDIUM` with an `evidence.flags += ["severity_defaulted"]` marker (per the spec edge case).

**Rationale**: Wazuh is an external contract we don't control; `extra="ignore"` keeps us robust to version
drift while Pydantic still validates the fields we depend on. A small, documented, deterministic severity
table is exactly the "determinism where it suffices" the constitution wants — and it's defensible line by
line, unlike a magic number. Defaulting-rather-than-dropping honors "never silently lose an alert."

**Alternatives rejected**: *Strict full-schema validation of every Wazuh field* — brittle against Wazuh's
evolving payload; rejects alerts for fields we don't use. *Config-driven severity thresholds* — a tunable
we don't need in v1; the band table lives in code and is trivially changed if it ever must be.

---

## ID7 — Grounding is deterministic and idempotent; the downstream handoff is a logging stub

**Decision**: `services/grounding.py::ground(incident)` builds the `Evidence` object from the
`NormalizedEvent` (the detector verdict + severity, the salient structured fields, and empty placeholders
for the retrieved context that #5/#6 will later fill) — a pure function, **no LLM**. The worker claims the
Incident with a guarded transition (`received → grounding`), runs grounding, sets `grounded`, then calls
`services/pipeline.py::dispatch_to_pipeline(incident)`, which in this component is a **logging no-op stub**
(the supervisor seam, filled by #7). Grounding is **idempotent**: if the Incident is already `grounded`,
the worker skips it.

**Rationale**: Grounding is the "real engineering work that assembles evidence before the agent reasons"
the brief calls out — and it's deterministic, so it belongs here, fully testable without a model. A
logging stub for the handoff lets the e2e pipeline go green now (`grounded` is a clean terminal state for
#4) while #7 fills the seam without touching this code. Idempotency makes ID2's at-least-once delivery
safe.

**Alternatives rejected**: *Have the worker invoke triage now* — out of scope (#8) and would block #4 on
later components. *Raise `NotImplementedError` in the handoff* — would mark every grounded incident
`failed`, breaking the e2e green-bar. *Do grounding in the request handler* — violates the thin-`202`
contract.

---

## ID8 — Webhook auth = one shared-secret bearer token from Vault (`secret/ingest`); full auth is #12

**Decision**: `POST /ingest/wazuh` requires `Authorization: Bearer <token>`, compared (constant-time)
against a token resolved from Vault `secret/ingest` at startup. The path is added to `vault.required_paths`
so a **missing secret fails boot**; `vault-seed` writes a dev token. A bad/absent token ⇒ `401`, no
Incident, no enqueue (US1 acceptance #3).

**Rationale**: An unauthenticated public webhook on a security platform is indefensible, but full
auth/roles are explicitly #12's scope. A single shared secret is the *minimal* honest guard — machine-to-
machine, the detector is a trusted client — and resolving it from Vault matches exactly how `secret/minio`
and `secret/llm` are handled (secrets fail at startup, constitution VII). It is deliberately a guard, not
an auth system.

**Alternatives rejected**: *No auth, defer entirely to #12* — leaves the front door open through all of
T1. *Token in `pydantic-settings` as `SecretStr`* — workable (matches the Postgres DSN pattern) but
inconsistent with the Vault-for-credentials convention; Vault is one extra seed line. *mTLS / OAuth* —
far beyond a single-SOC demo; overengineering.

---

## ID9 — Redis client = `redis.asyncio`, confined to `infra/`; one shared pool as a lifespan singleton

**Decision**: Add `redis>=5` and use `redis.asyncio`. `CacheProvider` builds one connection pool on
startup (Provider protocol, like `DbEngineProvider`) and disposes it on shutdown; `QueueProvider` reuses
that pool (or builds alongside it). Imports of `redis` appear **only** in `infra/cache.py` and
`infra/queue.py` — the no-bypass boundary, mirroring how #2 confined `presidio`/`opentelemetry` and #3
confined the vendor SDKs.

**Rationale**: `redis-py`'s async client is the maintained, official option with pooling, `BLMOVE`, and
`SET NX EX` built in. One pool as a singleton matches every other resource in `container.py`. Confining
the import keeps the dependency swappable and the layering enforceable by `import-linter`.

**Alternatives rejected**: *`aioredis`* — merged into `redis-py`; using the standalone package is
legacy. *Hand-rolled RESP client* — re-implements pooling and commands for nothing (see Complexity
Tracking).

---

## ID10 — Atomic accept-and-enqueue; size guard before parse; bounded retry → `failed`

**Decision**:
- **Order of operations** in `intake.accept()`: size check → parse/validate → redact → dedup → **persist
  Incident (`received`)** → **enqueue**. If enqueue raises (Redis down), the just-created Incident is
  rolled back / not committed and the endpoint returns `503` — **no orphan** (spec edge case). Persist and
  enqueue are sequenced so the committed state and the dispatch agree.
- **Size guard**: reject payloads over `ingest.max_alert_bytes` (default 256 KiB) with `413` **before**
  JSON parsing.
- **Retry**: the worker increments `incidents.attempts` on a processing exception; below
  `ingest.max_attempts` (default 3) it re-enqueues, at/above it sets `failed` (terminal). Crash-without-
  exception is caught by ID2's startup recovery.

**Rationale**: Sequencing persist-then-enqueue with rollback-on-enqueue-failure is the simplest way to
honor "no orphan Incident" without distributed-transaction machinery. A pre-parse size guard cheaply
bounds memory/DoS surface. Bounded retry-then-terminal guarantees "never lost or stuck" (SC-006) with a
plain counter.

**Alternatives rejected**: *Enqueue-then-persist* — a crash between them loses the incident record.
*Outbox pattern / 2-phase commit* — robust but heavyweight for a single-host demo; the rollback-on-failure
ordering is sufficient. *Unbounded retry* — risks a poison message looping forever.

---

## ID11 — No new eval gate; strengthen the existing smoke + redaction gates

**Decision**: Do **not** invent an ingestion-specific eval gate. Instead: the **smoke** gate's
fresh-clone `docker compose up` now necessarily brings up `redis` + `worker` (so it already covers them),
and the **redaction** gate's synthetic incident is driven **through the new `/ingest/wazuh` path** with
planted secrets/PII in alert fields, asserting zero unredacted leakage in the stored Incident, queue
message, logs, and spans.

**Rationale**: The plan assigns eval gates to the components that own the measured behaviour (triage F1 →
#8, routing → #7, retrieval/temporal → #6, red-team → #11). Ingestion has no model output to score; its
correctness is covered by the three test tiers plus the redaction safety gate. Inventing a gate here would
be ceremony, not coverage — the opposite of the "don't overengineer" steer.

**Alternatives rejected**: *A bespoke "ingestion accuracy" gate* — there is no judgment to evaluate;
deterministic mapping is covered by unit tests. *Defer redaction coverage of the ingest path to #13* —
the redaction gate exists now and the ingest path is exactly where untrusted alert text first lands, so it
should be exercised the moment the path exists.

---

## Resolved unknowns

| Question | Resolution |
|----------|-----------|
| Queue mechanism? | Reliable Redis-list (`BLMOVE` + processing list + startup drain) — ID2 |
| Dedup mechanism + window? | `SET NX EX` on a content fingerprint; default window 300 s — ID3 |
| Where does the Incident schema live? | `domain/incident.py`, Pydantic, JSONB sub-objects — ID4 |
| Which statuses now? | `received/grounding/grounded/failed` only; extended later — ID5 |
| Severity from Wazuh? | Deterministic `rule.level` band table; default MEDIUM + flag — ID6 |
| What does "grounding" do at #4? | Deterministic evidence assembly; handoff is a logging stub — ID7 |
| Webhook auth? | Shared-secret bearer from Vault `secret/ingest`; full auth is #12 — ID8 |
| Redis client + placement? | `redis.asyncio`, confined to `infra/`, one pooled singleton — ID9 |
| No-orphan / no-loss guarantees? | Atomic accept-and-enqueue; bounded retry → `failed`; size guard — ID10 |
| New eval gate? | None; strengthen existing smoke + redaction gates — ID11 |
