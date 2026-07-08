"""Final Output: aggregiert alle bestätigten Artefakte zu einem finalen
Voice-over-Projektplan (Phase 7).

confirmed_voiceover_project_plan.json ist die redaktionelle Quelle der
Wahrheit für die spätere Schnittplan-Pipeline. Dieses Modul erzeugt KEINEN
Schnittplan, KEINEN OTIO-Export und plant NICHTS neu von Gemini/Claude — es
liest ausschließlich bereits bestätigte Artefakte und aggregiert sie.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from otio_app.defaults import (
    AUDIO_STATUS_FAILED,
    AUDIO_STATUS_MISSING,
    AUDIO_STATUS_READY_WITH_WARNINGS,
    AUDIO_STATUS_STALE,
    PLAN_STATUS_AUDIO_PENDING,
    PLAN_STATUS_AUDIO_READY,
    PLAN_STATUS_NEEDS_REVIEW,
    PLAN_STATUS_READY_FOR_CUT,
    PLAN_STATUS_TEXT_READY,
    VO_ERROR_MISSING_ASSET_MAPPING,
    VO_ERROR_MISSING_SUPPLEMENT_REASON,
    VO_ERROR_WEAK_ASSET_MATCH,
    VOICEOVER_STATUS_NEEDS_USER_REVIEW,
    WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_confirmed_voiceover_project_plan_path,
    get_dramaturgy_plan_confirmed_path,
    get_folder_alignment_path,
    get_folder_voiceovers_confirmed_path,
    get_intro_alignment_path,
    get_intro_hook_confirmed_path,
    get_project_brief_path,
    get_voiceover_audio_manifest_path,
    get_voiceover_project_plan_csv_path,
    get_voiceover_project_plan_json_path,
    get_voiceover_project_plan_md_path,
    get_voiceover_style_profile_path,
)
from otio_app.services.voiceover_generation.audio_alignment_service import load_alignment
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.intro_hook_service import (
    get_active_dramaturgy_folder_names,
    load_confirmed_intro_hook,
    missing_confirmed_folder_names,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.models import (
    ConfirmedFolderPlanItem,
    ConfirmedIntroHook,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    DramaturgyFolderEntry,
    FolderVoiceoverDraft,
    ProjectPlanReadiness,
    ReadinessError,
    VoiceoverAudioManifest,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.services.voiceover_generation.tts_orchestration_service import load_audio_manifest
from otio_app.services.voiceover_generation.voiceover_author_service import (
    load_folder_voiceovers_confirmed,
)
from otio_app.services.voiceover_generation.voiceover_review_service import load_validation_reports

__all__ = [
    "build_confirmed_voiceover_project_plan",
    "load_confirmed_voiceover_project_plan",
    "save_confirmed_voiceover_project_plan",
    "export_voiceover_project_plan_json",
    "export_voiceover_project_plan_markdown",
    "export_voiceover_project_plan_csv",
    "validate_voiceover_project_plan_readiness",
    "is_project_plan_stale",
]


def _lookup_audio_item(manifest: VoiceoverAudioManifest, scope: str, folder_name: str):
    for item in manifest.items:
        if item.scope != scope:
            continue
        if scope == "intro":
            return item
        if item.folder_name == folder_name:
            return item
    return None


def _item_readiness_status(audio_status: str, has_alignment: bool, *, allow_blocked: bool) -> str:
    if audio_status == AUDIO_STATUS_MISSING:
        return "MISSING_AUDIO"
    if audio_status == AUDIO_STATUS_FAILED:
        return "BLOCKED" if allow_blocked else "WARNING"
    if audio_status == AUDIO_STATUS_STALE:
        return "STALE_AUDIO"
    if not has_alignment:
        return "MISSING_ALIGNMENT"
    if audio_status == AUDIO_STATUS_READY_WITH_WARNINGS:
        return "WARNING"
    return "READY"


def _build_intro_plan_item(
    project: Project, confirmed_hook: ConfirmedIntroHook | None, audio_manifest: VoiceoverAudioManifest
) -> ConfirmedIntroPlanItem:
    if confirmed_hook is None:
        return ConfirmedIntroPlanItem()

    audio_item = _lookup_audio_item(audio_manifest, "intro", "")
    audio_status = audio_item.status if audio_item is not None else AUDIO_STATUS_MISSING
    audio_path = audio_item.audio_path if audio_item is not None else ""
    audio_duration_sec = audio_item.audio_duration_sec if audio_item is not None else 0.0

    alignment = load_alignment(project, "intro", "")
    alignment_path = str(get_intro_alignment_path(project.work_dir_path)) if alignment is not None else ""
    alignment_items = alignment.items if alignment is not None else []

    readiness_status = _item_readiness_status(audio_status, bool(alignment_items), allow_blocked=False)

    return ConfirmedIntroPlanItem(
        hook_text=confirmed_hook.hook_text,
        word_count=confirmed_hook.word_count,
        hook_type=confirmed_hook.hook_type,
        used_folders=list(confirmed_hook.used_folders),
        used_sentence_ids=list(confirmed_hook.used_sentence_ids),
        visual_beats=list(confirmed_hook.visual_beats),
        audio_path=audio_path,
        audio_duration_sec=audio_duration_sec,
        alignment_path=alignment_path,
        alignment_items=alignment_items,
        audio_status=audio_status,
        readiness_status=readiness_status,
    )


def _build_folder_plan_item(
    project: Project,
    entry: DramaturgyFolderEntry,
    draft: FolderVoiceoverDraft,
    audio_manifest: VoiceoverAudioManifest,
    validation_reports,
) -> ConfirmedFolderPlanItem:
    audio_item = _lookup_audio_item(audio_manifest, "folder", entry.folder_name)
    audio_status = audio_item.status if audio_item is not None else AUDIO_STATUS_MISSING
    audio_path = audio_item.audio_path if audio_item is not None else ""
    audio_duration_sec = audio_item.audio_duration_sec if audio_item is not None else 0.0

    alignment = load_alignment(project, "folder", entry.folder_name)
    alignment_path = (
        str(get_folder_alignment_path(project.work_dir_path, entry.order_index, entry.folder_name))
        if alignment is not None
        else ""
    )
    alignment_items = alignment.items if alignment is not None else []

    report = validation_reports.reports.get(entry.folder_name)
    validation_status = report.status if report is not None else "UNKNOWN"

    missing_mapping = any(
        not item.primary_asset_id and not item.needs_supplement_asset for item in draft.sentence_items
    )
    missing_supplement_reason = any(
        item.needs_supplement_asset and not item.supplement_reason.strip() for item in draft.sentence_items
    )
    weak_match = any(
        item.primary_asset_id and item.asset_confidence < WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD
        for item in draft.sentence_items
    )
    if missing_mapping:
        asset_mapping_status = "BLOCKED"
    elif missing_supplement_reason or weak_match:
        asset_mapping_status = "WARNINGS"
    else:
        asset_mapping_status = "PASS"

    readiness_status = _item_readiness_status(audio_status, bool(alignment_items), allow_blocked=True)
    if readiness_status == "READY" and asset_mapping_status == "BLOCKED":
        readiness_status = "BLOCKED"
    elif readiness_status == "READY" and asset_mapping_status == "WARNINGS":
        readiness_status = "WARNING"

    return ConfirmedFolderPlanItem(
        folder_name=entry.folder_name,
        order_index=entry.order_index,
        dramaturgy_role=entry.dramaturgy_role,
        enabled=entry.enabled,
        voiceover_text_full=draft.voiceover_text_full,
        word_count=draft.word_count,
        target_words=draft.target_words,
        min_words=draft.min_words,
        max_words=draft.max_words,
        sentence_items=list(draft.sentence_items),
        audio_path=audio_path,
        audio_duration_sec=audio_duration_sec,
        alignment_path=alignment_path,
        alignment_items=alignment_items,
        audio_status=audio_status,
        validation_status=validation_status,
        asset_mapping_status=asset_mapping_status,
        readiness_status=readiness_status,
    )


def _build_source_artifacts(
    project: Project, project_brief, style_profile, dramaturgy_plan, confirmed_folders_doc, confirmed_hook, audio_manifest
) -> dict[str, Any]:
    return {
        "project_brief_path": str(get_project_brief_path(project.work_dir_path)),
        "style_profile_path": str(get_voiceover_style_profile_path(project.work_dir_path)),
        "dramaturgy_confirmed_path": str(get_dramaturgy_plan_confirmed_path(project.work_dir_path)),
        "folder_voiceovers_confirmed_path": str(get_folder_voiceovers_confirmed_path(project.work_dir_path)),
        "intro_hook_confirmed_path": str(get_intro_hook_confirmed_path(project.work_dir_path)),
        "audio_manifest_path": str(get_voiceover_audio_manifest_path(project.work_dir_path)),
        "created_from_hashes": {
            "project_brief": content_hash_of_model(project_brief),
            "style_profile": content_hash_of_model(style_profile),
            "dramaturgy": content_hash_of_model(dramaturgy_plan),
            "folder_voiceovers": content_hash_of_model(confirmed_folders_doc),
            "intro_hook": content_hash_of_model(confirmed_hook),
            "audio_manifest": content_hash_of_model(audio_manifest),
        },
    }


def build_confirmed_voiceover_project_plan(project: Project) -> ConfirmedVoiceoverProjectPlan:
    """Baut den finalen Plan aus allen bestätigten Artefakten. Reine Funktion —
    speichert NICHTS (siehe save_confirmed_voiceover_project_plan)."""
    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    dramaturgy_plan = load_confirmed_dramaturgy(project)
    confirmed_folders_doc = load_folder_voiceovers_confirmed(project)
    confirmed_hook = load_confirmed_intro_hook(project)
    audio_manifest = load_audio_manifest(project)
    validation_reports = load_validation_reports(project)

    intro_item = _build_intro_plan_item(project, confirmed_hook, audio_manifest)

    folder_items: list[ConfirmedFolderPlanItem] = []
    if dramaturgy_plan is not None:
        for entry in sorted(dramaturgy_plan.recommended_folder_order, key=lambda e: e.order_index):
            if not entry.enabled:
                continue  # deaktivierte Ordner werden NIE als aktiv im Plan geführt
            draft = next(
                (item for item in confirmed_folders_doc.items if item.folder_name == entry.folder_name), None
            )
            if draft is None:
                continue  # noch nicht bestätigt -> fehlt im Plan, wird als Blocker vermerkt
            folder_items.append(
                _build_folder_plan_item(project, entry, draft, audio_manifest, validation_reports)
            )

    project_title = project_brief.video_title
    if dramaturgy_plan is not None and dramaturgy_plan.project_title:
        project_title = dramaturgy_plan.project_title

    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        project_title=project_title,
        language=project_brief.language,
        intro=intro_item,
        folders=folder_items,
        project_brief_hash=content_hash_of_model(project_brief),
        style_profile_hash=content_hash_of_model(style_profile),
        dramaturgy_hash=content_hash_of_model(dramaturgy_plan),
        folder_voiceovers_hash=content_hash_of_model(confirmed_folders_doc),
        intro_hook_hash=content_hash_of_model(confirmed_hook),
        audio_manifest_hash=content_hash_of_model(audio_manifest),
        source_artifacts=_build_source_artifacts(
            project, project_brief, style_profile, dramaturgy_plan, confirmed_folders_doc, confirmed_hook, audio_manifest
        ),
    )

    return validate_voiceover_project_plan_readiness(project, plan)


def _append_audio_alignment_errors(
    warnings: list[ReadinessError],
    blockers: list[ReadinessError],
    *,
    folder_name: str,
    label: str,
    audio_status: str,
    has_alignment: bool,
) -> None:
    if audio_status == AUDIO_STATUS_MISSING:
        warnings.append(
            ReadinessError(
                type="MISSING_AUDIO", severity="WARNING", scope="audio", folder_name=folder_name,
                message=f"Audio fehlt noch ({label}).",
                fix_hint="Unter Audio / ElevenLabs vertonen.",
            )
        )
        return  # solange kein Audio existiert, ist auch kein Alignment zu erwarten
    if audio_status == AUDIO_STATUS_FAILED:
        blockers.append(
            ReadinessError(
                type="AUDIO_FAILED", severity="BLOCKER", scope="audio", folder_name=folder_name,
                message=f"Audio-Erzeugung ist fehlgeschlagen ({label}).",
                fix_hint="Unter Audio / ElevenLabs erneut vertonen.",
            )
        )
    elif audio_status == AUDIO_STATUS_STALE:
        blockers.append(
            ReadinessError(
                type="AUDIO_STALE", severity="BLOCKER", scope="audio", folder_name=folder_name,
                message=f"Audio ist veraltet — Text wurde nach der Vertonung geändert ({label}).",
                fix_hint="Unter Audio / ElevenLabs neu vertonen.",
            )
        )
    elif audio_status == AUDIO_STATUS_READY_WITH_WARNINGS:
        warnings.append(
            ReadinessError(
                type="AUDIO_DURATION_MISSING", severity="WARNING", scope="audio", folder_name=folder_name,
                message=f"Audio-Dauer konnte nicht ermittelt werden (ffprobe) ({label}).",
            )
        )
    if not has_alignment:
        warnings.append(
            ReadinessError(
                type="MISSING_ALIGNMENT", severity="WARNING", scope="alignment", folder_name=folder_name,
                message=f"Alignment fehlt ({label}).",
            )
        )


def _append_asset_mapping_errors(
    warnings: list[ReadinessError],
    blockers: list[ReadinessError],
    *,
    folder_name: str,
    sentence_id: str,
    primary_asset_id: str,
    needs_supplement_asset: bool,
    supplement_reason: str,
    asset_confidence: float,
) -> None:
    if not primary_asset_id and not needs_supplement_asset:
        blockers.append(
            ReadinessError(
                type=VO_ERROR_MISSING_ASSET_MAPPING, severity="BLOCKER", scope="sentence",
                folder_name=folder_name, sentence_id=sentence_id,
                message="Kein Asset zugeordnet und needs_supplement_asset ist nicht gesetzt.",
                fix_hint="Voice-over-Text bearbeiten oder Asset-Zuordnung ergänzen.",
            )
        )
    if needs_supplement_asset and not supplement_reason.strip():
        warnings.append(
            ReadinessError(
                type=VO_ERROR_MISSING_SUPPLEMENT_REASON, severity="WARNING", scope="sentence",
                folder_name=folder_name, sentence_id=sentence_id,
                message="needs_supplement_asset ist gesetzt, aber supplement_reason fehlt.",
            )
        )
    if primary_asset_id and asset_confidence < WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD:
        warnings.append(
            ReadinessError(
                type=VO_ERROR_WEAK_ASSET_MATCH, severity="WARNING", scope="sentence",
                folder_name=folder_name, sentence_id=sentence_id,
                message=f"Niedrige Asset-Confidence ({asset_confidence}).",
            )
        )


def validate_voiceover_project_plan_readiness(
    project: Project, plan: ConfirmedVoiceoverProjectPlan
) -> ConfirmedVoiceoverProjectPlan:
    """Berechnet readiness/status/warnings/blockers für einen bereits mit
    Intro-/Folder-Daten befüllten Plan. Reine Funktion — gibt eine
    aktualisierte KOPIE zurück, verändert `plan` nicht."""
    warnings: list[ReadinessError] = []
    blockers: list[ReadinessError] = []

    has_confirmed_dramaturgy = load_confirmed_dramaturgy(project) is not None
    has_confirmed_intro = load_confirmed_intro_hook(project) is not None
    missing_folders = missing_confirmed_folder_names(project)
    active_folders = get_active_dramaturgy_folder_names(project)
    all_folders_confirmed = has_confirmed_dramaturgy and bool(active_folders) and not missing_folders

    if not has_confirmed_dramaturgy:
        blockers.append(
            ReadinessError(
                type="MISSING_CONFIRMED_DRAMATURGY", severity="BLOCKER", scope="project",
                message="Keine bestätigte Dramaturgie vorhanden.", fix_hint="Dramaturgie bestätigen.",
            )
        )
    if not has_confirmed_intro:
        blockers.append(
            ReadinessError(
                type="MISSING_CONFIRMED_INTRO", severity="BLOCKER", scope="intro",
                message="Kein bestätigter Intro-Hook vorhanden.", fix_hint="Intro-Hook bestätigen.",
            )
        )
    for missing_name in missing_folders:
        blockers.append(
            ReadinessError(
                type="MISSING_FOLDER_VOICEOVER", severity="BLOCKER", scope="folder", folder_name=missing_name,
                message=f"Ordner '{missing_name}' hat noch keinen bestätigten Voice-over-Text.",
                fix_hint="Voice-over für diesen Ordner bestätigen.",
            )
        )

    intro = plan.intro
    if has_confirmed_intro:
        if not intro.hook_text.strip():
            blockers.append(
                ReadinessError(type="EMPTY_TEXT", severity="BLOCKER", scope="intro", message="Intro-Hook-Text ist leer.")
            )
        if not intro.visual_beats:
            warnings.append(
                ReadinessError(type="EMPTY_VISUAL_BEATS", severity="WARNING", scope="intro", message="Intro hat keine visual_beats.")
            )
        _append_audio_alignment_errors(
            warnings, blockers, folder_name="", label="Intro", audio_status=intro.audio_status,
            has_alignment=bool(intro.alignment_items),
        )
        for beat in intro.visual_beats:
            _append_asset_mapping_errors(
                warnings, blockers, folder_name="", sentence_id=beat.hook_beat_id,
                primary_asset_id=beat.primary_asset_id, needs_supplement_asset=beat.needs_supplement_asset,
                supplement_reason=beat.supplement_reason, asset_confidence=beat.asset_confidence,
            )

    for folder_item in plan.folders:
        if not folder_item.voiceover_text_full.strip():
            blockers.append(
                ReadinessError(
                    type="EMPTY_TEXT", severity="BLOCKER", scope="folder", folder_name=folder_item.folder_name,
                    message="Voice-over-Text ist leer.",
                )
            )
        if not folder_item.sentence_items:
            blockers.append(
                ReadinessError(
                    type="EMPTY_SENTENCE_ITEMS", severity="BLOCKER", scope="folder", folder_name=folder_item.folder_name,
                    message="Keine sentence_items vorhanden.",
                )
            )
        _append_audio_alignment_errors(
            warnings, blockers, folder_name=folder_item.folder_name, label=folder_item.folder_name,
            audio_status=folder_item.audio_status, has_alignment=bool(folder_item.alignment_items),
        )
        for sentence_item in folder_item.sentence_items:
            _append_asset_mapping_errors(
                warnings, blockers, folder_name=folder_item.folder_name, sentence_id=sentence_item.sentence_id,
                primary_asset_id=sentence_item.primary_asset_id,
                needs_supplement_asset=sentence_item.needs_supplement_asset,
                supplement_reason=sentence_item.supplement_reason, asset_confidence=sentence_item.asset_confidence,
            )
        if folder_item.validation_status == VOICEOVER_STATUS_NEEDS_USER_REVIEW:
            warnings.append(
                ReadinessError(
                    type="NEEDS_USER_REVIEW", severity="WARNING", scope="folder", folder_name=folder_item.folder_name,
                    message="Validierung hat NEEDS_USER_REVIEW ergeben.",
                )
            )

    has_blockers = bool(blockers)

    all_audio_present = intro.audio_status != AUDIO_STATUS_MISSING and all(
        f.audio_status != AUDIO_STATUS_MISSING for f in plan.folders
    )
    any_audio_present = intro.audio_status != AUDIO_STATUS_MISSING or any(
        f.audio_status != AUDIO_STATUS_MISSING for f in plan.folders
    )
    all_alignments_present = bool(intro.alignment_items) and all(bool(f.alignment_items) for f in plan.folders)
    no_stale_or_failed = intro.audio_status not in (AUDIO_STATUS_STALE, AUDIO_STATUS_FAILED) and all(
        f.audio_status not in (AUDIO_STATUS_STALE, AUDIO_STATUS_FAILED) for f in plan.folders
    )
    all_durations_known = (
        intro.audio_duration_sec > 0 if intro.audio_status != AUDIO_STATUS_MISSING else True
    ) and all(
        (f.audio_duration_sec > 0 if f.audio_status != AUDIO_STATUS_MISSING else True) for f in plan.folders
    )
    all_asset_mapped = not any(error.type == VO_ERROR_MISSING_ASSET_MAPPING for error in blockers)

    readiness = ProjectPlanReadiness(
        has_confirmed_dramaturgy=has_confirmed_dramaturgy,
        has_confirmed_intro=has_confirmed_intro,
        all_active_folders_have_confirmed_voiceover=all_folders_confirmed,
        all_required_audio_ready=all_audio_present and no_stale_or_failed,
        all_alignments_ready=all_alignments_present,
        has_asset_mapping_for_all_items=all_asset_mapped,
        has_no_blockers=not has_blockers,
    )

    # Statuslogik (Phase 7 §4): Reihenfolge ist bewusst so gewählt, dass
    # Blocker/unvollständiger Text immer Vorrang vor Audio-Fortschritt haben.
    if has_blockers or not (has_confirmed_dramaturgy and has_confirmed_intro and all_folders_confirmed):
        status = PLAN_STATUS_NEEDS_REVIEW
    elif not any_audio_present:
        status = PLAN_STATUS_TEXT_READY
    elif not all_audio_present:
        status = PLAN_STATUS_AUDIO_PENDING
    elif all_alignments_present and no_stale_or_failed and all_durations_known and all_asset_mapped:
        status = PLAN_STATUS_READY_FOR_CUT
    else:
        # Audio+Alignment technisch da, aber z. B. AUDIO_READY_WITH_WARNINGS
        # (Dauer unbekannt) oder fehlende Alignments -> nicht schnittbereit.
        status = PLAN_STATUS_AUDIO_READY

    updated_folders = [
        folder_item.model_copy(
            update={
                "warnings": [e.message for e in warnings if e.folder_name == folder_item.folder_name],
                "blockers": [e.message for e in blockers if e.folder_name == folder_item.folder_name],
            }
        )
        for folder_item in plan.folders
    ]

    return plan.model_copy(
        update={
            "warnings": warnings,
            "blockers": blockers,
            "readiness": readiness,
            "status": status,
            "folders": updated_folders,
        }
    )


def load_confirmed_voiceover_project_plan(project: Project) -> ConfirmedVoiceoverProjectPlan | None:
    path = get_confirmed_voiceover_project_plan_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ConfirmedVoiceoverProjectPlan.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_confirmed_voiceover_project_plan(
    project: Project, plan: ConfirmedVoiceoverProjectPlan
) -> ConfirmedVoiceoverProjectPlan:
    normalized = plan.model_copy(update={"project_id": project.id})
    path = get_confirmed_voiceover_project_plan_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def is_project_plan_stale(project: Project, plan: ConfirmedVoiceoverProjectPlan) -> bool:
    """True, wenn sich mindestens ein Quellartefakt seit der Plan-Erzeugung
    geändert hat (Vergleich über source_artifacts.created_from_hashes, §11)."""
    current_hashes = {
        "project_brief": content_hash_of_model(load_project_brief(project)),
        "style_profile": content_hash_of_model(load_style_profile(project)),
        "dramaturgy": content_hash_of_model(load_confirmed_dramaturgy(project)),
        "folder_voiceovers": content_hash_of_model(load_folder_voiceovers_confirmed(project)),
        "intro_hook": content_hash_of_model(load_confirmed_intro_hook(project)),
        "audio_manifest": content_hash_of_model(load_audio_manifest(project)),
    }
    stored_hashes = plan.source_artifacts.get("created_from_hashes", {})
    return any(stored_hashes.get(key) != value for key, value in current_hashes.items())


def export_voiceover_project_plan_json(
    project: Project, plan: ConfirmedVoiceoverProjectPlan
) -> Path:
    path = get_voiceover_project_plan_json_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return path


def _markdown_escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def export_voiceover_project_plan_markdown(
    project: Project, plan: ConfirmedVoiceoverProjectPlan
) -> Path:
    lines: list[str] = [f"# {plan.project_title or '(ohne Titel)'}", ""]

    lines.append("## Status")
    lines.append("")
    lines.append(f"- Language: {plan.language}")
    lines.append(f"- Project Status: {plan.status}")
    lines.append(f"- Ready for Cut: {'ja' if plan.status == PLAN_STATUS_READY_FOR_CUT else 'nein'}")
    lines.append(f"- Warnings: {len(plan.warnings)}")
    lines.append(f"- Blockers: {len(plan.blockers)}")
    lines.append("")

    lines.append("## Intro")
    lines.append("")
    lines.append(plan.intro.hook_text or "(kein bestätigter Intro-Hook)")
    lines.append("")
    lines.append(f"Audio: {plan.intro.audio_path or '—'} ({plan.intro.audio_status})")
    lines.append(f"Alignment: {plan.intro.alignment_path or '—'}")
    lines.append(f"Used Folders: {', '.join(plan.intro.used_folders) or '—'}")
    lines.append("")

    lines.append("## Ordner")
    lines.append("")
    for folder in plan.folders:
        lines.append(f"### {folder.order_index}. {folder.folder_name}")
        lines.append("")
        lines.append(f"Role: {folder.dramaturgy_role}")
        lines.append(f"Word Count: {folder.word_count}")
        lines.append(f"Audio: {folder.audio_path or '—'} ({folder.audio_status})")
        lines.append(f"Status: {folder.readiness_status}")
        lines.append("")
        lines.append("Voice-over Text:")
        lines.append("")
        lines.append(folder.voiceover_text_full)
        lines.append("")
        lines.append("Sentence / Asset Mapping:")
        lines.append("")
        lines.append(
            "| Sentence ID | Text | Primary Asset | Backup Assets | Needs Supplement | Audio Start | Audio End |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        alignment_by_id = {item.sentence_id: item for item in folder.alignment_items}
        for sentence_item in folder.sentence_items:
            alignment_item = alignment_by_id.get(sentence_item.sentence_id)
            audio_start = f"{alignment_item.audio_start_sec:.2f}" if alignment_item else "—"
            audio_end = f"{alignment_item.audio_end_sec:.2f}" if alignment_item else "—"
            lines.append(
                f"| {sentence_item.sentence_id} | {_markdown_escape_cell(sentence_item.text)} | "
                f"{sentence_item.primary_asset_id or '—'} | {', '.join(sentence_item.backup_asset_ids) or '—'} | "
                f"{sentence_item.needs_supplement_asset} | {audio_start} | {audio_end} |"
            )
        lines.append("")

    lines.append("## Warnings / Blockers")
    lines.append("")
    for error in plan.blockers:
        location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
        lines.append(f"- **BLOCKER** [{error.type}]{location}: {error.message}")
    for error in plan.warnings:
        location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
        lines.append(f"- WARNING [{error.type}]{location}: {error.message}")
    if not plan.blockers and not plan.warnings:
        lines.append("(keine)")
    lines.append("")

    lines.append("## Source Artifacts")
    lines.append("")
    for key, value in plan.source_artifacts.items():
        if key == "created_from_hashes":
            continue
        lines.append(f"- {key}: `{value}`")
    for key, value in plan.source_artifacts.get("created_from_hashes", {}).items():
        lines.append(f"- hash({key}): `{value}`")
    lines.append("")

    content = "\n".join(lines)
    path = get_voiceover_project_plan_md_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


_CSV_FIELDNAMES = [
    "scope",
    "order_index",
    "folder_name",
    "dramaturgy_role",
    "item_id",
    "sentence_id",
    "beat_id",
    "text",
    "visual_intent",
    "primary_asset_id",
    "backup_asset_ids",
    "needs_supplement_asset",
    "supplement_reason",
    "audio_path",
    "audio_start_sec",
    "audio_end_sec",
    "duration_sec",
    "asset_confidence",
    "readiness_status",
    "warnings",
]


def export_voiceover_project_plan_csv(
    project: Project, plan: ConfirmedVoiceoverProjectPlan
) -> Path:
    """Eine Zeile pro sentence_item / Intro-visual_beat (Phase 7 §9)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDNAMES)
    writer.writeheader()

    intro_alignment_by_id = {item.sentence_id: item for item in plan.intro.alignment_items}
    for beat in plan.intro.visual_beats:
        alignment_item = intro_alignment_by_id.get(beat.hook_beat_id)
        writer.writerow(
            {
                "scope": "intro",
                "order_index": 0,
                "folder_name": "000_intro",
                "dramaturgy_role": "",
                "item_id": beat.hook_beat_id,
                "sentence_id": "",
                "beat_id": beat.hook_beat_id,
                "text": beat.text,
                "visual_intent": beat.visual_intent,
                "primary_asset_id": beat.primary_asset_id,
                "backup_asset_ids": ";".join(beat.backup_asset_ids),
                "needs_supplement_asset": beat.needs_supplement_asset,
                "supplement_reason": beat.supplement_reason,
                "audio_path": plan.intro.audio_path,
                "audio_start_sec": alignment_item.audio_start_sec if alignment_item else "",
                "audio_end_sec": alignment_item.audio_end_sec if alignment_item else "",
                "duration_sec": alignment_item.duration_sec if alignment_item else "",
                "asset_confidence": beat.asset_confidence,
                "readiness_status": plan.intro.readiness_status,
                "warnings": "",
            }
        )

    for folder in plan.folders:
        alignment_by_id = {item.sentence_id: item for item in folder.alignment_items}
        for sentence_item in folder.sentence_items:
            alignment_item = alignment_by_id.get(sentence_item.sentence_id)
            writer.writerow(
                {
                    "scope": "folder",
                    "order_index": folder.order_index,
                    "folder_name": folder.folder_name,
                    "dramaturgy_role": folder.dramaturgy_role,
                    "item_id": sentence_item.sentence_id,
                    "sentence_id": sentence_item.sentence_id,
                    "beat_id": sentence_item.beat_id,
                    "text": sentence_item.text,
                    "visual_intent": sentence_item.visual_intent,
                    "primary_asset_id": sentence_item.primary_asset_id,
                    "backup_asset_ids": ";".join(sentence_item.backup_asset_ids),
                    "needs_supplement_asset": sentence_item.needs_supplement_asset,
                    "supplement_reason": sentence_item.supplement_reason,
                    "audio_path": folder.audio_path,
                    "audio_start_sec": alignment_item.audio_start_sec if alignment_item else "",
                    "audio_end_sec": alignment_item.audio_end_sec if alignment_item else "",
                    "duration_sec": alignment_item.duration_sec if alignment_item else "",
                    "asset_confidence": sentence_item.asset_confidence,
                    "readiness_status": folder.readiness_status,
                    "warnings": ";".join(folder.warnings),
                }
            )

    path = get_voiceover_project_plan_csv_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path
