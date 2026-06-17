# ML Anomaly Detector on SIEM Logs — Discussion & Decision

**Date:** 2026-06-16 · **Status:** decision record (spec **#17** specified & clarified — see §11)
**Context:** raised while specifying `014-detector`. Captures the research, the layering decision
(keep the deterministic detector, *add* ML — do not replace), datasets, and how to mock it in the demo.
See also: [resources/SOAR_brief.md](resources/SOAR_brief.md) (*Detection Strategy Update* addendum) and
[resources/v_2_3_plan.md](resources/v_2_3_plan.md).

---

## 1. The question

> Can a SOAR add an **ML anomaly detector on top of the SIEM** (reading SIEM **logs**, not raw network
> traffic) to catch threats that Wazuh's standard rules miss? Is it possible to add one (which dataset)?
> Can it be **mocked** in the demo when there is no real Wazuh system? And should it **replace** the
> deterministic rule detector?

## 2. Short answer

- **Yes, it's standard practice.** Mature stacks layer an ML/UEBA capability that reads SIEM
  logs/events and baselines "normal" behavior to flag deviations — catching compromised credentials,
  lateral movement, insider threat, and APT-style activity that signature rules miss.
- **Yes, it's addable and mockable** without a live Wazuh (train offline, replay logs through it).
- **No, it must not replace the deterministic detector.** The professional answer is **layering**:
  signature + anomaly cover each other's blind spots. Running anomaly detection *instead of* signatures
  is a misconception no serious SOC follows.

## 3. What the pro systems actually do (UEBA)

UEBA (User & Entity Behavior Analytics) is the category name. It learns a behavioral baseline from
historical **log** data and flags anomalies — explicitly marketed for **unknown threats, zero-day
exploits, and APTs** that traditional rules miss.

- **Splunk UBA**, **Microsoft Sentinel UEBA/Fusion**, **Exabeam** all ingest SIEM logs and apply ML
  baselining — they sit *on top of* normalized telemetry, not on the wire.
- **Wazuh specifically supports it** (matters — it's our stack): the **OpenSearch Anomaly Detection
  plugin** ingests `wazuh-alerts-*` / `wazuh-archives-*` indices and runs unsupervised models that adapt
  over time. So "ML on top of SIEM, reading logs" is a documented Wazuh pattern.
- The **"~97% accuracy" figure** in our roadmap is real: a peer-reviewed hybrid Wazuh framework reports
  **Random Forest 97.2%**, **DBSCAN 91.06%** with FP ≈ 0.082, classifying `wazuh-alerts` (not packets).

### Honesty caveat on "zero-day"
Anomaly detection does **not** detect zero-day *exploits* in the reverse-engineering sense. It detects
the **behavioral footprint** of attacks the rule-set never anticipated. That raises **recall on novel
*behavior***, which is the honest, defensible claim — not "detects zero-day exploits."

## 4. Where it fits Argus's architecture

It does **not** change where the SOAR sits and does **not** get welded into the response pipeline. It is
a **decoupled detection source** that emits the existing `#4` ingestion contract
(`WazuhAlert`/`NormalizedEvent`) with **zero downstream change** — the *same* integration boundary the
deterministic detector (`#14`) uses. "A SOAR welded to one detector can't serve a SOC that runs five —
the decoupling is the point."

- **`#14` deterministic rule/threshold detector** — built first, the real shipping source.
- **`#17` ML anomaly detector (UEBA-style)** — built after `#14`, complementary, reads SIEM logs.

## 5. Layering, not replacement — the decision & rationale

**Decision: keep `#14`, add `#17` as a complementary layer. Do not replace.**

### How the deterministic detector works
Config-backed rule/threshold matching over event fields (the Wazuh/Sigma model): signature matches on
known-bad patterns/IOCs, plus aggregation thresholds (e.g. *N failed logins within a window*). It fires
**deterministically with an exact, auditable reason** ("matched rule X"). Same input → same output.

### Why keep it even with ML

| Property | Deterministic rule detector (`#14`) | ML anomaly detector (`#17`) |
|---|---|---|
| Known threats (the ~80%) | Near-zero FP, instantly correct | Overkill; may miss a brand-new IOC it never trained on |
| Explainability | "matched rule X" — auditable | "score 0.87" — opaque |
| Cold start / data | Works day 1, **zero training data** | Needs labeled dataset + baseline period |
| Drift | None | Degrades; needs retraining/monitoring |
| Cost / latency | Sub-ms, no GPU, no serving | Feature pipeline + model serving + drift monitoring |
| Constitution IV | ✅ determinism-first | ⚠️ explicit recorded exception |
| Role | High-precision baseline for **known** bad | High-recall net for **novel behavior** |

Replacing rules with ML would discard the proposal's strongest, most reviewer-proof argument (layering),
contradict Constitution IV, inflate scope into "a separate project's lifecycle," and swap the one **real**
detector for a **mock-only** one in the demo.

## 6. Datasets (offline training) — ranked for "reads SIEM logs"

Prefer **log/auth** datasets over network-flow sets, to match the "logs not traffic" framing.

| Dataset | Why it fits | Notes |
|---|---|---|
| **CERT Insider Threat (r6.2)** ⭐ | Synthetic user-activity logs (logon, file, email, device) with **labeled insider scenarios** → UEBA | Easiest demo; widely used with Isolation Forest. ~3.5M logon + 2M file events, 4,000 users |
| **LANL Comprehensive Cyber-Security Events** ⭐ | Real **Windows auth** + DNS + process + flow with **red-team labels** → lateral movement / credential abuse | Closest to "SIEM logs." 58 days, ~1.6B events. *Very* imbalanced (~0.00007% malicious) |
| **LogHub** (HDFS, BGL, Thunderbird, OpenStack) | Labeled **system-log** anomaly benchmarks | Best framed as log-template anomaly detection rather than UEBA |
| UNSW-NB15 / CIC-IDS2017 | Common, well-documented | ⚠️ **network-flow** datasets — weaker fit for "logs not traffic" |

**Recommended:** **CERT Insider Threat** (cleanest labels, narrative-friendly, trains in minutes with
Isolation Forest) or **LANL auth logs** for the lateral-movement story.

## 7. Mocking it in the demo (no live Wazuh required)

We only need to produce `WazuhAlert`/`NormalizedEvent`-shaped records — not a live Wazuh feed. Three
honesty tiers, most-real to least:

1. **Real tiny model, offline-trained, replayed inference (recommended).** Train Isolation Forest / a
   small autoencoder offline on CERT or LANL, save the model artifact, then at demo time replay log
   events through it; anomalies over threshold fire alerts into ingestion. *Real ML, mock environment* —
   same honesty bar as `#14`.
2. **Precomputed-scores fixture replay.** A component with the real interface returns precomputed
   anomaly verdicts for a curated replay set. Model mocked, boundary real.
3. **Pure stub** behind the same seam returning canned anomaly alerts — for plumbing/e2e tests.

**Honesty boundary (mock-environment rule):** state plainly that the model is **trained offline on a
public dataset** and inference runs over **replayed logs**, not a live Wazuh stream — **no real-time
production-efficacy claim**. Mirrors the brief: "Demo runs on replayed sample alerts. The pipeline
behaves identically to live ingestion."

## 8. Constitution reconciliation

Constitution IV ("determinism first; ML/agents only for the ambiguous long tail") is preserved on the
**response** path. The ML detector is an explicit, recorded exception at the **detection** layer
(catching novel *behavior* is exactly where determinism does not suffice). It is **decoupled** from the
supervisor (adds no second writer, no new FSM edge) and **complements** the deterministic detector. To
be captured as a `DECISIONS.md` entry + a constitution note before `#17` implementation lands.

## 9. Sequencing & open items

- **Sequence:** `#14` (deterministic, real) → `#17` (ML anomaly, decoupled, mock-replayed).
- **Renumbering note:** `#17` was sketched as XDR-correlation in `v_2_3_plan.md`; ML anomaly now takes
  the 017 slot and **XDR rolls forward to a later slot / v3**. The roadmap still needs reconciling to
  match (017 = ML anomaly; XDR → v3d). *(Open: update `v_2_3_plan.md` §2 spec map + §5.)*
- ML anomaly detection is a substantial effort (dataset + training + baselining + drift + eval skew) —
  pursue `#17` only with genuine surplus; otherwise it stays designed-but-deferred.

## 10. Code structure — `backend/` across layers, NOT a standalone `ml/` dir

**Decision (plan-time): keep #17 inside `backend/`, distributed across the existing inward-only layers.**
Not a top-level `ml/` package; not even a `backend/ml/` sub-package. (Recorded as `DECISIONS.md` **AD1**.)

| Piece | Location | Layer rule it respects |
|---|---|---|
| Pure types + `AnomalyModel` Protocol | `backend/domain/anomaly.py` | `domain` isolated, no outward imports but `Severity` |
| Feature build / scoring / mapping (pure) | `backend/services/anomaly.py` | mirrors `services/detector.py` |
| sklearn wrapper (`joblib` load + `score_samples`) | `backend/infra/anomaly_model.py` | `infra` owns external SDKs (like `infra/llm_drivers.py`) |
| Offline trainer / replay runner | `backend/anomaly_train.py`, `backend/anomaly_detector.py` | one-shot entrypoints (like `backend/detector.py`) |
| Artifact | `backend/data/anomaly/model.joblib` | next to `backend/data/detector/rules.yaml` |
| Eval gate | `backend/eval/gates/anomaly_detection.py` | next to `gates/detection.py` |

**Why backend, not standalone `ml/`:**
1. **The runner calls back into `backend`** — it ends at `services/intake.accept(..., source="anomaly-detector")`
   and reuses config/redaction/queue/cache. A top-level `ml/` importing `backend.services.intake` inverts
   the dependency direction (outer → inner) and breaks the `import-linter` contract. The emitter belongs in
   the package that owns ingestion.
2. **"One image, many containers"** (platform decision #1) — the single backend image already runs
   API/migrate/worker/detector as different commands; `anomaly_train` + `anomaly_detector` are two more
   one-shot commands in that mold. A standalone dir implies a second runtime/uv-project for zero benefit
   (Isolation Forest is CPU-only, sub-second).
3. **The layers absorb ML cleanly** — pure transforms → `services`, the sklearn wrapper → `infra` (behind
   the injected `AnomalyModel` Protocol, faked in tests), entrypoints at top level. **No new top-level
   layer, no new import-linter contract.** A `backend/ml/` sub-package would straddle the
   `routers→services→agents→repositories→infra` graph (pure transforms + sklearn I/O + an entrypoint in one
   folder) — a layering smell. `scikit-learn` stays confined to `infra` + the train/eval entrypoints;
   `pandas` is dev/training-only, kept off the serve path.

**When a standalone dir *would* be right (deferred, not now):** only if the ML layer became a genuine
separate deployable — a model-serving microservice, a GPU runtime (autoencoder, rejected), or the cohort
"detection-focused project supplies this half" composition (v3a). Then it would be a **uv workspace member
with its own image**, talking to `backend` over the **ingestion contract (`POST /ingest`)**, not via
imports. That seam already exists, so #17 can spin out later with zero rework — the decoupling-as-architecture
exit ramp.

## 11. Spec #17 clarification decisions (2026-06-16)

Resolved during `/speckit-clarify` on `specs/017-ml-anomaly-detector/spec.md`. These supersede the
"decide at plan time" notes that were open items.

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | **Scoring granularity** | **Per-entity time window** (UEBA-style) | Aggregate each user/host's activity over a configured window into behavioral features, then score the window. Single events lack behavioral context; window scoring is the UEBA standard and matches CERT/LANL label semantics. |
| D2 | **Alert severity** | **Config-backed score→severity bands** | Map anomaly-score ranges onto the existing severity scale via config. Deterministic, tunable without code, and preserves the score's signal for supervisor routing (vs. a fixed severity that discards it). |
| D3 | **Eval gate posture** | **Blocking / required** | Committed precision/recall floors + FP ceiling fail CI — same posture as #14's detection gate and Constitution II default. Justified: gate scores the saved artifact (FR-010), so it is deterministic with no runtime variance to excuse softening. |
| D4 | **Dataset** | **CERT Insider Threat (r6.2)** | Scenario-labeled user-activity logs (logon, device, file, email, http); entities = users; clean malicious-vs-normal labels; manageable size for a solo build; widely paired with Isolation Forest. LANL was the considered alternative (richer, much larger, ~0.00007% malicious imbalance). |

**Remaining plan-time items** (not ambiguous, just config-level): CERT release/version, time-window
length, concrete threshold values, score→severity band breakpoints, model library (Isolation Forest
preferred — lightweight, GPU-free — vs. compact autoencoder), model-artifact storage (repo vs. MinIO).

## 12. Sources

- [Exabeam — UEBA tools & capabilities](https://www.exabeam.com/explainers/ueba/ueba-tools-key-capabilities-and-7-tools-you-should-know/)
- [Microsoft Learn — UEBA in Microsoft Sentinel](https://learn.microsoft.com/en-us/azure/sentinel/identify-threats-with-entity-behavior-analytics)
- [Exabeam — Microsoft Sentinel vs Splunk](https://www.exabeam.com/explainers/microsoft-sentinel/microsoft-sentinel-vs-splunk-6-key-differences-and-how-to-choose/)
- [Wazuh — Enhancing IT security with anomaly detection](https://wazuh.com/blog/enhancing-it-security-with-anomaly-detection/)
- [Wazuh GitHub — integration with external anomaly-detection module](https://github.com/wazuh/wazuh/discussions/14841)
- [MDPI — Improving Threat Detection in Wazuh Using ML (RF 97.2% / DBSCAN)](https://www.mdpi.com/2624-800X/5/2/34)
- [LANL — Comprehensive, Multi-Source Cyber-Security Events Data Set (OSTI)](https://www.osti.gov/biblio/1179829)
- [CERT Insider Threat dataset — Isolation Forest example](https://github.com/AymanMansur/Insider-threat-detection-using-cert-dataset-Logon-)
- [LogHub — collection of system log datasets (arXiv)](https://arxiv.org/pdf/2008.06448)
- [Lateral Movement Detection on LANL authentication logs (arXiv)](https://arxiv.org/pdf/2411.10279)
