"""Fill in a finish payload: observed vs inferred vs still unknown."""

from __future__ import annotations

from typing import Any

from core.measure import judge
from core.physics import WorldState
from core.plan import plan_health

_FINISH_STATUSES = ("success", "blocked", "failed")


def complete_finish(
    finish: dict[str, Any] | None,
    *,
    world: WorldState | None,
    plan: list[dict[str, Any]] | None,
    last_result: dict[str, Any] | None,
    goal: str = "",
) -> dict[str, Any]:
    body = dict(finish or {})
    status = str(body.get("status") or "success")
    if status not in _FINISH_STATUSES:
        status = "success"
    summary = str(body.get("summary") or "").strip() or "done"

    observed = body.get("observed")
    if not isinstance(observed, list):
        if world is not None:
            observed = [o.get("content") for o in world.known(limit=6)]
        elif isinstance(last_result, dict) and last_result.get("observed"):
            obs = last_result["observed"]
            observed = [f"{k}={v}" for k, v in list(obs.items())[:6]] if isinstance(obs, dict) else [str(obs)]
        else:
            observed = []

    inferred = body.get("inferred") if isinstance(body.get("inferred"), list) else []
    unknown = body.get("unknown")
    if not isinstance(unknown, list):
        unknown = world.unknown(goal, last_result if isinstance(last_result, dict) else None) if world else []

    health = plan_health(plan)
    if health.get("issue"):
        unknown = list(unknown) + [f"plan: {health['issue']}"]
    if health.get("seed"):
        unknown = list(unknown) + ["plan was still the seed graph"]

    verdict = judge(last_result)
    artifacts = body.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []

    return {
        "status": status,
        "summary": summary,
        "observed": observed[:8],
        "inferred": inferred[:8],
        "unknown": unknown[:8],
        "artifacts": artifacts[:12],
        "judgment": verdict,
        "plan_done": bool(health.get("all_done")),
    }
