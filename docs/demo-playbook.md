# Argus Demo Playbook — Stakeholder Showcase

## Reference: Severity Mapping

| Wazuh `rule.level` | Severity | Pipeline path |
|---|---|---|
| 1–3 | **LOW** | Fast-path → `resolved` (no LLM) |
| 4–7 | **MEDIUM** | Full pipeline → `watchlist_and_ticket` (auto-execute) |
| 8–11 | **HIGH** | Full pipeline → `enrich_tag_and_ticket` (auto-execute) |
| 12+ | **CRITICAL** | Skip triage/enrichment → `RESPONDING` → destructive actions → `AWAITING_APPROVAL` |

**Playbooks (from `backend/data/playbooks/catalog.yaml`):**

| Playbook | Severity + groups | Actions | Auto-execute? |
|---|---|---|---|
| `watchlist_and_ticket` | low, medium | `add_to_watchlist` + `open_ticket` | Yes |
| `enrich_tag_and_ticket` | high + indicator/malware | `enrich_and_tag` + `open_ticket` | Yes |
| `isolate_and_ticket` | critical + attack/intrusion/lateral_movement/privilege_escalation | `isolate_host` + `open_ticket` | **No — HITL** |
| `block_ip_and_ticket` | critical + network_attack/brute_force/scanning | `block_ip` + `open_ticket` | **No — HITL** |
| `disable_user_and_ticket` | critical + account_compromise/credential_stuffing/insider_threat | `disable_user` + `open_ticket` | **No — HITL** |
| `full_response` | critical (any) | `add_to_watchlist` + `isolate_host` + `open_ticket:critical` | **No — HITL** |

---

## Pre-flight

```bash
# Verify the stack is healthy
curl -s http://localhost:8000/health | jq .

# Set reusable vars
TOKEN="dev-webhook-token"
BASE="http://localhost:8000"
INGEST="$BASE/ingest/wazuh"
H_AUTH="Authorization: Bearer $TOKEN"
H_CT="Content-Type: application/json"

# Log in to the dashboard and capture JWT
JWT=$(curl -s -X POST "$BASE/auth/login" \
  -H "$H_CT" \
  -d '{"username":"admin","password":"argus-admin-2026"}' | jq -r '.access_token')
```

---

## ACT 1 — Noise Intelligence (4 incidents)

> **Talking point:** "Argus doesn't cry wolf — it correctly ignores authorized activity."

### 1.1 — Internal vulnerability scanner (LOW → auto-resolved, zero LLM)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-noise-scanner-001",
  "timestamp": "2026-06-15T08:00:00Z",
  "rule": { "id": "5701", "level": 2, "description": "Nmap scan detected", "groups": ["scanning"] },
  "agent": { "id": "012", "name": "dmz-web-01", "ip": "10.0.1.12" },
  "data": { "srcip": "10.0.99.5", "dstip": "10.0.1.12", "tool": "nmap" },
  "full_log": "nmap scan from security-scanner.internal (10.0.99.5) against dmz-web-01"
}' | jq '{incident_id, status}'
```

**Expected:** `resolved` immediately — no triage LLM call, no ticket.

---

### 1.2 — Monitoring agent ICMP sweep (LOW → auto-resolved)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-noise-icmp-001",
  "timestamp": "2026-06-15T08:05:00Z",
  "rule": { "id": "5000", "level": 3, "description": "ICMP sweep from monitoring host", "groups": ["network"] },
  "agent": { "id": "005", "name": "infra-mon-01", "ip": "10.0.0.5" },
  "data": { "srcip": "10.0.0.10", "proto": "icmp", "count": "254" },
  "full_log": "Nagios ICMP sweep 10.0.0.10 -> 10.0.0.0/24 — routine availability check"
}' | jq '{incident_id, status}'
```

**Expected:** `resolved` — LOW fast-path, never queued for LLM.

---

