# Feature Specification: Alert Ingestion Pipeline

**Feature Branch**: `004-ingestion-pipeline`

**Created**: 2026-06-08

**Status**: Draft

**Input**: User description: "depending on docs/resources/SOAR_brief.md and docs/resources/SOAR_Plan.md make things simple and don't overengineer while working" — Component #4 (`SPEC-ingestion`): Wazuh-format adapter, webhook, Redis queue, async worker, incident object schema, grounding pipeline.

## Overview

This component is the **front door** of Sentinel. It accepts security alerts in Wazuh's alert format,
turns each accepted alert into a durable **Incident** object, and hands that Incident to an async worker
that prepares ("grounds") it for the downstream triage → enrichment → response pipeline. It owns the
**Incident schema** — the single contract every later component imports — and the **grounding step** that
assembles the structured evidence the agents will reason over.

Per the plan's scope discipline, this slice is kept deliberately small: it moves an alert from
`source → queue → worker → incident object`. It does not triage, enrich, respond, or detect — those are
later components whose seams are merely reserved here.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest a Wazuh alert (Priority: P1)

The upstream detector (Wazuh) sends a security alert to Argus over a webhook. Argus validates the
payload, redacts sensitive content, records it as a new Incident, queues it for processing, and
immediately acknowledges receipt — without waiting for the alert to be analyzed.

**Why this priority**: This is the irreducible MVP. Without a reliable, non-blocking front door, no alert
ever enters the system and nothing downstream can run. On its own it already delivers value: alerts are
durably captured and visible as Incidents.

**Independent Test**: POST a sample Wazuh alert to the webhook and assert a `202 Accepted` with an
incident id is returned, exactly one Incident record exists in a `received` state, and exactly one
processing job is on the queue.

**Acceptance Scenarios**:

1. **Given** a well-formed Wazuh alert, **When** it is POSTed to the webhook, **Then** the system returns
   `202 Accepted` with an incident id, persists one Incident with an initial status, and enqueues one
   processing job.
2. **Given** a valid alert containing a secret or PII value, **When** it is ingested, **Then** the stored
   Incident, the queue message, and all log/trace output contain only redacted forms of that value.
3. **Given** the request is missing valid authentication, **When** it is POSTed, **Then** the system
   rejects it (`401`) and creates no Incident and enqueues no job.

---

### User Story 2 - Worker grounds the incident (Priority: P2)

An async worker continuously consumes queued jobs. For each one it loads the Incident, runs the grounding
step — normalizing the raw Wazuh alert into the Incident's structured evidence (normalized event fields,
the detector's verdict, and a severity derived from the Wazuh rule level) — persists the grounded
Incident, and hands it to the downstream pipeline entry point.

**Why this priority**: This produces the structured evidence the triage agent will later reason over and
proves the `queue → worker → incident object` half of the flow. It is the second and final piece of the
core spine.

**Independent Test**: Place a processing job for a known Incident on the queue, run the worker, and assert
the Incident transitions to `grounded`, its structured-evidence fields are populated from the alert, and
the downstream handoff seam was invoked exactly once.

**Acceptance Scenarios**:

1. **Given** a queued job for a received Incident, **When** the worker processes it, **Then** the Incident
   is normalized into structured evidence, its status becomes `grounded`, and the downstream pipeline seam
   is invoked once.
2. **Given** a worker that crashes part-way through grounding, **When** the job is retried, **Then** the
   Incident is not duplicated and ends in a single consistent state (grounding is idempotent).
3. **Given** a job that fails repeatedly, **When** the bounded retry budget is exhausted, **Then** the
   Incident is marked `failed` (terminal) and is never left stuck in an in-progress state.

---

### User Story 3 - Deduplicate repeat alerts (Priority: P3)

The same alert can arrive more than once (retries from the detector, replayed batches). Sentinel
recognizes a repeat of a recently-seen alert and does not create a second Incident for it.

**Why this priority**: Prevents duplicate work and duplicate Incidents flooding the queue and dashboard.
Valuable robustness, but the spine in P1/P2 is viable without it.

**Independent Test**: POST the same alert twice within the dedup window and assert exactly one Incident
exists; the second response returns the existing incident id.

**Acceptance Scenarios**:

1. **Given** an alert already ingested moments ago, **When** an identical alert arrives within the dedup
   window, **Then** no new Incident is created and the existing incident id is returned.
2. **Given** an identical alert arriving *after* the dedup window has elapsed, **When** it is ingested,
   **Then** a new Incident is created (it is treated as a fresh occurrence).

---

### Edge Cases

- **Malformed payload**: fails schema validation → `422` with a structured error; no Incident, no job.
- **Oversized payload**: exceeds the configured size limit → rejected (`413`); no Incident, no job.
- **Unknown extra fields** in the Wazuh alert are tolerated (ignored), not rejected.
- **Missing severity signal** (no usable rule level): the Incident is still created with a documented
  default severity and flagged, rather than dropped.
- **Queue backend unreachable at ingest time**: the request fails (`503`) and no orphan Incident is left
  committed — accept-and-enqueue is treated as one unit of work.
- **Redaction failure**: the system fails closed (the raw alert is never persisted, logged, or enqueued).
- **Burst of alerts**: the endpoint stays responsive because it only validates/redacts/persists/enqueues;
  the worker drains the backlog asynchronously.

## Requirements *(mandatory)*

### Functional Requirements

