"""Human permission protocol.

These string values are the cross-language contract (Python TUI today,
any other host later). Do not rename them without a migration.
"""

from __future__ import annotations

from typing import Any

ALLOW = "allow"
DENY = "deny"
ALLOW_RUN = "allow_run"

VALID = (ALLOW, DENY, ALLOW_RUN)


def normalize_decision(raw: Any) -> str:
    """Map a host response onto ALLOW / DENY / ALLOW_RUN."""
    text = str(raw or DENY).strip().lower().replace("-", "_")
    if text in ("allow_run", "always"):
        return ALLOW_RUN
    if text in ("allow", "yes", "y"):
        return ALLOW
    return DENY


def inspect_request(request: Any) -> tuple[bool, dict[str, Any] | None, str]:
    """Fail closed. Never rewrite a hostile payload into a 'safe' one.

    Returns (ok, request, error_code). On failure request is None and the
    human callback must not be invoked.
    """
    from core.boundary import contains_nul, encoded_size
    from core.contracts import MAX_TOOL_ARG_CHARS, MAX_TOOL_NAME_CHARS

    import logging

    log = logging.getLogger("agent.edge")
    if not isinstance(request, dict):
        log.error("REJECT permission/args_must_be_object")
        return False, None, "args_must_be_object"
    if contains_nul(request):
        log.error("REJECT permission/nul_byte")
        return False, None, "nul_byte"
    if "args" not in request:
        args: dict[str, Any] = {}
    else:
        args = request.get("args")
        if not isinstance(args, dict):
            log.error("REJECT permission/args_must_be_object")
            return False, None, "args_must_be_object"
    kind = request.get("kind", "use_tool")
    name = request.get("name", "")
    if not isinstance(kind, str) or not isinstance(name, str):
        log.error("REJECT permission/args_must_be_object")
        return False, None, "args_must_be_object"
    if len(name) > MAX_TOOL_NAME_CHARS:
        log.error("REJECT permission/unregistered_tool")
        return False, None, "unregistered_tool"
    size = encoded_size(request)
    if size < 0 or size > MAX_TOOL_ARG_CHARS:
        log.error("REJECT permission/args_too_large")
        return False, None, "args_too_large"
    return True, request, ""


def validate_request(request: Any) -> dict[str, Any]:
    """Back-compat wrapper. Prefer inspect_request — this still fail-closes."""
    ok, cleaned, _err = inspect_request(request)
    if not ok or cleaned is None:
        return {"kind": "rejected", "name": "", "args": {}}
    return cleaned


def permission_key(request: dict[str, Any]) -> str:
    """Stable id for 'allow this name for the rest of the run'."""
    kind = str(request.get("kind") or "use_tool")
    name = str(request.get("name") or "")
    return f"{kind}:{name}" if name else kind
