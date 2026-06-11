# Feature Specification: Platform & Infrastructure Foundation

**Feature Branch**: `001-platform-infra`

**Created**: 2026-06-06

**Status**: Draft

**Input**: User description: "depending on @docs/resources/SOAR_brief.md and @docs/resources/SOAR_Plan.md" — Component #1 of the Argus build plan: `SPEC-platform-infra` (the only Tier-1 component that depends on nothing). Scope: the local orchestration stack, secret resolution, object storage, typed configuration, the layered application skeleton, shared-resource lifecycle, schema migrations, and code-hygiene gates that every later component builds on.

## Overview

Argus is an AI-driven SOAR platform built spec-by-spec in dependency order. This foundation component delivers the **runnable, fail-fast baseline** that all later components (ingestion, memory, the agent pipeline, dashboard, evals) plug into. When this component is done, a person who has never seen the project can clone it and bring the whole local environment to a healthy state with one command, the application refuses to start in a misconfigured or secret-less state, shared connections are owned in one place, the relational schema is versioned, an object store exists for reports and snapshots, and basic code/secret hygiene is enforced before anything is committed. It contains **no business logic** — it is the spine that keeps the system in a valid, demonstrable state from day one.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-command bring-up from a fresh clone (Priority: P1)

A developer, mentor, or evaluator clones the repository for the first time, performs a single documented environment-file step, and runs one command. The complete local environment — the application plus every backing service it depends on — starts and reaches a healthy state with no further manual intervention.

**Why this priority**: This is the headline deliverable and the project's standing promise (a clean bring-up from a fresh clone is the acceptance bar for the whole capstone). Nothing else in the system can be demonstrated, tested end-to-end, or evaluated until the stack reliably comes up. It is the minimum viable slice on its own: a reviewer can verify it without any agent, memory, or dashboard existing yet.

**Independent Test**: From a clean checkout on a machine that has only the documented prerequisites, follow the documented setup step and run the single bring-up command; confirm the application and all backing services report healthy, and that an automated smoke check confirms the same in CI.

**Acceptance Scenarios**:

1. **Given** a fresh clone and the documented environment file in place, **When** the operator runs the single bring-up command, **Then** the application and all backing services start and each reports a healthy status within the documented time budget.
2. **Given** the stack is healthy, **When** the automated smoke check runs (locally or in CI), **Then** it passes and reports the stack reachable and healthy.
3. **Given** the stack is running, **When** the operator issues the documented shutdown command, **Then** all services stop cleanly with no orphaned processes or leaked resources.

---

### User Story 2 - Fail-fast configuration and secret resolution (Priority: P1)

When the application starts, it loads a single, validated configuration object and resolves every required secret from the secret store. If any required value is missing, malformed, an unknown/unexpected key is present, or the secret store is unreachable, the application refuses to start and surfaces a clear error that names the offending item — rather than booting into a half-working state.

**Why this priority**: A SOAR takes consequential actions; a silently misconfigured instance is a safety and trust hazard. Failing fast and loudly at startup is a core engineering standard of the project and protects every downstream component from inheriting an invalid environment. It is independently valuable and testable even before any feature logic exists.

**Independent Test**: Start the application with (a) a complete valid configuration, (b) a missing required secret, (c) an unknown configuration key, and (d) an unreachable secret store; confirm (a) starts healthy and (b)–(d) each refuse to start with a clear, specific, secret-free error message.

**Acceptance Scenarios**:

1. **Given** a complete and valid configuration with the secret store reachable, **When** the application starts, **Then** it boots successfully and reports its configuration as validated.
2. **Given** a required secret is missing or the secret store is unreachable, **When** the application starts, **Then** it refuses to boot and emits an error identifying the missing/unreachable item.
3. **Given** an unknown or extra configuration key is supplied, **When** the application starts, **Then** it refuses to boot and identifies the unexpected key.
4. **Given** any startup failure, **When** the error is emitted, **Then** the message names the offending configuration key or secret **without** revealing any secret value.

