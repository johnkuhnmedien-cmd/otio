"""Voice-over-Autor: schreibt echte Doku-Prosa + strukturierte Satz-/Beat-zu-Asset-
Zuordnung pro Ordner (Phase 4).

Der Zuschauer sieht nur voiceover_text_full. sentence_items sind die interne
Grundlage für spätere Phasen (Schnittplan, TTS-Alignment, Supplement Requests).
Dieses Modul schreibt niemals EditPlanDocuments und löst nie OTIO-Export aus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from otio_app.defaults import (
    PAUSE_AFTER_CHOICES,
    VO_ERROR_INVALID_ASSET_ID,
    VO_ERROR_MISSING_ASSET_MAPPING,
    VO_ERROR_MISSING_SUPPLEMENT_REASON,
    VO_ERROR_WEAK_ASSET_MATCH,
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
    VOICEOVER_STATUS_CONFIRMED,
    VOICEOVER_STATUS_DRAFT,
    VOICEOVER_STATUS_NEEDS_USER_REVIEW,
    VOICEOVER_STATUS_NEEDS_VALIDATION,
    VOICEOVER_STATUS_PARTIAL,
    VOICEOVER_STATUS_PASS,
    WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_folder_voiceovers_confirmed_path,
    get_folder_voiceovers_draft_path,
)
from otio_app.services.gemini_client import _extract_json
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import is_image_media, is_video_media, probe_duration_seconds
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    apply_standard_word_target_to_enabled_settings,
    apply_standard_word_target_to_folder,
    build_default_folder_voiceover_settings,
    load_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_FOLDER_VOICEOVER,
    STATUS_FAIL,
    STATUS_PARSE_FAILED,
    STATUS_PASS,
    content_hash,
    content_hash_of_model,
    create_llm_run_dir,
    write_llm_manifest,
    write_llm_parsed_response,
    write_llm_prompt,
    write_llm_raw_response,
)
from otio_app.services.voiceover_generation.model_settings_service import resolve_llm_model_id
from otio_app.services.voiceover_generation.models import (
    ClosingVisualPlan,
    DramaturgyPlan,
    FolderVoiceoverDraft,
    FolderVoiceoverSetting,
    FolderVoiceoversDocument,
    LlmRunManifest,
    SentenceItem,
    SentenceSegmentAssetPlan,
    ValidationError,
    VisualAssetPlanHint,
    as_str_list,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.prompts import build_folder_voiceover_prompt
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile

__all__ = [
    "FolderVoiceoverBuildResult",
    "load_folder_voiceovers_draft",
    "save_folder_voiceovers_draft",
    "get_folder_voiceover_draft",
    "upsert_folder_voiceover_draft_item",
    "load_folder_voiceovers_confirmed",
    "save_folder_voiceovers_confirmed",
    "build_inventory_asset_context",
    "parse_folder_voiceover_response",
    "validate_asset_ids_against_inventory",
    "generate_folder_voiceover",
    "generate_all_folder_voiceovers",
    "regenerate_folder_voiceover_with_standard_word_target",
    "regenerate_all_folder_voiceovers_with_standard_word_target",
    "update_folder_voiceover_text",
    "compute_current_hashes",
    "is_draft_stale",
]

ProgressCallback = Callable[[str, int, int], None]

# Sentinel für optionale preloaded-Parameter (compute_current_hashes /
# is_draft_stale): style_profile/plan/settings_doc können LEGITIM None sein
# (z. B. noch kein Style Profile oder keine bestätigte Dramaturgie) — daher
# darf "nicht übergeben" nicht mit dem Wert None verwechselt werden.
_UNSET: Any = object()


def _is_video(path: str, media_type: str) -> bool:
    if media_type == "video":
        return True
    if media_type == "image":
        return False
    return bool(path) and is_video_media(Path(path))


def _is_image(path: str, media_type: str) -> bool:
    if media_type == "image":
        return True
    if media_type == "video":
        return False
    return bool(path) and is_image_media(Path(path))


def _cached_probe_duration(
    path: str, duration_cache: dict[str, float | None] | None
) -> float | None:
    """Ein-Aufruf-pro-Pfad-Cache für probe_duration_seconds, analog zum
    Muster in cut_plan_validator.py / cut_plan_edit_plan_bridge.py: rein
    In-Memory, lebt nur für die Dauer eines einzelnen Aufrufs/Renderings
    (z. B. eines Streamlit-Rerenders), keine Persistenz über Reruns hinweg —
    verhindert dutzende/hunderte redundante ffprobe-Subprozessaufrufe, wenn
    dieselbe Datei mehrfach im selben Durchlauf geprüft wird (z. B. einmal
    pro aktivem Ordner in der Folder-Voice-overs-Seite)."""
    if duration_cache is None:
        return probe_duration_seconds(Path(path))
    if path not in duration_cache:
        duration_cache[path] = probe_duration_seconds(Path(path))
    return duration_cache[path]


def build_inventory_asset_context(
    project: Project,
    folder_name: str,
    *,
    duration_cache: dict[str, float | None] | None = None,
) -> list[dict[str, Any]]:
    """Kompakte Asset-Liste für Prompt + Validierung: asset_id, media_type,
    duration_sec, description — asset_id ist die einzige verlässliche Referenz.

    duration_cache ist optional: ohne Angabe wird probe_duration_seconds wie
    bisher direkt aufgerufen (kein Verhaltensunterschied für bestehende
    Aufrufer)."""
    inventory = load_folder_inventory(project, folder_name)
    assets: list[dict[str, Any]] = []
    for asset in inventory.assets:
        if not asset.path:
            continue
        asset_id = asset.asset_id or asset_id_for_path(asset.path)
        is_video = _is_video(asset.path, asset.media_type)
        is_image = _is_image(asset.path, asset.media_type)
        media_type = "video" if is_video else ("image" if is_image else "")
        duration_sec = 0.0
        if is_video:
            probed = _cached_probe_duration(asset.path, duration_cache)
            duration_sec = probed if probed and probed > 0 else 0.0
        assets.append(
            {
                "asset_id": asset_id,
                "path": asset.path,
                "media_type": media_type,
                "duration_sec": duration_sec,
                "description": asset.description,
            }
        )
    return assets


def _previous_and_next_folder(plan: DramaturgyPlan, folder_name: str) -> tuple[str | None, str | None]:
    enabled = sorted(
        (entry for entry in plan.recommended_folder_order if entry.enabled),
        key=lambda entry: entry.order_index,
    )
    names = [entry.folder_name for entry in enabled]
    if folder_name not in names:
        return None, None
    index = names.index(folder_name)
    previous_name = names[index - 1] if index > 0 else None
    next_name = names[index + 1] if index < len(names) - 1 else None
    return previous_name, next_name


def parse_folder_voiceover_response(raw_text: str) -> dict[str, Any]:
    """Parst die Autor-/Correction-Antwort zu einem dict. Wirft ValueError bei
    ungültigem JSON oder falls die Antwort kein JSON-Objekt ist."""
    payload = _extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Voice-over-Antwort ist kein JSON-Objekt.")
    return payload


def _count_words(text: str) -> int:
    return len([word for word in text.split() if word.strip()])


def _parse_visual_asset_plan(raw: Any) -> VisualAssetPlanHint:
    """Defensiv — ein fehlendes/unbrauchbares visual_asset_plan darf NIE
    das Parsen des ganzen sentence_item scheitern lassen (siehe
    VisualAssetPlanHint-Docstring: neutrale Defaults sind immer gültig)."""
    if not isinstance(raw, dict):
        return VisualAssetPlanHint()
    try:
        preferred_cut_count = int(raw.get("preferred_cut_count", 1))
    except (TypeError, ValueError):
        preferred_cut_count = 1
    reuse_risk = str(raw.get("reuse_risk") or "").strip().lower()
    if reuse_risk not in ("", "low", "medium", "high"):
        reuse_risk = ""
    return VisualAssetPlanHint(
        preferred_cut_count=max(1, preferred_cut_count),
        reuse_risk=reuse_risk,
        needs_visual_variety=bool(raw.get("needs_visual_variety", False)),
        asset_strategy_reason=str(raw.get("asset_strategy_reason", "")),
        supplement_search_hint=str(raw.get("supplement_search_hint", "")),
    )


def _parse_closing_visual_plan(raw: Any) -> ClosingVisualPlan:
    """Defensiv — analog zu `_parse_visual_asset_plan`: ein fehlendes/
    unbrauchbares closing_visual_plan darf NIE das Parsen der gesamten
    Antwort scheitern lassen; neutrale Defaults (leerer Shot, kein
    Supplement) sind immer gültig für ältere Prompts/Drafts vor dieser
    Phase."""
    if not isinstance(raw, dict):
        return ClosingVisualPlan()
    return ClosingVisualPlan(
        visual_intent=str(raw.get("visual_intent", "")),
        primary_asset_id=str(raw.get("primary_asset_id") or "").strip(),
        backup_asset_ids=as_str_list(raw.get("backup_asset_ids")),
        second_backup_asset_ids=as_str_list(raw.get("second_backup_asset_ids")),
        needs_supplement_asset=bool(raw.get("needs_supplement_asset", False)),
        supplement_reason=str(raw.get("supplement_reason", "")),
        supplement_search_hint=str(raw.get("supplement_search_hint", "")),
        asset_strategy_reason=str(raw.get("asset_strategy_reason", "")),
    )


def _sanitize_closing_visual_plan(
    plan: ClosingVisualPlan, valid_asset_ids: set[str]
) -> ClosingVisualPlan:
    """Entfernt ausschließlich halluzinierte (nicht im Inventory
    vorhandene) Asset-IDs — analog zu `_sanitize_sentence_items`. Setzt
    NICHT automatisch needs_supplement_asset, damit ein echter Review-/
    Correction-Loop eine entstehende Lücke sichtbar korrigieren kann."""
    return plan.model_copy(
        update={
            "primary_asset_id": (
                plan.primary_asset_id if plan.primary_asset_id in valid_asset_ids else ""
            ),
            "backup_asset_ids": [
                asset_id for asset_id in plan.backup_asset_ids if asset_id in valid_asset_ids
            ],
            "second_backup_asset_ids": [
                asset_id for asset_id in plan.second_backup_asset_ids if asset_id in valid_asset_ids
            ],
        }
    )


def _parse_planned_segments(raw: Any) -> list[SentenceSegmentAssetPlan]:
    """Defensiv — ein fehlendes/unbrauchbares planned_segments (z. B. kein
    List-Typ, oder Einträge ohne dict-Form) darf NIE das Parsen des ganzen
    sentence_item scheitern lassen; unbrauchbare Einträge werden einfach
    übersprungen statt den ganzen Satz ungültig zu machen."""
    if not isinstance(raw, list):
        return []
    segments: list[SentenceSegmentAssetPlan] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        try:
            segment_order = int(entry.get("segment_order", index))
        except (TypeError, ValueError):
            segment_order = index
        segments.append(
            SentenceSegmentAssetPlan(
                segment_order=max(1, segment_order),
                primary_asset_id=str(entry.get("primary_asset_id") or "").strip(),
                backup_asset_ids=as_str_list(entry.get("backup_asset_ids")),
            )
        )
    return segments


def _parse_sentence_items(raw_items: Any) -> list[SentenceItem]:
    items: list[SentenceItem] = []
    if not isinstance(raw_items, list):
        return items
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        try:
            confidence = float(raw.get("asset_confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            duration = float(raw.get("estimated_duration_sec", 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        items.append(
            SentenceItem(
                sentence_id=str(raw.get("sentence_id") or f"sentence_{index:03d}"),
                beat_id=str(raw.get("beat_id", "")),
                text=str(raw.get("text", "")),
                visual_intent=str(raw.get("visual_intent", "")),
                primary_asset_id=str(raw.get("primary_asset_id") or "").strip(),
                backup_asset_ids=as_str_list(raw.get("backup_asset_ids")),
                second_backup_asset_ids=as_str_list(raw.get("second_backup_asset_ids")),
                asset_match_reason=str(raw.get("asset_match_reason", "")),
                asset_confidence=confidence,
                estimated_duration_sec=duration,
                must_show=as_str_list(raw.get("must_show")),
                avoid_showing=as_str_list(raw.get("avoid_showing")),
                needs_supplement_asset=bool(raw.get("needs_supplement_asset", False)),
                supplement_reason=str(raw.get("supplement_reason", "")),
                source_inventory_asset_ids_considered=as_str_list(
                    raw.get("source_inventory_asset_ids_considered")
                ),
                pause_after=_valid_pause_after(raw.get("pause_after")),
                visual_asset_plan=_parse_visual_asset_plan(raw.get("visual_asset_plan")),
                planned_segments=_parse_planned_segments(raw.get("planned_segments")),
            )
        )
    return items


def _valid_pause_after(value: Any) -> str:
    """Fällt auf 'kein Pause-Tag' (leerer String) zurück, falls das Modell
    einen nicht erlaubten Wert liefert — verhindert, dass beliebiger Text als
    ElevenLabs-Pause-Tag interpretiert werden könnte (siehe tts_text_builder)."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in PAUSE_AFTER_CHOICES else ""


