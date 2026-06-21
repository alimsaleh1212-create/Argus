# Argus — documentation index

Start with the project [README](../README.md) for the overview and quick start. The documents below go
deeper into specific subsystems. All are grounded in the current code.

## Pipeline & architecture

- **[pipeline-end-to-end.md](pipeline-end-to-end.md)** — how an alert moves from detection through
  intake, the supervisor stages (triage, enrichment, response), verification, and the memory feedback
  loop. File references point at the collaborators implementing each step.
- **[incident-workflow.png](incident-workflow.png)** — the incident state-machine diagram
  (source: [incident-workflow.mmd](incident-workflow.mmd)).
- **[seeding-architecture.md](seeding-architecture.md)** — how the reference corpus and dev secrets
  are seeded at bring-up.

## Detection layer

- **[siem-ml-detector.md](siem-ml-detector.md)** — the ML anomaly detection layer (UEBA-style,
  Isolation Forest): training, features, scoring, and how findings enter the ingestion contract.
- **[anomal-detector-mechanism.md](anomal-detector-mechanism.md)** — the detector mechanism in
  detail.

## Operating Argus

- **[demo-playbook.md](demo-playbook.md)** — guided end-to-end scenarios for exercising the platform.

## Design artifacts

- **[superpowers/specs/](superpowers/specs/)** — brainstormed design specs (SOC pipeline map,
  incident journey & escalation actions).
- **[superpowers/plans/](superpowers/plans/)** — the implementation plans those specs drove.
- **[../specs/](../specs/)** — the numbered per-component specifications, plans, and decision logs
  (components #1–#17).
- **[../DECISIONS.md](../DECISIONS.md)** — the consolidated decision log.
