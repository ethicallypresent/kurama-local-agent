"""Reasoning engine — selects mental models (incl. Six Thinking Hats) per turn.

Injects a compact scaffold into the agent packet so Kurama reasons like a
structured thinker without bloating the system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MentalModel:
    id: str
    label: str
    prompt: str


# Edward de Bono — Six Thinking Hats
HATS: dict[str, MentalModel] = {
    "white": MentalModel(
        "white",
        "White hat (facts)",
        "List only verified facts, data, and tool results. Separate unknown from known.",
    ),
    "red": MentalModel(
        "red",
        "Red hat (feelings)",
        "Note gut feel, user emotion, and confidence — no justification required.",
    ),
    "black": MentalModel(
        "black",
        "Black hat (caution)",
        "Risks, failure modes, safety, irreversible side effects, what could go wrong.",
    ),
    "yellow": MentalModel(
        "yellow",
        "Yellow hat (benefit)",
        "Upside, value to the user, why this path is worth taking.",
    ),
    "green": MentalModel(
        "green",
        "Green hat (creative)",
        "Alternatives, novel approaches, skill/tool inventiveness.",
    ),
    "blue": MentalModel(
        "blue",
        "Blue hat (process)",
        "Meta: which step are we on, what to do next, when to finish or ask.",
    ),
}

# Extra lenses useful for agent work
LENSES: dict[str, MentalModel] = {
    "first_principles": MentalModel(
        "first_principles",
        "First principles",
        "Strip assumptions; rebuild from basics that must be true.",
    ),
    "inversion": MentalModel(
        "inversion",
        "Inversion",
        "How would this fail? Avoid that. What must never happen?",
    ),
    "second_order": MentalModel(
        "second_order",
        "Second-order effects",
        "After the immediate result — what happens next for the user/system?",
    ),
    "occams_razor": MentalModel(
        "occams_razor",
        "Simplest sufficient plan",
        "Prefer the smallest action that unblocks progress.",
    ),
    "premortem": MentalModel(
        "premortem",
        "Pre-mortem",
        "Imagine the run already failed — name the most likely cause.",
    ),
    "user_intent": MentalModel(
        "user_intent",
        "Intent inference",
        "If the ask is vague, state the best-guess intent and proceed unless a wrong guess is costly.",
    ),
}


def select_models(goal: str, *, mode: str, step: int, last_result: dict[str, Any] | None) -> list[MentalModel]:
    """Pick a small set of hats/lenses for this turn (keep prompt short)."""
    g = (goal or "").lower()
    chosen: list[str] = ["blue", "user_intent"]

    if mode == "chat":
        chosen += ["red", "yellow", "occams_razor"]
    else:
        chosen += ["white", "black", "occams_razor"]
        if step <= 1:
            chosen.append("first_principles")
        if any(w in g for w in ("create", "design", "idea", "improve", "skill", "new")):
            chosen.append("green")
        if any(w in g for w in ("delete", "overwrite", "risk", "fix", "bug", "break", "secure")):
            chosen += ["black", "premortem", "inversion"]
        if any(w in g for w in ("plan", "multi", "project", "refactor")):
            chosen.append("second_order")
        if last_result and not last_result.get("ok"):
            chosen += ["white", "inversion", "green"]

    # Dedupe preserve order
    seen: set[str] = set()
    models: list[MentalModel] = []
    for mid in chosen:
        if mid in seen:
            continue
        seen.add(mid)
        models.append(HATS.get(mid) or LENSES[mid])
    return models[:6]


def build_scaffold(goal: str, *, mode: str, step: int, last_result: dict[str, Any] | None) -> str:
    models = select_models(goal, mode=mode, step=step, last_result=last_result)
    lines = [
        "REASONING_LENSES for this turn — think with each briefly inside <think>, then act:",
    ]
    for i, m in enumerate(models, 1):
        lines.append(f"{i}. {m.label}: {m.prompt}")
    lines.append(
        "Synthesize the lenses into one clear next action. "
        "In <think>, label each claim observed (tool/user/verified memory), "
        "inferred, or speculative. "
        "Do not list the hat names in the user-facing finish.summary."
    )
    return "\n".join(lines)


def packet_with_reasoning(
    packet_body: str,
    *,
    goal: str,
    mode: str,
    step: int,
    last_result: dict[str, Any] | None,
) -> str:
    scaffold = build_scaffold(goal, mode=mode, step=step, last_result=last_result)
    return f"{scaffold}\n\n{packet_body}"
