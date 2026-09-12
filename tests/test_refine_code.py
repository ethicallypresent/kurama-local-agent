"""refine_code: tests grade, the model only revises."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop, DEFAULT_REFINE_MAX_PASSES, _extract_revised_code, _failure_signature
from core.paths import AgentPaths

FAILING = '''"""
SKILL MANIFEST
name: bump_text
description: prefix a mark
kind: transform
inputs: {"text": "str"}
outputs: {"ok": "bool", "text": "str"}
"""
TESTS = [{"args": {"text": "hi"}, "expect_ok": True}]

def run(text: str = "") -> dict:
    return {"ok": True}
'''

FIXED = '''"""
SKILL MANIFEST
name: bump_text
description: prefix a mark
kind: transform
inputs: {"text": "str"}
outputs: {"ok": "bool", "text": "str"}
"""
TESTS = [{"args": {"text": "hi"}, "expect_ok": True}]

def run(text: str = "") -> dict:
    return {"ok": True, "text": "x" + text}
'''


def test_extract_revised_code_strips_fence():
    raw = "```python\ndef run():\n    return 1\n```"
    assert "```" not in _extract_revised_code(raw)
    assert "def run" in _extract_revised_code(raw)


def test_failure_signature_stable():
    rows = [
        {"test": 0, "passed": False, "error": "boom"},
        {"test": 1, "passed": True},
    ]
    assert _failure_signature(rows) == "0:boom"


def test_refine_max_passes_default():
    assert DEFAULT_REFINE_MAX_PASSES == 3


def test_refine_code_dry_run_does_not_crash():
    loop = AgentLoop(AgentPaths.discover())
    loop._dry_run = True
    try:
        out = loop._dispatch({"action": "refine_code", "refine": {"skill_name": "does_not_exist_xyz"}})
        assert out.get("ok") is False
        assert "error" in out
    finally:
        loop.close()


def test_refine_code_all_pass_first_try(tmp_path: Path):
    from core.skill_manager import SkillManager

    class Paths:
        skills = tmp_path

    sm = SkillManager(Paths())
    sm.create_draft("bump_text", FIXED)
    loop = AgentLoop(AgentPaths.discover())
    loop.skills = sm
    loop._dry_run = True
    try:
        out = loop._refine_code({"skill_name": "bump_text", "max_passes": 2})
        assert out.get("ok") is True
        assert out.get("passes_used") == 1
        kinds = [e.get("kind") for e in loop.evolution.events if e.get("kind") == "refine" and e.get("name") == "bump_text"]
        assert kinds
    finally:
        loop.close()


def test_refine_code_revises_until_tests_pass(tmp_path: Path):
    from core.skill_manager import SkillManager

    class Paths:
        skills = tmp_path

    sm = SkillManager(Paths())
    created = sm.create_draft("bump_text", FAILING)
    assert created.get("promoted") is False
    loop = AgentLoop(AgentPaths.discover())
    loop.skills = sm
    loop._dry_run = False

    def fake_revise(code, failures):
        assert failures
        assert "current_code" not in str(failures)
        return FIXED

    loop._revise_skill_code = fake_revise  # type: ignore[method-assign]
    try:
        out = loop._refine_code({"skill_name": "bump_text", "max_passes": 3})
        assert out.get("ok") is True
        assert out.get("passes_used") == 2
        assert (tmp_path / "approved" / "bump_text.py").exists()
    finally:
        loop.close()


def test_refine_code_no_progress(tmp_path: Path):
    from core.skill_manager import SkillManager

    class Paths:
        skills = tmp_path

    sm = SkillManager(Paths())
    sm.create_draft("bump_text", FAILING)
    loop = AgentLoop(AgentPaths.discover())
    loop.skills = sm
    loop._dry_run = False
    loop._revise_skill_code = lambda code, failures: code  # type: ignore[method-assign]
    try:
        out = loop._refine_code({"skill_name": "bump_text", "max_passes": 3})
        assert out.get("ok") is False
        assert out.get("error") == "no_progress"
        assert out.get("failures")
    finally:
        loop.close()


def test_run_skill_tests_is_the_harness(tmp_path: Path):
    from core.skill_manager import SkillManager

    class Paths:
        skills = tmp_path

    sm = SkillManager(Paths())
    bad = sm.run_skill_tests("bump_text", FAILING)
    assert bad["ok"] is False
    assert bad["failures"]
    good = sm.run_skill_tests("bump_text", FIXED)
    assert good["ok"] is True
    assert not good["failures"]
