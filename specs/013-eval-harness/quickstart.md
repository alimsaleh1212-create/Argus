# Quickstart — Evaluation Harness (#13)

## Run the whole suite locally (fast, deterministic + single provider)

```bash
make eval            # = scripts/run-evals.sh  (memory-safe: one gate per subprocess)
```

- Runs the deterministic gates (supervisor_routing, retrieval, temporal_memory, redaction) and the LLM
  gates (triage, llm_provider) on **Ollama** only. Rationale judge and MinIO upload are skipped
  (freeze/nightly only).
- Prints a per-gate `PASS/FAIL — score vs threshold (provider, required|reported)` summary.
- **Exit non-zero** if any required gate fails (or an orphan/stale gate mismatch). All output is redacted.

Run a single gate (what the batched runner does under the hood):

```bash
uv run python -m backend.eval --gate triage --providers ollama
```

## Run a freeze (both providers + judge + report → MinIO)

Needs the compose stack up (MinIO + Ollama) and a real `GEMINI_API_KEY` seeded into Vault:

```bash
docker compose up -d --wait
make eval-freeze     # = python -m backend.eval --mode freeze --providers gemini,ollama --upload
```

- Evaluates every LLM gate on **both** providers; runs the pinned-judge rationale gate over both producers.
- Persists `eval_report.json` to the **`eval-reports`** bucket at `reports/{commit}/{run_id}.json`
  (freeze tag runs also copy to `freezes/{tag}/eval_report.json`). **History is retained.**
- Prints the **verdict**: `certifiable` / `not_certifiable` / `incomplete`.

## Read the report

```bash
# fetch the latest report for a commit from MinIO (mc, or the BlobClient.get_object helper)
mc cat local/eval-reports/reports/<commit_sha>/<run_id>.json | jq '.verdict, .summary, .rationale'
```

Validate against [contracts/eval-report.schema.json](contracts/eval-report.schema.json).

## Add a new gate (the contract that keeps gates from drifting)

1. Add the gate's threshold block to `config/eval_thresholds.yaml` (it becomes **declared**).
2. Register a runner in `backend/eval/gates/` (`GATE_REGISTRY[name] = runner`) sharing its scoring helper
   with `tests/eval/test_<gate>.py` — **read the threshold from the yaml, never hardcode** (FR-011).
3. The harness's orphan/stale check (exit 2) enforces: declared ⇔ registered. A gate with one but not the
   other fails fast.

## CI behaviour at a glance
- **Every PR** → the new required `eval` job (deterministic + Ollama). A regression fails the build.
- **Nightly + on `v*` tag / manual dispatch** → `eval-freeze.yml`: both providers + judge + report upload.

## Post-merge: add `eval` to branch protection required-status-checks (T045)

After the first green CI run of the `eval` job, add it to GitHub branch protection:

```
Settings → Branches → main → Require status checks to pass before merging
→ Add: "eval"   (this is the `name:` field from `.github/workflows/ci.yml`)
```

This enforces: no PR can merge unless the eval job passes. Until this step is done, the job runs
but does not block merges.


## What you will NOT find here (VD1)
No red-team / injection gate — deferred to **#11 (v3b)**. The harness reserves the name; no v1 report
claims injection coverage. See [plan.md](plan.md) Complexity Tracking + `DECISIONS.md` VD1.
