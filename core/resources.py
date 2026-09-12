"""Host resource probing + Ollama load tuning (stdlib only).

Keeps Kurama from over-allocating context/threads on small laptops
(e.g. ~6–8 GB RAM + iGPU) while still using spare cores when available.
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("agent.resources")

# Kurama's system prompt + constitution alone need ~3-4k tokens before any
# user packet — this is the floor below which a run can't fit its own brain.
MIN_PRACTICAL_NUM_CTX = 4096


@dataclass(frozen=True)
class HostResources:
    cpu_count: int
    ram_bytes: int
    has_discrete_gpu_hint: bool
    notes: tuple[str, ...] = ()

    @property
    def ram_gb(self) -> float:
        return self.ram_bytes / (1024**3)


def _ram_bytes_windows() -> int:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return 0
    return int(stat.ullTotalPhys)


def _ram_bytes_posix() -> int:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages) * int(page_size)
    except (ValueError, OSError, AttributeError):
        return 0


def probe_host() -> HostResources:
    cpus = os.cpu_count() or 4
    notes: list[str] = []
    ram = 0
    system = platform.system().lower()
    try:
        if system == "windows":
            ram = _ram_bytes_windows()
        else:
            ram = _ram_bytes_posix()
    except Exception as exc:  # noqa: BLE001
        notes.append(f"ram_probe_failed:{exc}")
        ram = 8 * 1024**3  # assume 8 GB if unknown

    # Conservative GPU hint: only treat NVIDIA as "use GPU layers" by default.
    # AMD iGPUs (e.g. Radeon 610M) often hurt more than they help for GGUF.
    gpu_hint = False
    if system == "windows":
        try:
            import subprocess

            out = subprocess.check_output(
                ["wmic", "path", "win32_VideoController", "get", "Name"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            names = " ".join(out.splitlines()).lower()
            if "nvidia" in names:
                gpu_hint = True
                notes.append("nvidia_detected")
            elif "radeon" in names or "amd" in names:
                notes.append("amd_igpu_prefer_cpu")
        except Exception:  # noqa: BLE001
            notes.append("gpu_probe_skipped")

    return HostResources(
        cpu_count=cpus,
        ram_bytes=max(ram, 2 * 1024**3),
        has_discrete_gpu_hint=gpu_hint,
        notes=tuple(notes),
    )


def recommend_llm_settings(host: HostResources | None = None) -> dict[str, Any]:
    """Return llm.* knobs tuned for this machine."""
    host = host or probe_host()
    ram_gb = host.ram_gb
    cpus = host.cpu_count

    # Leave headroom for OS + Python. Context must fit system+constitution+packet
    # (~3–4k tokens for Kurama's brain), so never go below 4096 on a real run.
    # Large context for long chats + multi-lens reasoning (1B GGUF is light).
    # Target band: ~30k–50k tokens.
    if ram_gb < 6.5:
        num_ctx, num_batch, max_tokens, keep_alive, preset = 32768, 512, 900, "10m", "max_context"
    elif ram_gb < 10:
        num_ctx, num_batch, max_tokens, keep_alive, preset = 40960, 512, 1000, "15m", "max_context"
    elif ram_gb < 16:
        num_ctx, num_batch, max_tokens, keep_alive, preset = 49152, 512, 1200, "20m", "max_context"
    else:
        num_ctx, num_batch, max_tokens, keep_alive, preset = 65536, 512, 1600, "30m", "max_context"

    # Use most cores but leave 1–2 for UI / OS responsiveness.
    if cpus <= 2:
        num_thread = cpus
    elif cpus <= 4:
        num_thread = max(1, cpus - 1)
    else:
        num_thread = max(2, cpus - 2)

    num_gpu = -1 if host.has_discrete_gpu_hint and ram_gb >= 10 else 0
    timeout_sec = 420 if ram_gb < 8 else 300

    return {
        "load_preset": preset,
        "num_ctx": num_ctx,
        "num_batch": num_batch,
        "num_gpu": num_gpu,
        "num_thread": num_thread,
        "max_tokens": max_tokens,
        "keep_alive": keep_alive,
        "timeout_sec": timeout_sec,
        "warmup": True,
        "resource_profile": {
            "cpu_count": cpus,
            "ram_gb": round(ram_gb, 2),
            "gpu_hint": host.has_discrete_gpu_hint,
            "notes": list(host.notes),
            "preset": preset,
        },
    }


def apply_resource_allocation(cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Merge host-tuned settings into cfg['llm'].

    Runs when llm.auto_resource_alloc is true (default) or force=True.
    Explicit user values in config still win unless force is set — we only
    fill zeros / missing auto fields and clamp dangerous over-allocation.
    """
    llm = dict(cfg.get("llm") or {})
    auto = bool(llm.get("auto_resource_alloc", True))
    if not auto and not force:
        cfg = dict(cfg)
        cfg["llm"] = llm
        return cfg

    host = probe_host()
    rec = recommend_llm_settings(host)

    if force or llm.get("load_preset") in (None, "", "auto"):
        for key in (
            "load_preset",
            "num_ctx",
            "num_batch",
            "num_gpu",
            "num_thread",
            "max_tokens",
            "keep_alive",
            "timeout_sec",
            "warmup",
        ):
            llm[key] = rec[key]
    else:
        # Soft clamp: keep context within what small laptops can hold, but
        # never below 4096 — Kurama's system+constitution already needs that.
        # Soft ceiling: ~50k on small laptops; allow configured 30k–50k band.
        #
        # This used to floor at 32768 regardless of what the caller asked
        # for, which silently overrode every named load preset with a
        # smaller num_ctx (cpu_optimal=4096, balanced=8192, fast_draft=2048
        # in core.loop.LOAD_PRESETS) the very next time config was loaded —
        # e.g. immediately after /preset in the TUI, since reload_loop()
        # reconstructs AgentLoop and re-runs this function. Floor at the
        # value this comment actually documents instead.
        if host.ram_gb < 6.5 and int(llm.get("num_ctx") or 0) > 49152:
            log.warning(
                "Clamping num_ctx %s → 49152 (only %.1f GB RAM)",
                llm.get("num_ctx"),
                host.ram_gb,
            )
            llm["num_ctx"] = 49152
        if int(llm.get("num_ctx") or 0) < MIN_PRACTICAL_NUM_CTX:
            llm["num_ctx"] = MIN_PRACTICAL_NUM_CTX
        if int(llm.get("num_thread") or 0) <= 0:
            llm["num_thread"] = rec["num_thread"]
        # iGPU machines: force CPU unless user set a non-zero gpu and disabled auto
        if not host.has_discrete_gpu_hint and int(llm.get("num_gpu") or 0) != 0 and auto:
            log.info("No discrete NVIDIA GPU detected; using num_gpu=0 for stable CPU runs")
            llm["num_gpu"] = 0

    llm["resource_profile"] = rec["resource_profile"]
    llm["auto_resource_alloc"] = auto
    cfg = dict(cfg)
    cfg["llm"] = llm
    # Keep process spawning bounded on small hosts.
    if host.ram_gb < 8:
        cfg["max_concurrent_processes"] = 1
    log.info(
        "Resource alloc: preset=%s ctx=%s threads=%s gpu=%s ram=%.1fGB cpus=%s",
        llm.get("load_preset"),
        llm.get("num_ctx"),
        llm.get("num_thread"),
        llm.get("num_gpu"),
        host.ram_gb,
        host.cpu_count,
    )
    return cfg
