"""Phase C: tool measurement contract, plan graph, sabbath finish."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["LLAMA_EMBED_URL"] = ""

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.steer import evaluate_steer
from core.loop import AgentLoop, build_user_packet
from core.measure import judge, normalize_tool_result, observed_text
from core.paths import AgentPaths, PathGuard
from core.plan import needs_real_plan, normalize_plan, plan_health
from core.reflection import TraceStep
from core.complete import complete_finish
from core.tool_registry import ToolRegistry


def test_tool_result_contract_unknown():
    out = normalize_tool_result("oops", name="list_dir")
    assert out["ok"] is False
    assert out["observed"] == {}
    assert out["error"]
    assert out["from"] == "list_dir"


def test_failed_tool_does_not_invent_success():
    raw = {"ok": False, "error": "no_search_backend", "query": "cats", "results": [{"title": "fake"}]}
    out = normalize_tool_result(raw, name="web_search")
    assert out["ok"] is False
    assert "results" not in out["observed"]
    assert out["observed"].get("query") == "cats"
    assert judge(out)["verdict"] == "not_good"


def test_registry_wires_core_tools_and_honest_web():
    paths = AgentPaths.discover()
    tools = ToolRegistry(paths)
    listed = tools.call("list_dir", {"path": ".", "glob": "workspace/*"})
    assert listed["ok"] is True
    assert "observed" in listed
    assert "entries" in listed["observed"]
    web = tools.call("web_search", {"query": "test"})
    assert web["ok"] is False
    assert web["error"]
    assert "observed" in web
    unknown = tools.call("not_a_tool", {})
    assert unknown["ok"] is False
    blocked = tools.call("write_file", {"path": "core/loop.py", "content": "nope"})
    assert blocked["ok"] is False


def test_belief_ranking_prefers_earned_tools():
    paths = AgentPaths.discover()
    tools = ToolRegistry(paths)
    beliefs = {
        "tool:web_search": {"p_success": 0.1},
        "tool:read_file": {"p_success": 0.95},
    }
    ranked = tools.list_filtered("read the file in workspace", k=8, beliefs=beliefs)
    names = [r["name"] for r in ranked]
    assert "read_file" in names
    assert ranked[0]["name"] in {"read_file", "list_dir", "write_file"}
    read = next(r for r in ranked if r["name"] == "read_file")
    assert read["p_success"] == 0.95
    assert read["domain"] == "disk"


def test_plan_one_in_progress():
    messy = [
        {"id": "a", "title": "one", "status": "in_progress"},
        {"id": "b", "title": "two", "status": "in_progress"},
        {"id": "c", "title": "three", "status": "pending"},
    ]
    cleaned = normalize_plan(messy)
    assert sum(1 for n in cleaned if n["status"] == "in_progress") == 1
    health = plan_health(cleaned)
    assert health["valid"] is True
    done = normalize_plan(
        [
            {"id": "a", "title": "one", "status": "done"},
            {"id": "b", "title": "two", "status": "done"},
        ]
    )
    assert plan_health(done)["all_done"] is True
    assert needs_real_plan(mode="action", step=3, nodes=normalize_plan(None)) is True
    assert needs_real_plan(mode="action", step=1, nodes=normalize_plan(None)) is False


def test_rest_when_plan_done():
    trace = [TraceStep(step=1, action="update_plan", rationale="named the work", result_ok=True)]
    steer = evaluate_steer(trace, {"ok": True}, plan_health={"all_done": True})
    assert steer["steer"] == "rest"


def test_complete_finish_fills_observed_and_unknown():
    finish = complete_finish(
        {"status": "success", "summary": "listed it"},
        world=None,
        plan=[{"id": "n1", "title": "Clarify goal and constraints", "status": "in_progress"}],
        last_result={"ok": True, "observed": {"entries": ["a"]}},
        goal="List workspace",
    )
    assert finish["status"] == "success"
    assert finish["observed"]
    assert "judgment" in finish
    assert finish["judgment"]["verdict"] == "good"
    assert any("seed" in u for u in finish["unknown"])


def test_path_guard_still_holds():
    guard = PathGuard(AgentPaths.discover())
    try:
        guard.resolve_writable("core/loop.py")
        raise AssertionError("should block")
    except PermissionError:
        pass


def test_packet_has_plan_health_and_tool_domain():
    loop = AgentLoop(AgentPaths.discover())
    try:
        loop.memory.seed_goal("List workspace")
        packet = build_user_packet(
            goal="List workspace",
            user_input="List workspace",
            memory=loop.memory,
            tools=loop.tools.list_filtered("List workspace", k=8, beliefs=loop._belief_map()),
            skills=[],
            world=loop.world,
            system=loop.system_prompt,
            named=[],
            steer=loop.steering.last,
            judgment=loop._last_judgment,
            paths=loop.paths,
        )
        import json

        obj = json.loads(packet.split("PACKET:\n", 1)[1])
        assert "plan_health" in obj
        assert obj["plan_health"]["in_progress"] == 1
        assert any(t.get("domain") == "disk" for t in obj["tool_registry"])
        assert "judgment" in obj
    finally:
        loop.close()


def test_dry_run_finish_is_sanctified():
    loop = AgentLoop(AgentPaths.discover())
    try:
        result = loop.run("List workspace", dry_run=True)
        assert result["ok"] is True
        finish = result.get("finish") or {}
        assert "observed" in finish
        assert "unknown" in finish
        assert "judgment" in finish
        assert finish.get("status") in {"success", "blocked", "failed"}
        listed = loop.tools.call("list_dir", {"path": ".", "glob": "workspace/*"})
        assert listed["ok"] is True
        assert observed_text(listed)
    finally:
        loop.close()


if __name__ == "__main__":
    test_tool_result_contract_unknown()
    test_failed_tool_does_not_invent_success()
    test_registry_wires_core_tools_and_honest_web()
    test_belief_ranking_prefers_earned_tools()
    test_plan_one_in_progress()
    test_rest_when_plan_done()
    test_complete_finish_fills_observed_and_unknown()
    test_path_guard_still_holds()
    test_packet_has_plan_health_and_tool_domain()
    test_dry_run_finish_is_sanctified()
    print("phase c tests passed")
