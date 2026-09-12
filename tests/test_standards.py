"""Checklist standards: initiation, immutability, adversity, recovery, fidelity."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.boot import initiate
from core.config import ConfigError, load, merge_and_save, restore, snapshot, validate
from core.contracts import ACTIONS, MAX_GOAL_CHARS
from core.loop import AgentLoop
from core.model import apply_choice, choice_from_spec
from core.paths import AgentPaths
from core.permission import inspect_request
from core.tool_registry import ToolRegistry


def _mini_root(tmp_path: Path) -> Path:
    root = tmp_path / "agent"
    for sub in ("brain", "core", "db", "skills", "tools", "workspace"):
        (root / sub).mkdir(parents=True)
    (root / "brain" / "constitution.md").write_text("# law\n")
    (root / "brain" / "system_prompt.md").write_text("# identity\n")
    (root / "brain" / "reasoning_config.json").write_text(
        json.dumps({"model": "test", "max_steps": 5, "llm": {"model": "test", "num_ctx": 4096}})
    )
    return root


def test_validate_rejects_nul_model_path():
    issues = validate({"llm": {}, "llama_server": {"model_path": "models/\x00evil.gguf"}})
    assert "model_path_nul" in issues


def test_load_returns_copy_not_shared_defaults(tmp_path: Path):
    root = _mini_root(tmp_path)
    paths = AgentPaths.discover(start=root)
    a = load(paths)
    b = load(paths)
    a["llm"]["model"] = "mutated"
    assert b["llm"]["model"] != "mutated"


def test_save_increments_revision_and_keeps_backup(tmp_path: Path):
    root = _mini_root(tmp_path)
    paths = AgentPaths.discover(start=root)
    merge_and_save(paths, llm={"model": "one"})
    merge_and_save(paths, llm={"model": "two"})
    on_disk = json.loads(paths.effective_config_path.read_text(encoding="utf-8"))
    assert on_disk["llm"]["model"] == "two"
    assert int(on_disk.get("config_revision") or 0) >= 2
    bak = paths.effective_config_path.with_name(paths.effective_config_path.name + ".bak")
    assert bak.exists()


def test_restore_rolls_config_back(tmp_path: Path):
    root = _mini_root(tmp_path)
    paths = AgentPaths.discover(start=root)
    before = snapshot(paths)
    merge_and_save(paths, llm={"model": "broken"})
    assert restore(paths, before) is True
    on_disk = json.loads(paths.effective_config_path.read_text(encoding="utf-8"))
    assert on_disk["llm"]["model"] == "test"


def test_empty_goal_does_not_run():
    loop = AgentLoop(AgentPaths.discover())
    try:
        result = loop.run("   ", dry_run=True)
        assert result["ok"] is False
        assert result["error"] == "empty_goal"
    finally:
        loop.close()


def test_tool_rejects_huge_and_non_object_args(tmp_path: Path):
    root = _mini_root(tmp_path)
    tools = ToolRegistry(AgentPaths.discover(start=root))
    huge = {"content": "x" * 250_000}
    out = tools.call("write_file", huge)
    assert out["ok"] is False
    assert out["error"] == "args_too_large"
    bad = tools.call("write_file", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert bad["ok"] is False
    assert bad["error"] == "args_must_be_object"
    nul = tools.call("list_dir\x00", {"path": "."})
    assert nul["ok"] is False
    assert nul["error"] == "unregistered_tool"


def test_permission_request_rejects_hostile_payload():
    ok, clean, err = inspect_request({"kind": "use_tool", "name": "list_dir", "args": {"path": "."}})
    assert ok is True and clean is not None and err == ""
    assert clean["name"] == "list_dir"
    ok, junk, err = inspect_request(["nope"])
    assert ok is False and junk is None and err == "args_must_be_object"
    ok, nul, err = inspect_request({"kind": "use_tool", "name": "x\x00", "args": {}})
    assert ok is False and nul is None and err == "nul_byte"


def test_model_spec_rejects_nul(tmp_path: Path):
    assert choice_from_spec("foo\x00.gguf", tmp_path, {}) is None


def test_apply_choice_restores_config_when_server_fails(tmp_path: Path, monkeypatch):
    root = _mini_root(tmp_path)
    gguf = root / "models"
    gguf.mkdir()
    model = gguf / "x.gguf"
    model.write_bytes(b"gguf")
    paths = AgentPaths.discover(start=root)
    monkeypatch.setattr(
        "core.model.ensure_running",
        lambda *_a, **_k: {"ok": False, "error": "boom"},
    )
    choice = choice_from_spec(str(model), root, {})
    assert choice is not None
    out = apply_choice(paths, choice, {"llm": {}, "llama_server": {}})
    assert out["ok"] is False
    on_disk = json.loads(paths.effective_config_path.read_text(encoding="utf-8"))
    assert on_disk["llm"]["model"] == "test"


def test_boot_reports_missing_brain(tmp_path: Path):
    root = tmp_path / "agent"
    for sub in ("brain", "core", "db", "skills", "tools"):
        (root / sub).mkdir(parents=True)
    (root / "brain" / "reasoning_config.json").write_text('{"llm": {"num_ctx": 4096}}')
    state = initiate(AgentPaths(root=root))
    assert state.ok is False
    assert any("missing:constitution.md" in i for i in state.issues)


def test_actions_contract_covers_finish():
    assert "finish" in ACTIONS
    assert "use_tool" in ACTIONS
    assert MAX_GOAL_CHARS >= 1000