def _sanitize_sentence_items(
    items: list[SentenceItem], valid_asset_ids: set[str]
) -> list[SentenceItem]:
    """Entfernt ausschließlich halluzinierte (nicht im Inventory vorhandene)
    Asset-IDs. Setzt NICHT automatisch needs_supplement_asset — das ist eine
    Validierungsfrage (siehe validate_asset_ids_against_inventory), damit der
    Review-/Correction-Loop echte Lücken sichtbar korrigieren kann."""
    sanitized: list[SentenceItem] = []
    for item in items:
        primary = item.primary_asset_id if item.primary_asset_id in valid_asset_ids else ""
        backups = [asset_id for asset_id in item.backup_asset_ids if asset_id in valid_asset_ids]
        second_backups = [
            asset_id for asset_id in item.second_backup_asset_ids if asset_id in valid_asset_ids
        ]
        considered = [
            asset_id
            for asset_id in item.source_inventory_asset_ids_considered
            if asset_id in valid_asset_ids
        ]
        sanitized_segments = [
            segment.model_copy(
                update={
                    "primary_asset_id": (
                        segment.primary_asset_id if segment.primary_asset_id in valid_asset_ids else ""
                    ),
                    "backup_asset_ids": [
                        asset_id for asset_id in segment.backup_asset_ids if asset_id in valid_asset_ids
                    ],
                }
            )
            for segment in item.planned_segments
        ]
        sanitized.append(
            item.model_copy(
                update={
                    "primary_asset_id": primary,
                    "backup_asset_ids": backups,
                    "second_backup_asset_ids": second_backups,
                    "source_inventory_asset_ids_considered": considered,
                    "planned_segments": sanitized_segments,
                }
            )
        )
    return sanitized


