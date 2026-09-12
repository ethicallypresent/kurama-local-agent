"""Capability evolution: gaps → drafts → tests → stats → reuse.

This is the self-improvement mechanism. The model may propose skills;
this module records whether they actually earned a place in the library.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("agent.evolution")

DEFAULT_TESTS = [{"args": {}, "expect_ok": True}]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SkillStat:
    name: str
    uses: int = 0
    successes: int = 0
    failures: int = 0
    last_error: str = ""
    last_used: str = ""
    origin: str = "draft"

    @property
    def success_rate(self) -> float | None:
        """None (not 1.0) when the skill has never been used — an untested
        skill has no earned success rate, and should not read as perfect."""
        return self.successes / self.uses if self.uses else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "uses": self.uses,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 3) if self.success_rate is not None else None,
            "last_error": self.last_error,
            "last_used": self.last_used,
            "origin": self.origin,
        }


class EvolutionLog:
    def __init__(self, db_dir: Path):
        self.path = db_dir / "evolution.json"
        self.db_dir = db_dir
        self.stats: dict[str, SkillStat] = {}
        self.gaps: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except OSError as exc:
            log.warning("Could not read evolution log at %s: %s", self.path, exc)
            return
        except json.JSONDecodeError as exc:
            corrupt_copy = self.path.with_suffix(".corrupt.json")
            try:
                corrupt_copy.write_text(self.path.read_text(), encoding="utf-8")
            except OSError:
                pass
            log.error(
                "Evolution log at %s is corrupt (%s); preserved as %s and starting fresh.",
                self.path, exc, corrupt_copy,
            )
            return
        for name, row in (raw.get("stats") or {}).items():
            try:
                self.stats[name] = SkillStat(
                    **{k: row[k] for k in SkillStat.__dataclass_fields__ if k in row}
                )
            except TypeError as exc:
                log.warning("Skipping malformed stat entry %r: %s", name, exc)
        self.gaps = raw.get("gaps") or []
        self.events = raw.get("events") or []

    def persist(self) -> None:
        with self._lock:
            self.db_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "stats": {k: v.to_dict() for k, v in self.stats.items()},
                "gaps": self.gaps[-50:],
                "events": self.events[-200:],
            }
            text = json.dumps(payload, indent=2)
            if self._atomic_write(self.path, text):
                return
            fallback = Path(tempfile.gettempdir()) / "agent_evolution.json"
            log.error(
                "Could not persist evolution log to %s; falling back to %s. "
                "Will keep retrying the real path on next persist().",
                self.path, fallback,
            )
            self._atomic_write(fallback, text)

    def _atomic_write(self, path: Path, text: str) -> bool:
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            return True
        except OSError as exc:
            log.warning("Atomic write to %s failed: %s", path, exc)
            return False

    def record_gap(self, goal: str, reason: str) -> dict[str, Any]:
        gap = {"at": _now(), "goal": goal, "reason": reason}
        self.gaps.append(gap)
        self.events.append({"at": _now(), "kind": "gap", **gap})
        self.persist()
        return gap

    def record_created(self, name: str, promoted: bool) -> None:
        name = (name or "").strip()
        if not name:
            log.warning("record_created called with an empty skill name; ignoring.")
            return
        self.stats.setdefault(name, SkillStat(name=name))
        self.events.append({"at": _now(), "kind": "created", "name": name, "promoted": promoted})
        self.persist()

    def record_refine(self, name: str, pass_number: int, passed: bool, signature: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        self.events.append(
            {
                "at": _now(),
                "kind": "refine",
                "name": name,
                "pass": int(pass_number),
                "passed": bool(passed),
                "failure_signature": (signature or "")[:300],
            }
        )
        self.persist()

    def record_use(self, name: str, ok: bool, error: str = "") -> SkillStat | None:
        name = (name or "").strip()
        if not name:
            log.warning("record_use called with an empty skill name; ignoring.")
            return None
        stat = self.stats.setdefault(name, SkillStat(name=name))
        stat.uses += 1
        stat.last_used = _now()
        if ok:
            stat.successes += 1
        else:
            stat.failures += 1
            stat.last_error = error[:300]
            # Success is already in stats. Only failed uses are worth the event log.
            self.events.append(
                {
                    "at": _now(),
                    "kind": "use_failed",
                    "name": name,
                    "ok": False,
                    "error": error[:200],
                }
            )
        self.persist()
        return stat

    def leaderboard(self, k: int = 8) -> list[dict[str, Any]]:
        # Rank proven skills by track record; untested skills (uses == 0) sort
        # last rather than tying with a real 100%-success skill.
        rows = sorted(
            self.stats.values(),
            key=lambda s: (s.uses > 0, s.successes, s.success_rate or 0.0),
            reverse=True,
        )
        return [s.to_dict() for s in rows[:k]]

    def should_create_skill(self, goal: str, skill_names: list[str], last_result: Any) -> tuple[bool, str]:
        g = goal.lower()
        if last_result and (last_result.get("promoted") or last_result.get("from") == "run_skill"):
            return False, ""
        if any(w in g for w in ("new skill", "create a skill", "extend", "self-improv", "teach yourself")):
            if skill_names:
                return True, "user asked the agent to extend its capabilities"
            return True, "user asked the agent to extend its capabilities (library currently empty)"
        if last_result and last_result.get("ok") is False:
            err = str(last_result.get("error", ""))
            if "unknown" in err or "not found" in err or "no tool" in err:
                return True, f"last action missed a capability: {err}"
        if not skill_names and "skill" in g:
            return True, "library is empty for a skill-shaped goal"
        return False, ""


def extract_tests(source: str) -> list[dict[str, Any]]:
    """Pull an optional `TESTS = [...]` list out of a skill draft's source
    WITHOUT executing the draft. Skill code is model-generated and untrusted
    (constitution rule 10) — running it just to read a constant would defeat
    the point of the sandboxed test-runner entirely. This only parses the
    AST and evaluates literal values; it never calls exec/eval on the source.
    """
    try:
        tree = ast.parse(source, filename="<skill>")
    except SyntaxError as exc:
        log.warning("Skill draft has a syntax error, cannot extract TESTS: %s", exc)
        return DEFAULT_TESTS

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "TESTS" not in targets:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            log.warning("TESTS assignment is not a literal list; ignoring and using default tests.")
            return DEFAULT_TESTS
        if isinstance(value, list) and value:
            return value
        log.warning("TESTS was found but is empty or not a list; using default tests.")
        return DEFAULT_TESTS

    return DEFAULT_TESTS