---

### User Story 3 - Shared-resource lifecycle and injectable dependencies (Priority: P2)

Long-lived shared resources — the relational database engine/session factory, the object-store client, the secret-store client, and other singletons — are created exactly once when the application starts and disposed cleanly when it shuts down. Every component obtains these resources through one consistent dependency-injection mechanism rather than reaching for module-level globals, so they can be swapped for test doubles.

**Why this priority**: Single ownership of shared connections prevents resource leaks and duplicated clients, and the injection seam is what makes the later three-tier test discipline (mocking the LLM, swapping backends) possible. It is foundational but only meaningful once bring-up (P1) works, hence P2.

**Independent Test**: Start and stop the application repeatedly and confirm shared resources are created once and released on shutdown with no leaked connections/handles; in a test context, confirm a component can receive a substituted (mock) dependency through the injection mechanism.

**Acceptance Scenarios**:

1. **Given** the application starts, **When** initialization completes, **Then** each shared resource has been constructed exactly once and is retrievable through the injection mechanism.
2. **Given** the application shuts down, **When** the lifecycle completes, **Then** every shared resource is disposed and no connections or handles remain open.
3. **Given** a test harness, **When** a component requests a shared dependency, **Then** a test double can be injected in place of the real resource without changing the component's code.

---

### User Story 4 - Versioned relational store and object storage (Priority: P2)

The relational database schema is created and evolved through versioned migrations that can be applied to an empty database to produce the current schema (and rolled back), and an object store is available for storing reports and per-incident context snapshots that later components will write.

**Why this priority**: Reproducible schema and a place to store artifacts are prerequisites for ingestion, memory, audit, and eval-report storage. They must exist before those components can persist anything, but they are not needed to merely bring the stack up, hence P2.

**Independent Test**: Apply migrations to an empty database and confirm the resulting schema matches the current definition with no drift; roll back and confirm the database returns to empty; write and read back an object to/from the object store.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** migrations are applied, **Then** the schema reaches the current version with no pending/undetected drift.
2. **Given** a migrated database, **When** the latest migration is rolled back, **Then** the schema returns to its prior version cleanly.
3. **Given** the object store is running, **When** a component stores and then retrieves an object, **Then** the retrieved content matches what was stored.

---

### User Story 5 - Code and secret hygiene gates (Priority: P3)

Before any change is committed, automated checks run linting, formatting, and secret-scanning, and block the commit if any check fails. Project dependencies are pinned/locked so a fresh install reproduces identical versions.

**Why this priority**: These gates protect quality and prevent credential leaks throughout the rest of the build, but the system is demonstrable without them, so they are P3. They are still part of this foundation because every later commit should pass through them.

**Independent Test**: Attempt to commit code that fails formatting/linting and, separately, a file containing a fake secret; confirm each commit is blocked. Perform a fresh dependency install and confirm the resolved versions match the locked versions exactly.

**Acceptance Scenarios**:

1. **Given** a change that violates formatting or lint rules, **When** the contributor attempts to commit, **Then** the commit is blocked with a message identifying the failure.
2. **Given** a change that contains a credential-like secret, **When** the contributor attempts to commit, **Then** the secret-scan blocks the commit.
3. **Given** the dependency lock, **When** dependencies are installed from a clean state, **Then** the installed versions match the locked versions exactly.

---

### Edge Cases

