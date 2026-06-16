# Contract: `detection` Eval Gate

**Owner**: `014-detector`. **Harness**: `backend/eval/` (#13). **Kind**: deterministic,
provider-independent (the detector has no LLM). **Declared in `config/eval_thresholds.yaml` AND
registered in `GATE_REGISTRY` in the same change** — an orphan declaration or stale runner is a hard
error (exit 2) per #13.

## Inputs

- **Rule set**: a test rule set (`tests/fixtures/detector/rules.yaml`).
- **Labeled replay set**: `tests/fixtures/detector/replay/scenarios.json` — raw events each carrying a
  ground-truth `label` (`malicious`/`benign`) and, for threshold scenarios, a `group` id and an
  `expected_rule` (the rule that should fire). Benign events carry no `expected_rule`.

## Scoring

Run `services/detector.evaluate(events, rules)` over the replay set and compare fired alerts to labels:

- **True positive (TP)**: a malicious-labeled event (or threshold group) produces an alert attributed to
  its `expected_rule`.
- **False positive (FP)**: a benign-labeled event produces any alert.
- **False negative (FN)**: a malicious-labeled event/group produces no alert.

```
precision = TP / (TP + FP)
recall    = TP / (TP + FN)
```

Threshold scenarios count as **one** expected detection per group (the Nth qualifying event fires once —
SC-007); firing on fewer than N is an FN, firing more than once per group is an FP.

## Thresholds (committed in `eval_thresholds.yaml`)

```yaml
detection:
  description: >
    Detection precision/recall gate (SPEC-detector #14). Deterministic / provider-independent.
    Runs a labeled replay set through the rule/threshold detector and scores precision & recall.
    Extends the suite; does not duplicate existing gates.
  kind: deterministic
  required: true
  threshold:
    precision_min: 0.90
    recall_min: 0.90
```

(Seed values; tighten once the replay fixture is finalized. Both must hold to pass — SC-002/SC-003.)

## Registration

`backend/eval/gates/detection.py` defines `async def run_detection(spec, provider) -> GateResult` and
registers it: `GATE_REGISTRY["detection"] = run_detection`. `validate_registry()` then sees `detection`
declared (yaml) ⇔ registered (code). Score is the dict `{"precision": p, "recall": r}`; `passed` iff
`p >= precision_min and r >= recall_min`.

## Out of scope for this gate

- No live/network data (replay fixture only).
- No model/anomaly scoring (that is the future `#17` gate).
- Does not assert anything about downstream triage/response (covered by existing gates + the e2e test).