def validate_asset_ids_against_inventory(
    project: Project, folder_name: str, draft: FolderVoiceoverDraft
) -> list[ValidationError]:
    """Deterministische Asset-Prüfung (Phase 4 §7): läuft unabhängig von einer
    vorherigen Sanitisierung — prüft IMMER gegen das aktuelle Inventory."""
    inventory_assets = build_inventory_asset_context(project, folder_name)
    valid_ids = {asset["asset_id"] for asset in inventory_assets}
    errors: list[ValidationError] = []

    for item in draft.sentence_items:
        if item.primary_asset_id and item.primary_asset_id not in valid_ids:
            errors.append(
                ValidationError(
                    type=VO_ERROR_INVALID_ASSET_ID,
                    severity="BLOCKER",
                    folder_name=folder_name,
                    sentence_id=item.sentence_id,
                    message=f"primary_asset_id '{item.primary_asset_id}' existiert nicht im Inventory.",
                    fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                )
            )
        for backup_id in item.backup_asset_ids:
            if backup_id not in valid_ids:
                errors.append(
                    ValidationError(
                        type=VO_ERROR_INVALID_ASSET_ID,
                        severity="BLOCKER",
                        folder_name=folder_name,
                        sentence_id=item.sentence_id,
                        message=f"backup_asset_id '{backup_id}' existiert nicht im Inventory.",
                        fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                    )
                )
        for second_backup_id in item.second_backup_asset_ids:
            if second_backup_id not in valid_ids:
                errors.append(
                    ValidationError(
                        type=VO_ERROR_INVALID_ASSET_ID,
                        severity="BLOCKER",
                        folder_name=folder_name,
                        sentence_id=item.sentence_id,
                        message=(
                            f"second_backup_asset_id '{second_backup_id}' existiert nicht im "
                            "Inventory."
                        ),
                        fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                    )
                )
        for segment in item.planned_segments:
            if segment.primary_asset_id and segment.primary_asset_id not in valid_ids:
                errors.append(
                    ValidationError(
                        type=VO_ERROR_INVALID_ASSET_ID,
                        severity="BLOCKER",
                        folder_name=folder_name,
                        sentence_id=item.sentence_id,
                        message=(
                            f"planned_segments[segment_order={segment.segment_order}]."
                            f"primary_asset_id '{segment.primary_asset_id}' existiert nicht im "
                            "Inventory."
                        ),
                        fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                    )
                )
            for segment_backup_id in segment.backup_asset_ids:
                if segment_backup_id not in valid_ids:
                    errors.append(
                        ValidationError(
                            type=VO_ERROR_INVALID_ASSET_ID,
                            severity="BLOCKER",
                            folder_name=folder_name,
                            sentence_id=item.sentence_id,
                            message=(
                                f"planned_segments[segment_order={segment.segment_order}]."
                                f"backup_asset_ids enthält unbekannte ID '{segment_backup_id}'."
                            ),
                            fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                        )
                    )
        for considered_id in item.source_inventory_asset_ids_considered:
            if considered_id not in valid_ids:
                errors.append(
                    ValidationError(
                        type=VO_ERROR_INVALID_ASSET_ID,
                        severity="WARNING",
                        folder_name=folder_name,
                        sentence_id=item.sentence_id,
                        message=(
                            "source_inventory_asset_ids_considered enthält unbekannte "
                            f"ID '{considered_id}'."
                        ),
                        fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                    )
                )
        if not item.primary_asset_id and not item.needs_supplement_asset:
            errors.append(
                ValidationError(
                    type=VO_ERROR_MISSING_ASSET_MAPPING,
                    severity="BLOCKER",
                    folder_name=folder_name,
                    sentence_id=item.sentence_id,
                    message="Kein primary_asset_id und needs_supplement_asset ist nicht gesetzt.",
                    fix_hint=(
                        "Entweder ein passendes Asset zuordnen oder "
                        "needs_supplement_asset=true mit Begründung setzen."
                    ),
                )
            )
        if item.needs_supplement_asset and not item.supplement_reason.strip():
            errors.append(
                ValidationError(
                    type=VO_ERROR_MISSING_SUPPLEMENT_REASON,
                    severity="BLOCKER",
                    folder_name=folder_name,
                    sentence_id=item.sentence_id,
                    message="needs_supplement_asset ist gesetzt, aber supplement_reason fehlt.",
                    fix_hint="Kurze Begründung ergänzen, welches Motiv fehlt.",
                )
            )
        if not (0.0 <= item.asset_confidence <= 1.0):
            errors.append(
                ValidationError(
                    type=VO_ERROR_WEAK_ASSET_MATCH,
                    severity="WARNING",
                    folder_name=folder_name,
                    sentence_id=item.sentence_id,
                    message=f"asset_confidence {item.asset_confidence} liegt außerhalb von 0.0-1.0.",
                    fix_hint="asset_confidence auf einen Wert zwischen 0.0 und 1.0 setzen.",
                )
            )
        elif item.primary_asset_id and item.asset_confidence < WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD:
            errors.append(
                ValidationError(
                    type=VO_ERROR_WEAK_ASSET_MATCH,
                    severity="WARNING",
                    folder_name=folder_name,
                    sentence_id=item.sentence_id,
                    message=(
                        f"asset_confidence {item.asset_confidence} liegt unter dem "
                        f"Schwellenwert {WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD}."
                    ),
                    fix_hint="Besseres Asset suchen oder needs_supplement_asset setzen.",
                )
            )

    errors.extend(_validate_closing_visual_plan_asset_ids(folder_name, draft.closing_visual_plan, valid_ids))
    return errors


