"""Spawn gate for llama-server. Validates before any process is created.

Independent of the agent loop and of config.py — no layer trusts another.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from core.boundary import contains_nul, reject
from core.contracts import PORT_MAX, PORT_MIN

log = logging.getLogger("agent.launcher")

_ALIAS_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def parse_port(raw: Any) -> tuple[int | None, str | None]:
    """Allowlist: integer in [PORT_MIN, PORT_MAX]. Strings that are not digits fail."""
    if isinstance(raw, bool):
        return None, "invalid_port"
    if isinstance(raw, int):
        port = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        port = int(raw.strip())
    else:
        log.error("REJECT launcher/invalid_port value=%r", raw)
        return None, "invalid_port"
    if port < PORT_MIN or port > PORT_MAX:
        log.error("REJECT launcher/invalid_port value=%r", raw)
        return None, "invalid_port"
    return port, None


def allowed_gguf(root: Path, model_path: str | Path) -> tuple[Path | None, str | None]:
    """Allowlist: existing *.gguf whose resolved path is under <root>/models/."""
    raw = str(model_path or "")
    if not raw or contains_nul(raw):
        log.error("REJECT launcher/invalid_model_path empty_or_nul")
        return None, "invalid_model_path"
    models = (root / "models").resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        log.error("REJECT launcher/invalid_model_path unresolvable")
        return None, "invalid_model_path"
    if resolved.suffix.lower() != ".gguf":
        log.error("REJECT launcher/not_a_gguf path=%s", resolved)
        return None, "not_a_gguf"
    try:
        resolved.relative_to(models)
    except ValueError:
        log.error("REJECT launcher/model_not_under_models path=%s", resolved)
        return None, "model_not_under_models"
    if not resolved.is_file():
        log.error("REJECT launcher/model_not_found path=%s", resolved)
        return None, "model_not_found"
    return resolved, None


def validate_launch(
    *,
    root: Path,
    model_path: str | Path,
    port: Any,
    alias: str = "pocket",
    host: str = "127.0.0.1",
) -> dict[str, Any]:
    """Refuse hostile spawn parameters. Success payload is the allowlisted values."""
    if contains_nul(host) or contains_nul(alias):
        return reject("invalid_model_path", layer="launcher", detail="nul in host/alias")
    if not _ALIAS_RE.match(str(alias or "")):
        return reject("invalid_alias", layer="launcher", detail=repr(alias))
    port_i, port_err = parse_port(port)
    if port_err:
        return reject(port_err, layer="launcher")
    gguf, path_err = allowed_gguf(root, model_path)
    if path_err:
        return reject(path_err, layer="launcher")
    return {
        "ok": True,
        "model": gguf,
        "port": port_i,
        "alias": str(alias),
        "host": str(host),
    }
