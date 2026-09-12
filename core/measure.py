"""Measurement — Day 5 creatures and Day 6 judgment.

A tool result is not a story. It is a collapse: `observed` is what entered
the light cone; `error` is darkness; `ok` is whether the measurement ran.
The brain may treat only `observed` as fact.

After every act the image-bearer judges: good / not_good / unknown.
"""

from __future__ import annotations

from typing import Any

# Residual context allowed on a failed call (the experiment still happened).
_FAIL_KEEP = frozenset({"path", "url", "query", "stdout", "stderr", "status", "name"})


def _clip(value: Any, *, limit: int = 800) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, list):
        return value[:40]
    if isinstance(value, dict):
        return {str(k): _clip(v, limit=400) for k, v in list(value.items())[:24]}
    return value


def normalize_tool_result(raw: Any, *, name: str) -> dict[str, Any]:
    """Force {ok, observed, error, from}. Never invent a successful observation."""
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "observed": {},
            "error": f"tool {name!r} returned {type(raw).__name__}, not a dict",
            "from": name,
        }
    ok = bool(raw.get("ok"))
    error = "" if ok else str(raw.get("error") or "tool failed")
    if ok:
        observed = {
            k: _clip(v)
            for k, v in raw.items()
            if k not in {"ok", "error", "from", "hint", "observed", "judgment", "rationale"}
        }
    else:
        observed = {k: _clip(raw[k]) for k in _FAIL_KEEP if k in raw and raw[k] not in (None, "")}
    out = {
        **raw,
        "ok": ok,
        "observed": observed,
        "error": error,
        "from": raw.get("from") or name,
    }
    return out


def judge(result: dict[str, Any] | None) -> dict[str, Any]:
    """Day 6: after the act, say whether it was good."""
    if not isinstance(result, dict):
        return {"verdict": "unknown", "reason": "no measurement"}
    if result.get("ok") is True and result.get("error"):
        return {"verdict": "unknown", "reason": "ok flag set but error present"}
    if result.get("ok") is True:
        return {"verdict": "good", "reason": "measurement succeeded"}
    if result.get("ok") is False:
        return {"verdict": "not_good", "reason": str(result.get("error") or "failed")}
    return {"verdict": "unknown", "reason": "missing ok"}


def observed_text(result: dict[str, Any] | None, *, fallback: str = "") -> str:
    """Compact string the cone may admit. Never the model's summary."""
    if not isinstance(result, dict):
        return fallback
    obs = result.get("observed")
    if isinstance(obs, dict) and obs:
        parts = []
        for k, v in list(obs.items())[:8]:
            parts.append(f"{k}={v!s}"[:80])
        return "; ".join(parts)[:500]
    if result.get("error"):
        return str(result.get("error"))[:500]
    return fallback[:500]