_CLOSING_VISUAL_PLAN_SENTENCE_ID = "closing"


def _validate_closing_visual_plan_asset_ids(
    folder_name: str, plan: ClosingVisualPlan, valid_ids: set[str]
) -> list[ValidationError]:
    """Dieselbe deterministische Asset-Prüfung wie für sentence_items,
    angewendet auf den Closing-Shot (siehe ClosingVisualPlan) — nutzt
    `sentence_id="closing"` als Marker, da kein echter Satz existiert."""
    errors: list[ValidationError] = []
    if plan.primary_asset_id and plan.primary_asset_id not in valid_ids:
        errors.append(
            ValidationError(
                type=VO_ERROR_INVALID_ASSET_ID,
                severity="BLOCKER",
                folder_name=folder_name,
                sentence_id=_CLOSING_VISUAL_PLAN_SENTENCE_ID,
                message=f"closing_visual_plan.primary_asset_id '{plan.primary_asset_id}' existiert nicht im Inventory.",
                fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
            )
        )
    for backup_id in plan.backup_asset_ids:
        if backup_id not in valid_ids:
            errors.append(
                ValidationError(
                    type=VO_ERROR_INVALID_ASSET_ID,
                    severity="BLOCKER",
                    folder_name=folder_name,
                    sentence_id=_CLOSING_VISUAL_PLAN_SENTENCE_ID,
                    message=f"closing_visual_plan.backup_asset_id '{backup_id}' existiert nicht im Inventory.",
                    fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                )
            )
    for second_backup_id in plan.second_backup_asset_ids:
        if second_backup_id not in valid_ids:
            errors.append(
                ValidationError(
                    type=VO_ERROR_INVALID_ASSET_ID,
                    severity="BLOCKER",
                    folder_name=folder_name,
                    sentence_id=_CLOSING_VISUAL_PLAN_SENTENCE_ID,
                    message=(
                        "closing_visual_plan.second_backup_asset_id "
                        f"'{second_backup_id}' existiert nicht im Inventory."
                    ),
                    fix_hint="Nur asset_id-Werte aus dem bereitgestellten Inventory verwenden.",
                )
            )
    if plan.needs_supplement_asset and not plan.supplement_reason.strip():
        errors.append(
            ValidationError(
                type=VO_ERROR_MISSING_SUPPLEMENT_REASON,
                severity="BLOCKER",
                folder_name=folder_name,
                sentence_id=_CLOSING_VISUAL_PLAN_SENTENCE_ID,
                message="closing_visual_plan.needs_supplement_asset ist gesetzt, aber supplement_reason fehlt.",
                fix_hint="Kurze Begründung ergänzen, welches Abschlussmotiv fehlt.",
            )
        )
    return errors


