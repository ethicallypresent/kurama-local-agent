"""Self-improvement persists only what a future run can act on."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.evolution import EvolutionLog
from core.permission import ALLOW, ALLOW_RUN, DENY, normalize_decision, permission_key
from core.reflection import ReflectionEngine, TraceStep


def test_normalize_decision_contract():
    assert normalize_decision("allow") == ALLOW
    assert normalize_decision("Y") == ALLOW
    assert normalize_decision("allow-run") == ALLOW_RUN
    assert normalize_decision("always") == ALLOW_RUN
    assert normalize_decision("no") == DENY
    assert normalize_decision(None) == DENY


def test_permission_key_stable():
    assert permission_key({"kind": "use_tool", "name": "list_dir"}) == "use_tool:list_dir"
    assert permission_key({"kind": "confirmation"}) == "confirmation"


def test_successful_run_does_not_save_lift_stats(tmp_path: Path):
    engine = ReflectionEngine({"max_steps": 50, "max_skill_drafts_per_run": 3}, tmp_path)
    trace = [
        TraceStep(step=1, action="use_tool", rationale="list", result_ok=True, tool_name="list_dir"),
        TraceStep(step=2, action="finish", rationale="done", result_ok=True),
    ]
    report = engine.reflect(trace, {"ok": True})
    texts = " ".join(item["text"] for item in report.lessons).lower()
    assert "more likely" not in texts
    assert "lift" not in texts
    assert "worst-alternative" not in texts


def test_failed_tool_becomes_failure_lesson(tmp_path: Path):
    engine = ReflectionEngine({"max_steps": 50}, tmp_path)
    trace = [
        TraceStep(
            step=1,
            action="use_tool",
            rationale="search",
            result_ok=False,
            result_error="no_search_backend",
            tool_name="web_search",
        )
    ]
    report = engine.reflect(trace, {"ok": False})
    kinds = {item["kind"] for item in report.lessons}
    assert "failure" in kinds
    blob = " ".join(item["text"] for item in report.lessons)
    assert "web_search" in blob
    assert "no_search_backend" in blob


def test_successful_skill_use_does_not_log_event(tmp_path: Path):
    evo = EvolutionLog(tmp_path)
    evo.record_use("normalize_text", True)
    assert evo.stats["normalize_text"].uses == 1
    assert evo.stats["normalize_text"].successes == 1
    assert not any(ev.get("kind") == "use" for ev in evo.events)
    assert not any(ev.get("kind") == "use_failed" for ev in evo.events)


def test_failed_skill_use_logs_event(tmp_path: Path):
    evo = EvolutionLog(tmp_path)
    evo.record_use("normalize_text", False, "boom")
    assert evo.stats["normalize_text"].failures == 1
    assert any(ev.get("kind") == "use_failed" and ev.get("error") == "boom" for ev in evo.events)
