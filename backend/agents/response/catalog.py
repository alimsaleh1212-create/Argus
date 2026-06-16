"""Playbook catalog types + config-backed loader (RD10)."""

from __future__ import annotations

from pathlib import Path

from backend.agents.response._log import get_logger

_logger = get_logger(__name__)


class PlaybookEntry:
    def __init__(
        self,
        id: str,
        description: str,
        criteria: dict,
        actions: list[dict],
        strength: int | None = None,
    ) -> None:
        self.id = id
        self.description = description
        self.criteria = criteria
        self.actions = actions
        self.strength = strength if strength is not None else 0


PlaybookCatalog = list[PlaybookEntry]


def load_playbook_catalog(catalog_dir: str) -> PlaybookCatalog:
    """Load the playbook catalog from the config-backed directory (RD10)."""
    catalog_path = Path(catalog_dir)
    if not catalog_path.is_absolute():
        catalog_path = Path.cwd() / catalog_dir

    entries: PlaybookCatalog = []
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        _logger.warning("playbook_catalog_yaml_missing")
        return entries

    for file in sorted(catalog_path.glob("*.yaml")):
        try:
            with file.open() as f:
                data = yaml.safe_load(f)
            for pb in data.get("playbooks", []):
                entries.append(
                    PlaybookEntry(
                        id=pb["id"],
                        description=pb.get("description", ""),
                        criteria=pb.get("criteria", {}),
                        actions=pb.get("actions", []),
                        strength=pb.get("strength"),
                    )
                )
        except Exception as exc:
            _logger.warning("playbook_catalog_load_error", file=str(file), error=str(exc))
    return entries