def _aggregate_status(items: list[FolderVoiceoverDraft]) -> str:
    if not items:
        return VOICEOVER_STATUS_DRAFT
    statuses = {item.status for item in items}
    if statuses == {VOICEOVER_STATUS_CONFIRMED}:
        return VOICEOVER_STATUS_CONFIRMED
    if VOICEOVER_STATUS_NEEDS_USER_REVIEW in statuses:
        return VOICEOVER_STATUS_NEEDS_USER_REVIEW
    if statuses <= {VOICEOVER_STATUS_PASS, VOICEOVER_STATUS_CONFIRMED}:
        return VOICEOVER_STATUS_PASS
    if len(statuses) > 1:
        return VOICEOVER_STATUS_PARTIAL
    return next(iter(statuses))


def load_folder_voiceovers_draft(project: Project) -> FolderVoiceoversDocument:
    path = get_folder_voiceovers_draft_path(project.work_dir_path)
    if not path.is_file():
        return FolderVoiceoversDocument(project_id=project.id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FolderVoiceoversDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return FolderVoiceoversDocument(project_id=project.id)


def save_folder_voiceovers_draft(
    project: Project, document: FolderVoiceoversDocument
) -> FolderVoiceoversDocument:
    normalized = document.model_copy(
        update={"project_id": project.id, "status": _aggregate_status(document.items)}
    )
    path = get_folder_voiceovers_draft_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def get_folder_voiceover_draft(project: Project, folder_name: str) -> FolderVoiceoverDraft | None:
    document = load_folder_voiceovers_draft(project)
    return next((item for item in document.items if item.folder_name == folder_name), None)


def upsert_folder_voiceover_draft_item(
    project: Project, item: FolderVoiceoverDraft
) -> FolderVoiceoversDocument:
    """Fügt einen Ordner-Entwurf ein/ersetzt ihn — alle anderen Ordner im
    Dokument bleiben unverändert erhalten."""
    document = load_folder_voiceovers_draft(project)
    items = [existing for existing in document.items if existing.folder_name != item.folder_name]
    items.append(item)
    updated_document = document.model_copy(update={"items": items})
    return save_folder_voiceovers_draft(project, updated_document)


def load_folder_voiceovers_confirmed(project: Project) -> FolderVoiceoversDocument:
    path = get_folder_voiceovers_confirmed_path(project.work_dir_path)
    if not path.is_file():
        return FolderVoiceoversDocument(project_id=project.id, status=VOICEOVER_STATUS_CONFIRMED)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FolderVoiceoversDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return FolderVoiceoversDocument(project_id=project.id, status=VOICEOVER_STATUS_CONFIRMED)


def save_folder_voiceovers_confirmed(
    project: Project, document: FolderVoiceoversDocument
) -> FolderVoiceoversDocument:
    normalized = document.model_copy(update={"project_id": project.id})
    path = get_folder_voiceovers_confirmed_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def compute_current_hashes(
    project: Project,
    folder_name: str,
    *,
    project_brief: Any = _UNSET,
    style_profile: Any = _UNSET,
    plan: DramaturgyPlan | None | Any = _UNSET,
    settings_doc: Any = _UNSET,
    duration_cache: dict[str, float | None] | None = None,
) -> dict[str, str]:
    """Aktuelle Hashes der fünf Staleness-Quellen (Phase 4 §13).

    project_brief/style_profile/plan/settings_doc können optional
    vorab geladen und durchgereicht werden (z. B. einmal pro Seiten-Rendering
    statt einmal pro Ordner) — ohne Angabe (Standardwert _UNSET) wird wie
    bisher jeweils frisch von der Platte geladen, kein Verhaltensunterschied
    für bestehende Aufrufer. Explizit übergebenes None bleibt None (z. B.
    'kein Style Profile vorhanden') statt automatisch neu geladen zu werden.
    duration_cache siehe build_inventory_asset_context."""
    if project_brief is _UNSET:
        project_brief = load_project_brief(project)
    if style_profile is _UNSET:
        style_profile = load_style_profile(project)
    if plan is _UNSET:
        plan = load_confirmed_dramaturgy(project)
    if settings_doc is _UNSET:
        settings_doc = load_folder_voiceover_settings(project)
    setting = (
        next((s for s in settings_doc.settings if s.folder_name == folder_name), None)
        if settings_doc is not None
        else None
    )
    inventory_assets = build_inventory_asset_context(
        project, folder_name, duration_cache=duration_cache
    )

    return {
        "project_brief_hash": content_hash_of_model(project_brief),
        "style_profile_hash": content_hash_of_model(style_profile),
        "dramaturgy_hash": content_hash_of_model(plan),
        "settings_hash": content_hash_of_model(setting),
        "inventory_hash": content_hash(json.dumps(inventory_assets, sort_keys=True)),
    }


def is_draft_stale(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    *,
    project_brief: Any = _UNSET,
    style_profile: Any = _UNSET,
    plan: DramaturgyPlan | None | Any = _UNSET,
    settings_doc: Any = _UNSET,
    duration_cache: dict[str, float | None] | None = None,
) -> bool:
    """True, wenn sich mindestens eine der fünf Quellen seit der Erzeugung
    geändert hat. Löscht/überschreibt nichts — nur eine UI-Warnung (§13).

    Optionale Parameter siehe compute_current_hashes — erlauben es Aufrufern
    (z. B. der Drafts-Liste mit vielen Ordnern), teure Operationen (Datei-
    Ladevorgänge, ffprobe-Aufrufe) über mehrere is_draft_stale-Aufrufe hinweg
    innerhalb EINES Renderings zu teilen statt sie pro Ordner zu wiederholen."""
    current = compute_current_hashes(
        project,
        folder_name,
        project_brief=project_brief,
        style_profile=style_profile,
        plan=plan,
        settings_doc=settings_doc,
        duration_cache=duration_cache,
    )
    checks = (
        ("project_brief_hash", draft.project_brief_hash),
        ("style_profile_hash", draft.style_profile_hash),
        ("dramaturgy_hash", draft.dramaturgy_hash),
        ("settings_hash", draft.settings_hash),
        ("inventory_hash", draft.inventory_hash),
    )
    return any(stored and stored != current[key] for key, stored in checks)


@dataclass
class FolderVoiceoverBuildResult:
    status: str  # PASS | FAIL | PARSE_FAILED (Generierungs-Status, nicht Review-Status)
    draft: FolderVoiceoverDraft | None
    error: str | None
    llm_run_id: str
    provider: str
    model: str


def generate_folder_voiceover(
    project: Project,
    folder_name: str,
    *,
    provider: str,
    model: str,
) -> FolderVoiceoverBuildResult:
    """Erzeugt den Voice-over-Entwurf für EINEN aktiven Ordner.

    Voraussetzung: Der Ordner ist Teil der bestätigten Dramaturgie und dort
    enabled=true. Überschreibt einen bestehenden Draft nur bei Erfolg."""
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        raise ValueError("Keine bestätigte Dramaturgie vorhanden.")
    entry = next(
        (e for e in plan.recommended_folder_order if e.folder_name == folder_name), None
    )
    if entry is None:
        raise ValueError(f"Ordner '{folder_name}' ist nicht Teil der bestätigten Dramaturgie.")
    if not entry.enabled:
        raise ValueError(f"Ordner '{folder_name}' ist in der Dramaturgie deaktiviert.")

    settings_doc = load_folder_voiceover_settings(project)
    if settings_doc is None:
        settings_doc = build_default_folder_voiceover_settings(project)
    setting = next((s for s in settings_doc.settings if s.folder_name == folder_name), None)
    if setting is None:
        setting = FolderVoiceoverSetting(
            folder_name=folder_name,
            order_index=entry.order_index,
            enabled=entry.enabled,
            dramaturgy_role=entry.dramaturgy_role,
            target_words=entry.recommended_word_count or VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
            min_words=entry.recommended_min_words or VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
            max_words=entry.recommended_max_words or VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
        )

    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    previous_name, next_name = _previous_and_next_folder(plan, folder_name)
    inventory_assets = build_inventory_asset_context(project, folder_name)

    run_id, run_dir = create_llm_run_dir(project, STAGE_FOLDER_VOICEOVER)
    prompt = build_folder_voiceover_prompt(
        project_brief=project_brief,
        style_profile=style_profile,
        dramaturgy_entry=entry,
        setting=setting,
        previous_folder_name=previous_name,
        next_folder_name=next_name,
        inventory_assets=inventory_assets,
    )
    prompt_hash = content_hash(prompt)
    write_llm_prompt(run_dir, prompt)

    model_id = resolve_llm_model_id(provider, model)

    try:
        llm_response = generate_plan_text_with_metadata(prompt=prompt, model=model_id)
    except Exception as exc:  # noqa: BLE001 — jeder LLM-/SDK-/Netzwerkfehler soll als
        # kontrollierter FAIL-Status zurückkommen statt die Streamlit-Seite crashen zu
        # lassen (nicht nur der eng gefasste PlanLlmNotConfiguredError-Fall).
        write_llm_raw_response(run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model)
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_FOLDER_VOICEOVER,
                provider=provider,
                model=model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return FolderVoiceoverBuildResult(
            status=STATUS_FAIL, draft=None, error=str(exc), llm_run_id=run_id,
            provider=provider, model=model,
        )

    write_llm_raw_response(
        run_dir,
        raw_text=llm_response.raw_text,
        provider=llm_response.provider,
        model=llm_response.model,
        latency_ms=llm_response.latency_ms,
        token_usage=llm_response.token_usage,
    )

    try:
        payload = parse_folder_voiceover_response(llm_response.raw_text)
    except (ValueError, TypeError) as exc:
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_FOLDER_VOICEOVER,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_PARSE_FAILED,
                latency_ms=llm_response.latency_ms,
                token_usage=llm_response.token_usage,
            ),
        )
        return FolderVoiceoverBuildResult(
            status=STATUS_PARSE_FAILED, draft=None, error=str(exc), llm_run_id=run_id,
            provider=llm_response.provider, model=llm_response.model,
        )

    valid_asset_ids = {asset["asset_id"] for asset in inventory_assets}
    sentence_items = _sanitize_sentence_items(
        _parse_sentence_items(payload.get("sentence_items", [])), valid_asset_ids
    )
    closing_visual_plan = _sanitize_closing_visual_plan(
        _parse_closing_visual_plan(payload.get("closing_visual_plan")), valid_asset_ids
    )
    voiceover_text_full = str(payload.get("voiceover_text_full", ""))

    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name=folder_name,
        order_index=entry.order_index,
        language=project_brief.language,
        target_words=setting.target_words,
        min_words=setting.min_words,
        max_words=setting.max_words,
        voiceover_text_full=voiceover_text_full,
        word_count=_count_words(voiceover_text_full),
        sentence_items=sentence_items,
        closing_visual_plan=closing_visual_plan,
        transition_from_previous_used=bool(payload.get("transition_from_previous_used", False)),
        transition_to_next_used=bool(payload.get("transition_to_next_used", False)),
        callback_to_previous_used=bool(payload.get("callback_to_previous_used", False)),
        contrast_or_commonality_used=bool(payload.get("contrast_or_commonality_used", False)),
        used_asset_evidence=[item.primary_asset_id for item in sentence_items if item.primary_asset_id],
        author_run_id=run_id,
        status=VOICEOVER_STATUS_DRAFT,
        risks=as_str_list(payload.get("risks")),
        project_brief_hash=content_hash_of_model(project_brief),
        style_profile_hash=content_hash_of_model(style_profile),
        dramaturgy_hash=content_hash_of_model(plan),
        settings_hash=content_hash_of_model(setting),
        inventory_hash=content_hash(json.dumps(inventory_assets, sort_keys=True)),
    )
    upsert_folder_voiceover_draft_item(project, draft)

    write_llm_parsed_response(run_dir, draft.model_dump(mode="json"))
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id,
            stage=STAGE_FOLDER_VOICEOVER,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_hash=prompt_hash,
            status=STATUS_PASS,
            latency_ms=llm_response.latency_ms,
            token_usage=llm_response.token_usage,
        ),
    )

    return FolderVoiceoverBuildResult(
        status=STATUS_PASS, draft=draft, error=None, llm_run_id=run_id,
        provider=llm_response.provider, model=llm_response.model,
    )