### 1.3 — Admin login from corporate VPN during business hours (MEDIUM → LLM triages as noise)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-noise-vpn-admin-001",
  "timestamp": "2026-06-15T09:30:00Z",
  "rule": { "id": "5501", "level": 5, "description": "Privileged login from remote host", "groups": ["authentication_success", "privileged"] },
  "agent": { "id": "001", "name": "prod-db-01", "ip": "10.0.2.20" },
  "data": { "srcip": "203.0.113.45", "user": "svc-deploy", "method": "publickey", "dstport": "22" },
  "full_log": "Accepted publickey for svc-deploy from 203.0.113.45 port 41820 ssh2 — CI/CD pipeline deploy job"
}' | jq '{incident_id, status}'
```

**Expected:** MEDIUM → triage LLM sees deploy user + publickey + known VPN range → `noise` → `resolved`.

---

### 1.4 — Scheduled backup job generating high I/O alerts (MEDIUM → noise)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-noise-backup-001",
  "timestamp": "2026-06-15T02:00:00Z",
  "rule": { "id": "5901", "level": 4, "description": "High volume file reads detected", "groups": ["file_monitor"] },
  "agent": { "id": "003", "name": "fileserver-01", "ip": "10.0.3.10" },
  "data": { "user": "svc-backup", "path": "/data/archives/", "read_count": "48200" },
  "full_log": "svc-backup read 48200 files in /data/archives/ — matches nightly backup window 02:00-04:00"
}' | jq '{incident_id, status}'
```

**Expected:** MEDIUM → LLM sees backup service account + known schedule → `noise` → `resolved`.

---

## ACT 2 — Real Threats, Auto-Remediated (4 incidents)

> **Talking point:** "For confirmed threats below the destructive threshold, Argus acts immediately — no human needed."

### 2.1 — SSH brute force from Tor exit node (MEDIUM → watchlist + ticket)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-real-ssh-brute-001",
  "timestamp": "2026-06-15T10:15:00Z",
  "rule": { "id": "5710", "level": 6, "description": "SSH brute force — 12 failed attempts", "groups": ["authentication_failures", "brute_force"] },
  "agent": { "id": "002", "name": "bastion-01", "ip": "10.0.1.2" },
  "data": { "srcip": "185.220.101.47", "dstport": "22", "failed_count": "12", "user": "root" },
  "full_log": "12 consecutive SSH auth failures from 185.220.101.47 (Tor exit node) targeting root@bastion-01"
}' | jq '{incident_id, status}'
```

**Expected:** MEDIUM → triage: `real` → enrichment: Tor IP + MITRE T1110 → `watchlist_and_ticket` → `remediated`.

---

### 2.2 — Web application login flood / credential stuffing (MEDIUM → watchlist + ticket)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-real-web-bruteforce-001",
  "timestamp": "2026-06-15T10:30:00Z",
  "rule": { "id": "31151", "level": 7, "description": "Web application brute force attempt", "groups": ["authentication_failures", "web", "brute_force"] },
  "agent": { "id": "008", "name": "webapp-prod-01", "ip": "10.0.4.8" },
  "data": { "srcip": "91.108.56.200", "url": "/api/auth/login", "method": "POST", "http_code": "401", "count": "340" },
  "full_log": "340 POST /api/auth/login 401 responses in 60s from 91.108.56.200 — credential stuffing pattern"
}' | jq '{incident_id, status}'
```

**Expected:** MEDIUM → `real` → enrichment: MITRE T1110.004 → `watchlist_and_ticket` → `remediated`.

---

### 2.3 — Malware beacon to known C2 domain (HIGH → enrich_tag + ticket)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-real-c2-beacon-001",
  "timestamp": "2026-06-15T11:00:00Z",
  "rule": { "id": "87101", "level": 9, "description": "Outbound connection to known malware C2", "groups": ["indicator", "malware", "c2"] },
  "agent": { "id": "015", "name": "workstation-ali", "ip": "10.0.5.55" },
  "data": {
    "srcip": "10.0.5.55", "dstip": "45.142.212.100", "dstport": "443",
    "domain": "update-service.xyz", "proto": "tcp",
    "bytes_out": "1240", "interval_secs": "300"
  },
  "full_log": "workstation-ali -> 45.142.212.100:443 (update-service.xyz) every 300s — consistent beacon interval, known Cobalt Strike C2"
}' | jq '{incident_id, status}'
```

**Expected:** HIGH + `indicator` + `malware` → `enrich_tag_and_ticket` → `enrich_and_tag` + `open_ticket` (both auto-execute) → `remediated`.

---

### 2.4 — Obfuscated PowerShell spawned by Word (HIGH → enrich_tag + ticket)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-real-powershell-001",
  "timestamp": "2026-06-15T11:20:00Z",
  "rule": { "id": "92201", "level": 8, "description": "Obfuscated PowerShell command execution", "groups": ["indicator", "execution", "defense_evasion"] },
  "agent": { "id": "020", "name": "hr-laptop-07", "ip": "10.0.6.77" },
  "data": {
    "user": "jsmith", "process": "powershell.exe",
    "cmdline": "powershell -enc JABjAGwAaQBlAG4AdAAgAD0AIABOAGUAdwAtAE8AYgBqAGUAYwB0AA==",
    "parent_process": "winword.exe"
  },
  "full_log": "winword.exe spawned powershell.exe with base64-encoded payload on hr-laptop-07 — macro execution pattern"
}' | jq '{incident_id, status}'
```

