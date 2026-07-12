"""Pro-Ordner-Einstellungen für die Voice-over-Erzeugung (Phase 4).

Defaults werden aus dem bestätigten Dramaturgie-Plan vorbefüllt. Enthält alle
Ordner aus der Dramaturgie (auch deaktivierte, zur Sichtbarkeit) — nur
`enabled` entscheidet, ob ein Ordner für die automatische Generierung infrage
kommt.
"""

from __future__ import annotations

import json

from otio_app.defaults import (
    FACTUALITY_MODE_STRICT_INVENTORY_ONLY,
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
)
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
    "apply_standard_word_target_to_enabled_settings",
    "apply_standard_word_target_to_folder",
    "apply_strict_inventory_factuality_to_folders",
    "enabled_settings",
]


def _dramaturgy_hash(plan) -> str:
    return content_hash_of_model(plan)


def build_default_folder_voiceover_settings(project: Project) -> FolderVoiceoverSettingsDocument:
    """Leitet Default-Settings aus dramaturgy_plan.confirmed.json ab.

    Fehlen Wortanzahl-Werte im Dramaturgie-Eintrag (0 oder nicht gesetzt),
    wird auf die Phase-3-Heuristik zurückgegriffen.

    transition_from_previous/use_contrast_with_previous (rückwärtsgerichtet)
    werden für den ERSTEN aktivierten Ordner immer auf False gezwungen — es
    gibt dort nichts Vorheriges, auf das sich beziehen ließe. transition_to_next
    (vorwärtsgerichtet) wird spiegelbildlich für den LETZTEN aktivierten
    Ordner immer auf False gezwungen — dort gibt es nichts Nächstes."""
    plan = load_confirmed_dramaturgy(project)
    settings: list[FolderVoiceoverSetting] = []
    if plan is not None:
        sorted_entries = sorted(plan.recommended_folder_order, key=lambda item: item.order_index)
        enabled_entries = [entry for entry in sorted_entries if entry.enabled]
        first_enabled_folder_name = enabled_entries[0].folder_name if enabled_entries else None
        last_enabled_folder_name = enabled_entries[-1].folder_name if enabled_entries else None

        for entry in sorted_entries:
            target_words = entry.recommended_word_count
            min_words = entry.recommended_min_words
            max_words = entry.recommended_max_words
            if target_words <= 0 or min_words <= 0 or max_words <= 0:
                summary = build_folder_inventory_summary(project, entry.folder_name)
                target_words = target_words or summary.estimated_voiceover_word_count
                min_words = min_words or summary.estimated_min_words
                max_words = max_words or summary.estimated_max_words

            is_first_enabled = entry.folder_name == first_enabled_folder_name
            is_last_enabled = entry.folder_name == last_enabled_folder_name

            settings.append(
                FolderVoiceoverSetting(
                    folder_name=entry.folder_name,
                    order_index=entry.order_index,
                    enabled=entry.enabled,
                    dramaturgy_role=entry.dramaturgy_role,
                    target_words=target_words,
                    min_words=min_words,
                    max_words=max_words,
                    transition_from_previous=(
                        bool(entry.transition_from_previous_hint) and not is_first_enabled
                    ),
                    use_contrast_with_previous=(
                        bool(entry.contrast_or_commonality_hint) and not is_first_enabled
                    ),
                    transition_to_next=(
                        bool(entry.transition_goal_to_next) and not is_last_enabled
                    ),
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
            "transition_to_next",
            "callback_to_previous",
            "use_contrast_with_previous",
            "use_commonality_with_previous",
            "folder_extra_prompt",
            "factuality_mode",
            "energy",
            "segment_asset_planning_mode",
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


def apply_standard_word_target_to_enabled_settings(project: Project) -> FolderVoiceoverSettingsDocument:
    """Setzt target_words/min_words/max_words für ALLE aktivierten
    (enabled=True) Folder-Settings explizit auf den neuen Standard
    (VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS/_MIN_WORDS/_MAX_WORDS) —
    ausschließlich bei explizitem Klick auf den Button „Zielwortanzahl 135
    auf alle aktiven Folder anwenden“ (Nutzerwunsch, Juli 2026). Bestehende
    Settings werden NIE automatisch/heimlich überschrieben — nur dieser
    eine, bewusste Aufruf ändert etwas.

    Ändert AUSSCHLIESSLICH diese drei Wortanzahl-Felder; alle anderen
    Settings (Übergänge, must_include/-avoid, folder_extra_prompt, etc.)
    bleiben unverändert. Ändert NICHT bereits generierte Voice-over-Texte
    selbst — dafür ist weiterhin ein expliziter „Erneut generieren“-Klick
    nötig. Deaktivierte (enabled=False) Folder bleiben unverändert.

    Wirft ValueError, wenn noch keine Settings existieren."""
    existing = load_folder_voiceover_settings(project)
    if existing is None:
        raise ValueError("Keine Folder Voice-over Settings vorhanden — bitte zuerst erstellen.")

    updated_settings = [
        (
            setting.model_copy(
                update={
                    "target_words": VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
                    "min_words": VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
                    "max_words": VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
                }
            )
            if setting.enabled
            else setting
        )
        for setting in existing.settings
    ]
    new_document = existing.model_copy(update={"settings": updated_settings})
    return save_folder_voiceover_settings(project, new_document)


def apply_standard_word_target_to_folder(
    project: Project, folder_name: str
) -> FolderVoiceoverSettingsDocument:
    """Wie apply_standard_word_target_to_enabled_settings, aber gezielt NUR
    für GENAU EINEN Ordner (unabhängig von dessen enabled-Status) —
    Baustein für die kombinierte 'Asset-bewusst neu generieren'-Aktion
    (Phase 6, siehe voiceover_author_service.
    regenerate_folder_voiceover_with_standard_word_target), damit bereits
    vor Phase 1 generierte Projekte einzelne Ordner gezielt auf den neuen
    Wortanzahl-Standard heben können, ohne alle anderen Ordner anzufassen.

    Wirft ValueError, wenn noch keine Settings existieren oder folder_name
    darin nicht vorkommt."""
    existing = load_folder_voiceover_settings(project)
    if existing is None:
        raise ValueError("Keine Folder Voice-over Settings vorhanden — bitte zuerst erstellen.")
    if not any(setting.folder_name == folder_name for setting in existing.settings):
        raise ValueError(f"Ordner '{folder_name}' ist nicht in den Folder Voice-over Settings vorhanden.")

    updated_settings = [
        (
            setting.model_copy(
                update={
                    "target_words": VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
                    "min_words": VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
                    "max_words": VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
                }
            )
            if setting.folder_name == folder_name
            else setting
        )
        for setting in existing.settings
    ]
    new_document = existing.model_copy(update={"settings": updated_settings})
    return save_folder_voiceover_settings(project, new_document)


def apply_strict_inventory_factuality_to_folders(
    project: Project, folder_names: list[str]
) -> FolderVoiceoverSettingsDocument:
    """Setzt factuality_mode NUR für die genannten Ordner auf
    ``strict_inventory_only`` — alle anderen Settings-Felder und alle nicht
    genannten Ordner bleiben unverändert. Speichert sofort.

    Wirft ValueError, wenn noch keine Settings existieren."""
    existing = load_folder_voiceover_settings(project)
    if existing is None:
        raise ValueError("Keine Folder Voice-over Settings vorhanden — bitte zuerst erstellen.")

    target = {name for name in folder_names if name}
    if not target:
        return existing

    updated_settings = [
        (
            setting.model_copy(update={"factuality_mode": FACTUALITY_MODE_STRICT_INVENTORY_ONLY})
            if setting.folder_name in target
            else setting
        )
        for setting in existing.settings
    ]
    new_document = existing.model_copy(update={"settings": updated_settings})
    return save_folder_voiceover_settings(project, new_document)


def enabled_settings(document: FolderVoiceoverSettingsDocument | None) -> list[FolderVoiceoverSetting]:
    if document is None:
        return []
    return [setting for setting in document.settings if setting.enabled]