def generate_all_folder_voiceovers(
    project: Project,
    *,
    provider: str,
    model: str,
    progress_callback: ProgressCallback | None = None,
) -> list[FolderVoiceoverBuildResult]:
    """Erzeugt Voice-overs für alle aktiven Ordner SEQUENZIELL (bessere Logs,
    einfachere Fehleranalyse — analog zum Modellvergleichs-Workflow)."""
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        raise ValueError("Keine bestätigte Dramaturgie vorhanden.")
    enabled_folders = [
        entry.folder_name
        for entry in sorted(plan.recommended_folder_order, key=lambda entry: entry.order_index)
        if entry.enabled
    ]

    results: list[FolderVoiceoverBuildResult] = []
    total = len(enabled_folders)
    for index, folder_name in enumerate(enabled_folders, start=1):
        if progress_callback is not None:
            progress_callback(folder_name, index, total)
        try:
            result = generate_folder_voiceover(project, folder_name, provider=provider, model=model)
        except ValueError as exc:
            result = FolderVoiceoverBuildResult(
                status=STATUS_FAIL, draft=None, error=str(exc), llm_run_id="",
                provider=provider, model=model,
            )
        results.append(result)
    return results


def regenerate_folder_voiceover_with_standard_word_target(
    project: Project, folder_name: str, *, provider: str, model: str
) -> FolderVoiceoverBuildResult:
    """Phase 6 (Asset-bewusste Cut-Plan-Vorbereitung): Komfort-Aktion für
    bereits VOR Phase 1 generierte Ordner — kombiniert zwei bereits
    einzeln getestete, bestehende Schritte in EINEM Klick:
      1) hebt target_words/min_words/max_words NUR für diesen Ordner auf
         den neuen Standard (apply_standard_word_target_to_folder);
      2) generiert das Voice-over für diesen Ordner NEU
         (generate_folder_voiceover) — nutzt dabei automatisch den bereits
         asset-bewussten Prompt (Phase 3/4), OHNE eigene neue Prompt-Logik.

    Wirft ValueError, wenn Schritt 1 fehlschlägt (z. B. keine Settings
    vorhanden) — in diesem Fall wird NICHT generiert und der bestehende
    Draft bleibt unangetastet."""
    apply_standard_word_target_to_folder(project, folder_name)
    return generate_folder_voiceover(project, folder_name, provider=provider, model=model)


