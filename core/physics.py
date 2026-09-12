"""Creation physics — Genesis order as invariants the runtime can check.

These are not metaphors in comments. They are the laws the nervous system
enforces. Mapping:

  Conservation of identity (Noether) — the brain's law does not change mid-run.
  Causal light cone (finite c)       — only admitted observations are facts.
  Energy / action budget (1st law)   — each step costs energy; no free work.
  Entropy of the unknown (2nd law)   — uncertainty falls only after measurement
                                       (a tool result or an explicit user statement).
  Least action (Hamilton)            — one action per turn.
  Potential barrier (interface)      — PathGuard: no tunneling into protected paths.
  Measurement collapse               — tool results are the only collapse of
                                       possibility into observation.
  Ground state                       — finish when work is done or energy is gone.

Day 1 of creation is light: divide observed from dark. This module is that cut.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from core.event_bus import Component, Event

# Epistemic labels the cortex must use to divide light from dark.
LIGHT_LABELS = ("observed", "inferred", "speculative")
PARTIAL_LIGHT_LABELS = ("intent", "next", "risk", "memory", "plan")

_LABEL_RE = re.compile(
    r"\b(observed|inferred|speculative|observation|intent|next|risk|memory|plan)\b",
    re.IGNORECASE,
)


def hash_identity(*parts: str) -> str:
    """Stable fingerprint of the brain's identity+law (conserved quantity)."""
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


@dataclass
class Observation:
    """One event inside the agent's causal cone."""

    step: int
    source: str  # user | tool:<name> | memory:verified | constitution
    content: str
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "source": self.source,
            "content": (self.content or "")[:240],
            "ok": self.ok,
        }


@dataclass
class WorldState(Component):
    """The physical world the agent inhabits for one run.

    Energy is the step budget. The cone is everything that has been measured.
    Entropy is 1 when nothing but the user's words has been observed.
    """

    name: str = "world"
    energy: float = 50.0
    energy_cost: float = 1.0
    identity_hash: str = ""
    observations: list[Observation] = field(default_factory=list)
    weak_think_turns: int = 0
    conservation_violations: int = 0

    @classmethod
    def from_config(cls, cfg: dict[str, Any], *, identity_hash: str) -> WorldState:
        cap = int(cfg.get("max_steps") or 50)
        action_cap = cfg.get("max_actions_per_run")
        if action_cap is not None:
            cap = min(cap, int(action_cap))
        cost = float((cfg.get("creation") or {}).get("energy_per_step") or 1.0)
        return cls(energy=float(cap), energy_cost=cost, identity_hash=identity_hash)

    # --- 1st law ---

    def spend(self) -> bool:
        """Spend one quantum of energy. False if already at ground state."""
        if self.energy < self.energy_cost:
            self.energy = 0.0
            return False
        self.energy -= self.energy_cost
        return True

    @property
    def at_ground_state(self) -> bool:
        return self.energy < self.energy_cost

    # --- 2nd law / measurement ---

    def admit(self, obs: Observation) -> None:
        """Collapse a possibility into the cone. This is the only way entropy falls."""
        content = (obs.content or "").strip()
        if not content:
            return
        self.observations.append(
            Observation(
                step=obs.step,
                source=obs.source,
                content=content[:500],
                ok=bool(obs.ok),
            )
        )
        if len(self.observations) > 48:
            self.observations = self.observations[-48:]

    def entropy(self) -> float:
        """1.0 = total dark; falls only when measurements (ok observations) accumulate."""
        measured = sum(1 for o in self.observations if o.ok and not o.source.startswith("user"))
        return 1.0 / (1.0 + measured)

    def known(self, *, limit: int = 8) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for obs in reversed(self.observations):
            if not obs.ok:
                continue
            out.append(obs.to_dict())
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def unknown(self, goal: str, last_result: dict[str, Any] | None) -> list[str]:
        gaps: list[str] = []
        if not any(o.source.startswith("tool:") for o in self.observations):
            gaps.append("no tool measurement yet")
        if last_result is None:
            gaps.append("no last_tool_result")
        elif not last_result.get("ok"):
            gaps.append("last measurement failed")
        if goal and not self.observations:
            gaps.append("goal not yet admitted to the cone")
        return gaps[:6]

    def conflicts(self) -> list[str]:
        """Two ok observations that disagree at the source+ok level (coarse)."""
        found: list[str] = []
        by_source: dict[str, list[Observation]] = {}
        for obs in self.observations:
            by_source.setdefault(obs.source, []).append(obs)
        for source, rows in by_source.items():
            oks = {r.ok for r in rows[-4:]}
            if len(oks) > 1:
                found.append(f"{source} has both ok and failed measurements")
        return found[:4]

    def check_identity(self, current_hash: str) -> bool:
        if not self.identity_hash:
            self.identity_hash = current_hash
            return True
        if current_hash != self.identity_hash:
            self.conservation_violations += 1
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "energy_remaining": round(self.energy, 3),
            "energy_cost_per_step": self.energy_cost,
            "entropy": round(self.entropy(), 4),
            "observation_count": len(self.observations),
            "weak_think_turns": self.weak_think_turns,
            "identity_conserved": self.conservation_violations == 0,
        }

    def receive(self, event: Event) -> list[Event]:
        kind = event.kind
        if kind == "observe":
            payload = event.payload or {}
            self.admit(
                Observation(
                    step=int(payload.get("step") or event.step),
                    source=str(payload.get("source") or "unknown"),
                    content=str(payload.get("content") or ""),
                    ok=bool(payload.get("ok", True)),
                )
            )
            return [
                Event(
                    channel="feedback",
                    kind="observations_updated",
                    payload={"entropy": self.entropy(), "observation_count": len(self.observations)},
                    step=event.step,
                )
            ]
        if kind == "tick":
            ok = self.spend()
            if not ok or self.at_ground_state:
                return [
                    Event(
                        channel="control",
                        kind="budget_exhausted",
                        payload={"energy": self.energy},
                        step=event.step,
                    )
                ]
            return []
        if kind == "identity_check":
            current = str((event.payload or {}).get("hash") or "")
            if not self.check_identity(current):
                return [
                    Event(
                        channel="control",
                        kind="conservation_violation",
                        payload={"expected": self.identity_hash, "got": current},
                        step=event.step,
                    )
                ]
            return []
        if kind == "weak_think":
            self.weak_think_turns += 1
            return []
        return []


def evaluate_think(
    think: str,
    *,
    min_chars: int = 20,
    require_block: bool = True,
    require_labels: bool = True,
) -> dict[str, Any]:
    """Day 1: is this think-block actually light, or still dark?

    Returns issues the autonomic system can act on. Does not invent facts.
    """
    text = (think or "").strip()
    issues: list[str] = []
    labels = {m.group(1).lower() for m in _LABEL_RE.finditer(text)}
    if require_block and not text:
        issues.append("no_think")
    elif text and len(text) < max(1, int(min_chars)):
        issues.append("think_too_short")
    if require_labels and text:
        has_epistemic = bool(set(LIGHT_LABELS) & labels)
        has_partial = {"intent", "next"} <= labels or bool(set(PARTIAL_LIGHT_LABELS) & labels)
        if not has_epistemic and not has_partial:
            issues.append("missing_epistemic_labels")
    return {
        "ok": not issues,
        "issues": issues,
        "labels": sorted(labels),
        "chars": len(text),
    }
