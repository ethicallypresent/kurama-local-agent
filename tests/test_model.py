"""Model catalog: llama-server ids + GGUFs on disk."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.llama_server import build_server_args
from core.model import catalog, choice_from_spec, normalize_base_url, resolve_gguf, scan_ggufs


def test_normalize_base_url_adds_v1():
    assert normalize_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080/v1"
    assert normalize_base_url("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1"


def test_scan_ggufs_recursive(tmp_path: Path):
    models = tmp_path / "models"
    nested = models / "vendor"
    nested.mkdir(parents=True)
    (nested / "alpha.gguf").write_bytes(b"gguf")
    (models / "beta.gguf").write_bytes(b"gguf")
    (models / "notes.txt").write_text("no")
    found = scan_ggufs(tmp_path)
    names = {p.name for p in found}
    assert names == {"alpha.gguf", "beta.gguf"}


def test_resolve_gguf_from_name(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    target = models / "Qwen.gguf"
    target.write_bytes(b"gguf")
    assert resolve_gguf("Qwen.gguf", tmp_path) == target.resolve()
    assert resolve_gguf("Qwen", tmp_path) == target.resolve()
    assert resolve_gguf(str(target), tmp_path) == target.resolve()


def test_catalog_lists_server_then_disk(tmp_path: Path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    (models / "local.gguf").write_bytes(b"gguf")
    monkeypatch.setattr("core.model.is_healthy", lambda *_a, **_k: True)
    monkeypatch.setattr("core.model.list_models", lambda *_a, **_k: ["served-id"])
    cfg = {"llm": {"base_url": "http://127.0.0.1:8080/v1"}, "llama_server": {}}
    items = catalog(tmp_path, cfg)
    assert items[0].kind == "server"
    assert items[0].model_id == "served-id"
    assert any(i.kind == "gguf" and i.model_id == "local" for i in items)


def test_choice_from_spec_prefers_file(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    path = models / "foo.gguf"
    path.write_bytes(b"gguf")
    choice = choice_from_spec("foo.gguf", tmp_path, {})
    assert choice is not None
    assert choice.kind == "gguf"
    assert choice.path == str(path.resolve())


def test_build_server_args_includes_jinja_and_model(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    gguf = models / "ok.gguf"
    gguf.write_bytes(b"gguf")
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"")
    args = build_server_args(
        binary,
        gguf,
        alias="ok",
        host="127.0.0.1",
        port=8080,
        num_ctx=4096,
        num_thread=4,
        num_batch=128,
        num_gpu=0,
        jinja=True,
    )
    assert "--jinja" in args
    assert str(gguf.resolve()) in args
    assert args[args.index("-m") + 1].endswith("ok.gguf")
