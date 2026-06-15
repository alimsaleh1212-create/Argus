# Contract — Eval Runner CLI & CI Gates (#13)

## CLI — `python -m backend.eval`

Single entry point (peer to `python -m backend.worker` / `backend.seed_corpus`). Reads
`config/eval_thresholds.yaml` + `EvalSettings`; emits an `EvalReport` (stdout summary + optional upload).

| Flag | Default | Meaning |
|---|---|---|
| `--mode {per_pr,nightly,freeze}` | `per_pr` | selects provider set (`providers_per_pr` vs `providers_freeze`) and whether the rationale gate runs |
| `--providers a,b` | from mode | override the provider list (e.g. `--providers ollama`) |
| `--gate NAME` | all | run one gate (used by the batched memory-safe runner to fan out per gate) |
| `--upload` | off (on for freeze) | persist the report to MinIO under the commit/run key |
| `--out PATH` | `-` | also write `eval_report.json` locally |

**Exit codes** (FR-012):
- `0` — all **required** gates passed (and no catastrophic-floor breach).
- `1` — at least one required gate failed, or a reported-only gate breached its catastrophic floor.
- `2` — **orphan/stale gate** mismatch between the yaml and the registry (FR-002) — abort before scoring.
- `3` — `incomplete`: a required dimension could not complete (provider/store outage) or the MinIO
  upload failed at a freeze/nightly run (FR-009/FR-016).

**Always-redacted**: every line printed and every `evidence` field passes the `Redactor` (FR-014).

## Local convenience
- `make eval` → `scripts/run-evals.sh` (memory-safe, one gate per subprocess; `--mode per_pr`).
- `make eval-freeze` → `python -m backend.eval --mode freeze --upload` (needs MinIO + both providers).

## CI contract

### Per-PR (`.github/workflows/ci.yml`, new `eval` job — **required**)
- Trigger: `push`/`pull_request` (existing CI triggers).
- Runs: deterministic gates (supervisor_routing, retrieval, temporal_memory, redaction) **+** LLM gates
  (triage, llm_provider) on **Ollama only** (`--mode per_pr`). **No** rationale judge, **no** MinIO upload.
- Service: the compose `ollama` (small pinned tag, layer-cached) — no `GEMINI_API_KEY` needed (fork-safe).
- Gate: **blocking** — a required-gate regression fails the build (closes the current "gates not in CI" gap).
- Budget: target ≤ ~6 min added to per-PR CI (R5). *Fallback:* Ollama gates may be demoted to
  non-blocking if CI-infra-flaky, deterministic set stays blocking (recorded if exercised).

### Nightly + Freeze (`.github/workflows/eval-freeze.yml`, **new**)
- Triggers: `schedule` (nightly cron) · `workflow_dispatch` (manual freeze) · `push: tags: ['v*']`.
- Brings up the compose stack (MinIO + Ollama), injects `GEMINI_API_KEY` from repo secrets.
- Runs: `python -m backend.eval --mode freeze --providers gemini,ollama --upload` — every LLM gate on
  **both** providers; rationale judge (pinned Gemini) over both producers; report → MinIO
  (`reports/{commit}/{run_id}.json`, freeze also `freezes/{tag}/eval_report.json`); history retained.
- Gate: a **required** gate failing on **either** provider → `not_certifiable` → workflow fails (FR-007).
- Notification: GitHub-native failed-scheduled-workflow email (no new alerting infra, R8).

## NOT in this contract (VD1)
No `red_team` / `injection` gate, job, or probe set in v1 CI. The registry reserves the name; the
harness asserts no report claims injection coverage (FR-015). Lands with #11 in v3b.
