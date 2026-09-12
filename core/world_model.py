"""Land — named world model (Genesis 2: the first job is naming).

Dry land is a place that holds identity. This organ upserts observed
entities (files, tools, skills, places, constraints, the live goal) into
SQLite so recall is not only cosine over sentences.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.event_bus import Component, Event

log = logging.getLogger("agent.world_model")

ENTITY_KINDS = ("person", "project", "file", "constraint", "tool", "skill", "place")

_PATH_RE = re.compile(
    r"(?:workspace|skills|brain|db|core|models|tests|tools)/[A-Za-z0-9_./\\-]+"
    r"|[A-Za-z0-9_.-]+\.(?:py|md|json|txt|gguf|sql|ps1|cs|xaml)"
)
_PLACE_RE = re.compile(r"\b(workspace|skills/drafts|skills/approved|brain|db)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_entities(text: str, source: str) -> list[tuple[str, str]]:
    """Cheap naming: paths, tools, skills, places. No invented people."""
    found: list[tuple[str, str]] = []
    src = (source or "").strip()
    blob = text or ""
    if src.startswith("tool:"):
        name = src.split(":", 1)[1].strip()
        if name:
            found.append(("tool", name))
    if src.startswith("skill:"):
        name = src.split(":", 1)[1].strip()
        if name:
            found.append(("skill", name))
    for m in _PATH_RE.finditer(blob):
        found.append(("file", m.group(0).replace("\\", "/")))
    for m in _PLACE_RE.finditer(blob):
        found.append(("place", m.group(1).replace("\\", "/")))
    if src == "user" and blob.strip():
        found.append(("project", blob.strip()[:80]))
    # Dedup preserve order
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for kind, name in found:
        key = (kind, name.lower())
        if key in seen or not name:
            continue
        seen.add(key)
        out.append((kind, name))
    return out[:16]


class WorldModel(Component):
    """Named entities in the same SQLite file as episodes."""

    name = "world_model"

    def __init__(self, db_dir: Path):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.sqlite = sqlite3.connect(self.db_dir / "memory.sqlite", check_same_thread=False)
        self.sqlite.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self.sqlite.executescript(
                """
                CREATE TABLE IF NOT EXISTS world_entities (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  name TEXT NOT NULL,
                  attributes TEXT,
                  source TEXT,
                  verified INTEGER,
                  first_seen TEXT,
                  last_seen TEXT,
                  mentions INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_world_kind_name
                  ON world_entities(kind, name);
                """
            )
            self.sqlite.commit()

    def upsert(
        self,
        kind: str,
        name: str,
        *,
        source: str = "",
        verified: bool = False,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        if kind not in ENTITY_KINDS:
            raise ValueError(f"invalid entity kind {kind!r}")
        name = (name or "").strip()[:160]
        if not name:
            raise ValueError("entity name empty")
        now = _now()
        with self._lock:
            row = self.sqlite.execute(
                "SELECT id, mentions FROM world_entities WHERE kind = ? AND name = ?",
                (kind, name),
            ).fetchone()
            if row:
                mentions = int(row["mentions"] or 1) + 1
                self.sqlite.execute(
                    """
                    UPDATE world_entities
                    SET last_seen = ?, mentions = ?, source = ?,
                        verified = CASE WHEN ? = 1 THEN 1 ELSE verified END,
                        attributes = CASE WHEN ? != '{}' THEN ? ELSE attributes END
                    WHERE id = ?
                    """,
                    (
                        now,
                        mentions,
                        source or "",
                        int(verified),
                        json.dumps(attributes or {}),
                        json.dumps(attributes or {}),
                        row["id"],
                    ),
                )
                self.sqlite.commit()
                return str(row["id"])
            eid = str(uuid4())
            self.sqlite.execute(
                "INSERT INTO world_entities VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    eid,
                    kind,
                    name,
                    json.dumps(attributes or {}),
                    source or "",
                    int(verified),
                    now,
                    now,
                    1,
                ),
            )
            self.sqlite.commit()
            return eid

    def name_from_observation(self, *, text: str, source: str, verified: bool = False) -> list[dict[str, Any]]:
        named: list[dict[str, Any]] = []
        for kind, name in extract_entities(text, source):
            try:
                eid = self.upsert(kind, name, source=source, verified=verified)
            except ValueError:
                continue
            named.append({"id": eid, "kind": kind, "name": name})
        return named

    def recall(self, query: str = "", *, k: int = 8) -> list[dict[str, Any]]:
        q = (query or "").lower()
        with self._lock:
            rows = self.sqlite.execute(
                """
                SELECT kind, name, source, verified, mentions, last_seen
                FROM world_entities
                ORDER BY mentions DESC, last_seen DESC
                LIMIT 48
                """
            ).fetchall()
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            name = str(row["name"])
            hay = f"{row['kind']} {name} {row['source'] or ''}".lower()
            score = int(row["mentions"] or 1)
            if q:
                score += 3 * sum(1 for w in q.split() if w and w in hay)
            scored.append(
                (
                    score,
                    {
                        "kind": row["kind"],
                        "name": name,
                        "source": row["source"],
                        "verified": bool(row["verified"]),
                        "mentions": int(row["mentions"] or 1),
                    },
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]

    def receive(self, event: Event) -> list[Event]:
        if event.kind != "observe":
            return []
        payload = event.payload or {}
        named = self.name_from_observation(
            text=str(payload.get("content") or ""),
            source=str(payload.get("source") or event.source or ""),
            verified=bool(payload.get("ok", True)),
        )
        if not named:
            return []
        return [
            Event(
                channel="feedback",
                kind="named",
                payload={"count": len(named), "names": [n["name"] for n in named[:6]]},
                step=event.step,
                source=self.name,
            )
        ]

    def close(self) -> None:
        try:
            self.sqlite.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("Error closing world model: %s", exc)