**Expected:** HIGH + `indicator` + `execution` → enrichment cross-correlates MITRE T1059.001 + T1027 → `enrich_tag_and_ticket` → `remediated`.

---

## ACT 3 — HITL Approval Flow (2 incidents)

> **Talking point:** "Destructive actions never execute without a human in the loop. The system stops and waits."

### 3.1 — Lateral movement: internal host scanning critical subnet → APPROVE

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-hitl-lateral-001",
  "timestamp": "2026-06-15T13:00:00Z",
  "rule": { "id": "40101", "level": 12, "description": "Internal host conducting port scan of critical subnet", "groups": ["attack", "lateral_movement", "scanning"] },
  "agent": { "id": "030", "name": "dev-server-03", "ip": "10.0.7.30" },
  "data": {
    "srcip": "10.0.7.30", "dst_subnet": "10.0.2.0/24",
    "ports_scanned": "22,135,139,445,3389,5985", "scan_type": "SYN",
    "hosts_hit": "52"
  },
  "full_log": "dev-server-03 SYN-scanned 52 hosts in prod subnet 10.0.2.0/24 across SMB/RDP/WinRM ports — consistent with post-compromise recon (MITRE T1046)"
}' | jq '{incident_id, status}'
```

**Expected:** CRITICAL + `lateral_movement` → `isolate_and_ticket` → `isolate_host` destructive → `awaiting_approval`.

**Approve it:**
```bash
APPROVAL_ID=$(curl -s "$BASE/approvals" \
  -H "Authorization: Bearer $JWT" | jq -r '.[0].id')

curl -s -X POST "$BASE/approvals/$APPROVAL_ID/decision" \
  -H "Authorization: Bearer $JWT" -H "$H_CT" \
  -d '{"decision": "approve", "rationale": "Confirmed lateral movement — isolate immediately"}' | jq .
```

**Outcome:** → `RESPONDING` re-enters → `isolate_host` executes → `remediated`. Audit row created.

---

### 3.2 — Impossible travel: login from two continents 4 minutes apart → REJECT

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-hitl-impossible-travel-001",
  "timestamp": "2026-06-15T14:00:00Z",
  "rule": { "id": "62001", "level": 12, "description": "Impossible travel — account compromise indicator", "groups": ["account_compromise", "credential_stuffing"] },
  "agent": { "id": "001", "name": "idp-prod-01", "ip": "10.0.1.1" },
  "data": {
    "user": "c.moore@company.com",
    "login_1": { "ip": "64.233.160.0", "geo": "US-CA", "time": "2026-06-15T13:56:00Z" },
    "login_2": { "ip": "91.108.4.200", "geo": "RU-MOW", "time": "2026-06-15T14:00:00Z" },
    "delta_minutes": "4",
    "distance_km": "9400"
  },
  "full_log": "c.moore logged in from California then Moscow 4 minutes later — physically impossible, credential theft likely"
}' | jq '{incident_id, status}'
```

**Expected:** CRITICAL + `account_compromise` → `disable_user_and_ticket` → `disable_user` destructive → `awaiting_approval`.

**Reject it (show both approve/reject flows to stakeholders):**
```bash
APPROVAL_ID=$(curl -s "$BASE/approvals" \
  -H "Authorization: Bearer $JWT" | jq -r '.[0].id')

curl -s -X POST "$BASE/approvals/$APPROVAL_ID/decision" \
  -H "Authorization: Bearer $JWT" -H "$H_CT" \
  -d '{"decision": "reject", "rationale": "User contacted SOC — travel is legitimate, VPN misconfiguration"}' | jq .
```

**Outcome:** → `rejected_by_human`. Audit row created. Account stays active.

---

