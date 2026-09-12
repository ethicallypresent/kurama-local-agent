"""Season pass — archive what did not bear fruit (Day 4).

A cheap post-run (and weekly) look at evolution stats. Skills that have
been used enough to have a record and still fail most of the time are
moved to archived/. Memories are not deleted; unused ones just age.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.evolution import EvolutionLog
from core.skill_manager import SkillManager

log = logging.getLogger("agent.season")

SEASON_FILE = "last_season.json"
WEEK_SECONDS = 7 * 24 * 3600
MIN_USES_TO_ARCHIVE = 5
FAILING_RATE = 0.25


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def archive_stale_skills(
    evo: EvolutionLog,
    skills: SkillManager,
    db_dir: Path,
    *,
    now: datetime | None = None,
    force_weekly: bool = False,
) -> dict[str, Any]:
    """Archive skills that fail most of the time. Weekly unless force_weekly."""
    now = now or _now()
    path = Path(db_dir) / SEASON_FILE
    last_at = None
    if path.exists():
        try:
            last_at = _parse_iso(json.loads(path.read_text(encoding="utf-8")).get("at"))
        except (OSError, json.JSONDecodeError, TypeError):
            last_at = None
    elapsed = None if last_at is None else (now - last_at).total_seconds()
    weekly = force_weekly or last_at is None or (elapsed is not None and elapsed >= WEEK_SECONDS)

    archived: list[str] = []
    if weekly:
        for stat in list(evo.stats.values()):
            rate = stat.success_rate
            if stat.uses >= MIN_USES_TO_ARCHIVE and rate is not None and rate < FAILING_RATE:
                out = skills.archive(stat.name)
                if out.get("ok"):
                    archived.append(stat.name)
                    evo.events.append(
                        {
                            "at": now.isoformat(),
                            "kind": "season_archive",
                            "name": stat.name,
                            "success_rate": rate,
                            "uses": stat.uses,
                        }
                    )
        if archived:
            evo.persist()

    report = {
        "at": now.isoformat(),
        "weekly": weekly,
        "archived": archived,
        "skills_considered": len(evo.stats),
    }
    try:
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write season report: %s", exc)
    try:
        from core.maintain import rest
        from core.paths import AgentPaths as _AgentPaths

        rest(_AgentPaths(root=Path(db_dir).resolve().parent))
    except Exception as exc:  # noqa: BLE001
        log.warning("idle maintenance after archive failed: %s", exc)
    return report
