"""Protected file tools. Agent code must not edit this module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.paths import AgentPaths, PathGuard


def read_file(paths: AgentPaths, path: str, max_chars: int = 20000) -> dict[str, Any]:
    target = PathGuard(paths).resolve_readable(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {
        "ok": True,
        "path": str(target.relative_to(paths.root)),
        "content": text[:max_chars],
        "truncated": truncated,
        "bytes": target.stat().st_size,
    }


def write_file(paths: AgentPaths, path: str, content: str) -> dict[str, Any]:
    target = PathGuard(paths).resolve_writable(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(target.relative_to(paths.root)), "bytes": len(content.encode())}


def list_dir(paths: AgentPaths, path: str, glob: str = "*") -> dict[str, Any]:
    target = PathGuard(paths).resolve_readable(path)
    if not target.is_dir():
        return {"ok": False, "error": "not a directory"}
    entries = []
    for child in sorted(target.glob(glob)):
        rel = child.relative_to(paths.root)
        entries.append(
            {
                "path": str(rel),
                "is_dir": child.is_dir(),
                "bytes": child.stat().st_size if child.is_file() else None,
            }
        )
    return {"ok": True, "entries": entries[:200]}
