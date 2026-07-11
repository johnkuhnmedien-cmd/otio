"""Persistenz für das Project Brief (Projekt ohne Voice-Over)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from otio_app.models import Project
from otio_app.project_layout import get_project_brief_path
from otio_app.services.voiceover_generation.models import (
    DEFAULT_NEGATIVE_RULE_FLAGS,
    ProjectBrief,
)


def parse_forbidden_phrases_text(text: str) -> list[str]:
    """Zerlegt das mehrzeilige Textfeld in einzelne Phrasen (eine pro Zeile)."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def default_project_brief(project: Project) -> ProjectBrief:
    """Neutrale Ausgangswerte — alle Negativregeln sind standardmäßig aktiv."""
    return ProjectBrief(
        project_id=project.id,
        language="DE",
        negative_rule_flags=dict(DEFAULT_NEGATIVE_RULE_FLAGS),
    )


def load_project_brief(project: Project) -> ProjectBrief:
    path = get_project_brief_path(project.work_dir_path)
    if not path.is_file():
        return default_project_brief(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProjectBrief.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_project_brief(project)


def save_project_brief(project: Project, brief: ProjectBrief) -> ProjectBrief:
    normalized = brief.model_copy(
        update={"project_id": project.id, "generated_at": datetime.now(timezone.utc)}
    )
    path = get_project_brief_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized
