"""Schnittplan aus Voice-over, Zuordnung und Inventar erzeugen."""

from __future__ import annotations

import json
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
from otio_app.services.edit_plan_rules import apply_edit_plan_rules, load_edit_plan_rules
from otio_app.services.gemini_client import GeminiNotConfiguredError, plan_passage_assets
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.shot_timing import TimedPart, allocate_time_by_text, shots_from_timed_parts
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping


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
) -> list[dict]:
    if use_api:
        try:
            parts = plan_passage_assets(
                passage_text,
                folder_name,
                assets,
                language,
                model=gemini_model or settings.gemini_model,
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

    shots: list[EditPlanShot] = []
    assets_by_folder: dict[str, list[str]] = {}
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

    rules_doc = load_edit_plan_rules(project)
    shots = apply_edit_plan_rules(shots, rules_doc, assets_by_folder)

    return EditPlanDocument(
        project_id=project.id,
        confirmed=False,
        settings=plan_settings,
        shots=shots,
    )


def save_edit_plan(project: Project, document: EditPlanDocument) -> Path:
    path = project.edit_plan_path
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_edit_plan(project: Project) -> EditPlanDocument | None:
    path = project.edit_plan_path
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
