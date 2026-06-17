# Feature Specification: ML Anomaly Detection Layer (UEBA-style)

**Feature Branch**: `017-ml-anomaly-detector`

**Created**: 2026-06-16

**Status**: Draft

**Input**: User description: "depending on @docs/resources/SOAR_brief.md and @docs/resources/v_2_3_plan.md" — resolved to Component #17 (`SPEC-ml-anomaly-detector`): a **decoupled, complementary** ML behavioral-anomaly detection source (UEBA-style) that reads replayed SIEM logs, scores deviation from a learned baseline, and *fires* alerts into the existing ingestion contract with zero downstream change. Per the **2026-06-16 Detection Strategy Update** (SOAR_brief.md), the ML anomaly layer — previously documented-only as `v2a/v3` in v_2_3_plan.md — is now brought **in-project as #17**, built **after** the rule detector (#14); the XDR-correlation slot rolls forward to #18/v3.

## Overview

Argus is a **response** platform that sits deliberately downstream of detection. Component #14 added
Argus's first **own** detection source — a *deterministic* rule/threshold detector that fires alerts
into the existing `#4` ingestion front door. This component adds a **second, complementary** source:
an **ML behavioral-anomaly detector** (UEBA-style) that **reads replayed SIEM logs (not raw network
traffic)**, baselines what "normal" looks like, and flags **deviations** — the **high-recall net for
novel behavior** that signature rules structurally cannot catch (compromised credentials, lateral
movement, insider-threat / APT-style activity). When an entity's behavior scores anomalous above a
config-backed threshold, it fires an alert in **the exact same ingestion contract** the Wazuh adapter
(#4) and the rule detector (#14) use, and that alert flows through the unchanged triage → enrichment →
response pipeline.

**Layering, not replacement.** Signature detection (#14) and anomaly detection (#17) **cover each
other's blind spots** — this is the brief's central detection thesis. The deterministic detector
handles known-bad with near-zero false positives and an exact, auditable reason ("matched rule X"); the
ML layer extends **recall** to novel *behavior* a rule was never written for. Running anomaly detection
*instead of* signatures is a misconception no mature SOC follows; **Argus runs both**. This component
therefore changes nothing about #14 — it adds a sibling source.

**Honest mock, real ML.** No live SIEM/Wazuh is required. A small model (e.g. Isolation Forest or a
compact autoencoder) is **trained offline** on a **public log-based dataset** and saved as a versioned
artifact; at demo/eval time, log events are **replayed** through the saved model and anomalies over
threshold fire alerts into the ingestion path — *real ML, mock environment*, the same honesty bar #14
holds. The writeup states plainly: **trained offline on a public dataset, inference over replayed logs,
not a live feed — and it raises recall on novel *behavior*, not literal zero-day exploits, and makes no
real-time production-efficacy claim.**

**Decoupling is the architecture.** Like #14, this detector is a **separate source** emitting through
the existing public ingestion contract — never a stage welded into the supervisor pipeline. It adds **no
second writer** over incident state and **no new supervisor FSM edge**. It is the same shape any sibling
detection project would attach with.

**Constitution exception (recorded).** This is the project's first ML at the **detection** layer.
Determinism-first (Principle IV) is **preserved on the response path** (the supervisor stays a
deterministic state machine; agents still reason only over supplied evidence). Catching novel behavior
is exactly where determinism does not suffice, so this is an **explicit, recorded exception** at the
detection layer — captured as a `DECISIONS.md` entry + a constitution note **before** implementation
lands (see Dependencies and Constitution Alignment).

## Clarifications

### Session 2026-06-16

- Q: At what granularity does the ML model score for anomalies? → A: Per-entity time window — aggregate each entity's (user/host) activity over a time window into behavioral features, then score the window (UEBA standard).
- Q: How is severity assigned to an ML anomaly alert in the ingestion contract? → A: Config-backed score→severity bands — map anomaly-score ranges onto the existing severity scale (deterministic, tunable without code).
- Q: What CI posture should the ML detection precision/recall/false-positive eval gate have? → A: Blocking / required — committed floors + FP ceiling fail CI, same as #14; justified because the gate is deterministic against the saved artifact (FR-010).
- Q: Which public log dataset is used for offline training and the held-out eval set? → A: CERT Insider Threat — scenario-labeled user-activity logs; entities = users; clean malicious-vs-normal labels; manageable size.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Anomalous behavior with no signature match is detected and runs end to end (Priority: P1) 🎯 MVP

A SOC operator replays SIEM log events for an entity (user/host) whose behavior **deviates from its
learned-normal baseline** (e.g., a user account suddenly authenticating to many hosts at an unusual
hour) — activity that **no signature rule would catch**. The ML anomaly detector scores the behavior
above the configured threshold, fires an alert in the standard ingestion format carrying the anomaly
score and the contributing features, and that alert is captured as an Incident and runs the full triage
→ enrichment → response pipeline — the same as any Wazuh- or rule-detector-sourced alert.

**Why this priority**: This is the irreducible MVP and the headline value — it proves Argus can detect
**novel behavior** that signatures miss, raising recall, while keeping detection cleanly decoupled from
response. Without it nothing in this component delivers value.

**Independent Test**: Replay a labeled log slice containing one entity whose behavior is anomalous (and
matches no rule); assert exactly one Incident is created from the ML-detector source, it carries the
anomaly score and contributing-feature evidence, and it reaches a terminal disposition through the
existing pipeline with no downstream code change.

**Acceptance Scenarios**:

1. **Given** a model trained offline on the public dataset and a config-backed anomaly threshold,
   **When** a replayed entity's behavior scores above the threshold, **Then** the detector emits exactly
   one alert in the existing ingestion contract and an Incident attributed to the ML-detector source is
   created and processed end to end.
2. **Given** an anomalous entity, **When** the alert is emitted, **Then** the resulting Incident carries
   the anomaly score, the contributing features/evidence, and the affected entity — visible in the trace
   so the triage agent reasons over it exactly like any other alert.
3. **Given** the ML detector is running, **When** alerts are emitted, **Then** all pre-existing
   downstream behavior (ingestion, supervisor routing, the agent stages, existing eval gates) is
   unchanged.

---

### User Story 2 - Normal behavior produces no alert (low false-positive rate) (Priority: P2)

Replayed log events for entities behaving **within their learned-normal baseline** score below the
anomaly threshold. The detector produces **nothing** — no alert, no Incident, no noise.

**Why this priority**: Anomaly detection lives or dies on its false-positive rate — a model that flags
everything as "anomalous" floods the SOC and is worse than none. Suppressing normal behavior is what
makes the precision / false-positive side of the eval gate meaningful and the source trustworthy.

**Independent Test**: Replay a labeled slice of normal-behavior log events; assert the false-positive
rate is at or below the committed ceiling (ideally zero alerts on clearly-normal entities) and no
spurious Incidents are created.

**Acceptance Scenarios**:

1. **Given** an entity whose behavior is within learned-normal bounds, **When** its log events are
   replayed, **Then** the anomaly score is below threshold and the detector emits no alert and creates
   no Incident.
2. **Given** the committed labeled normal set, **When** it is replayed, **Then** the measured
   false-positive rate does not exceed the committed ceiling.

---

### User Story 3 - The ML detector complements the rule detector, not replaces it (Priority: P3)

The deterministic rule detector (#14) and the ML anomaly detector (#17) run over the same replayed
source. The rule detector catches a known-bad pattern with an exact rule match; the ML detector catches
a *different* entity's novel behavioral deviation. **Both** emit through the same ingestion contract and
**both** resulting Incidents are correctly attributable to their respective sources — neither path
interferes with the other.

**Why this priority**: "Signature **and** anomaly cover each other's blind spots" is the proposal's
central detection thesis and the decision this component embodies. Lower priority because US1/US2 already
deliver a demonstrable, gated ML source; this story proves the *layering* property explicitly.

**Independent Test**: Replay a mixed set where one entity trips a #14 rule and a different entity trips
the #17 anomaly threshold; assert two Incidents are created, one attributable to each source, both
flowing the existing pipeline, with no interference and no downstream change.

**Acceptance Scenarios**:

1. **Given** both detectors active over one replayed source, **When** a known-bad pattern and a novel
   behavioral deviation each occur, **Then** the rule detector fires the signature alert and the ML
   detector fires the anomaly alert, each Incident attributable to its source.
2. **Given** both detectors active, **When** alerts are emitted, **Then** the ML detector adds **no**
   second writer over incident state and **no** new supervisor FSM edge — #14's behavior is unchanged.

---

### Edge Cases

- **Anomaly score exactly at the threshold boundary** → resolved by a single documented comparison rule
  (e.g., score ≥ threshold fires), applied consistently so the operating point is deterministic and the
  eval is reproducible.
- **Malformed / partial log record** → the record is skipped, the detection run continues with the
  remaining events, and nothing crashes (graceful degradation).
- **Empty or absent replay set** → the detector runs cleanly and emits no alerts (no crash, no default
  firing).
- **Model artifact missing or unloadable** → the detector **fails closed**: it emits no alerts and
  surfaces a clear error, rather than firing on un-scored input.
- **A true zero-day exploit that produces no behavioral deviation** → not detected; this is the honest
  caveat — the layer raises recall on novel *behavior*, not literal zero-day exploits.
- **The same anomalous entity also trips a #14 rule** → both sources may fire; the existing dedup
  fingerprint absorbs genuine duplicates, and each surviving Incident remains attributable to its source.
- **Replaying the same log set twice** → no duplicate Incidents (relies on the existing dedup
  fingerprint; the detector adds no new dedup authority).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A behavioral-anomaly model MUST be trained **offline** on a **public log-based dataset**
  (e.g., CERT Insider Threat or LANL authentication logs — *not* raw network-flow data) and saved as a
  **versioned artifact**. Training MUST be reproducible (pinned random seed) and MUST NOT run on the
  inference/request path.
- **FR-002**: At inference, the detector MUST aggregate each entity's (user/host) replayed activity over
  a configured **time window** into behavioral features, then load the saved model artifact and score
  **each entity-time-window** for deviation from learned-normal behavior, producing an **anomaly score**
  per window (UEBA-style; individual log events are not scored in isolation).
- **FR-003**: When an entity-time-window's anomaly score crosses the configured threshold, the detector
  MUST emit an alert in the **existing Wazuh-format ingestion contract** — carrying the anomaly score, the
  contributing features/evidence, the affected entity, and a **severity** derived from the score — so it
  flows through the existing ingestion → triage → enrichment → response pipeline with **no downstream
  change**.
- **FR-004**: The anomaly-score → alert threshold MUST be **config-backed** (not hardcoded in detection
  logic), so the precision/recall operating point can be tuned without code changes.
- **FR-004a**: The emitted alert's **severity** MUST be derived deterministically from the anomaly score
  via **config-backed score→severity bands** that map onto the existing severity scale — so a stronger
  deviation routes at higher severity, and the bands are tunable without code changes (no fixed,
  signal-discarding severity).
- **FR-005**: The detector MUST NOT emit an alert for behavior within learned-normal bounds (score below
  threshold) — normal behavior is suppressed and the false-positive rate is bounded.
- **FR-006**: Each emitted alert MUST identify the **ML anomaly detector** as its source, distinguishable
  in the audit/trace from upstream Wazuh-sourced and rule-detector-(#14)-sourced alerts.
- **FR-007**: The detector MUST operate over a **replayed SIEM log set** (not raw network traffic, not a
  live feed) as a one-shot/replayable operation — **no live capture, no standing connection, and no
  real-time production-efficacy claim**.
- **FR-008**: The detector MUST **complement, not replace**, the deterministic rule detector (#14): both
  MUST be able to run over the same replayed source, each emitting through the same contract, each
  resulting Incident attributable to its source. It MUST NOT modify or disable #14.
- **FR-009**: Detection quality MUST be measured by a committed, **blocking (`required: true`)**
  **precision / recall** eval gate with a **bounded false-positive ceiling** over a **held-out labeled**
  portion of the dataset; CI MUST fail when precision or recall falls below the committed thresholds or
  the false-positive rate exceeds its ceiling (the gate is declared in the threshold config **and**
  registered as a runner together — an orphan declaration is a hard error per the eval harness). The
  blocking posture is justified by FR-010 — the gate scores the saved artifact deterministically, so
  there is no runtime variance to excuse a reported-only gate.
- **FR-010**: The eval gate MUST be **reproducible** — evaluated against the saved model artifact (or a
  retrain with a pinned seed) so the scored precision/recall are deterministic across CI runs.
- **FR-011**: The detector MUST degrade gracefully on a malformed or partial log record — skip that
  record and continue the run without failing the whole detection pass.
- **FR-012**: The detector MUST **fail closed** if the model artifact is missing or cannot be loaded —
  emit no alerts and surface a clear error rather than firing on un-scored input.
- **FR-013**: The detector MUST be replay-safe — replaying the same log set MUST NOT create duplicate
  Incidents (it relies on the existing dedup fingerprinting and introduces no second dedup authority).
- **FR-014**: The detector MUST NOT alter the Incident schema, the supervisor FSM (**no new edge, no
  second writer**), the agent stages, or any existing eval gate — zero downstream change is a hard
  requirement, not optional.
- **FR-015**: The alert's evidence MUST be honest about its nature — it carries an **anomaly score and
  contributing features**, not an exact rule identity; the writeup and any operator-facing surface MUST
  NOT imply the model was trained or validated on live production traffic.

### Key Entities

- **Behavioral Baseline Model**: The offline-trained artifact (e.g., Isolation Forest or a compact
  autoencoder) capturing learned-normal behavior; versioned, saved, and loaded at inference. Trained with
  a pinned seed for reproducibility.
- **Entity Activity Window** (the scoring unit): A single entity's (user/host) replayed log activity
  aggregated over a configured time window into the behavioral features the model scores. Built from raw
  SIEM log records, but it is the **window**, not the individual event, that is scored. Distinct from raw
  network packets and from a pre-made alert.
- **Anomaly Finding**: A scored deviation — the affected entity, the anomaly score, the contributing
  features, and a timestamp — produced when a score crosses the configured threshold.
- **Emitted Alert**: The ingestion-contract payload the detector produces when an anomaly fires, carrying
  the anomaly score, the contributing features/evidence, the affected entity, and a severity derived from
  the score via config-backed bands, tagged with the ML detector as source.
- **Training Dataset**: The **CERT Insider Threat** public log dataset (user-activity logs — logon,
  device, file, email, http) used for offline training and held-out evaluation; scenario labels
  distinguish malicious user behavior from normal.
- **Labeled Evaluation Set**: A held-out, labeled portion of the dataset used to compute the
  precision / recall / false-positive eval gate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A replayed log event exhibiting anomalous behavior that matches **no** signature rule is
  detected by the model and produces an Incident that completes the full pipeline end to end.
- **SC-002**: On the held-out labeled set, detection **precision and recall each meet or exceed** the
  thresholds committed to the eval config, verified in CI (a provider-independent gate — the detector
  itself uses no LLM).
- **SC-003**: Normal-behavior replay produces a **false-positive rate at or below** the committed
  ceiling.
- **SC-004**: All pre-existing tests and eval gates remain green — the ML detector introduces **zero**
  downstream change.
- **SC-005**: The rule detector (#14) and the ML anomaly detector (#17) both run over the same replayed
  source; each fired Incident is correctly attributable to its source and neither interferes with the
  other.
- **SC-006**: The anomaly threshold can be changed via **configuration alone** (no code change) and
  shifts the precision/recall operating point in the expected direction.
- **SC-007**: Replaying the same log set twice yields the **same set** of Incidents (no duplicates).
- **SC-008**: The eval is **reproducible** — the committed model artifact produces the same precision /
  recall on the held-out set across repeated CI runs.

## Assumptions

- **Re-sequencing per the 2026-06-16 Detection Strategy Update.** The ML anomaly layer — documented as
  `v2a` / `v3 (doc only)` in the older v_2_3_plan.md (which used the `017` slot for XDR correlation) — is
  now brought **in-project as `#17`**, built **after** the rule detector (#14); the XDR-correlation slot
  rolls forward to **#18 / v3**. This spec follows the newer brief update and `CLAUDE.md`.
- **Offline training, replay inference.** The model is trained offline on a public dataset and saved;
  inference runs over a **replayed** log set, not a live feed. No real-time production-efficacy claim is
  made anywhere.
- **Dataset chosen: CERT Insider Threat** (scenario-labeled user-activity logs — logon, device, file,
  email, http; entities = users). Chosen for cleanest demo fit, clear malicious-vs-normal scenario labels,
  and manageable size for a solo build. LANL auth/DNS/process events was the considered alternative
  (richer but heavier); network-flow sets (UNSW-NB15, CIC-IDS2017) are a weaker fit for the "logs, not
  traffic" framing and are not used. The concrete CERT release/version and the time-window length remain
  config-backed values to fix at `/speckit-plan`.
- **Model & threshold choices fixed at `/speckit-plan`.** Isolation Forest is preferred for being
  lightweight, GPU-free, and trivially CI-runnable; a compact autoencoder is the alternative. The concrete
  anomaly threshold (the precision/recall operating point) is a config-backed value chosen during
  planning.
- **Decoupled emission through the existing contract.** The detector emits via the existing public
  ingestion contract/path (the same one the Wazuh adapter and #14 use), so downstream is untouched.
  Whether emission is in-process or over the existing webhook is an implementation detail for
  `/speckit-plan`, mirroring #14.
- **Reads pre-collected SIEM logs.** Input is structured log records (user/host activity), not raw
  network capture.
- **Model-artifact storage** (committed to the repo for a compact model vs. stored in MinIO alongside
  eval reports) is an implementation detail to fix at `/speckit-plan`.
- **Layering after #14.** This T-tier component lands additively after the deterministic rule detector
  (#14) is green and tagged; it does not destabilize v1 and adds no v1-scope behavior.
- **Honest caveat baked into the writeup.** Behavioral anomaly detection raises recall on novel
  *behavior* (compromised credentials, lateral movement, insider threat), **not** literal zero-day
  exploits.

## Dependencies

- **#4 Ingestion pipeline** — owns the alert/Incident schema and the webhook → queue → worker → Incident
  front door the detector emits into. This is the only downstream contract the detector touches.
- **#14 Rule/threshold detector** — the sibling detection source this component **complements**; #17
  lands after #14, reusing the proven detector→ingestion seam and the decoupled-source pattern.
- **Public dataset availability** — the **CERT Insider Threat** public log dataset for offline training
  and held-out evaluation.
- **ML dependency** — a model library (e.g. scikit-learn for Isolation Forest) added per Constitution VII
  (pinned deps); kept lightweight and GPU-free where possible.
- **Governance prerequisite** — a `DECISIONS.md` entry **and** a constitution note recording the
  ML-at-the-detection-layer exception to Principle IV (per the 2026-06-16 Detection Strategy Update) MUST
  be recorded **before** this component's implementation lands.
- **Enables (out of scope here)** — a future feed-to-detector tuning of the ML model (the `016-M2`
  analog), and `#18` XDR multi-source correlation (which needs a second source — this provides it).

## Out of Scope

- **Live SIEM/log feed ingestion or real-time scoring** — replayed logs only (live feeds are v3c); no
  raw network traffic.
- **Concept-drift monitoring or in-production model retraining** — documented as future work; the replay
  demo makes no drift or real-time-efficacy claim.
- **Feeding memory-derived intel back to tune the ML model** (the `016-M2` analog for #17) — a future
  extension.
- **XDR multi-source correlation** (`#18` / v3) — this component supplies the second source it needs but
  does not build the fusion layer.
- **Replacing or modifying the deterministic rule detector (#14)** — the two layer; they do not compete.
- **Any change to the Incident schema, supervisor FSM, agent stages, or existing eval gates.**
- **Any real-time production-efficacy claim.**

## Constitution Alignment

- **IV (Determinism First) — explicit, recorded exception at the detection layer.** Determinism-first is
  **preserved on the response path**: the supervisor stays a deterministic state machine, and agents
  still reason only over supplied evidence. Catching novel *behavior* is exactly where determinism does
  not suffice, so ML at the **detection** layer is an explicit exception — recorded as a `DECISIONS.md`
  entry + a constitution note **before** implementation lands. The detector is **decoupled** (no second
  writer, no new FSM edge) and **complements** (does not replace) the deterministic #14.
- **VII (Production Engineering Standards)** — config-backed threshold (`extra="forbid"` settings), async,
  typed boundaries, pinned ML deps, reproducible training (pinned seed + saved versioned artifact).
- **I (Spec-Driven Delivery)** — own spec, three-tier tests, committed eval gate, its own tag.
- **II (Test-First, Eval-Gated)** — precision/recall + bounded-false-positive gate committed to the
  threshold config and registered as a runner together; reproducible. The gate is **provider-independent**
  (the detector uses no LLM), though the pipeline it feeds still runs on both LLM providers.
- **VI (Temporal Memory)** — the detector's findings can later feed temporal memory / be tuned by the
  feedback loop (the `016-M2` analog), but that is out of scope here.
- **Decoupling thesis (brief)** — a separate source emitting the existing contract, never a stage welded
  into the supervisor pipeline.
- **Honest mock (brief)** — trained offline on a public dataset, inference over replayed logs, no
  real-time production-efficacy claim; raises recall on novel behavior, not literal zero-day exploits.
