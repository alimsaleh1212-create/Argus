# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]

**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]

**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]

**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]

**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]

**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]

**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]

**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]

**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Derived from `.specify/memory/constitution.md` (v1.0.0). Confirm this plan satisfies each gate, or
record a justified, time-bound exception in `DECISIONS.md` and the Complexity Tracking table below.

- [ ] **I. Spec-Driven Delivery**: a `SPEC.md` precedes code; "done" = unit + integration + e2e
      green in CI and pushed behind a PR ≤ ~400 lines; big specs commit at each internal milestone.
- [ ] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: unit/integration/e2e planned and
      green daily; ≥80% on new code (higher on remediation + safety boundary); eval thresholds in
      `eval_thresholds.yaml` gate CI and pass on **both** LLM providers.
- [ ] **III. Structural Security Boundaries**: triage holds no action tools (enforced via DI);
      injection/jailbreak rails cover alert *and* feed text with a red-team CI gate; redaction runs
      before every log/trace/memory/dashboard write.
- [ ] **IV. Determinism First**: supervisor is a deterministic state machine; obvious cases take the
      no-LLM fast-path; a hard step/token cap is enforced; agents reason only over supplied evidence
      and emit an evidence-cited rationale.
- [ ] **V. Human-in-the-Loop**: destructive actions raise an approval interrupt (`awaiting_approval`)
      with a config-backed auto/approval policy, a defined approval timeout + terminal state, and an
      audit row per executed action.
- [ ] **VI. Temporal Memory & Graceful Degradation**: time-validity preserved (invalidate, not
      delete); seeded corpus closes cold-start; Graphiti → pgvector `valid_from`/`valid_to` fallback
      decided; feed/knowledge text passes the same guardrails as alert text.
- [ ] **VII. Production Engineering Standards**: async I/O, DI, lifespan singletons, Pydantic at
      every boundary, structured logging with trace IDs, observability off the synchronous path,
      typed `pydantic-settings` (`extra="forbid"`, secrets fail at startup), `uv` for deps.
- [ ] **Scope & Tiers**: stays within v1 scope (no ML detector / multi-tenancy / embeddable widget /
      live capture / LLM supervisor / 4th agent); respects the layering contract (T1 before any v2).

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
