"""Resolve and apply which model Kurama talks to.

A choice is either a GGUF file (we start llama-server with it) or an id
already advertised by a running OpenAI-compatible server.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.llama_server import ensure_running, is_healthy, list_models, sampling_kwargs, stop as stop_llama
from core.paths import AgentPaths


@dataclass(frozen=True)
class ModelChoice:
    kind: str  # "server" | "gguf"
    model_id: str
    label: str
    path: str | None = None
    source: str = ""


def normalize_base_url(raw: str | None, *, host: str = "127.0.0.1", port: int = 8080) -> str:
    url = (raw or "").strip() or f"http://{host}:{port}/v1"
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


def scan_ggufs(root: Path, extra_dirs: list[str] | None = None) -> list[Path]:
    """Allowlist: only *.gguf under <root>/models/. extra_dirs is ignored."""
    del extra_dirs
    models = (root / "models").resolve()
    found: list[Path] = []
    if not models.is_dir():
        return found
    try:
        hits = list(models.rglob("*.gguf"))
    except OSError:
        return found
    for path in hits:
        try:
            resolved = path.resolve()
            resolved.relative_to(models)
        except (OSError, ValueError):
            continue
        if path.is_file():
            found.append(resolved)
    found.sort(key=lambda p: p.name.lower())
    return found


def resolve_gguf(spec: str, root: Path) -> Path | None:
    from core.boundary import contains_nul
    from core.contracts import MAX_MODEL_SPEC_CHARS
    from core.launcher import allowed_gguf

    raw = (spec or "").strip().strip('"')
    if not raw or contains_nul(raw) or len(raw) > MAX_MODEL_SPEC_CHARS:
        return None
    if ".." in Path(raw).parts:
        return None
    name = Path(raw).name
    gguf_name = name if name.lower().endswith(".gguf") else name + ".gguf"
    candidates = []
    if raw.lower().endswith(".gguf"):
        candidates.append(raw)
    candidates.append(Path("models") / gguf_name)
    for candidate in candidates:
        path, _err = allowed_gguf(root, candidate)
        if path is not None:
            return path
    return None


def catalog(root: Path, cfg: dict[str, Any]) -> list[ModelChoice]:
    """Server ids first (if llama-server is up), then GGUFs on disk."""
    llm = cfg.get("llm") or {}
    ls = cfg.get("llama_server") or {}
    base = normalize_base_url(
        str(llm.get("base_url") or ""),
        host=str(ls.get("host") or "127.0.0.1"),
        port=int(ls.get("port") or 8080),
    )
    out: list[ModelChoice] = []
    seen: set[str] = set()
    if is_healthy(base):
        for mid in list_models(base):
            key = f"server:{mid}"
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ModelChoice(
                    kind="server",
                    model_id=mid,
                    label=f"{mid}  ·  llama-server",
                    source="server",
                )
            )
    for path in scan_ggufs(root):
        key = f"gguf:{path}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ModelChoice(
                kind="gguf",
                model_id=path.stem,
                label=f"{path.name}  ·  {path.parent}",
                path=str(path),
                source="disk",
            )
        )
    return out


def choice_from_spec(spec: str, root: Path, cfg: dict[str, Any]) -> ModelChoice | None:
    from core.contracts import MAX_MODEL_SPEC_CHARS

    raw = (spec or "").strip().strip('"')
    if not raw or "\x00" in raw or len(raw) > MAX_MODEL_SPEC_CHARS:
        return None
    gguf = resolve_gguf(raw, root)
    if gguf is not None:
        return ModelChoice(
            kind="gguf",
            model_id=gguf.stem,
            label=gguf.name,
            path=str(gguf),
            source="disk",
        )
    for item in catalog(root, cfg):
        if item.model_id == raw:
            return item
    return None


def apply_choice(
    paths: AgentPaths,
    choice: ModelChoice,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Write config and start/reuse llama-server. Restore config if start fails."""
    from core.config import merge_and_save, restore, snapshot
    from core.contracts import MODEL_KINDS

    if choice.kind not in MODEL_KINDS:
        return {"ok": False, "error": "unknown_model_kind"}
    llm = cfg.get("llm") or {}
    ls = cfg.get("llama_server") or {}
    host = str(ls.get("host") or "127.0.0.1")
    port = int(ls.get("port") or 8080)
    alias = choice.model_id
    previous = snapshot(paths)
    if choice.kind == "gguf" and choice.path:
        rel = choice.path
        try:
            rel = str(Path(choice.path).resolve().relative_to(paths.root.resolve()).as_posix())
        except ValueError:
            rel = choice.path
        merge_and_save(
            paths,
            llm={"model": alias, "fallback_model": alias, "backend": "llamacpp"},
            llama_server={"model_path": rel, "alias": alias, "auto_start": True},
        )
        boot = ensure_running(
            paths.root,
            model_path=choice.path,
            host=host,
            port=port,
            alias=alias,
            num_ctx=int(llm.get("num_ctx") or 4096),
            num_thread=int(llm.get("num_thread") or 6),
            num_batch=int(llm.get("num_batch") or 128),
            num_gpu=max(0, int(llm.get("num_gpu") or 0)),
            force_restart=True,
            jinja=bool(ls.get("jinja", True)),
            **sampling_kwargs(llm),
        )
        if not boot.get("ok"):
            restore(paths, previous)
        boot["model_id"] = alias
        boot["choice"] = choice.kind
        return boot

    merge_and_save(paths, llm={"model": alias, "fallback_model": alias})
    base = normalize_base_url(str(llm.get("base_url") or ""), host=host, port=port)
    if is_healthy(base):
        ids = list_models(base)
        if alias in ids:
            return {
                "ok": True,
                "started": False,
                "base_url": base,
                "model_id": alias,
                "choice": "server",
                "ids": ids,
            }
        gguf = resolve_gguf(alias, paths.root)
        if gguf is not None:
            return apply_choice(
                paths,
                ModelChoice(kind="gguf", model_id=alias, label=gguf.name, path=str(gguf), source="disk"),
                cfg,
            )
        return {
            "ok": False,
            "error": f"llama-server has {ids}, not {alias!r}",
            "ids": ids,
            "model_id": alias,
        }
    model_path = str(ls.get("model_path") or "")
    if model_path:
        boot = ensure_running(
            paths.root,
            model_path=model_path,
            host=host,
            port=port,
            alias=alias,
            num_ctx=int(llm.get("num_ctx") or 4096),
            num_thread=int(llm.get("num_thread") or 6),
            num_batch=int(llm.get("num_batch") or 128),
            num_gpu=max(0, int(llm.get("num_gpu") or 0)),
            force_restart=False,
            **sampling_kwargs(llm),
        )
        if not boot.get("ok"):
            restore(paths, previous)
        boot["model_id"] = alias
        boot["choice"] = "server"
        return boot
    restore(paths, previous)
    stop_llama()
    return {
        "ok": False,
        "error": "llama-server is not running and no GGUF is configured",
        "model_id": alias,
    }
