# Feature Specification: Deterministic Rule/Threshold Detector

**Feature Branch**: `014-detector`

**Created**: 2026-06-16

**Status**: Draft

**Input**: User description: "what is the next spec @docs/resources/SOAR_brief.md @docs/resources/v_2_3_plan.md do we go for spec14, or we have to do something else" — resolved to Component #14 (`SPEC-detector`, v2/T3): a deterministic rule/threshold detector that *fires* alerts from replayed events into the existing ingestion schema, with zero downstream change.

## Overview

Sentinel is a **response** platform that sits deliberately downstream of detection. Until now it has
only ever consumed alerts produced by an upstream detector (Wazuh-format, via the `#4` ingestion front
door). This component adds Sentinel's **own deterministic detection source**: it reads replayed raw
events, applies a config-backed set of rules and thresholds, and — when an event matches — **fires an
alert in the exact same ingestion contract the Wazuh adapter uses**. That alert then flows through the
unchanged triage → enrichment → response pipeline.

The architectural point is **decoupling, not welding**: the detector is a *separate source* that emits
through the existing public ingestion contract, exactly as a sibling detection project would attach.
"A SOAR welded to one detector can't serve a SOC that runs five — the decoupling is the point." So this
component owns **zero** downstream change: no new pipeline stage, no supervisor edge, no incident-schema
change. It is the same shape as any other alert source, and it is **deterministic — no ML, no LLM**
(rule/threshold matching only; anomaly scoring is a later, separate effort).

Delivering this component is also what **unblocks** the two deferred post-detector milestones —
`015-M2` (the recurrence monitoring loop) and `016-M2` (feeding memory-derived intel back to the
detector) — but both of those remain out of scope here.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A raw event with no pre-made alert is detected and runs end to end (Priority: P1) 🎯 MVP

A SOC operator replays raw event records (e.g., log events) that carry **no pre-made alert**. A record
matches a configured detection rule. The detector fires an alert in the standard ingestion format, and
that alert is captured as an Incident and runs the full triage → enrichment → response pipeline — the
same as any Wazuh-sourced alert.

