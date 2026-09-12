"""Frozen portable exe root discovery."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.paths import AgentPaths, RUNTIME_SUBDIRS


def test_frozen_exe_dir_is_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "portable"
    for name in RUNTIME_SUBDIRS:
        (root / name).mkdir(parents=True)
    exe = root / "Kurama.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("AGENT_ROOT", raising=False)
    paths = AgentPaths.discover()
    assert paths.root == root.resolve()
    assert paths.brain == root.resolve() / "brain"


def test_agent_root_env_wins(tmp_path: Path, monkeypatch):
    root = tmp_path / "envroot"
    for name in RUNTIME_SUBDIRS:
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("AGENT_ROOT", str(root))
    paths = AgentPaths.discover()
    assert paths.root == root.resolve()
