"""Safety net: no test in this suite may leave the live project config touched.

Most tests build an isolated root with AgentPaths.discover(start=tmp_path/"agent"),
but several (test_creation.py, test_edge.py, test_permission.py, test_phase_b.py,
test_phase_c.py, test_refine_code.py, test_scaffold.py, test_standards.py) call
AgentLoop(AgentPaths.discover()) / AgentPaths.discover() with no start= override,
which resolves against this real repo. Nothing in the suite is supposed to write
to the live brain/reasoning_config.json, but it has been observed to happen at
least once without a confirmed root cause. Restore the exact bytes after every
test as a backstop regardless of which test (or future test) is at fault.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REAL_CONFIG = Path(__file__).resolve().parents[1] / "brain" / "reasoning_config.json"


@pytest.fixture(autouse=True)
def _protect_real_config():
    before = _REAL_CONFIG.read_bytes() if _REAL_CONFIG.exists() else None
    yield
    if before is None:
        return
    after = _REAL_CONFIG.read_bytes() if _REAL_CONFIG.exists() else None
    if after != before:
        _REAL_CONFIG.write_bytes(before)
