"""Phase B: land (world model), seed (promotion + skill kinds), lights (steer + season)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["LLAMA_EMBED_URL"] = ""

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.steer import evaluate_steer
from core.loop import AgentLoop, build_user_packet
from core.memory import Memory, hash_embedding
from core.paths import AgentPaths
from core.reflection import TraceStep
from core.season import archive_stale_skills
from core.skill_manager import SkillManager, infer_skill_kind, kind_contract_errors
from core.world_model import WorldModel, extract_entities


def test_extract_and_name():
    found = extract_entities("read workspace/out.md using list_dir", "tool:list_dir")
    kinds = {k for k, _ in found}
    names = {n for _, n in found}
    assert "tool" in kinds
    assert "list_dir" in names
    assert any("workspace" in n for n in names)


def test_world_model_upsert(tmp_path: Path):
    db = tmp_path
    land = WorldModel(db)
    try:
        land.name_from_observation(
            text="wrote workspace/out.md",
            source="tool:write_file",
            verified=True,
        )
        rows = land.recall("workspace", k=8)
        assert any(r["kind"] == "file" and "out.md" in r["name"] for r in rows)
        assert any(r["kind"] == "tool" and r["name"] == "write_file" for r in rows)
    finally:
        land.close()


def test_propose_is_not_verified(tmp_path: Path):
    mem = Memory(tmp_path)
    try:
        eid = mem.propose(
            f"The workspace listing is a hypothesis until promoted ({os.getpid()}).",
            kind="lesson",
        )
        assert eid
        hits = mem.retrieve("workspace listing hypothesis promoted", k=5)
        assert hits
        assert hits[0]["verified"] is False
        assert hits[0]["status"] == "proposal"
        assert mem.promote(eid) is True
        hits2 = mem.retrieve("workspace listing hypothesis", k=5)
        assert any(h["id"] == eid and h["verified"] and h["status"] == "promoted" for h in hits2)
    finally:
        mem.close()


def test_embed_spaces_do_not_mix(tmp_path: Path):
    mem = Memory(tmp_path)
    try:
        hash_vec = hash_embedding("alpha lesson about hashing")
        server_vec = [0.1] * 128
        mem.ltm.save(
            "alpha lesson about hashing",
            kind="lesson",
            embedding=hash_vec,
            embed_space="hash",
            skip_dedup=True,
        )
        mem.ltm.save(
            "beta lesson about servers",
            kind="lesson",
            embedding=server_vec,
            embed_space="server",
            skip_dedup=True,
        )
        # Query is hash space (no embed server). Must not surface the 128-d row.
        hits = mem.retrieve("alpha lesson about hashing", k=8)
        spaces = []
        for h in hits:
            assert "beta lesson about servers" not in h["content"]
            spaces.append(h["content"])
        assert any("alpha" in c for c in spaces)
    finally:
        mem.close()


def test_skill_kind_contract():
    assert infer_skill_kind("normalize_text", "trim") == "transform"
    assert infer_skill_kind("web_search", "find pages") == "retrieve"
    assert kind_contract_errors("transform", {"ok": True, "text": "x"}) == []
    assert kind_contract_errors("transform", {"ok": True}) != []
    assert kind_contract_errors("retrieve", {"ok": True, "items": []}) == []


def test_skill_kind_blocks_promotion(tmp_path: Path):
    root = tmp_path
    for sub in ("drafts", "approved", "_core", "archived"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    class Paths:
        skills = root

    sm = SkillManager(Paths())
    code = '''"""
SKILL MANIFEST
name: empty_transform
description: returns ok only
kind: transform
inputs: {}
outputs: {"ok": "bool"}
"""
TESTS = [{"args": {}, "expect_ok": True}]
def run(**kwargs):
    return {"ok": True}
'''
    out = sm.create_draft("empty_transform", code, kind="transform")
    assert out.get("promoted") is False
    assert "transform" in str(out.get("error") or "") or any(
        "transform" in str(r.get("error") or "") for r in (out.get("test_results") or [])
    )


def test_stop_spin_after_three_failed_tools():
    trace = [
        TraceStep(step=1, action="use_tool", rationale="a", result_ok=False, tool_name="web_search"),
        TraceStep(step=2, action="use_tool", rationale="b", result_ok=False, tool_name="web_search"),
        TraceStep(step=3, action="use_tool", rationale="c", result_ok=False, tool_name="web_search"),
    ]
    steer = evaluate_steer(trace, {"ok": False})
    assert steer["steer"] == "stop_spin"


def test_season_archives_failing_skill(tmp_path: Path):
    root = tmp_path
    skills_root = root / "skills"
    db = root / "db"
    db.mkdir(parents=True, exist_ok=True)
    for sub in ("drafts", "approved", "_core", "archived"):
        (skills_root / sub).mkdir(parents=True, exist_ok=True)
    (skills_root / "approved" / "bad_skill.py").write_text(
        'def run(**kwargs):\n    return {"ok": True, "echo": kwargs}\n',
        encoding="utf-8",
    )

    class Paths:
        skills = skills_root

    from core.evolution import EvolutionLog, SkillStat

    evo = EvolutionLog(db)
    evo.stats["bad_skill"] = SkillStat(name="bad_skill", uses=6, successes=0, failures=6)
    sm = SkillManager(Paths())
    report = archive_stale_skills(evo, sm, db, force_weekly=True)
    assert "bad_skill" in report["archived"]
    assert (skills_root / "archived" / "bad_skill.py").exists()


def test_packet_includes_entities_and_steer():
    loop = AgentLoop(AgentPaths.discover())
    try:
        loop.world_model.name_from_observation(text="workspace/out.md", source="tool:list_dir", verified=True)
        packet = build_user_packet(
            goal="List workspace",
            user_input="List workspace",
            memory=loop.memory,
            tools=[{"name": "list_dir", "description": "list"}],
            skills=[{"name": "normalize_text", "kind": "transform", "status": "approved"}],
            world=loop.world,
            system=loop.system_prompt,
            named=loop.world_model.recall("workspace"),
            steer=loop.steering.last,
            paths=loop.paths,
        )
        obj = json.loads(packet.split("PACKET:\n", 1)[1])
        assert "named_entities" in obj
        assert "steer" in obj
        assert obj["steer"]["steer"] == "continue"
    finally:
        loop.close()


def test_dry_run_still_finishes():
    loop = AgentLoop(AgentPaths.discover())
    try:
        result = loop.run("List workspace", dry_run=True)
        assert result["ok"] is True
        assert "world_model" in result.get("event_bus", {}).get("components", [])
        assert "steering" in result.get("event_bus", {}).get("components", [])
        assert result.get("steer", {}).get("steer") in {"continue", "replan", "stop_spin", "rest"}
    finally:
        loop.close()


if __name__ == "__main__":
    test_extract_and_name()
    test_world_model_upsert()
    test_propose_is_not_verified()
    test_embed_spaces_do_not_mix()
    test_skill_kind_contract()
    test_skill_kind_blocks_promotion()
    test_stop_spin_after_three_failed_tools()
    test_season_archives_failing_skill()
    test_packet_includes_entities_and_steer()
    test_dry_run_still_finishes()
    print("phase b tests passed")
