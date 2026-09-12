"""Live think / answer splitter. Ports WPF RenderStreamBuffer + StripJsonFence."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class StreamView:
    thinking: str
    answer: str
    is_thinking: bool
    thinking_label: str


def _find_ci(hay: str, needle: str, start: int = 0) -> int:
    return hay.lower().find(needle.lower(), start)


def find_think_open(buf: str) -> tuple[int, int]:
    a = _find_ci(buf, "<think>")
    if a >= 0:
        return a, 7
    b = _find_ci(buf, "<thinking>")
    if b >= 0:
        return b, 10
    return -1, 0


def find_think_close(buf: str, start: int) -> int:
    a = _find_ci(buf, "</think>", start)
    b = _find_ci(buf, "</thinking>", start)
    if a < 0:
        return b
    if b < 0:
        return a
    return min(a, b)


def strip_json_fence(text: str) -> str:
    """Prefer finish.summary when the model emitted an action JSON blob."""
    if not text:
        return ""
    low = text.lower()
    fence = low.find("```json")
    if fence < 0:
        return _strip_bare_action_json(text)
    visible = text[:fence].strip()
    json_part = text[fence + 7 :]
    end = json_part.find("```")
    if end >= 0:
        json_part = json_part[:end]
    summary = _finish_summary(json_part.strip())
    if summary:
        return summary if not visible else visible + "\n\n" + summary
    return visible if visible else _strip_bare_action_json(text)


def _finish_summary(raw: str) -> str:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    finish = obj.get("finish") or {}
    if isinstance(finish, dict):
        summary = finish.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return ""


def _strip_bare_action_json(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped.startswith("{") or '"action"' not in stripped:
        return text
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return text
    summary = _finish_summary(stripped[start : end + 1])
    if summary:
        prefix = stripped[:start].strip()
        return summary if not prefix else prefix + "\n\n" + summary
    # Hide internal action JSON with no user-facing summary.
    prefix = stripped[:start].strip()
    return prefix


class StreamSplitter:
    """Incremental <think>…</think> then answer. Blank-line fallback for tiny models."""

    def __init__(self) -> None:
        self.buf = ""
        self._think_closed = False
        self._seen_open = False
        self._thinking = ""
        self._answer = ""
        self._is_thinking = True
        self._label = "Thinking"

    def reset(self) -> None:
        self.buf = ""
        self._think_closed = False
        self._seen_open = False
        self._thinking = ""
        self._answer = ""
        self._is_thinking = True
        self._label = "Thinking"

    def feed(self, token: str) -> StreamView:
        if token:
            self.buf += token
        return self.view()

    def view(self) -> StreamView:
        self._render()
        return StreamView(
            thinking=self._thinking,
            answer=self._answer,
            is_thinking=self._is_thinking,
            thinking_label=self._label,
        )

    def finalize(self) -> StreamView:
        self._render()
        self._is_thinking = False
        if self._seen_open or self._thinking:
            self._label = "Thought"
        if not (self._answer or "").strip() and (self._thinking or "").strip():
            think = self._thinking.strip()
            parts = [p for p in think.split("\n\n") if p.strip()]
            if len(parts) >= 2:
                self._thinking = "\n\n".join(parts[:-1]).strip()
                self._answer = strip_json_fence(parts[-1].strip())
            else:
                self._answer = strip_json_fence(think)
        else:
            self._answer = strip_json_fence(self._answer)
        return StreamView(
            thinking=self._thinking,
            answer=self._answer,
            is_thinking=False,
            thinking_label=self._label,
        )

    def _render(self) -> None:
        buf = self.buf
        open_idx, open_len = find_think_open(buf)
        close_idx = find_think_close(buf, open_idx + open_len) if open_idx >= 0 else -1

        if open_idx >= 0:
            self._seen_open = True
            after_open = open_idx + open_len
            if close_idx < 0:
                self._think_closed = False
                self._is_thinking = True
                self._label = "Thinking"
                self._thinking = buf[after_open:]
                self._answer = ""
                return
            self._think_closed = True
            close_len = 12 if buf[close_idx:].lower().startswith("</thinking>") else 8
            self._thinking = buf[after_open:close_idx].strip()
            self._is_thinking = False
            self._label = "Thought"
            after_close = close_idx + close_len
            self._answer = strip_json_fence(buf[after_close:].lstrip())
            return

        self._is_thinking = True
        self._label = "Thinking"
        split = buf.find("\n\n")
        if not self._think_closed and split > 40 and len(buf) - split > 12:
            self._seen_open = True
            self._think_closed = True
            self._thinking = buf[:split].strip()
            self._is_thinking = False
            self._label = "Thought"
            self._answer = strip_json_fence(buf[split + 2 :].lstrip())
        elif self._think_closed:
            prior = self._thinking or ""
            idx = buf.find(prior)
            if idx >= 0 and idx + len(prior) + 2 <= len(buf):
                self._answer = strip_json_fence(buf[idx + len(prior) :].lstrip("\n\r "))
            else:
                self._answer = strip_json_fence(buf)
            self._is_thinking = False
            self._label = "Thought"
        else:
            self._seen_open = True
            self._thinking = buf
            self._answer = ""
