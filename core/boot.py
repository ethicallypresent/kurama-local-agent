"""INITIATION: known state before any run.

Order: paths → directories → config → brain identity → idle maintenance.
No loop step runs until this returns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.config import ConfigError, load as load_config
from core.paths import AgentPaths

log = logging.getLogger("agent.boot")

_RUNTIME_DIRS = ("db", "workspace", "skills/drafts", "skills/approved", "skills/archived")


@dataclass(frozen=True)
class BootState:
    paths: AgentPaths
    config: dict[str, Any]
    issues: tuple[str, ...]
    ok: bool


def _ensure_dirs(paths: AgentPaths) -> list[str]:
    issues: list[str] = []
    for rel in _RUNTIME_DIRS:
        folder = paths.root / rel
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            issues.append(f"mkdir:{rel}:{exc}")
    return issues


def initiate(paths: AgentPaths | None = None) -> BootState:
    """Validate dependencies and load config. Does not start llama-server."""
    issues: list[str] = []
    try:
        paths = paths or AgentPaths.discover()
    except FileNotFoundError as exc:
        raise ConfigError(str(exc)) from exc
    issues.extend(_ensure_dirs(paths))
    try:
        from core.resources import apply_resource_allocation

        config = apply_resource_allocation(load_config(paths))
    except ConfigError as exc:
        issues.append(f"config:{exc}")
        config = {"llm": {}, "boot_failed": True}
    brain = paths.brain
    for name in ("system_prompt.md", "constitution.md"):
        if not (brain / name).is_file():
            issues.append(f"missing:{name}")
    ok = not any(i.startswith("config:") or i.startswith("missing:") for i in issues)
    if issues:
        log.warning("boot issues: %s", issues)
    return BootState(paths=paths, config=config, issues=tuple(issues), ok=ok)
