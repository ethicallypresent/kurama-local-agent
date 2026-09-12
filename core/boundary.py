"""Edge gate. Hostile input is refused, never rewritten.

Every rejection logs at ERROR. Callers must stop — they must not continue
with a cleaned payload.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("agent.edge")


def contains_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, bytes):
        return b"\x00" in value
    if isinstance(value, dict):
        return any(contains_nul(k) or contains_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_nul(item) for item in value)
    return False


def encoded_size(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return -1


def reject(code: str, *, detail: str = "", layer: str = "edge") -> dict[str, Any]:
    """Fail closed. Log loud. Return a standard error dict — do not execute."""
    extra = f" {detail}" if detail else ""
    log.error("REJECT %s/%s%s", layer, code, extra)
    return {
        "ok": False,
        "error": code,
        "from": layer,
        "observed": {},
    }
