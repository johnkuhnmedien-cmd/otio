"""Schnittplan aus Voice-over, Zuordnung und Inventar erzeugen."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanSettings,
    EditPlanShot,
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
)
from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_FALLBACK_ORDER,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
    FALLBACK_SOURCE_LOCAL,
    FALLBACK_SOURCE_MISSING,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_edit_plan_path,
    get_folder_edit_plan_path,
)
from otio_app.services.edit_plan_rules import (
    apply_edit_plan_rules,
    export_rule_options,
    gemini_prompt_text,
    load_edit_plan_rules,
)
from otio_app.services.gemini_client import GeminiNotConfiguredError, plan_passage_assets
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.shot_timing import (
    TimedPart,
    allocate_time_by_text,
    shots_from_timed_parts,
)
from otio_app.services.timeline_plan_builder import (
    assign_global_timeline_positions,
    build_timeline_items_for_folder,
    shots_from_timeline_items,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping


class EditPlanLocationState(str, Enum):
    OPEN = "open"
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class EditPlanLocationStatus:
    folder_name: str
    state: EditPlanLocationState
    shot_count: int = 0


def resolve_edit_plan_location_state(
    folder_name: str,
    saved: EditPlanDocument | None,
    draft: EditPlanDocument | None = None,
) -> EditPlanLocationStatus:
    """Ermittelt den Status eines Ortes aus gespeicherter Datei und optionalem Entwurf."""
    effective = draft or saved
    if effective is None or not effective.shots:
        return EditPlanLocationStatus(folder_name=folder_name, state=EditPlanLocationState.OPEN)
    if effective.confirmed:
        return EditPlanLocationStatus(
            folder_name=folder_name,
            state=EditPlanLocationState.CONFIRMED,
            shot_count=len(effective.shots),
        )
    return EditPlanLocationStatus(
        folder_name=folder_name,
        state=EditPlanLocationState.DRAFT,
        shot_count=len(effective.shots),
    )


def get_mapped_folders(project: Project) -> list[str]:
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None:
        return []
    return sorted(
        {
            entry.folder
            for entry in mapping.entries
            if entry.folder and entry.confirmed
        }
    )


def load_voice_analysis(project: Project) -> VoiceAnalysisDocument:
    path = project.voice_analysis_path
    if not path.is_file():
        raise FileNotFoundError(
            f"Voice-over-Analyse fehlt: {path}. Bitte zuerst unter „① Analysen“ ausführen."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return VoiceAnalysisDocument.model_validate(payload)


def local_split_passage(text: str, splitters: list[str]) -> list[str]:
    """Einfache Text-Trennung als Fallback ohne Gemini."""
    remaining = text.strip()
    if not remaining:
        return []
    for splitter in splitters:
        if splitter in remaining:
            pieces = [piece.strip() for piece in remaining.split(splitter) if piece.strip()]
            if len(pieces) > 1:
                return pieces
    return [remaining]


def _validate_asset_path(asset_path: str | None, allowed_paths: set[str]) -> str | None:
    if not asset_path:
        return None
    if asset_path in allowed_paths:
        return asset_path
    return None


def _parts_from_gemini_or_local(
    passage_text: str,
    folder_name: str,
    assets: list[dict[str, str]],
    language: str,
    settings: EditPlanSettings,
    *,
    use_api: bool,
    gemini_model: str | None,
    gemini_prompt: str = "",
) -> list[dict]:
    if use_api:
        try:
            parts = plan_passage_assets(
                passage_text,
                folder_name,
                assets,
                language,
                model=gemini_model or settings.gemini_model,
                extra_instructions=gemini_prompt,
            )
            if parts:
                return parts
        except GeminiNotConfiguredError:
            raise

    texts = local_split_passage(passage_text, settings.text_splitters)
    return [
        {
            "text": piece,
            "motif": piece[:80],
            "asset_path": assets[0]["path"] if assets else None,
            "confidence": "low",
        }
        for piece in texts
    ]


def build_edit_plan(
    project: Project,
    settings: EditPlanSettings | None = None,
    *,
    use_api: bool = True,
    folder_names: list[str] | None = None,
) -> EditPlanDocument:
    """Erzeugt einen Schnittplan-Vorschlag für bestätigte Voice-over-Zuordnungen."""
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        raise ValueError(
            "Voice-over-Zuordnung fehlt oder ist nicht bestätigt. "
            "Bitte zuerst unter „② Zuordnung“ speichern."
        )

    voice_doc = load_voice_analysis(project)
    plan_settings = settings or EditPlanSettings(
        shot_min_sec=DEFAULT_SHOT_MIN_SEC,
        shot_max_sec=DEFAULT_SHOT_MAX_SEC,
        audio_offset_sec=DEFAULT_AUDIO_OFFSET_SEC,
        fallback_order=list(DEFAULT_FALLBACK_ORDER),
    )
    rules_doc = load_edit_plan_rules(project)
    gemini_prompt = gemini_prompt_text(rules_doc)
    trim_leading_sec = export_rule_options(rules_doc).trim_leading_sec
    plan_settings = plan_settings.model_copy(
        update={
            "video_head_trim_sec": trim_leading_sec,
            "video_head_trim_policy": "fixed_trim" if trim_leading_sec > 0 else "disabled",
            "voiceover_trim_policy": "disabled",
            "voiceover_trim_start_sec": 0.0,
            "voiceover_trim_end_sec": 0.0,
        }
    )

    voice_files = {entry.path: entry for entry in voice_doc.files}
    mapping_by_voice = {
        entry.voice_file: entry.folder
        for entry in mapping.entries
        if entry.folder and entry.confirmed
    }

    if folder_names is not None:
        allowed = set(folder_names)
        mapping_by_voice = {
            voice: folder
            for voice, folder in mapping_by_voice.items()
            if folder in allowed
        }

    primary_folder: str | None = None
    if folder_names is not None and len(folder_names) == 1:
        primary_folder = folder_names[0]

    shots: list[EditPlanShot] = []
    assets_by_folder: dict[str, list[str]] = {}
    assets_payload_by_folder: dict[str, list[dict[str, str]]] = {}
    for voice_path, folder_name in mapping_by_voice.items():
        voice_entry = voice_files.get(voice_path)
        if voice_entry is None:
            continue

        folder_inventory = load_folder_inventory(project, folder_name)
        asset_payload = [
            {"path": asset.path, "description": asset.description}
            for asset in folder_inventory.assets
            if asset.description or asset.path
        ]
        allowed_paths = {asset["path"] for asset in asset_payload}
        assets_by_folder[folder_name] = [asset["path"] for asset in asset_payload]
        assets_payload_by_folder[folder_name] = list(asset_payload)

        for segment in voice_entry.segments:
            if not segment.text.strip():
                continue
            raw_parts = _parts_from_gemini_or_local(
                segment.text,
                folder_name,
                asset_payload,
                voice_doc.language,
                plan_settings,
                use_api=use_api,
                gemini_model=plan_settings.gemini_model,
                gemini_prompt=gemini_prompt,
            )
            texts = [str(part.get("text", "")).strip() for part in raw_parts]
            time_ranges = allocate_time_by_text(
                segment.start_sec,
                segment.end_sec,
                texts,
            )
            timed_parts: list[TimedPart] = []
            for part, (start_sec, end_sec) in zip(raw_parts, time_ranges):
                asset_path = _validate_asset_path(
                    part.get("asset_path"),
                    allowed_paths,
                )
                timed_parts.append(
                    TimedPart(
                        text=str(part.get("text", "")).strip(),
                        motif=str(part.get("motif", "")).strip(),
                        start_sec=start_sec,
                        end_sec=end_sec,
                        asset_path=asset_path,
                        confidence=str(part.get("confidence")) if part.get("confidence") else None,
                    )
                )

            normalized = shots_from_timed_parts(
                timed_parts,
                min_sec=plan_settings.shot_min_sec,
                max_sec=plan_settings.shot_max_sec,
            )
            for part in normalized:
                source = FALLBACK_SOURCE_LOCAL if part.asset_path else FALLBACK_SOURCE_MISSING
                shots.append(
                    EditPlanShot(
                        voice_file=voice_path,
                        folder=folder_name,
                        voice_start_sec=part.start_sec,
                        voice_end_sec=part.end_sec,
                        duration_sec=max(0.0, part.end_sec - part.start_sec),
                        asset_path=part.asset_path,
                        asset_source=source,
                        motif=part.motif,
                        passage_text=part.text,
                        confidence=part.confidence,
                    )
                )

    shots = apply_edit_plan_rules(shots, rules_doc, assets_by_folder)

    timeline_items: list = []
    plan_errors: list[str] = []
    voiceover_plan = None
    item_counter = 1
    grouped: dict[tuple[str, str], list] = {}
    for shot in shots:
        if shot.section_outro:
            continue
        grouped.setdefault((shot.folder, shot.voice_file), []).append(shot)

    for (folder_name, voice_path), folder_shots in grouped.items():
        folder_shots.sort(key=lambda s: (s.voice_start_sec, s.voice_end_sec))
        section_items, section_voiceover, errors = build_timeline_items_for_folder(
            folder_shots,
            folder_name=folder_name,
            voice_file=voice_path,
            settings=plan_settings,
            folder_assets=assets_payload_by_folder.get(folder_name, []),
            trim_leading_sec=trim_leading_sec,
            item_index_start=item_counter,
        )
        plan_errors.extend(errors)
        timeline_items.extend(section_items)
        voiceover_plan = section_voiceover
        item_counter += len(section_items)

    if plan_errors and not timeline_items:
        raise ValueError("\n".join(plan_errors))

    shots = shots_from_timeline_items(timeline_items)

    return EditPlanDocument(
        project_id=project.id,
        folder_name=primary_folder,
        confirmed=False,
        settings=plan_settings,
        voiceover=voiceover_plan,
        shots=shots,
        timeline_items=timeline_items,
    )


def _read_edit_plan_file(path: Path) -> EditPlanDocument | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def migrate_legacy_edit_plan(project: Project) -> list[Path]:
    """Teilt eine alte edit_plan.json im Projektroot in pro-Ort-Dateien auf."""
    legacy_path = get_edit_plan_path(project.project_root_path)
    if not legacy_path.is_file():
        return []

    document = _read_edit_plan_file(legacy_path)
    if document is None or not document.shots:
        return []

    saved: list[Path] = []
    by_folder: dict[str, list] = {}
    for shot in document.shots:
        by_folder.setdefault(shot.folder, []).append(shot)

    for folder_name, shots in by_folder.items():
        target = get_folder_edit_plan_path(project.work_dir_path, folder_name)
        if target.is_file():
            continue
        folder_doc = document.model_copy(
            update={
                "folder_name": folder_name,
                "shots": shots,
            }
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(folder_doc.model_dump_json(indent=2), encoding="utf-8")
        saved.append(target)

    if saved:
        backup = legacy_path.with_suffix(".json.migrated")
        legacy_path.rename(backup)
    return saved


def list_saved_edit_plan_folders(project: Project) -> list[str]:
    """Ordnernamen mit gespeicherter Schnittplan-JSON (nach Migration)."""
    migrate_legacy_edit_plan(project)
    edit_plan_dir = get_edit_plan_dir(project.work_dir_path)
    if not edit_plan_dir.is_dir():
        return []

    folders: list[str] = []
    for path in sorted(edit_plan_dir.glob("*.json")):
        document = _read_edit_plan_file(path)
        if document is None:
            continue
        folder_name = document.folder_name or _folder_name_from_shots(document)
        if folder_name:
            folders.append(folder_name)
    return folders


def _folder_name_from_shots(document: EditPlanDocument) -> str | None:
    folders = {shot.folder for shot in document.shots if shot.folder}
    if len(folders) == 1:
        return next(iter(folders))
    return None


def load_edit_plan(project: Project, folder_name: str) -> EditPlanDocument | None:
    from otio_app.services.edit_plan_cache import load_edit_plan_cached

    return load_edit_plan_cached(project, folder_name)


def save_edit_plan(
    project: Project,
    document: EditPlanDocument,
    folder_name: str,
) -> Path:
    path = get_folder_edit_plan_path(project.work_dir_path, folder_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = document.model_copy(
        update={
            "project_id": project.id,
            "folder_name": folder_name,
        }
    )
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    from otio_app.services.edit_plan_cache import invalidate_edit_plan_cache

    invalidate_edit_plan_cache(project.id, folder_name)
    return path


def mapped_folders_have_confirmed_plans(
    project: Project,
    folder_names: list[str],
) -> bool:
    from otio_app.services.edit_plan_cache import mapped_folders_all_confirmed

    return mapped_folders_all_confirmed(project, folder_names)
