<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `001-platform-infra` (Component #1 — Platform & Infrastructure Foundation)
- Plan: `specs/001-platform-infra/plan.md`
- Spec: `specs/001-platform-infra/spec.md`
- Design: `specs/001-platform-infra/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): Python 3.12 (`uv`), FastAPI + uvicorn, `pydantic-settings`
(`extra="forbid"`, `SecretStr`), async SQLAlchemy 2.0 + asyncpg + Alembic, HashiCorp Vault
(async via `httpx`), MinIO (`aioboto3`), Docker Compose v2; `ruff` + `gitleaks` + `pre-commit`;
`pytest` + `testcontainers`. Layered `app/` (api/services/agents/repositories/domain/infra) with
inward-only imports; lifespan singletons via the provider seam in `app/infra/container.py`.
<!-- SPECKIT END -->