## ACT 4 — Escalation Showcase (2 incidents)

> **Talking point:** "The most severe threats trigger immediate destructive-action proposals — and if the cap is exceeded, they escalate to human triage."

### 4.1 — Active ransomware: mass encryption + shadow copy deletion

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-escalate-ransomware-001",
  "timestamp": "2026-06-15T15:00:00Z",
  "rule": { "id": "99001", "level": 14, "description": "Ransomware activity — mass file modification and VSS deletion", "groups": ["attack", "intrusion", "malware"] },
  "agent": { "id": "040", "name": "fileserver-prod-02", "ip": "10.0.3.40" },
  "data": {
    "process": "svchost.exe", "user": "SYSTEM",
    "files_modified": "8420", "extensions_renamed_to": ".locked",
    "vss_deleted": "true", "backup_catalog_wiped": "true",
    "ransom_note_dropped": "README_RESTORE.txt"
  },
  "full_log": "fileserver-prod-02: 8420 files renamed .locked in 90s, VSS snapshots deleted, ransom note dropped — active LockBit variant indicators"
}' | jq '{incident_id, status}'
```

**Expected:** CRITICAL → `full_response` playbook (`add_to_watchlist` + `isolate_host:critical`) → `isolate_host` destructive → `awaiting_approval` or `escalated` if hard cap hit.

---

### 4.2 — Massive data exfiltration to unknown external IP

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-escalate-exfil-001",
  "timestamp": "2026-06-15T15:30:00Z",
  "rule": { "id": "87500", "level": 13, "description": "Abnormal outbound data transfer volume", "groups": ["network_attack", "attack", "intrusion"] },
  "agent": { "id": "045", "name": "analytics-db-01", "ip": "10.0.4.45" },
  "data": {
    "srcip": "10.0.4.45", "dstip": "198.51.100.77", "dstport": "443",
    "bytes_out": "42949672960", "duration_s": "1800",
    "avg_mbps": "190", "dst_asn": "AS64496", "dst_country": "Unknown"
  },
  "full_log": "analytics-db-01 sent 40 GB to 198.51.100.77 (unregistered ASN) over 30 minutes via HTTPS — no business justification found"
}' | jq '{incident_id, status}'
```

**Expected:** CRITICAL + `network_attack` → `block_ip_and_ticket` → `block_ip` destructive → `awaiting_approval`.

---

## ACT 5 — Enrichment Deep Dive (3 incidents)

> **Talking point:** "Argus reasons across three knowledge sources simultaneously: threat intel, MITRE ATT&CK corpus, and episodic memory of past incidents."

### 5.1 — Insider threat: after-hours bulk download (HIGH — memory cross-correlation)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-enrich-insider-001",
  "timestamp": "2026-06-15T02:45:00Z",
  "rule": { "id": "60501", "level": 10, "description": "Bulk file download outside business hours", "groups": ["indicator", "malware"] },
  "agent": { "id": "025", "name": "sharepoint-gw-01", "ip": "10.0.5.25" },
  "data": {
    "user": "t.bradley", "action": "bulk_download",
    "files_downloaded": "3200", "total_bytes": "12884901888",
    "time": "02:45", "business_hours": "false",
    "destination": "personal-nas.home (DHCP, outside corp)"
  },
  "full_log": "t.bradley downloaded 3200 files (12 GB) to personal NAS at 02:45 — 3rd after-hours bulk download this month, previous two unresolved"
}' | jq '{incident_id, status}'
```

**Expected:** HIGH + `indicator` → enrichment queries `MemoryStore.search_similar` → finds prior two after-hours incidents for same user → enrichment LLM flags correlated pattern → `enrich_tag_and_ticket` → `remediated`.

**Dashboard:** Trace inspector will show the memory retrieval hit with `relevance` score.

---

### 5.2 — Supply chain: malicious pip package with network callback (HIGH — MITRE corpus hit)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-enrich-supply-chain-001",
  "timestamp": "2026-06-15T11:45:00Z",
  "rule": { "id": "92500", "level": 8, "description": "Suspicious package installation with outbound connection", "groups": ["indicator", "execution"] },
  "agent": { "id": "033", "name": "ci-runner-04", "ip": "10.0.6.33" },
  "data": {
    "process": "pip", "user": "ci-runner",
    "package": "requestss==2.28.1",
    "install_source": "pypi.org",
    "post_install_connection": "http://malicious-cdn.io/init.js",
    "parent": "github-actions-runner"
  },
  "full_log": "ci-runner-04 installed requestss (typosquat of requests) via pip; package immediately called malicious-cdn.io — supply chain injection via CI pipeline"
}' | jq '{incident_id, status}'
```

