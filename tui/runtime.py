"""Disk/config helpers for the terminal GUI. No Textual import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.loop import LOAD_PRESETS, AgentLoop, apply_load_preset, load_config
from core.model import scan_ggufs as scan_model_files
from core.paths import AgentPaths
from core.resources import apply_resource_allocation


def scan_ggufs(root: Path) -> list[Path]:
    return scan_model_files(root)


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f" {n / 1024:.0f} KB".strip()
    if n < 1024**3:
        return f" {n / 1024 ** 2:.1f} MB".strip()
    return f" {n / 1024 ** 3:.2f} GB".strip()


def alias_for_path(path: str | Path) -> str:
    name = Path(path).stem
    return name or "pocket"


def relativize(root: Path, full: Path) -> str:
    try:
        rel = full.resolve().relative_to(root.resolve())
        return rel.as_posix()
    except ValueError:
        return str(full)


def read_config(paths: AgentPaths) -> dict[str, Any]:
    try:
        return json.loads(paths.effective_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(paths: AgentPaths, data: dict[str, Any]) -> None:
    from core.config import save

    save(paths, data)


def merge_config(
    paths: AgentPaths,
    *,
    top: dict[str, Any] | None = None,
    llm: dict[str, Any] | None = None,
    llama_server: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from core.config import merge_and_save

    return merge_and_save(paths, top=top, llm=llm, llama_server=llama_server)


def apply_preset(paths: AgentPaths, preset: str) -> dict[str, Any]:
    cfg = load_config(paths)
    if preset == "auto":
        cfg = apply_resource_allocation(cfg, force=True)
        llm = dict(cfg.get("llm") or {})
        llm["load_preset"] = "auto"
        cfg["llm"] = llm
    else:
        cfg = apply_load_preset(cfg, preset)
    llm = dict(cfg.get("llm") or {})
    merge_config(
        paths,
        llm={
            "load_preset": llm.get("load_preset", preset),
            "num_ctx": llm.get("num_ctx"),
            "num_batch": llm.get("num_batch"),
            "num_gpu": llm.get("num_gpu"),
            "num_thread": llm.get("num_thread"),
            "max_tokens": llm.get("max_tokens"),
            "keep_alive": llm.get("keep_alive"),
            "timeout_sec": llm.get("timeout_sec"),
            "warmup": llm.get("warmup"),
        },
    )
    return cfg


def preset_names() -> list[str]:
    return ["auto", *sorted(LOAD_PRESETS.keys())]


def create_loop(
    paths: AgentPaths,
    *,
    on_token=None,
    on_permission=None,
    previous: AgentLoop | None = None,
) -> AgentLoop:
    if previous is not None:
        try:
            previous.close()
        except Exception:  # noqa: BLE001
            pass
    loop = AgentLoop(paths)
    loop.on_token = on_token
    loop.on_permission = on_permission
    return loop
