"""Sandboxed Python execution: subprocess, best-effort no-network, timeout.

This is the ONLY place untrusted (model-generated or skill) code should
ever execute — never in-process via exec()/import.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SEC = 15
MAX_OUTPUT_CHARS = 8000


def _minimal_env() -> dict[str, str]:
    """Strip inherited secrets/API keys, but keep enough for the interpreter
    to actually start. "/usr/bin:/bin" is a no-op on Windows (no such paths,
    and sys.executable is an absolute path anyway) — worse, a Windows child
    process without SystemRoot set can fail to initialize its CRT/sockets.
    """
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return {
            "PATH": f"{system_root}\\System32;{system_root}",
            "SystemRoot": system_root,
        }
    return {"PATH": "/usr/bin:/bin"}


def run_python(code: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC, max_output_chars: int = MAX_OUTPUT_CHARS) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="agent_coderunner_") as tmp:
        script = Path(tmp) / "snippet.py"
        script.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", str(script)],  # isolated mode, no site-packages
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=_minimal_env(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timed out after {timeout_sec}s"}
        except OSError as exc:
            return {"ok": False, "error": f"could not launch subprocess: {exc}"}

        stdout, stderr = proc.stdout[:max_output_chars], proc.stderr[:max_output_chars]
        if proc.returncode != 0:
            return {"ok": False, "error": stderr or f"exited {proc.returncode}", "stdout": stdout, "stderr": stderr}
        return {"ok": True, "stdout": stdout, "stderr": stderr}