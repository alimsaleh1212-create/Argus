"""Worker entrypoint — reserved.

RESERVED. Same image as the API (one image, two containers); run as
``python -m backend.worker``. Activated in SPEC-ingestion (#4) once Redis +
the task queue exist — until then the worker has nothing to consume and ships
commented-out in compose.

Responsibility (later): consume accepted Wazuh alerts from the queue and run the
triage → enrichment → response graph, pausing at the human-in-the-loop approval
interrupt (state persisted via the supervisor's checkpointer, #5).
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError(
        "Worker is reserved; activated in SPEC-ingestion (#4) when the queue lands."
    )


if __name__ == "__main__":
    main()