- **Secret store unreachable at startup**: the application refuses to boot and reports the secret store as unreachable; it never starts in a degraded "no secrets" mode.
- **Backing service slow to become ready**: during bring-up the application reports unhealthy (not failed) until its required dependencies are reachable, then transitions to healthy; it does not report healthy prematurely.
- **Missing environment file on a fresh clone**: startup fails fast with an error that names the required environment values and points to the documented setup step.
- **Host port already in use**: bring-up fails with a clear, attributable error rather than a silent partial start.
- **Shutdown mid-operation**: shared resources are disposed gracefully without leaking connections or corrupting in-flight state ownership.
- **Schema drift between code and a running database**: the mismatch is detectable (e.g., pending-migration / drift check) rather than silently ignored.
- **Partial bring-up (some services up, one failing)**: the overall stack health reflects the failure; the application does not advertise itself as fully healthy while a required dependency is down.

## Requirements *(mandatory)*

### Functional Requirements

**Configuration & secrets**

- **FR-001**: The system MUST load all runtime configuration from a single, typed, validated configuration object resolved at startup.
- **FR-002**: The system MUST reject unknown or extra configuration keys at startup and refuse to boot when any are present.
- **FR-003**: The system MUST resolve all required secrets from a centralized secret store at startup, and MUST refuse to start if the secret store is unreachable or any required secret is missing or malformed.
- **FR-004**: On any startup validation failure, the system MUST emit a clear, actionable error that identifies the specific offending configuration key or secret.
- **FR-005**: Startup error output MUST NOT contain raw secret values.
- **FR-006**: The system MUST distinguish required from optional configuration/secrets, treating only missing **required** items as fatal.

**Bring-up & health**

- **FR-007**: The system MUST provide a single command that brings up the complete local stack (application plus all backing services it owns) from a fresh clone, requiring no manual steps beyond a single documented environment-file setup.
- **FR-008**: Each backing service MUST expose a health signal, and the application MUST report itself healthy only when all of its required dependencies are reachable.
- **FR-009**: The system MUST provide an automated smoke check, runnable in CI, that verifies the stack reaches a healthy state from a clean checkout.
- **FR-010**: The system MUST provide a documented, clean shutdown path that stops all services without leaving orphaned processes or leaked resources.

**Shared-resource lifecycle & injection**

- **FR-011**: The system MUST initialize each long-lived shared resource (relational engine/session factory, object-store client, secret-store client, and other singletons) exactly once at startup and dispose each cleanly at shutdown.
- **FR-012**: Components MUST obtain shared resources through one consistent dependency-injection mechanism rather than module-level globals.
- **FR-013**: The dependency-injection mechanism MUST allow shared resources to be substituted with test doubles without modifying the consuming component.
- **FR-014**: The lifecycle mechanism MUST provide a registration seam so that later components can attach their own startup-initialized singletons (e.g., additional clients) without changing the foundation.

**Persistence foundations**

- **FR-015**: The relational store schema MUST be created and evolved exclusively through versioned migrations.
- **FR-016**: Applying all migrations to an empty database MUST reproduce the current schema, and the most recent migration MUST be reversible.
- **FR-017**: The system MUST make an object store available for storing reports and per-incident context snapshots, accessible to application components through the injection mechanism.

**Application structure**

- **FR-018**: The codebase MUST be organized into clearly separated architectural layers (such as interface/API, services, agents, repositories, domain, and infrastructure) with a documented, enforced dependency direction.
- **FR-019**: The foundation MUST contain no incident/business logic; it provides only orchestration, configuration, lifecycle, persistence scaffolding, and structure for later components.

**Hygiene & reproducibility**

- **FR-020**: Pre-commit checks MUST run linting, formatting, and secret-scanning, and MUST block any commit that fails any of them.
- **FR-021**: Project dependencies MUST be pinned/locked so that a fresh install reproduces identical versions.

### Key Entities *(include if feature involves data)*

