#!/usr/bin/env python3
"""Sync project-root agent files into dist/Kurama-Portable.

Source of truth: the repo root (brain/, tui/, models/, …).
Portable is a snapshot for the self-contained exe.

Usage:
  python scripts/sync_portable.py              # copy runtime data files only
  python scripts/sync_portable.py --publish    # also rebuild Kurama.exe
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "Kurama-Portable"

COPY_DIRS = ("brain", "core", "tui", "db", "skills", "tools", "models", "workspace", "tests", "data")
COPY_FILES = ("main.py", "requirements.txt")


def _rm_tree_retry(path: Path, attempts: int = 6) -> None:
    if not path.exists():
        return
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            time.sleep(0.6 * (i + 1))
    for child in path.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except PermissionError:
            print(f"WARNING: locked, skipped {child}", file=sys.stderr)


def sync_runtime() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    for name in COPY_DIRS:
        src = ROOT / name
        if not src.exists():
            continue
        dst = DIST / name
        if dst.exists():
            _rm_tree_retry(dst)
        shutil.copytree(src, dst)
        print(f"synced {name}/")
    for name in COPY_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, DIST / name)
            print(f"synced {name}")


def publish_exe() -> None:
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "publish_portable.py")])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true", help="Also rebuild portable Kurama.exe")
    args = ap.parse_args()
    if os.name == "nt":
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Process Kurama,KuramaStudio,llama-server -EA SilentlyContinue | Stop-Process -Force",
            ],
            check=False,
        )
        time.sleep(1)
    if args.publish:
        publish_exe()
    else:
        sync_runtime()
    print(f"DONE → {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
