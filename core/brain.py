"""Brain — governing organ: identity, law, cognition policy.

The loop is the body. llama-server is breath. This module is the soul that
must be present before anything else lives (Genesis 2:7 as a boot invariant:
no run without both law and a way to speak).

Canonical law is the *.full.md files when they exist. Compact *.md files are
the projection onto a small context window — lower-resolution measurement of
the same field, not a second constitution.

The concatenation shape is a contract with Studio's BrainService:

    <identity>\\n\\n---\\n# Constitution (binding)\\n\\n<law>
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from core.event_bus import Component, Event
from core.paths import AgentPaths
from core.physics import hash_identity

log = logging.getLogger("agent.brain")

CONSTITUTION_MARK = "\n\n---\n# Constitution (binding)\n\n"
FULL_CTX_THRESHOLD = 8192


def _read_text(path: Any) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"brain file missing: {path}") from exc


def _read_optional(path: Any) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return ""


@dataclass
class Brain(Component):
    """Loaded brain. Immutable for the life of a run (identity is conserved)."""

    name: str
    identity_compact: str
    law_compact: str
    identity_full: str
    law_full: str
    policy: dict[str, Any]
    num_ctx: int
    identity_hash: str
    used_full: bool

    def system_message(self, num_ctx: int | None = None) -> str:
        """Projection of identity+law into the model's context.

        Truncate the packet, never this string: the law is the ceiling of the
        expanse, not a field that yields to tool JSON.
        """
        ctx = int(self.num_ctx if num_ctx is None else num_ctx)
        use_full = ctx >= FULL_CTX_THRESHOLD and (
            self.identity_full != self.identity_compact or self.law_full != self.law_compact
        )
        ident = self.identity_full if use_full else self.identity_compact
        law = self.law_full if use_full else self.law_compact
        return ident.rstrip() + CONSTITUTION_MARK + law.lstrip()

    def fingerprint_on_disk(self, paths: AgentPaths) -> str:
        """Re-read identity+law from disk. Conservation fails if this diverges."""
        ident_c = _read_text(paths.brain / "system_prompt.md")
        law_c = _read_text(paths.brain / "constitution.md")
        ident_f = _read_optional(paths.brain / "system_prompt.full.md") or ident_c
        law_f = _read_optional(paths.brain / "constitution.full.md") or law_c
        return hash_identity(ident_f, law_f)

    def boot(self) -> dict[str, Any]:
        """Void check: identity and law must be non-empty. Breath is separate."""
        issues: list[str] = []
        if not (self.identity_compact or "").strip():
            issues.append("empty_identity")
        if not (self.law_compact or "").strip():
            issues.append("empty_law")
        if not self.identity_hash:
            issues.append("missing_identity_hash")
        return {
            "ok": not issues,
            "issues": issues,
            "identity_hash": self.identity_hash,
            "num_ctx": self.num_ctx,
            "used_full": self.used_full,
            "compact_chars": len(self.identity_compact) + len(self.law_compact),
            "full_chars": len(self.identity_full) + len(self.law_full),
        }

    def receive(self, event: Event) -> list[Event]:
        if event.kind == "compose":
            ctx = event.payload.get("num_ctx")
            text = self.system_message(int(ctx) if ctx is not None else None)
            return [
                Event(
                    channel="internal",
                    kind="system_message",
                    payload={"text": text, "chars": len(text)},
                    step=event.step,
                    source=self.name,
                )
            ]
        if event.kind == "boot":
            return [
                Event(
                    channel="control",
                    kind="boot_result",
                    payload=self.boot(),
                    step=event.step,
                    source=self.name,
                )
            ]
        if event.kind == "identity_hash":
            return [
                Event(
                    channel="control",
                    kind="identity_hash",
                    payload={"hash": self.identity_hash},
                    step=event.step,
                    source=self.name,
                )
            ]
        return []

    @classmethod
    def load(cls, paths: AgentPaths, cfg: dict[str, Any] | None = None) -> Brain:
        cfg = cfg or {}
        ident_c = _read_text(paths.brain / "system_prompt.md")
        law_c = _read_text(paths.brain / "constitution.md")
        ident_f = _read_optional(paths.brain / "system_prompt.full.md") or ident_c
        law_f = _read_optional(paths.brain / "constitution.full.md") or law_c
        num_ctx = int((cfg.get("llm") or {}).get("num_ctx") or FULL_CTX_THRESHOLD)
        used_full = num_ctx >= FULL_CTX_THRESHOLD and (
            ident_f != ident_c or law_f != law_c
        )
        # Conservation fingerprint is the canonical (full) field, even when
        # the model only receives the compact projection.
        ident_hash = hash_identity(ident_f, law_f)
        return cls(
            name="brain",
            identity_compact=ident_c,
            law_compact=law_c,
            identity_full=ident_f,
            law_full=law_f,
            policy=cfg,
            num_ctx=num_ctx,
            identity_hash=ident_hash,
            used_full=used_full,
        )


def load_system_prompt(paths: AgentPaths, cfg: dict[str, Any] | None = None) -> str:
    """Concatenated identity + constitution string sent as the system prompt."""
    if cfg is None:
        try:
            raw = json.loads(paths.effective_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        cfg = raw
    return Brain.load(paths, cfg=cfg).system_message()
