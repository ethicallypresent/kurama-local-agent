"""Expanse — typed turn packet (Day 2 firmament).

The packet is the air the cortex breathes. The law (system message) is the
ceiling; this JSON is the floor. When context is scarce, truncate the packet,
never the law.

Perception.known / unknown / conflicts is Day 1 light rendered as structure.
physics is the WorldState snapshot so the model can feel energy and entropy.
"""

from __future__ import annotations

import json
from typing import Any

from core.physics import WorldState


def estimate_tokens(text: str) -> int:
    # 4 chars/token is a coarse but stable budget for local GGUFs.
    return max(1, (len(text) + 3) // 4)


def perception_block(
    *,
    user_input: str,
    last: dict[str, Any] | None,
    world: WorldState | None,
    goal: str,
) -> dict[str, Any]:
    known: list[dict[str, Any]] = []
    unknown: list[str] = []
    conflicts: list[str] = []
    if world is not None:
        known = world.known()
        unknown = world.unknown(goal, last if isinstance(last, dict) else None)
        conflicts = world.conflicts()
    elif last is None:
        unknown = ["no last_tool_result"]
    return {
        "user_input": user_input,
        "last_tool_result": last,
        "known": known,
        "unknown": unknown,
        "conflicts": conflicts,
    }


def fit_payload(
    payload: dict[str, Any],
    *,
    system: str,
    num_ctx: int,
    max_tokens: int,
    max_packet_tokens: int | None = None,
) -> dict[str, Any]:
    """Shrink the packet until it fits under the firmament (ctx - law - completion)."""
    reserve = estimate_tokens(system) + int(max_tokens) + 256
    cap_tokens = max(512, int(num_ctx) - reserve)
    if max_packet_tokens is not None:
        cap_tokens = min(cap_tokens, max(256, int(max_packet_tokens)))
    blob = json.dumps(payload, separators=(",", ":"), default=str)
    if estimate_tokens(blob) <= cap_tokens:
        return payload

    out = json.loads(json.dumps(payload, default=str))  # deep-ish copy via json
    box = out.get("context")
    if isinstance(box, dict):
        mem = box.get("memory")
        if isinstance(mem, dict) and isinstance(mem.get("recalled"), list):
            mem["recalled"] = mem["recalled"][:4]
        if "beliefs" in box and isinstance(box["beliefs"], dict):
            items = list(box["beliefs"].items())[:6]
            box["beliefs"] = dict(items)
    if isinstance(out.get("skill_registry"), list):
        out["skill_registry"] = out["skill_registry"][:4]
    if isinstance(out.get("tool_registry"), list):
        out["tool_registry"] = out["tool_registry"][:6]
    if isinstance(out.get("named_entities"), list):
        out["named_entities"] = out["named_entities"][:6]
    if isinstance(out.get("plan"), list) and len(out["plan"]) > 8:
        out["plan"] = out["plan"][:8]
    perc = out.get("perception")
    if isinstance(perc, dict) and isinstance(perc.get("known"), list):
        perc["known"] = perc["known"][-4:]

    blob = json.dumps(out, separators=(",", ":"), default=str)
    if estimate_tokens(blob) > cap_tokens:
        # Last cut: drop recalled entirely rather than nibble the law.
        if isinstance(box, dict) and isinstance(box.get("memory"), dict):
            box["memory"]["recalled"] = []
            box["memory"]["recall_count"] = 0
    return out


def slim_last_result(last: Any) -> Any:
    if not isinstance(last, dict):
        return last
    if len(json.dumps(last, default=str)) <= 1200:
        return last
    return {
        "ok": last.get("ok"),
        "from": last.get("from"),
        "error": last.get("error"),
        "summary": str(last)[:400],
    }