**Why this priority**: This is the irreducible MVP and the headline demo (brief demo #6) — it proves
Sentinel can *originate* detections, not only consume them, while keeping detection cleanly decoupled
from response. Without it, nothing in this component delivers value.

**Independent Test**: Replay a sample event set containing one event that matches a configured rule;
assert exactly one Incident is created from the detector source, it carries the matched rule's
identity/severity, and it reaches a terminal disposition through the existing pipeline with no
downstream code change.

**Acceptance Scenarios**:

1. **Given** a config-backed rule that matches a specific event pattern, **When** a raw event matching
   that pattern is replayed, **Then** the detector emits exactly one alert in the existing ingestion
   contract and an Incident attributed to the detector source is created and processed end to end.
2. **Given** a matched event, **When** the alert is emitted, **Then** the resulting Incident carries the
   matched rule's identifier, description, and a severity derived from the rule — visible in the trace
   exactly like a Wazuh-sourced incident.
3. **Given** the detector is running, **When** alerts are emitted, **Then** all pre-existing downstream
   behavior (ingestion, supervisor routing, the agent stages, eval gates) is unchanged.

---

### User Story 2 - Benign events produce no alert (no over-firing) (Priority: P2)

A replayed event matches no rule and crosses no threshold. The detector produces **nothing** — no
alert, no Incident, no noise.

**Why this priority**: Detection value is precision *and* recall. A detector that fires on everything is
worse than none — it floods the SOC. Suppressing benign events is what makes the precision side of the
eval gate meaningful and the source trustworthy.

**Independent Test**: Replay a sample set of benign events that match no rule and fall below all
thresholds; assert zero alerts are emitted and zero Incidents are created.

**Acceptance Scenarios**:

1. **Given** a raw event matching no configured rule, **When** it is replayed, **Then** the detector
   emits no alert and creates no Incident.
2. **Given** an event whose qualifying-event count is below a threshold rule's configured count, **When**
   it is replayed, **Then** no threshold alert fires.

---

### User Story 3 - Threshold/aggregation detection fires one correlated alert (Priority: P3)

Individually-benign events that cross a configured count within a configured window (e.g., repeated
failed logins from one source) fire **a single** correlated alert rather than one per event.

**Why this priority**: "Rule **and** threshold" is the spec's name. Single-event signature matching alone
is the trivial half; the threshold/aggregation case is the realistic SOC pattern (brute force, scanning)
and demonstrates the detector reasons over a window, not just a row. Lower priority because US1/US2
already deliver a demonstrable, gated detector.

**Independent Test**: Replay N qualifying events within the configured window where N meets the rule's
count; assert exactly one alert fires (not N), attributed to the threshold rule.

**Acceptance Scenarios**:

1. **Given** a threshold rule of "count ≥ N within window W", **When** N qualifying events are replayed
   within W, **Then** exactly one alert fires.
2. **Given** the same rule, **When** fewer than N qualifying events occur within W, **Then** no alert
   fires.

---

### Edge Cases

- **Multiple rule matches on one event** → the detector emits a single alert attributed to the
  highest-severity matched rule (one event → at most one alert), keeping precision/recall labeling clean;
  the existing dedup layer absorbs genuine repeats.
- **Malformed / partial raw event** → the event is skipped, the detection run continues with the
  remaining events, and nothing crashes (graceful degradation).
- **Empty or absent rule set** → the detector runs cleanly and emits no alerts (no crash, no default
  firing).
- **Detector and the upstream Wazuh adapter both active** → both emit through the same contract; each
  resulting Incident is attributable to its source, and neither path interferes with the other.
- **Replaying the same event set twice** → no duplicate Incidents (relies on the existing dedup
  fingerprint; the detector adds no new dedup authority).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The detector MUST evaluate each replayed raw event against a configured set of
  deterministic detection rules and thresholds, using **no LLM and no ML/anomaly scoring**.
- **FR-002**: When an event satisfies a rule, the detector MUST emit an alert in the **existing
  Wazuh-format ingestion contract** (carrying the matched rule's identity, description, groups, and the
  originating event's fields) so it flows through the existing ingestion → triage → enrichment →
  response pipeline with **no downstream change**.
- **FR-003**: The detector MUST NOT emit an alert for an event that matches no rule and crosses no
  threshold (benign events are suppressed; the detector does not over-fire).
- **FR-004**: The detector MUST support threshold/aggregation rules that fire **a single** alert when a
  count of qualifying events crosses a configured count within a configured time window.
- **FR-005**: The rule and threshold set MUST be **config-backed** (not hardcoded in detection logic), so
  rules can be added or changed without code changes.
- **FR-006**: Each emitted alert MUST identify the detector as its source, distinguishable in the
  audit/trace from upstream Wazuh-sourced alerts.
- **FR-007**: Each emitted alert MUST carry a severity derived from its matched rule, mapped onto the
  existing severity scale.
- **FR-008**: The detector MUST be replay-safe: replaying the same event set MUST NOT create duplicate
  Incidents (it relies on the existing dedup fingerprinting and introduces no second dedup authority).
- **FR-009**: The detector MUST degrade gracefully on a malformed or partial raw event — skip that event
  and continue the run without failing the whole detection pass.
- **FR-010**: The detector MUST operate over a **replayed sample event set** as a one-shot/replayable
  operation — **no live network capture and no standing feed connection**.
- **FR-011**: When an event matches more than one rule, the detector MUST emit a single alert attributed
  to the **highest-severity** matched rule.
- **FR-012**: Detection quality MUST be measured by a committed **precision/recall eval gate** over a
  labeled replay set; CI MUST fail when precision or recall falls below the committed thresholds (the gate
  is declared in the threshold config **and** registered as a runner together — an orphan declaration is a
  hard error per the eval harness).
- **FR-013**: The detector MUST NOT alter the Incident schema, the supervisor, the agent stages, or any
  existing eval gate — zero downstream change is a hard requirement, not optional.

### Key Entities

- **Detection Rule**: A config-backed unit of detection — match criteria over event fields, an optional
  threshold (count + window), a severity/level, an identifier and description, and optional technique
  mapping. Mirrors the shape an upstream rule-based detector would carry.
- **Raw Event**: A replayed source event record the detector evaluates (structured fields, timestamp,
  originating host/agent). Distinct from a pre-made alert — it has not yet been judged by any detector.
- **Emitted Alert**: The ingestion-contract payload the detector produces when a rule fires, carrying the
  matched rule's identity and the originating event's fields, tagged with the detector as source.
- **Labeled Replay Set**: A fixture of raw events each labeled malicious/benign (and, for thresholds,
  grouped) used to compute the precision/recall eval gate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A raw replayed event carrying no pre-made alert is detected and produces an Incident that
  completes the full pipeline end to end (brief demo #6).
- **SC-002**: On the labeled replay set, detection **precision and recall each meet or exceed** the
  thresholds committed to the eval config, verified in CI.
- **SC-003**: Benign events in the replay set produce **zero** alerts (false-positive rate below the
  committed threshold).
- **SC-004**: All pre-existing tests and eval gates remain green — the detector introduces **zero**
  downstream change.
- **SC-005**: A new detection rule can be added via **configuration alone** (no code change) and fires on
  a matching replayed event.
- **SC-006**: Replaying the same event set twice yields the **same set** of Incidents (no duplicates).
- **SC-007**: A threshold rule fires exactly **one** alert when its count is crossed within its window,
  and none when it is not.

## Assumptions

- **Decoupled emission through the existing contract.** The detector emits alerts via the existing public
  ingestion contract/path (the same one the Wazuh adapter uses), so downstream is untouched. Whether that
  emission is in-process or over the existing webhook is an implementation detail to fix at `/speckit-plan`.
- **Replayed file/config-backed event source.** Raw events are read from a replayable sample source
  (structured records), not from live network capture — consistent with the brief's "demo runs on
  replayed sample alerts" and the "no live capture" scope discipline.
- **Rule-derived severity.** Each rule carries a configured level that maps onto the existing severity
  scale; the detector does not invent severity beyond its rules.
- **One event → at most one alert.** Multi-rule matches collapse to the highest-severity rule; genuine
  repeats are absorbed by the existing dedup fingerprint. This keeps precision/recall labeling clean.
- **In-run threshold state only.** Threshold-window state is tracked within a single replay run; the
  detector persists no cross-run detection state in this version.
- **Rule-set *scope* is fixed at `/speckit-plan`, not here.** Per the roadmap, the concrete rule/threshold
  set and the dwell/window values are config-backed values chosen during planning.
- **Layering contract.** This is a **T3** component that lands additively after the **T2** tier
  (`015-M1` + `016-M1`) is frozen/tagged; it does not destabilize v1 and adds no v1-scope behavior.

## Dependencies

- **#4 Ingestion pipeline** — owns the alert/Incident schema and the webhook → queue → worker → Incident
  front door the detector emits into. This is the only contract the detector touches.
- **T2 freeze** — `015-M1` (remediation verification) and `016-M1` (memory feedback loop), per the
  layering contract, frozen before this T3 work lands.
- **Enables (out of scope here)** — `015-M2` (recurrence monitoring loop) and `016-M2` (feed
  memory-derived intel back to the detector) both become buildable once this component exists.

## Out of Scope

- **ML / anomaly / behavioral (UEBA) scoring** — that is v2a/v3, a separate project's lifecycle.
- **Live network capture or standing feed connections** — replayed events only (live feeds are v3c).
- **Feeding memory-derived intel back into the detector** (`016-M2`) — gated on this component plus memory.
- **The recurrence monitoring loop consumer** (`015-M2`) — this component only *fires* alerts.
- **XDR multi-source correlation** (`017`) — needs a second source and surplus capacity.
- **Any change to the Incident schema, supervisor FSM, agent stages, or existing eval gates.**

## Constitution Alignment

- **IV (Determinism First)** — rule/threshold matching only; no LLM, no ML. Detection here is exactly the
  enumerable core the constitution reserves for determinism.
- **VII (Production Engineering Standards)** — config-backed rule set (`extra="forbid"` settings), async,
  typed boundaries.
- **I (Spec-Driven Delivery)** — own spec, three-tier tests, committed eval gate, its own tag.
- **II (Test-First, Eval-Gated)** — precision/recall gate committed to threshold config and registered as
  a runner together; passes on both LLM providers (the pipeline it feeds uses LLMs even though the
  detector itself does not).
- **Decoupling thesis (brief)** — the detector is a separate source emitting the existing contract, never
  a stage welded into the response pipeline.
