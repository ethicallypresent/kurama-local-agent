"""Compose-smoke the terminal GUI when textual is installed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

textual = pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_tui_composes_dry_run():
    from tui.app import build_app

    app = build_app(dry_run=True)
    async with app.run_test() as pilot:
        assert app.query_one("#prompt")
        assert app.query_one("#chat")
        assert app.query_one("#status")
        await pilot.press("/")
        await pilot.press("h")
        await pilot.press("e")
        await pilot.press("l")
        await pilot.press("p")
        await pilot.press("enter")
        await pilot.pause()
        chat = app.query_one("#chat")
        assert len(list(chat.children)) >= 2
