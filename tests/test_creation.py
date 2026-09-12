"""Phase A: brain organ, creation physics, nervous bus, typed packet."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.brain import Brain, CONSTITUTION_MARK, load_system_prompt
from core.loop import AgentLoop, build_user_packet, parse_model_output
from core.event_bus import EventBus, Event
from core.packet import fit_payload
from core.paths import AgentPaths, PathGuard
from core.physics import WorldState, evaluate_think


def test_brain_concatenation_shape():
    paths = AgentPaths.discover()
    text = load_system_prompt(paths)
    assert CONSTITUTION_MARK in text
    organ = Brain.load(paths)
    boot = organ.boot()
    assert boot["ok"] is True
    assert organ.identity_hash
    assert organ.system_message() == text or CONSTITUTION_MARK in organ.system_message()
    assert organ.fingerprint_on_disk(paths) == organ.identity_hash


def test_path_guard_still_blocks_brain_and_core():
    guard = PathGuard(AgentPaths.discover())
    for rel in ("core/loop.py", "brain/constitution.md", "db/schema.sql"):
        try:
            guard.resolve_writable(rel)
            raise AssertionError(f"should have blocked {rel}")
        except PermissionError:
            pass


def test_world_energy_and_cone():
    world = WorldState(energy=3, energy_cost=1, identity_hash="abc")
    assert world.entropy() == 1.0
    assert world.spend() is True
    world.admit(type("O", (), {"step": 1, "source": "tool:list_dir", "content": "ok", "ok": True})())
    assert world.entropy() < 1.0
    assert world.known()
    assert world.check_identity("abc") is True
    assert world.check_identity("nope") is False
    snap = world.snapshot()
    assert snap["identity_conserved"] is False
    assert "energy_remaining" in snap


def test_evaluate_think():
    dark = evaluate_think("", min_chars=20, require_block=True, require_labels=True)
    assert dark["ok"] is False
    assert "no_think" in dark["issues"]
    partial = evaluate_think(
        "intent: greet\nobserved: none\ninferred: chat\nspeculative: none\nnext: finish",
        min_chars=20,
        require_block=True,
        require_labels=True,
    )
    assert partial["ok"] is True


def test_event_bus_wires_brain_and_world():
    paths = AgentPaths.discover()
    brain = Brain.load(paths)
    world = WorldState.from_config({"max_steps": 4}, identity_hash=brain.identity_hash)
    bus = EventBus()
    bus.attach(brain)
    bus.attach(world)
    replies = bus.fire(Event(channel="control", kind="boot"), target="brain")
    assert replies and replies[0].kind == "boot_result"
    bus.fire(
        Event(
            channel="input",
            kind="observe",
            payload={"step": 1, "source": "user", "content": "hello", "ok": True},
            target="world",
        ),
        target="world",
    )
    assert world.observations
    assert "brain" in bus.component_names and "world" in bus.component_names


def test_packet_has_run_state_and_steer():
    paths = AgentPaths.discover()
    loop = AgentLoop(paths)
    packet = build_user_packet(
        goal="List workspace",
        user_input="List workspace",
        memory=loop.memory,
        tools=[{"name": "list_dir", "description": "list"}],
        skills=[],
        world=loop.world,
        system=loop.system_prompt,
        num_ctx=4096,
        max_tokens=400,
        paths=paths,
    )
    blob = packet.split("PACKET:\n", 1)[1]
    obj = json.loads(blob)
    assert "known" in obj["perception"]
    assert "unknown" in obj["perception"]
    assert "energy_remaining" in obj["run_state"]
    loop.memory.close()


def test_fit_payload_truncates_packet_not_law():
    system = "LAW" * 100
    payload = {
        "context": {
            "memory": {"recalled": [{"content": "x" * 400} for _ in range(20)]},
            "beliefs": {f"k{i}": i for i in range(20)},
        },
        "skill_registry": [{"name": f"s{i}"} for i in range(20)],
        "tool_registry": [{"name": f"t{i}"} for i in range(20)],
        "perception": {"known": [{"content": "k"} for _ in range(12)]},
    }
    out = fit_payload(payload, system=system, num_ctx=800, max_tokens=200)
    assert len(out["skill_registry"]) <= 4
    assert len(json.dumps(out)) < len(json.dumps(payload))


def test_dry_run_still_finishes():
    loop = AgentLoop(AgentPaths.discover())
    result = loop.run("List workspace", dry_run=True)
    assert result["ok"] is True
    assert result.get("run_state", {}).get("identity_conserved") is True
    assert "brain" in (result.get("event_bus") or {}).get("components", [])
    loop.memory.close()


def test_timeout_is_not_connection_error():
    from core.loop import _classify_llm_exception
    import urllib.error

    assert _classify_llm_exception(TimeoutError("timed out")) == "timeout"
    err = urllib.error.URLError(TimeoutError("timed out"))
    assert _classify_llm_exception(err) == "timeout"
    refused = urllib.error.URLError(OSError(10061, "connection refused"))
    assert _classify_llm_exception(refused) == "connection_error"


def test_parse_action_unchanged():
    raw = """<think>ok</think>
```json
{"action": "finish", "finish": {"status": "success", "summary": "done"}}
```"""
    think, action = parse_model_output(raw)
    assert think == "ok"
    assert action["action"] == "finish"


if __name__ == "__main__":
    test_brain_concatenation_shape()
    test_path_guard_still_blocks_brain_and_core()
    test_world_energy_and_cone()
    test_evaluate_think()
    test_event_bus_wires_brain_and_world()
    test_packet_has_run_state_and_steer()
    test_fit_payload_truncates_packet_not_law()
    test_dry_run_still_finishes()
    test_timeout_is_not_connection_error()
    test_parse_action_unchanged()
    print("creation tests passed")
