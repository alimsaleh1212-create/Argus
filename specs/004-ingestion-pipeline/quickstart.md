# Quickstart — Alert Ingestion Pipeline (#4)

Verify the front door end to end: a Wazuh alert flows `webhook → queue → worker → grounded Incident`,
duplicates collapse, secrets are redacted, and a worker crash still terminates the Incident.

> Prereqves: the stack is up (`docker compose up`). #4 activates the **`redis`** service and the
> **`worker`** container; `vault-seed` writes the `secret/ingest` webhook token.

---

## 1. Configure (env / Vault)

Bootstrap config is plain compose `environment:`; the webhook token is a Vault secret.

```bash
# .env (read only by vault-seed) — the webhook shared secret
INGEST_WEBHOOK_TOKEN=dev-ingest-token        # vault-seed writes this to secret/ingest

# Optional tunables (defaults shown) — ARGUS__<SECTION>__<FIELD>
ARGUS__REDIS__URL=redis://redis:6379/0
ARGUS__INGEST__MAX_ALERT_BYTES=262144     # 256 KiB → 413 above this
ARGUS__INGEST__DEDUP_WINDOW_S=300         # duplicate window
ARGUS__INGEST__MAX_ATTEMPTS=3             # worker retry budget → failed
```

A missing `secret/ingest` **fails boot** (it's in `vault.required_paths`).

## 2. Post a sample alert → `202`

```bash
curl -sS -X POST http://localhost:8000/ingest/wazuh \
  -H "Authorization: Bearer dev-ingest-token" \
  -H "Content-Type: application/json" \
  --data @tests/fixtures/wazuh_alerts/ssh_bruteforce.json
# → 202 { "incident_id": "…", "status": "received", "deduplicated": false }
```

Within moments the worker grounds it. Check status (psql, or the #12 dashboard later):

```bash
docker compose exec postgres psql -U sentinel -d sentinel \
  -c "select id, status, severity from incidents order by created_at desc limit 1;"
# → status = grounded,  severity ∈ {low,medium,high,critical}
```

## 3. Duplicate collapses (dedup)

```bash
# POST the same file again within DEDUP_WINDOW_S:
curl -sS -X POST http://localhost:8000/ingest/wazuh -H "Authorization: Bearer dev-ingest-token" \
  -H "Content-Type: application/json" --data @tests/fixtures/wazuh_alerts/ssh_bruteforce.json
# → 200 { "incident_id": "<same id>", "deduplicated": true }   (no second incident)
```

## 4. Redaction holds

```bash
# An alert whose full_log carries a fake AWS key / bearer token:
curl -sS -X POST http://localhost:8000/ingest/wazuh -H "Authorization: Bearer dev-ingest-token" \
  -H "Content-Type: application/json" --data @tests/fixtures/wazuh_alerts/with_secret.json
# The stored incident.raw_alert, the queue message, logs, and spans contain only [REDACTED:*] forms.
docker compose exec postgres psql -U sentinel -d sentinel \
  -c "select raw_alert from incidents order by created_at desc limit 1;" | grep -c AKIA   # → 0
```

## 5. Rejection paths (no side effects)

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/ingest/wazuh \
  -H "Content-Type: application/json" --data '{}'                         # → 401 (no token)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/ingest/wazuh \
  -H "Authorization: Bearer dev-ingest-token" -H "Content-Type: application/json" \
  --data '{"not":"a wazuh alert"}'                                        # → 422 (no incident, no job)
```

## 6. Resilience

```bash
# Redis down ⇒ /ready reports not-ready and ingest returns 503 (no orphan incident):
docker compose stop redis
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/ready      # → 503
docker compose start redis

# Worker crash mid-job ⇒ on restart, recover() reclaims the in-flight job; the incident
# still reaches grounded or failed (never stuck). Exercised by the fault-injection e2e test.
```

---

## Test tiers (what "done" looks like)

```bash
uv run pytest tests/unit -q          # wazuh mapping, severity banding, dedup fingerprint, grounding, intake FSM
uv run pytest tests/integration -q   # real redis (queue/dedup/recover) + postgres (incidents migration + repo)
uv run pytest tests/e2e -q           # POST → 202 → grounded; dedup; redaction; crash-recovery
uv run ruff check . && uv run lint-imports
```

Done = all three tiers green, ≥80% coverage on new code, the **smoke** gate brings up `redis`+`worker`
clean, and the **redaction** gate passes through the new ingest path.
