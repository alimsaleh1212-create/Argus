# Phase 1 Data Model: Deterministic Rule/Threshold Detector (#14)

All types are **pure Pydantic v2** in `backend/domain/detector.py` (`extra="forbid"`, `frozen=True`
where natural), with **no outward imports** except `Severity` from `domain/incident.py` (domain→domain
is allowed under the isolation contract). **No persistence model — no migration.** The detector reuses
the existing `Incident`/`WazuhAlert` schema for emission.

---

## RawEvent

A replayed source event the detector evaluates. Distinct from a pre-made alert.

| Field | Type | Notes |
|---|---|---|
| `event_time` | `datetime` | drives threshold windows (no wall-clock) |
| `fields` | `dict[str, Any]` | structured event fields (dotted paths are addressed by rules) |
| `source_host` | `str \| None` | originating host/agent, optional |

- Validation: `event_time` required and parseable. A record failing validation is **skipped** (FR-009),
  not fatal.

## DetectionRule (discriminated union on `kind`)

### MatchRule (`kind="match"`) — signature, single event

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | stable rule id (becomes the emitted `WazuhRule.id`) |
| `kind` | `Literal["match"]` | discriminator |
| `description` | `str` | human reason (becomes `WazuhRule.description`) |
| `field` | `str` | dotted path into `RawEvent.fields` |
| `op` | `Literal["equals","contains","regex","in_list"]` | match operator |
| `value` | `str \| None` | literal for equals/contains/regex |
| `list_ref` | `str \| None` | name of a config-backed list (e.g. `ioc_ips`) for `in_list` |
| `severity` | `Severity` | reused enum (low/medium/high/critical) |
| `technique` | `str \| None` | optional MITRE technique id |

### ThresholdRule (`kind="threshold"`) — aggregation, windowed

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | stable rule id |
| `kind` | `Literal["threshold"]` | discriminator |
| `description` | `str` | human reason |
| `match` | `MatchRule`-shaped condition | qualifying-event predicate |
| `group_by` | `str` | dotted field to group on (e.g. `src_ip`) |
| `count` | `int` (`gt=0`) | N qualifying events to fire |
| `window_seconds` | `int` (`gt=0`) | window W over `event_time` |
| `severity` | `Severity` | emitted severity |
| `technique` | `str \| None` | optional MITRE technique id |

- Validation: `op="in_list"` requires `list_ref`; `op` in {equals,contains,regex} requires `value`.
  Invalid `regex` → settings/load-time error (fail fast, not per-event).

## RuleSet

| Field | Type | Notes |
|---|---|---|
| `rules` | `list[DetectionRule]` | ordered; order breaks severity ties (D4) |
| `lists` | `dict[str, list[str]]` | named lists referenced by `in_list` (e.g. IOC lists) |

- Loaded from `backend/data/detector/rules.yaml` (path is config-backed; empty/absent set → no alerts,
  no crash, per Edge Cases).

## FiredAlert

The pure output of `evaluate()` — one per detection, before mapping to the ingestion contract.

| Field | Type | Notes |
|---|---|---|
| `rule_id` | `str` | the (highest-severity, D4) matched rule |
| `description` | `str` | matched rule description |
| `severity` | `Severity` | matched rule severity |
| `technique` | `str \| None` | MITRE technique, if any |
| `event` | `RawEvent` | the originating event (or the triggering event for a threshold group) |
| `group_key` | `str \| None` | for threshold rules, the `group_by` value |

## Mapping: FiredAlert → WazuhAlert (emission contract)

`services/detector.fired_alert_to_wazuh_alert(fired) -> WazuhAlert` builds the **existing** ingestion
type (`domain/incident.py`):

- `WazuhAlert.rule = WazuhRule(id=fired.rule_id, level=<severity→level>, description=fired.description,
  groups=[fired.technique] if technique else [])`
- `WazuhAlert.data = fired.event.fields` (carries originating fields through)
- `WazuhAlert.full_log` = a compact deterministic summary string
- `WazuhAlert.agent` = `WazuhAgent(name=fired.event.source_host)` when present

The severity→level inverse uses the existing `level_to_severity` thresholds in `services/wazuh.py`
(pick a representative level per severity band) so the downstream `intake.accept()` re-derives the same
`Severity`. Emission then calls `intake.accept(..., alert=<WazuhAlert>, source="detector")`.

## State / lifecycle

- The detector is **stateless across runs** (one-shot). Threshold windows are computed within a single
  `evaluate()` call over the replay set, ordered by `event_time`.
- It creates **new** `Incident` rows (status `received`) exactly as any alert source does; it has **no**
  authority over incident state transitions (the supervisor remains single writer).
- **Idempotency / replay-safety** is delegated to the existing dedup fingerprint in `intake.accept()`
  (FR-008) — re-running the same replay set creates no duplicates.