**Ingestion endpoint**

- **FR-001**: System MUST expose an authenticated webhook endpoint that accepts security alerts in Wazuh
  alert JSON format.
- **FR-002**: System MUST validate each alert against a typed schema and reject malformed payloads with a
  `422` and a structured error, creating no Incident and enqueuing no job.
- **FR-003**: System MUST reject payloads larger than a configured maximum size.
- **FR-004**: System MUST redact PII and credentials from alert content (via the observability redaction
  seam) before that content is persisted, logged, or enqueued; redaction failure MUST fail closed.
- **FR-005**: System MUST persist each accepted alert as one Incident with a unique incident id and an
  initial status, and MUST return `202 Accepted` with that id without waiting for processing to complete.
- **FR-006**: System MUST enqueue exactly one processing job per newly-created Incident; if the job cannot
  be enqueued, the request MUST fail and leave no orphan Incident.
- **FR-007**: System MUST deduplicate alerts: an alert whose dedup key matches one seen within a
  configurable window MUST NOT create a second Incident, and the endpoint MUST return the existing
  incident id.

**Worker & grounding**

- **FR-008**: System MUST provide an async worker (the reserved `worker` process) that consumes processing
  jobs from the queue.
- **FR-009**: The worker MUST run a grounding step that normalizes the Wazuh alert into the Incident's
  structured evidence — normalized event fields, the detector's verdict, and a severity derived from the
  Wazuh rule level.
- **FR-010**: The worker MUST persist the grounded Incident and hand it to the downstream pipeline entry
  point (a reserved seam; a stub at this component).
- **FR-011**: A job that fails processing MUST be retried a bounded number of times and then mark its
  Incident `failed` (terminal); grounding MUST be idempotent so a retried job neither duplicates the
  Incident nor leaves it stuck in an in-progress state.

**Contracts & cross-cutting**

- **FR-012**: The Incident object MUST be defined once as a typed domain model and be the single import
  contract for downstream components (no re-declaration).
- **FR-013**: Every ingest and worker step MUST emit a trace span and propagate a correlation id, and MUST
  never emit unredacted alert content to logs or traces.
- **FR-014**: The readiness gate MUST report not-ready when the queue backend is unreachable.

### Key Entities *(include if feature involves data)*

- **Wazuh Alert**: the raw inbound payload (rule metadata incl. level/id/description, agent, event data,
  full log, timestamp). Treated as **untrusted input**. Accepted as a representative subset of Wazuh's
  documented alert fields; unknown fields tolerated.
- **Incident**: the canonical internal object and downstream contract. Holds the incident id, status,
  derived severity, the normalized event, the grounded structured evidence, the dedup key, a correlation
  id, and timestamps. Created at ingest, advanced by the worker.
- **Processing Job**: the queue message that references an Incident id and triggers worker processing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The webhook acknowledges an accepted alert in under 300 ms at p95, independent of how long
  downstream processing later takes.
- **SC-002**: Over a replay of the sample alert set, 100% of accepted alerts result in exactly one
  Incident and exactly one enqueued job — no loss, no duplication.
- **SC-003**: Duplicate alerts within the dedup window produce exactly one Incident (zero duplicates) over
  the sample set.
- **SC-004**: 100% of malformed or oversized payloads are rejected with no Incident created and no job
  enqueued.
- **SC-005**: A planted secret/PII value never appears unredacted in any persisted Incident, queue
  message, log line, or trace span.
- **SC-006**: Under fault injection (worker crash mid-job), every affected Incident reaches a terminal
  state (`grounded` or `failed`) and none remains stuck in an in-progress state.
- **SC-007**: 100% of grounded Incidents have their structured-evidence fields populated from the alert.

## Assumptions

- **Wazuh format** is the external contract; we accept a representative subset of the documented Wazuh
  alerts JSON and tolerate unknown fields. Sample alerts drive tests and the demo; behavior is identical
  to live ingestion (per the brief's "replayed sample alerts" decision).
- **Redis** is the queue backend and the dedup store, and the worker is the reserved
  `python -m backend.worker` process — both locked by Component #1. The `queue` seam reserved in #1 is
  filled here; queue semantics are kept simple (a job is delivered, retried a bounded number of times on
  failure, then dead-lettered to a `failed` Incident state) rather than a full visibility-timeout broker.
- **Webhook authentication** reuses a simple shared-secret bearer token resolved from Vault (the detector
  is a trusted machine client). Richer auth/roles are the dashboard's concern (Component #12) and out of
  scope here.
- **Redaction** and **tracing/correlation-id** are consumed from the Component #2 observability seam, not
  re-implemented.
- **Dedup key** is derived from stable alert content (e.g., rule id + agent + a content hash) over a
  configurable time window (default on the order of minutes).
- The **downstream pipeline** (supervisor #7, agents #8–#10) is stubbed; the worker hands off to a
  reserved seam and does not analyze the Incident.

## Out of Scope

- The IOC/intel cache and outbound rate-limiting (consumed by the enrichment agent, Component #9) — only
  the queue and alert dedup are added here.
- The full incident lifecycle states and transitions (owned by the supervisor, Component #7); this
  component defines only the minimal `received → grounded | failed` statuses needed for the ingest spine.
- Triage, enrichment, response, and remediation logic (Components #8–#10).
- A detector that *fires* alerts from raw events (Component #14 / T3); this component consumes alerts, it
  does not produce them.
