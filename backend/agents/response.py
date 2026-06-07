"""Response agent — reserved.

RESERVED. Final stage: propose/execute remediation. Action tools are gated
through the provider seam and a human-in-the-loop approval interrupt before any
state-changing action runs. Executed in the worker, never the request path.
Implemented by SPEC-response.
"""

from __future__ import annotations
