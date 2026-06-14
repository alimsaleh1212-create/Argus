"""Unit tests — T031: SC-006 no-LLM guard and layering contract.

Verifies that:
- backend/services/supervisor.py imports no LLM client (SC-006)
- The inward-only layering contract holds: services → agents → repositories → infra,
  domain isolated (no outward imports from domain/pipeline.py)
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).parent.parent.parent / "backend"


def _get_imports(filepath: Path) -> list[str]:
    """Return all top-level module names imported by a file."""
    source = filepath.read_text()
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def test_supervisor_does_not_import_llm_client():
    """services/supervisor.py must not import any LLM client module (SC-006)."""
    supervisor_path = BACKEND_ROOT / "services" / "supervisor.py"
    imports = _get_imports(supervisor_path)

    llm_modules = {"backend.infra.llm", "backend.infra.llm_drivers"}
    bad = [i for i in imports if any(i.startswith(m) for m in llm_modules)]
    assert bad == [], f"supervisor.py must not import LLM modules; found: {bad}"


def test_supervisor_does_not_import_google_genai():
    """No vendor AI SDK in the orchestration layer."""
    supervisor_path = BACKEND_ROOT / "services" / "supervisor.py"
    imports = _get_imports(supervisor_path)
    vendor_prefixes = ("google", "openai", "anthropic", "langchain", "langgraph", "gemini")
    bad = [i for i in imports if any(i.startswith(p) for p in vendor_prefixes)]
    assert bad == [], f"supervisor.py must not import vendor AI SDKs; found: {bad}"


def test_domain_pipeline_has_no_outward_imports():
    """domain/pipeline.py must not import from services, repositories, or infra."""
    pipeline_path = BACKEND_ROOT / "domain" / "pipeline.py"
    imports = _get_imports(pipeline_path)
    outward_prefixes = (
        "backend.services",
        "backend.repositories",
        "backend.infra",
        "backend.routers",
    )
    bad = [i for i in imports if any(i.startswith(p) for p in outward_prefixes)]
    assert bad == [], f"domain/pipeline.py must not have outward imports; found: {bad}"


def test_supervisor_does_not_import_domain_llm():
    """supervisor.py must not import backend.domain.llm (the LLM contract types)."""
    supervisor_path = BACKEND_ROOT / "services" / "supervisor.py"
    imports = _get_imports(supervisor_path)
    assert "backend.domain.llm" not in imports, "supervisor.py imported backend.domain.llm"
