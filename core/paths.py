"""Filesystem layout and path-safety enforcement for the agent.

PathGuard is the single source of truth for what the agent may read and
write. Every tool that touches the filesystem must go through it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("agent.paths")

# Subdirectories that must exist for a directory to be recognized as an
# agent root during auto-discovery (source tree).
REQUIRED_SUBDIRS = ("brain", "core", "db", "skills", "tools")
# Frozen portable exe: core/ lives inside the bundle, not next to Kurama.exe.
RUNTIME_SUBDIRS = ("brain", "db", "skills", "tools")


@dataclass(frozen=True)
class AgentPaths:
    root: Path
    # Optional override: if set, PathGuard and load_config read this file
    # instead of brain/reasoning_config.json. Set via --config in main.py.
    config_path: Path | None = None

    # --- derived paths (properties so they're always in sync with root) ---

    @property
    def brain(self) -> Path:
        return self.root / "brain"

    @property
    def core(self) -> Path:
        return self.root / "core"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def db(self) -> Path:
        return self.root / "db"

    @property
    def tests(self) -> Path:
        return self.root / "tests"

    @property
    def effective_config_path(self) -> Path:
        """The config file that should be read — override if set, else default."""
        return self.config_path or (self.brain / "reasoning_config.json")

    @classmethod
    def discover(cls, start: Path | None = None, config_path: Path | None = None) -> "AgentPaths":
        """Find the agent root and return an AgentPaths for it.

        Resolution order:
        1. AGENT_ROOT environment variable (explicit override, useful in CI/containers)
        2. If frozen (PyInstaller), the directory containing the executable when it
           looks like a portable runtime (brain/db/skills/tools on disk).
        3. Walk upward from `start` (defaults to this file's location) until a
           directory is found that contains all REQUIRED_SUBDIRS.
        4. Walk upward from cwd looking for a portable runtime root.
        5. Raise FileNotFoundError if nothing matches.
        """
        env_root = os.environ.get("AGENT_ROOT")
        if env_root:
            root = Path(env_root).resolve()
            log.debug("Using AGENT_ROOT=%s", root)
            return cls(root=root, config_path=config_path)
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            if cls._is_runtime_root(exe_dir):
                log.debug("Using frozen exe dir as agent root: %s", exe_dir)
                return cls(root=exe_dir, config_path=config_path)
        try:
            root = cls._find_root(start or Path(__file__).resolve())
        except FileNotFoundError:
            cwd = Path.cwd().resolve()
            if cls._is_runtime_root(cwd):
                root = cwd
            else:
                raise
        return cls(root=root, config_path=config_path)

    @staticmethod
    def _is_runtime_root(candidate: Path) -> bool:
        return candidate.is_dir() and all((candidate / s).is_dir() for s in RUNTIME_SUBDIRS)

    @staticmethod
    def _find_root(start: Path) -> Path:
        for candidate in [start, *start.parents]:
            if candidate.is_dir() and all((candidate / s).is_dir() for s in REQUIRED_SUBDIRS):
                return candidate
        raise FileNotFoundError(
            f"Could not locate agent root above {start} "
            f"(looked for {', '.join(REQUIRED_SUBDIRS)}/). "
            "Set AGENT_ROOT to specify it explicitly."
        )


class PathGuard:
    """Enforces the constitution's read/write boundaries on every file operation.

    - Anything within root is readable (constitution: protected paths are
      'read-only to the agent', meaning writable access is denied, not all
      access).
    - Writes are allowed only in writable_prefixes (workspace/, skills/drafts/).
    - Writes are denied in protected_prefixes even if they are also listed
      in writable_prefixes (protected always wins — constitution rule 12).

    Config is loaded once at construction and cached; do not construct a new
    PathGuard per tool call — hold one instance and reuse it, or pass it in.
    """

    def __init__(self, paths: AgentPaths, cfg: dict[str, Any] | None = None):
        self.paths = paths
        if cfg is None:
            try:
                cfg = json.loads(paths.effective_config_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"PathGuard could not load reasoning config from "
                    f"{paths.effective_config_path}: {exc}"
                ) from exc
        self.protected: tuple[str, ...] = tuple(cfg.get("protected_prefixes") or [])
        self.writable: tuple[str, ...] = tuple(cfg.get("writable_prefixes") or [])

    def _resolve(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"path must be a non-empty string, got {raw!r}")
        p = Path(raw)
        target = (p if p.is_absolute() else (self.paths.root / p)).resolve()
        root = self.paths.root.resolve()
        if target != root and root not in target.parents:
            raise PermissionError(f"path escapes agent root: {raw!r}")
        return target

    def resolve_readable(self, raw: str) -> Path:
        """Resolve a path for reading. Raises PermissionError or FileNotFoundError."""
        target = self._resolve(raw)
        if not target.exists():
            raise FileNotFoundError(f"no such path: {raw!r}")
        return target

    def resolve_writable(self, raw: str) -> Path:
        """Resolve a path for writing. Protected paths are always denied;
        only writable_prefixes are allowed. Raises PermissionError."""
        target = self._resolve(raw)
        rel = target.relative_to(self.paths.root).as_posix()

        # Protected check first — overrides writable (constitution rule 12)
        if any(rel.startswith(p) for p in self.protected):
            log.warning("Write rejected: protected path %r", rel)
            raise PermissionError(f"protected path (read-only): {rel!r}")

        if not any(rel.startswith(p) for p in self.writable):
            log.warning("Write rejected: not in writable prefixes %r", rel)
            raise PermissionError(
                f"path {rel!r} is not in a writable prefix; "
                f"allowed: {self.writable}"
            )

        return target