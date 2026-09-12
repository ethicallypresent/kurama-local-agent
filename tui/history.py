"""Persist chat messages to db/chat_history.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Message:
    """One chat turn. Field names are the JSON contract (snake_case)."""

    who: str
    text: str = ""
    thinking: str = ""
    is_agent: bool = False
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def history_path(db_dir: Path) -> Path:
    return db_dir / "chat_history.json"


def load_history(db_dir: Path) -> list[Message]:
    path = history_path(db_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        thinking = str(item.get("thinking") or "")
        if not text.strip() and not thinking.strip():
            continue
        out.append(
            Message(
                who=str(item.get("who") or ""),
                text=text,
                thinking=thinking,
                is_agent=bool(item.get("is_agent", item.get("isAgent"))),
                at=str(item.get("at") or ""),
            )
        )
    return out


def save_history(db_dir: Path, messages: list[Message], *, limit: int = 500) -> None:
    db_dir.mkdir(parents=True, exist_ok=True)
    payload: list[dict[str, Any]] = []
    for b in messages[-limit:]:
        payload.append(
            {
                "who": b.who,
                "text": b.text,
                "thinking": b.thinking,
                "is_agent": b.is_agent,
                "at": b.at or datetime.now(timezone.utc).isoformat(),
            }
        )
    path = history_path(db_dir)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
