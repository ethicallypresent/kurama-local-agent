"""Think / answer splitter (WPF parity)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tui.stream import StreamSplitter, strip_json_fence


def test_think_then_json_summary():
    s = StreamSplitter()
    raw = (
        "<think>observed: workspace\ninferred: list first</think>\n"
        '```json\n{"action":"finish","finish":{"status":"success","summary":"listed files"}}\n```'
    )
    s.feed(raw)
    view = s.finalize()
    assert "observed: workspace" in view.thinking
    assert view.answer == "listed files"
    assert view.is_thinking is False
    assert view.thinking_label == "Thought"


def test_streaming_open_tag_keeps_answer_empty():
    s = StreamSplitter()
    s.feed("<think>hello")
    view = s.view()
    assert view.is_thinking is True
    assert "hello" in view.thinking
    assert view.answer == ""


def test_thinking_alias_tags():
    s = StreamSplitter()
    s.feed("<thinking>plan</thinking>\nhello user")
    view = s.finalize()
    assert view.thinking == "plan"
    assert "hello user" in view.answer


def test_blank_line_fallback():
    s = StreamSplitter()
    thinking = "x" * 50
    s.feed(thinking + "\n\n" + "the answer is 4")
    view = s.finalize()
    assert thinking in view.thinking
    assert "the answer is 4" in view.answer


def test_strip_json_fence_prefers_summary():
    text = 'intro\n```json\n{"action":"finish","finish":{"summary":"done"}}\n```'
    assert "done" in strip_json_fence(text)


def test_strip_bare_action_hides_json():
    s = StreamSplitter()
    s.feed('<think>n</think>\n{"action":"update_plan","plan":{"nodes":[]}}')
    view = s.finalize()
    assert "update_plan" not in view.answer