**Expected:** HIGH + `indicator` → enrichment hits MITRE corpus (T1195.002 Compromise Software Supply Chain) → `enrich_tag_and_ticket` → `remediated`.

**Dashboard:** Trace shows corpus retrieval with MITRE technique match and confidence score.

---

### 5.3 — Web shell upload on public-facing server (HIGH — threat intel + corpus)

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-enrich-webshell-001",
  "timestamp": "2026-06-15T12:15:00Z",
  "rule": { "id": "31302", "level": 11, "description": "Web shell file detected on web server", "groups": ["indicator", "malware", "web"] },
  "agent": { "id": "010", "name": "nginx-prod-01", "ip": "10.0.1.10" },
  "data": {
    "srcip": "195.123.246.101", "method": "PUT",
    "url": "/uploads/support/cache.php",
    "user_agent": "python-requests/2.28.0",
    "file_hash": "d41d8cd98f00b204e9800998ecf8427e",
    "file_content_preview": "<?php system($_GET['\''cmd'\'']); ?>",
    "upload_user": "anonymous"
  },
  "full_log": "nginx-prod-01: anonymous PUT /uploads/support/cache.php from 195.123.246.101 — PHP webshell pattern, matches China Chopper variant, HAFNIUM IOC"
}' | jq '{incident_id, status}'
```

**Expected:** HIGH + `indicator` + `malware` → enrichment: intel lookup on `195.123.246.101` + MITRE corpus hits T1505.003 (Web Shell) → `enrich_tag_and_ticket` → `remediated`.

**Dashboard:** Trace shows dual retrieval — intel + corpus — in a single enrichment LLM call.

---

## SSE Live Feed Finale

> Send one more incident **while the dashboard is open** to demonstrate real-time push without a page refresh.

```bash
curl -s -X POST "$INGEST" -H "$H_AUTH" -H "$H_CT" -d '{
  "id": "demo-live-sse-001",
  "timestamp": "2026-06-15T16:00:00Z",
  "rule": { "id": "5712", "level": 6, "description": "SSH brute force resumed from new IP", "groups": ["authentication_failures", "brute_force"] },
  "agent": { "id": "002", "name": "bastion-01", "ip": "10.0.1.2" },
  "data": { "srcip": "185.220.101.48", "dstport": "22", "failed_count": "25" },
  "full_log": "Resumed brute force from new Tor exit node 185.220.101.48 targeting bastion-01"
}' | jq '{incident_id}'
```

Watch it appear in the incident queue live — no refresh needed.

---

## Dashboard Showcase Sequence

After all incidents are processed, walk through the UI in this order:

1. **Incident Queue (`/`)** — filter by `awaiting_approval` to show HITL backlog; filter by `remediated` to show auto-handled volume; filter by `resolved` to show noise correctly dismissed.
2. **KPI Cards** — memory hit rate (visible after Act 5.1), volume-over-time chart, triage real/noise breakdown.
3. **Incident Detail — lateral movement case** — show the full audit trail (ingested → triaging → enriching → responding → awaiting_approval → approved → remediated) and trace inspector with per-LLM-call token counts and latency.
4. **Approval Panel — impossible travel case (rejected)** — show rejection rationale, immutable audit log entry with operator identity and timestamp.
5. **SSE live feed** — fire the finale incident above while the queue is visible.

---

## Stakeholder Talking Points

| Audience | Best incidents | Key message |
|---|---|---|
| **CISO** | Acts 1 + 3 | "No destructive action executes without a human signature. Full immutable audit trail." |
| **SOC Lead** | Acts 2 + 5 | "~70% of medium/high incidents handled automatically in under 30 seconds." |
| **Engineering** | Act 5 + trace inspector | "Every LLM call is traced, timed, token-counted, and PII-redacted before storage." |
| **Compliance** | Act 3 (reject flow) | "Every decision — approve or reject — is an immutable audit record with rationale and operator identity." |
| **Business** | Full KPI dashboard | "MTTD and MTTR are live metrics, not a post-hoc spreadsheet." |