- **Configuration object**: the single validated representation of all runtime settings; attributes include the typed settings, which are required vs optional, and the rejection of unknown keys.
- **Secret**: a sensitive credential resolved from the secret store at startup; classified as required or optional; never emitted in logs or error output.
- **Shared resource / singleton**: a long-lived client or connection (relational engine/session factory, object-store client, secret-store client, and later additions) created once and disposed once, obtained via injection.
- **Backing service**: a containerized dependency in the local stack that exposes a health signal (the foundation owns the orchestration scaffold plus the secret store, object store, and relational store; other components attach their own services to the same scaffold).
- **Schema migration**: a versioned, ordered, reversible change to the relational schema; the set of applied migrations defines the current schema version.
- **Health status**: the aggregate readiness of the application and its required dependencies, used by bring-up and the smoke check.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A person with no prior exposure to the project can bring the full local stack to a healthy state from a fresh clone using one documented setup step and one command, in under 10 minutes on a typical developer machine.
- **SC-002**: For every defined startup-failure case (missing required secret, unreachable secret store, unknown configuration key, malformed required value), the application refuses to start 100% of the time and emits an error naming the offending item.
- **SC-003**: No startup failure ever leaves a running but misconfigured application process (0% partial/"half-up" boots across the defined failure cases).
- **SC-004**: The automated smoke check passes (green) on a fresh checkout in CI on every commit to the foundation.
- **SC-005**: After shutdown, 100% of shared connections/handles are released, with zero leaked resources observed across repeated start/stop cycles.
- **SC-006**: Applying migrations to an empty database reproduces the current schema with zero detected drift, and the latest migration rolls back cleanly.
- **SC-007**: 100% of seeded fake secrets are caught and blocked by the pre-commit secret-scan before they can be committed.
- **SC-008**: A fresh dependency install reproduces the locked versions exactly (0 version differences).
- **SC-009**: No startup or runtime error message produced by the foundation contains a raw secret value (verified against the defined failure cases).

## Assumptions

- **Single organization / single tenant**: a SOC monitors one organization; no multi-tenancy or tenant isolation is in scope for this foundation (consistent with the project's scope discipline).
- **Local/demo deployment target**: the stack runs locally on a single host for development and demo; no cloud, cluster, or multi-host orchestration is in scope for v1.
- **Mandated stack (pre-decided project constraints, recorded here rather than as requirements)**: the project brief fixes the implementing technologies — containerized orchestration via docker-compose; secret management via Vault (boot refused if unreachable); object/blob storage via MinIO; relational schema migrations via Alembic; Python environment and dependency management via `uv`; linting/formatting via `ruff` and secret-scanning via `gitleaks`, enforced through pre-commit; and asynchronous I/O plus dependency injection as cross-cutting engineering standards. These are honored by the implementation but the requirements above are stated as capability outcomes so they remain verifiable independently of any specific tool.
- **Observability is a separate component**: structured logging, tracing, and redaction are specified in `SPEC-observability` and consume the layered skeleton and lifecycle seams this foundation provides; this spec only guarantees that startup errors do not leak secret values and that a registration seam exists for observability to attach to.
- **Backing-service ownership / seam**: this foundation owns the orchestration scaffold plus the secret store, object store, relational store, configuration, application skeleton, and migration tooling. Other components (e.g. the message queue/cache, the temporal graph store, the vector store, the guardrails sidecar) attach their own service definitions and singletons to the same scaffold via the registration seam defined here, so adding them requires no rework of the foundation.
- **Three-tier test discipline applies**: the dependency-injection seam exists specifically to enable the project's unit/integration/e2e test tiers (mocking shared resources in unit tests, exercising real services in integration tests); the foundation ends with all three test levels wired against a healthy empty pipeline.
- **Documented prerequisites**: the operator's machine has the documented baseline tooling (a container runtime and the project's environment manager) installed before bring-up; provisioning that baseline tooling is out of scope.
- **Default time/quality targets**: where the brief did not fix a number, reasonable defaults are used (e.g. the ≤10-minute fresh-clone bring-up target in SC-001) and can be tightened during planning without changing the requirements.

## Dependencies

- **None.** This is the root component of the build plan (`SPEC-platform-infra` depends on nothing). Every other Argus component depends, directly or transitively, on this foundation.
