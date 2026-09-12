"""Human-in-the-loop tool permission and confirmation resume."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop, denied_result
from core.paths import AgentPaths


def _loop() -> AgentLoop:
    return AgentLoop(AgentPaths.discover())


def test_denied_result_shape():
    out = denied_result("list_dir", extra={"args": {"path": "."}})
    assert out["ok"] is False
    assert out["error"] == "user_denied"
    assert out["observed"]["denied"] is True
    assert out["from"] == "list_dir"


def test_no_callback_auto_allows():
    loop = _loop()
    try:
        result = loop._dispatch(
            {"action": "use_tool", "tool": {"name": "list_dir", "args": {"path": ".", "glob": "*"}}}
        )
        assert result.get("ok") is True
    finally:
        loop.close()


def test_deny_does_not_call_tool():
    loop = _loop()
    loop.on_permission = lambda _req: "deny"
    try:
        result = loop._dispatch(
            {"action": "use_tool", "tool": {"name": "list_dir", "args": {"path": ".", "glob": "*"}}}
        )
        assert result.get("ok") is False
        assert result.get("error") == "user_denied"
        assert result.get("observed", {}).get("denied") is True
    finally:
        loop.close()


def test_allow_run_skips_second_prompt():
    calls: list[dict] = []

    def perm(req: dict) -> str:
        calls.append(req)
        return "allow_run"

    loop = _loop()
    loop.on_permission = perm
    loop._run_allowed = set()
    try:
        r1 = loop._dispatch(
            {"action": "use_tool", "tool": {"name": "list_dir", "args": {"path": ".", "glob": "*"}}}
        )
        r2 = loop._dispatch(
            {"action": "use_tool", "tool": {"name": "list_dir", "args": {"path": ".", "glob": "*"}}}
        )
        assert r1.get("ok") is True
        assert r2.get("ok") is True
        assert len(calls) == 1
        assert calls[0]["name"] == "list_dir"
    finally:
        loop.close()


def test_create_skill_gated():
    loop = _loop()
    loop.on_permission = lambda req: "deny"
    try:
        result = loop._dispatch(
            {
                "action": "create_skill",
                "skill": {
                    "name": "should_not_exist_perm_test",
                    "description": "gated",
                    "code": "def run(**kwargs):\n    return {'ok': True}\n",
                },
            }
        )
        assert result.get("ok") is False
        assert result.get("error") == "user_denied"
        draft = loop.paths.skills / "drafts" / "should_not_exist_perm_test.py"
        assert not draft.exists()
    finally:
        loop.close()


def test_confirmation_deny_continues():
    loop = _loop()
    loop.on_permission = lambda _req: "deny"
    try:
        result, kind = loop._resume_confirmation(
            {
                "action": "request_confirmation",
                "rationale": "delete",
                "confirmation_request": {
                    "action_type": "delete_file",
                    "description": "delete workspace/x.txt",
                    "target": "workspace/x.txt",
                },
            }
        )
        assert kind == "request_confirmation"
        assert result.get("ok") is False
        assert result.get("error") == "user_denied"
    finally:
        loop.close()


def test_confirmation_allow_dispatches_tool():
    loop = _loop()
    loop.on_permission = lambda _req: "allow"
    try:
        result, kind = loop._resume_confirmation(
            {
                "action": "request_confirmation",
                "confirmation_request": {
                    "action_type": "overwrite_file",
                    "description": "list instead",
                    "target": ".",
                },
                "tool": {"name": "list_dir", "args": {"path": ".", "glob": "*"}},
            }
        )
        assert kind == "use_tool"
        assert result.get("ok") is True
    finally:
        loop.close()


def test_dry_run_still_finishes_without_callback():
    loop = _loop()
    try:
        result = loop.run("List workspace", dry_run=True)
        assert result.get("ok") is True
        assert result.get("steps", 0) >= 1
    finally:
        loop.close()


def test_run_resets_allow_run_grants():
    calls: list[str] = []

    def perm(req: dict) -> str:
        calls.append(str(req.get("name")))
        return "allow_run"

    loop = _loop()
    loop.on_permission = perm
    try:
        loop.run("List workspace", dry_run=True)
        n_first = len(calls)
        loop.run("List workspace", dry_run=True)
        # A new run() must prompt again even if the previous run granted allow_run.
        assert len(calls) >= n_first + 1
    finally:
        loop.close()