def regenerate_all_folder_voiceovers_with_standard_word_target(
    project: Project,
    *,
    provider: str,
    model: str,
    progress_callback: ProgressCallback | None = None,
) -> list[FolderVoiceoverBuildResult]:
    """Bulk-Variante von regenerate_folder_voiceover_with_standard_word_target
    für ALLE aktivierten Ordner — hebt zuerst alle aktivierten Ordner auf den
    neuen Wortanzahl-Standard (apply_standard_word_target_to_enabled_settings)
    und generiert danach SEQUENZIELL neu (generate_all_folder_voiceovers),
    analog zu 'Alle aktiven Folder Voice-overs generieren'.

    Wirft ValueError, wenn Schritt 1 fehlschlägt — in diesem Fall wird NICHTS
    neu generiert."""
    apply_standard_word_target_to_enabled_settings(project)
    return generate_all_folder_voiceovers(
        project, provider=provider, model=model, progress_callback=progress_callback
    )


def update_folder_voiceover_text(
    project: Project, folder_name: str, new_text: str
) -> FolderVoiceoverDraft:
    """Manuelle Textbearbeitung: aktualisiert word_count und setzt den Status
    auf NEEDS_VALIDATION (Phase 4 §11) — löst KEINE Neuvalidierung selbst aus.

    Ist new_text IDENTISCH zum bereits gespeicherten Text, wird NICHTS
    verändert (Status/confirmed_at bleiben unangetastet) — sonst würde ein
    erneutes 'Speichern' unveränderten Texts eine bereits erteilte
    Bestätigung stillschweigend zurücksetzen. Besonders relevant bei
    'Alle Texte speichern', das für jeden Ordner speichert, unabhängig davon,
    ob dort überhaupt etwas bearbeitet wurde (Nutzerfeedback Juli 2026)."""
    draft = get_folder_voiceover_draft(project, folder_name)
    if draft is None:
        raise ValueError(f"Kein Voice-over-Entwurf für '{folder_name}' vorhanden.")
    if new_text == draft.voiceover_text_full:
        return draft
    updated = draft.model_copy(
        update={
            "voiceover_text_full": new_text,
            "word_count": _count_words(new_text),
            "status": VOICEOVER_STATUS_NEEDS_VALIDATION,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_folder_voiceover_draft_item(project, updated)
    return updated
