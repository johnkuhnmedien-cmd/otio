"""Pro-Ordner-Einstellungen für die Voice-over-Erzeugung (Phase 4).

Defaults werden aus dem bestätigten Dramaturgie-Plan vorbefüllt. Enthält alle
Ordner aus der Dramaturgie (auch deaktivierte, zur Sichtbarkeit) — nur
`enabled` entscheidet, ob ein Ordner für die automatische Generierung infrage
kommt.
"""

from __future__ import annotations

import json

from otio_app.models import Project
from otio_app.project_layout import get_folder_voiceover_settings_path
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_inventory_summary import (
    build_folder_inventory_summary,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.models import (
    FolderVoiceoverSetting,
    FolderVoiceoverSettingsDocument,
)

__all__ = [
    "build_default_folder_voiceover_settings",
    "load_folder_voiceover_settings",
    "save_folder_voiceover_settings",
    "update_folder_voiceover_settings",
    "enabled_settings",
]


def _dramaturgy_hash(plan) -> str:
    return content_hash_of_model(plan)


def build_default_folder_voiceover_settings(project: Project) -> FolderVoiceoverSettingsDocument:
    """Leitet Default-Settings aus dramaturgy_plan.confirmed.json ab.

    Fehlen Wortanzahl-Werte im Dramaturgie-Eintrag (0 oder nicht gesetzt),
    wird auf die Phase-3-Heuristik zurückgegriffen."""
    plan = load_confirmed_dramaturgy(project)
    settings: list[FolderVoiceoverSetting] = []
    if plan is not None:
        for entry in sorted(plan.recommended_folder_order, key=lambda item: item.order_index):
            target_words = entry.recommended_word_count
            min_words = entry.recommended_min_words
            max_words = entry.recommended_max_words
            if target_words <= 0 or min_words <= 0 or max_words <= 0:
                summary = build_folder_inventory_summary(project, entry.folder_name)
                target_words = target_words or summary.estimated_voiceover_word_count
                min_words = min_words or summary.estimated_min_words
                max_words = max_words or summary.estimated_max_words

            settings.append(
                FolderVoiceoverSetting(
                    folder_name=entry.folder_name,
                    order_index=entry.order_index,
                    enabled=entry.enabled,
                    dramaturgy_role=entry.dramaturgy_role,
                    target_words=target_words,
                    min_words=min_words,
                    max_words=max_words,
                    transition_from_previous=bool(entry.transition_from_previous_hint),
                    use_contrast_with_previous=bool(entry.contrast_or_commonality_hint),
                )
            )

    return FolderVoiceoverSettingsDocument(
        project_id=project.id,
        dramaturgy_hash=_dramaturgy_hash(plan),
        settings=settings,
    )


def load_folder_voiceover_settings(project: Project) -> FolderVoiceoverSettingsDocument | None:
    path = get_folder_voiceover_settings_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FolderVoiceoverSettingsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_folder_voiceover_settings(
    project: Project, settings: FolderVoiceoverSettingsDocument
) -> FolderVoiceoverSettingsDocument:
    normalized = settings.model_copy(update={"project_id": project.id})
    path = get_folder_voiceover_settings_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def update_folder_voiceover_settings(
    project: Project, edited_rows: list[dict]
) -> FolderVoiceoverSettingsDocument:
    """Übernimmt manuelle Tabellen-Bearbeitungen (z. B. aus st.data_editor) in
    die bestehenden Settings und speichert sie erneut."""
    existing = load_folder_voiceover_settings(project)
    if existing is None:
        existing = build_default_folder_voiceover_settings(project)

    settings_by_folder = {setting.folder_name: setting for setting in existing.settings}
    updated: list[FolderVoiceoverSetting] = []
    for row in edited_rows:
        folder_name = row.get("folder_name")
        current = settings_by_folder.get(folder_name)
        if current is None:
            continue
        updates: dict = {}
        for key in (
            "order_index",
            "enabled",
            "dramaturgy_role",
            "target_words",
            "min_words",
            "max_words",
            "word_tolerance_percent",
            "transition_from_previous",
            "callback_to_previous",
            "use_contrast_with_previous",
            "use_commonality_with_previous",
            "folder_extra_prompt",
            "factuality_mode",
            "energy",
            "status",
        ):
            if key in row:
                updates[key] = row[key]
        if "must_include" in row:
            value = row["must_include"]
            updates["must_include"] = (
                [item.strip() for item in value.split(",") if item.strip()]
                if isinstance(value, str)
                else list(value)
            )
        if "must_avoid" in row:
            value = row["must_avoid"]
            updates["must_avoid"] = (
                [item.strip() for item in value.split(",") if item.strip()]
                if isinstance(value, str)
                else list(value)
            )
        updated.append(current.model_copy(update=updates))

    new_document = existing.model_copy(update={"settings": updated})
    return save_folder_voiceover_settings(project, new_document)


def enabled_settings(document: FolderVoiceoverSettingsDocument | None) -> list[FolderVoiceoverSetting]:
    if document is None:
        return []
    return [setting for setting in document.settings if setting.enabled]
