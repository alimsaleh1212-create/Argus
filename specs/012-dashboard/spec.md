# Feature Specification: React Operations Dashboard

**Feature Branch**: `012-dashboard`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "depending on @docs/resources/SOAR_brief.md and @docs/resources/SOAR_Plan.md make" — the next component spec (plan row #12, `SPEC-dashboard`). Keep it simple; don't overengineer.

## Overview

The operations dashboard is the human surface of Argus: one authenticated single-page console where a SOC operator watches incidents flow through the triage → enrichment → response pipeline, inspects the evidence and rationale behind each decision, **approves or rejects the destructive remediations that parked on the human-in-the-loop interrupt** (the seam #10 left for a human), and reads at-a-glance KPIs that show the system is working and getting smarter over time. It is read-only except for the approve/reject decision — the supervisor remains the single writer of incident state. Everything it displays is already redacted at write time; the dashboard adds no path that re-exposes raw secrets or PII.

## Clarifications

### Session 2026-06-12

- Q: How should the single admin operator authenticate? → A: Admin username + password (credential in the platform secret store) exchanged for a short-lived **signed session token** carrying a role attribute.
- Q: What does the live incident queue show by default? → A: **All** incidents (active + resolved), defaulted to the active/in-flight view, with resolved/terminal history browsable via filter.
- Q: How is "memory-hit rate" defined? → A: Share of incidents that **reached enrichment** for which ≥1 relevant prior incident or temporal fact was surfaced (denominator = enriched incidents).
- Q: How do the queue/KPIs stay live? → A: **Server push** over a persistent stream (WebSocket/SSE), with graceful reconnect and fallback to on-demand refresh.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate the live incident queue (Priority: P1)

An operator signs in and lands on a live incident queue. Every incident shows its status, severity, and disposition at a glance; the operator filters and sorts (e.g. "show me everything awaiting approval", "highest severity first"), pages through the backlog, and opens any incident to a detail view showing its grounded summary, evidence, current disposition, and the audit trail of actions taken on it.

**Why this priority**: This is the minimum viable console. Without the queue and a detail view, none of the system's work is visible and nothing else in the dashboard is reachable. It is independently shippable and already delivers value: an operator can see what the pipeline is doing.

**Independent Test**: Sign in, load the queue against seeded incidents, confirm status/severity/disposition render, filter by status, open one incident, and confirm the detail view shows its summary, evidence, disposition, and audit rows.

**Acceptance Scenarios**:

1. **Given** seeded incidents in mixed states, **When** the operator opens the dashboard, **Then** a queue lists each incident with its status, severity, and disposition, most-recent activity first.
2. **Given** the queue is displayed, **When** the operator filters by status "awaiting_approval", **Then** only parked incidents remain visible.
3. **Given** the queue is displayed, **When** the operator opens an incident, **Then** the detail view shows the grounded summary, evidence, current status/disposition, and the incident's audit trail.
4. **Given** no operator session, **When** any incident data is requested, **Then** access is rejected and the operator is sent to sign in.

---

### User Story 2 - Approve or reject a parked destructive remediation (Priority: P2)

An operator reviews an incident parked in `awaiting_approval`, sees exactly which destructive actions the response stage proposed (e.g. isolate-host, disable-user, block-IP) and the rationale for them, and the deadline before the approval expires. The operator approves — the pipeline resumes, the actions execute against the mock environment, and the disposition updates to `remediated`; or rejects — the incident resolves as `rejected_by_human`. Either way the decision and its outcome are reflected back in the dashboard, and the audit trail records who decided.

**Why this priority**: This is the headline human-in-the-loop capability and the project's signature demo moment — it makes the #10 interrupt/resume seam usable by a human. It builds on the queue/detail surface (P1) but is independently testable and high value.

**Independent Test**: Park an incident in `awaiting_approval` with a known destructive plan, open it in the dashboard, approve it, and confirm the pipeline resumes to `remediated` with the executed actions recorded in the audit trail; repeat with reject and confirm `rejected_by_human`.

**Acceptance Scenarios**:

1. **Given** an incident awaiting approval, **When** the operator opens it, **Then** the dashboard shows the specific proposed destructive actions, the response rationale, and the approval deadline.
2. **Given** a parked approval, **When** the operator approves, **Then** the pipeline resumes, the actions are recorded as applied, and the dashboard reflects the `remediated` disposition.
3. **Given** a parked approval, **When** the operator rejects, **Then** the incident resolves as `rejected_by_human` and no destructive action is recorded as applied.
4. **Given** an approval already decided or expired (e.g. by the timeout sweeper) while the operator was viewing it, **When** the operator submits a decision, **Then** the decision is safely refused and the dashboard shows the approval is no longer actionable — no second remediation runs.

---

### User Story 3 - Inspect the pipeline trace, evidence, and per-agent telemetry (Priority: P2)

An operator opens an incident's trace inspector and sees the full triage → enrichment → response trace as a navigable tree. Each step shows the evidence it considered and the plain-language rationale it produced, plus per-step telemetry: tokens in/out, model, latency, and whether the step succeeded or errored. An error path (a failed tool, a fallback) is visibly handled rather than hidden. Every value shown is redacted.

**Why this priority**: The trace/evidence inspector is what makes the system's intelligence auditable and trustable, and is a graded showcase surface. It depends on the detail view (P1) but is a distinct, independently testable capability.

**Independent Test**: Run one incident end-to-end (with mocked externals), open its trace inspector, and confirm all three pipeline stages appear as a tree with each step's evidence, rationale, and token/latency/status telemetry — and that a seeded sensitive value never appears unredacted.

**Acceptance Scenarios**:

1. **Given** a completed incident, **When** the operator opens its trace, **Then** triage, enrichment, and response stages render as a tree with each step's evidence and rationale.
2. **Given** the trace is displayed, **When** the operator inspects a step, **Then** its tokens in/out, model, latency, and status are shown; missing token usage renders as "unknown", not zero.
3. **Given** an incident whose run hit a handled error path, **When** the operator inspects the trace, **Then** the errored step is clearly marked and the recovery is visible.
4. **Given** any incident, **When** the operator views its trace, evidence, or detail, **Then** no unredacted secret or PII value appears anywhere.

---

### User Story 4 - Monitor operational KPIs (Priority: P3)

An operator opens the KPI view and reads the health and value of the system at a glance: alert volume over time, the split of auto-resolved vs escalated vs awaiting-approval, mean time to disposition, and the memory-hit rate (how often temporal memory surfaced a relevant prior incident). The figures are informative visualizations, not raw tables, and reconcile with the underlying incident records.

**Why this priority**: KPIs demonstrate the system is working and getting smarter, but they are summaries over data the other stories already expose. Valuable for the demo, lowest of the four for reaching a usable console.

**Independent Test**: With a seeded set of incidents spanning dispositions and timestamps, open the KPI view and confirm all four metric families render and their counts match the underlying incident records.

**Acceptance Scenarios**:

1. **Given** seeded incidents across dispositions and times, **When** the operator opens the KPI view, **Then** alert volume over time, the auto-resolved/escalated/awaiting-approval split, mean time to disposition, and memory-hit rate all render.
2. **Given** the KPI view is displayed, **When** its counts are compared to the incident records, **Then** they reconcile exactly.

---

### Edge Cases

- **Approval decided out from under the operator**: a pending approval is expired by the timeout sweeper or decided in another tab while being viewed → the decision is refused with a clear "already decided / expired" state; no double execution.
- **Incident still in flight**: an incident with no trace spans yet (mid-pipeline) → the trace inspector shows a graceful partial/empty state, not an error or blank screen.
- **Backend unavailable**: a data or decision endpoint is down or times out → the dashboard shows a clear error state and lets the operator retry; it never shows a blank or corrupted view.
- **Live stream drops**: the server-push connection is interrupted → the dashboard shows a "reconnecting" indicator, reconnects automatically, and reconciles the queue on reconnect, falling back to on-demand refresh meanwhile.
- **Missing telemetry**: a provider omitted token usage → shown as "unknown" rather than `0`.
- **Empty system**: a fresh install with no incidents → the queue and KPI views show a clear empty state.
- **Large backlog**: hundreds of incidents → the queue pages/limits results and stays responsive.
- **Stale session**: the operator session expires mid-session → the next action prompts re-authentication rather than failing silently.

## Requirements *(mandatory)*

### Functional Requirements

**Authentication & authorization**

- **FR-001**: System MUST require an authenticated operator session for every incident, approval, trace, audit, and KPI endpoint the dashboard consumes; unauthenticated requests MUST be rejected.
- **FR-002**: System MUST authenticate a single `admin` operator for v1 via an admin username + password credential held in the platform secret store, exchanged for a short-lived **signed session token** that carries an explicit role attribute, so that additional roles can be added later **without reworking the API or the UI**.
- **FR-003**: The signed session token MUST expire after a bounded period and require re-authentication; expiry MUST surface as a sign-in prompt, not a silent failure.

**Incident queue & detail**

- **FR-004**: The dashboard MUST present a live incident queue of all incidents (active and resolved), defaulted to the active/in-flight view, showing each incident's status, severity, and disposition at a glance, updated in near-real-time via server push (see FR-023).
- **FR-005**: Operators MUST be able to filter and sort the queue — at minimum by status (including `awaiting_approval`) and severity, and to switch between the active view and resolved/terminal history — and page through large result sets.
- **FR-006**: Operators MUST be able to open any incident from the queue to a detail view.
- **FR-007**: The incident detail view MUST show the incident's current status, severity, disposition, source, timestamps, and grounded summary/evidence.
- **FR-008**: The incident detail view MUST show the incident's audit trail — actor, action, target, outcome, and timestamp — for every action executed or attempted on it.

**Human-in-the-loop approval**

- **FR-009**: The dashboard MUST surface every incident parked awaiting approval, showing the specific destructive actions the response stage proposed and the response rationale.
- **FR-010**: Operators MUST be able to approve or reject a parked remediation; the decision MUST drive the existing approval-decision flow that resumes the pipeline (it MUST NOT execute actions or mutate incident state directly).
- **FR-011**: After a decision, the dashboard MUST reflect the resulting disposition (e.g. `remediated`, `rejected_by_human`).
- **FR-012**: The system MUST prevent a second decision on an already-decided or expired approval and reflect that state to the operator, guaranteeing no double execution.
- **FR-013**: The dashboard MUST show each pending approval's deadline and reflect when an approval has reached its expiry/timeout terminal state (`approval_expired`).

**Trace, evidence & telemetry inspector**

- **FR-014**: The dashboard MUST render the triage → enrichment → response trace as a navigable tree, showing each step's evidence considered and its plain-language rationale.
- **FR-015**: The trace inspector MUST show per-step telemetry — tokens in/out, model, latency, and status — and MUST render missing token usage as "unknown" rather than zero.
- **FR-016**: A handled error path within a trace MUST be clearly marked as errored, with its recovery visible.

**Redaction**

- **FR-017**: The dashboard MUST display only redacted values and MUST NOT expose any unredacted secret or PII in any view; it relies on data redacted at write time and introduces no de-redaction path.

**KPIs**

- **FR-018**: The dashboard MUST present operational KPIs: alert volume over time; counts of auto-resolved vs escalated vs awaiting-approval; mean time to disposition; and memory-hit rate — defined as the share of incidents that reached enrichment for which at least one relevant prior incident or temporal fact was surfaced (denominator = incidents that reached enrichment).
- **FR-019**: KPI figures MUST reconcile with the underlying incident and disposition records.

**Scope & resilience**

- **FR-020**: The dashboard MUST be read-only except for the approve/reject action; it MUST NOT edit incidents, re-run pipeline stages, or write incident state directly.
- **FR-021**: The dashboard MUST degrade gracefully when a backend endpoint is unavailable or an incident has no trace yet — showing clear error/empty states and a retry path, never a blank or broken view.
- **FR-022**: The console MUST be clean, modern, responsive, and usable in a live demo.
- **FR-023**: The dashboard MUST receive incident-queue and KPI updates over a server-push stream (WebSocket/SSE); on stream loss it MUST show a clear reconnecting state, reconnect automatically, and reconcile queue state without loss or duplication, falling back to on-demand refresh while disconnected.

### Key Entities

- **Incident (queue item / detail)**: an alert progressing through the pipeline — identity, status, severity, disposition, source, grounded summary and evidence, created/updated timestamps. The primary object the operator browses.
- **Approval request**: a destructive remediation parked for a human decision — the proposed actions, the response rationale, the deadline, and its status (pending / approved / rejected / expired). Drives the approve/reject control.
- **Audit entry**: an append-only accountability record of an executed or attempted action — actor (agent or operator), action, target, outcome, timestamp.
- **Trace / span tree**: the per-incident record of pipeline work — triage/enrichment/response steps and their tool/retrieval/LLM sub-steps, each with evidence, rationale, tokens in/out, model, latency, and status; all attributes pre-redacted.
- **KPI snapshot**: derived aggregates — volume over time, disposition split, mean time to disposition, memory-hit rate.
- **Operator session**: the authenticated `admin` identity and its role attribute, time-bounded.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can locate and open any incident from the queue in 3 or fewer interactions.
- **SC-002**: 100% of incidents awaiting approval display their proposed destructive actions and rationale before any decision can be submitted.
- **SC-003**: After approving or rejecting, the operator sees the updated disposition within 3 seconds.
- **SC-004**: A seeded fake secret or PII value never appears unredacted in any dashboard view.
- **SC-005**: For a completed incident, the trace inspector shows all three pipeline stages, each with per-step token and latency figures (or an explicit "unknown").
- **SC-006**: The KPI view shows all four metric families, and its counts reconcile exactly with the underlying incident records.
- **SC-007**: Every incident, approval, trace, audit, and KPI endpoint rejects unauthenticated access.
- **SC-008**: Re-submitting a decision on an already-decided or expired approval is safely refused and surfaced as "already decided / expired"; no second remediation executes.
- **SC-009**: The queue loads within 2 seconds for at least 200 incidents.
- **SC-010**: A new or changed incident appears in the queue within 5 seconds of the change, with no manual refresh.

## Assumptions

- **Authentication mechanism** (clarified 2026-06-12): the admin signs in with a username + password held in the platform secret store; the backend verifies it and issues a short-lived **signed session token** carrying an explicit role attribute, sent on every API call. Additional roles can be added later without reworking the API or UI. Self-service user management and multi-user RBAC are out of scope for v1.
- **"Live" updates** (clarified 2026-06-12): the queue and KPI views are updated by **server push over a persistent stream (WebSocket/SSE)**; the client shows a reconnecting state and reconnects on drop, falling back to on-demand refresh while disconnected.
- **Read-only boundary**: the dashboard performs no incident mutation except the approve/reject decision, which is routed through the already-shipped `/approvals` decision endpoint (#10); the supervisor remains the single writer of incident state.
- **Read-side incident endpoints**: the queue, detail, trace, audit, and KPI data are served by read endpoints added under the reserved `/incidents` router; the approve/reject flow reuses the existing `/approvals` endpoints from #10.
- **Redaction is upstream**: all displayed data (evidence, spans, audit) is already redacted at write time by the observability/redaction layer (#2); the dashboard does not redact afresh and must not request raw data.
- **Memory-hit rate** (clarified 2026-06-12) = share of incidents that reached enrichment for which enrichment surfaced ≥1 relevant prior incident or temporal fact; incidents resolved before enrichment are excluded from the denominator.
- **Single organization**: no multi-tenancy (a SOC monitors one org); no embeddable widget — one authenticated console.
- **Mock environment**: approved actions execute against the mock executors inherited from #10; the demo runs on replayed sample alerts.
- **Frontend packaging**: the dashboard ships as a separate image and toolchain (the reserved `frontend/` placeholder, `deploy/frontend/Dockerfile`), not part of the Python backend image or virtual environment.

## Dependencies

- **#10 Response & remediation** (done): the `/approvals` list + decision endpoints, the destructive-action plans, dispositions (`auto_remediated` / `remediated` / `rejected_by_human` / `approval_expired` / `escalated_response`), the approval timeout, and the audit log this dashboard reads and acts on.
- **#2 Observability & redaction** (done): the trace spans (with tokens/model/latency, pre-redacted attributes) and the redaction boundary the trace inspector relies on.
- **#3 LLM provider** (done): the per-step token/model telemetry recorded on LLM spans.
- **#5 / #7 Supervisor** (done): incident statuses, dispositions, and the resume-on-decision behaviour the approval control triggers.
- **#1 Platform** (done): the secret store backing admin auth and the reserved `frontend/` image slot.
