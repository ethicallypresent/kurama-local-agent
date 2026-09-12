"""Plan graph — Day 6 dominion is named work, not vibes.

Exactly one node may be in_progress while work remains. When every node is
done/failed/blocked, the garden is ready for sabbath.
"""

from __future__ import annotations

from typing import Any

PLAN_STATUSES = ("pending", "in_progress", "done", "blocked", "failed")
_SEED_TITLE = "Clarify goal and constraints"


def _node(raw: Any, *, idx: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "id": f"n{idx}",
            "title": str(raw)[:160] or f"step {idx}",
            "status": "pending",
            "depends_on": [],
            "note": "",
        }
    status = str(raw.get("status") or "pending")
    if status not in PLAN_STATUSES:
        status = "pending"
    depends = raw.get("depends_on")
    if not isinstance(depends, list):
        depends = []
    nid = str(raw.get("id") or f"n{idx}")
    return {
        "id": nid,
        "title": str(raw.get("title") or nid)[:160],
        "status": status,
        "depends_on": [str(d) for d in depends[:12]],
        "note": str(raw.get("note") or "")[:240],
    }


def normalize_plan(nodes: list[Any] | None) -> list[dict[str, Any]]:
    """Repair a plan so it is a legal task graph."""
    cleaned = [_node(n, idx=i + 1) for i, n in enumerate(nodes or [])]
    if not cleaned:
        return [
            {
                "id": "n1",
                "title": _SEED_TITLE,
                "status": "in_progress",
                "depends_on": [],
                "note": "",
            }
        ]
    in_prog = [n for n in cleaned if n["status"] == "in_progress"]
    open_work = [n for n in cleaned if n["status"] in ("pending", "in_progress")]
    terminal = {"done", "failed", "blocked"}
    if not open_work:
        for n in cleaned:
            if n["status"] == "in_progress":
                n["status"] = "done"
        return cleaned
    if len(in_prog) > 1:
        keep = in_prog[0]["id"]
        for n in cleaned:
            if n["status"] == "in_progress" and n["id"] != keep:
                n["status"] = "pending"
    elif len(in_prog) == 0:
        # Promote the first pending whose deps are terminal or empty.
        done_ids = {n["id"] for n in cleaned if n["status"] in terminal}
        promoted = False
        for n in cleaned:
            if n["status"] != "pending":
                continue
            deps = n["depends_on"]
            if not deps or all(d in done_ids for d in deps):
                n["status"] = "in_progress"
                promoted = True
                break
        if not promoted:
            cleaned[0]["status"] = "in_progress"
    return cleaned


def plan_health(nodes: list[Any] | None) -> dict[str, Any]:
    plan = [n for n in (nodes or []) if isinstance(n, dict)]
    counts = {s: 0 for s in PLAN_STATUSES}
    for n in plan:
        st = n.get("status")
        if st in counts:
            counts[st] += 1
    in_prog = counts["in_progress"]
    open_work = counts["pending"] + in_prog
    all_done = bool(plan) and open_work == 0
    valid = (in_prog == 1 and open_work >= 1) or all_done
    issue = ""
    if not plan:
        issue = "empty plan"
        valid = False
    elif in_prog > 1:
        issue = "more than one in_progress node"
    elif in_prog == 0 and open_work:
        issue = "work remains but nothing is in_progress"
    seed = is_seed_plan(plan)
    return {
        "node_count": len(plan),
        "in_progress": in_prog,
        "pending": counts["pending"],
        "done": counts["done"],
        "blocked": counts["blocked"],
        "failed": counts["failed"],
        "all_done": all_done,
        "valid": valid,
        "seed": seed,
        "issue": issue,
    }


def is_seed_plan(nodes: list[Any] | None) -> bool:
    plan = [n for n in (nodes or []) if isinstance(n, dict)]
    if len(plan) != 1:
        return False
    return _SEED_TITLE.lower() in str(plan[0].get("title") or "").lower()


def needs_real_plan(*, mode: str, step: int, nodes: list[Any] | None) -> bool:
    """Action-mode goals past two steps must name the work, not keep the seed."""
    return mode == "action" and step > 2 and is_seed_plan(nodes)
