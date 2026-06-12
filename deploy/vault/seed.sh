#!/bin/sh
# Seeds Vault KV v2 with dev credentials and user-provided API keys.
# Run by the one-shot `vault-seed` compose service; variables come from .env
# (all optional — dev defaults below keep the stack bootable from a fresh clone).
set -eu

vault kv put secret/minio \
  access_key=minioadmin \
  secret_key=minioadmin

# Gemini is the primary LLM provider; without a key the stack runs on the
# local Ollama fallback only ("placeholder" fails closed at the Gemini driver).
vault kv put secret/llm \
  api_key="${GEMINI_API_KEY:-placeholder}"

vault kv put secret/ingest \
  token="${INGEST_WEBHOOK_TOKEN:-dev-webhook-token}"

vault kv put secret/memory \
  username=neo4j \
  password=dev-neo4j-password \
  uri=bolt://neo4j:7687

# Dashboard admin login. Dev default: admin / argus-admin-2026
# (hash = pbkdf2_hmac('sha256', b'argus-admin-2026', b'dev-salt', 260000)).
vault kv put secret/dashboard \
  password_hash="${ARGUS_ADMIN_PASSWORD_HASH:-3eee6f8928ca5333e1408d31347a1bad8c4bca1486e1ecd3f4aaa0eaf51ea23d}" \
  salt="${ARGUS_ADMIN_SALT:-dev-salt}" \
  iterations="${ARGUS_ADMIN_ITERATIONS:-260000}" \
  jwt_secret="${ARGUS_JWT_SECRET:-dev-jwt-secret-change-in-production}"

# On-demand threat intel is optional and config-gated — seed only when provided.
if [ -n "${INTEL_API_KEY:-}" ]; then
  vault kv put secret/intel api_key="${INTEL_API_KEY}"
fi

echo "vault-seed: all secrets written"
