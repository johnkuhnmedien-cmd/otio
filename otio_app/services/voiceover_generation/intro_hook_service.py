"""Intro-Hook-Erzeugung aus allen bestätigten Folder-Voice-overs (Phase 5).

Erzeugt genau 5 Hook-Kandidaten mit vollständiger visueller Zuordnung
(visual_beats). Schreibt niemals EditPlanDocuments und löst nie OTIO-Export
aus. intro_hook.confirmed.json entsteht ausschließlich durch explizite
Nutzerbestätigung (confirm_intro_hook) — niemals automatisch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from otio_app.defaults import (
    INTRO_HOOK_CANDIDATE_COUNT,
    INTRO_HOOK_STATUS_READY,
    INTRO_HOOK_TYPE_CINEMATIC_PROMISE,
    INTRO_HOOK_TYPES,
    VO_ERROR_FORBIDDEN_TERM_USED,
    VO_ERROR_INVALID_ASSET_ID,
    VO_ERROR_INVALID_FOLDER_REFERENCE,
    VO_ERROR_INVALID_SENTENCE_REFERENCE,
    VO_ERROR_MISSING_ASSET_MAPPING,
    VO_ERROR_MISSING_SUPPLEMENT_REASON,
    VO_ERROR_WEAK_ASSET_MATCH,
    VO_ERROR_WORD_COUNT_OUT_OF_RANGE,
    WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD,
)
from otio_app.models import Project
from otio_app.project_layout import get_intro_hook_candidates_path, get_intro_hook_confirmed_path
from otio_app.services.gemini_client import _extract_json
from otio_app.services.plan_llm_client import (
    generate_plan_text_with_metadata,
)
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.intro_hook_settings_service import (
    load_intro_hook_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_INTRO_HOOK,
    STATUS_FAIL,
    STATUS_PARSE_FAILED,
    STATUS_PASS,
    content_hash,
    create_llm_run_dir,
    write_llm_manifest,
    write_llm_parsed_response,
    write_llm_prompt,
    write_llm_raw_response,
)
from otio_app.services.voiceover_generation.model_settings_service import resolve_llm_model_id
from otio_app.services.voiceover_generation.models import (
    ConfirmedIntroHook,
    FolderVoiceoverDraft,
    IntroHookCandidate,
    IntroHookCandidatesDocument,
    IntroHookSettings,
    IntroHookVisualBeat,
    LlmRunManifest,
    SentenceItem,
    as_str_list,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.prompts import build_intro_hook_prompt
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.services.voiceover_generation.voiceover_author_service import (
    _count_words,
    build_inventory_asset_context,
    load_folder_voiceovers_confirmed,
)

__all__ = [
    "IntroHookBuildResult",
    "get_active_dramaturgy_folder_names",
    "get_confirmed_folder_voiceover_names",
    "missing_confirmed_folder_names",
    "all_active_folders_confirmed",
    "missing_intro_source_folder_names",
    "intro_source_ready",
    "get_intro_source_folder_names",
    "load_intro_source_folder_drafts",
    "parse_intro_hook_response",
    "validate_intro_hook_candidate",
    "build_intro_hook_candidates",
    "regenerate_intro_hook_candidates",
    "load_intro_hook_candidates",
    "save_intro_hook_candidates",
    "load_confirmed_intro_hook",
    "save_confirmed_intro_hook",
    "confirm_intro_hook",
    "unconfirm_intro_hook",
    "update_intro_hook_candidate",
]


def get_active_dramaturgy_folder_names(project: Project) -> list[str]:
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        return []
    return [
        entry.folder_name
        for entry in sorted(plan.recommended_folder_order, key=lambda entry: entry.order_index)
        if entry.enabled
    ]


def get_confirmed_folder_voiceover_names(project: Project) -> list[str]:
    document = load_folder_voiceovers_confirmed(project)
    return [item.folder_name for item in document.items]


def missing_confirmed_folder_names(project: Project) -> list[str]:
    """Aktive Ordner laut bestätigter Dramaturgie, die noch KEINEN
    bestätigten Voice-over-Eintrag haben (Phase 5 §1)."""
    active = get_active_dramaturgy_folder_names(project)
    confirmed = set(get_confirmed_folder_voiceover_names(project))
    return [name for name in active if name not in confirmed]


def all_active_folders_confirmed(project: Project) -> bool:
    active = get_active_dramaturgy_folder_names(project)
    return bool(active) and not missing_confirmed_folder_names(project)


def _enhanced_locked_chapter_names(project: Project) -> list[str]:
    """Kapitel mit Segmenten im gesperrten Enhanced-Skript (Dramaturgie-Reihenfolge)."""
    from otio_app.services.without_voiceover_enhanced.script_author_service import (
        chapter_narration_text,
        list_enabled_dramaturgy_folders,
    )
    from otio_app.services.without_voiceover_enhanced.script_lock_service import (
        load_locked_script,
    )

    locked = load_locked_script(project)
    if locked is None or not locked.segments:
        return []
    names: list[str] = []
    for entry in list_enabled_dramaturgy_folders(project):
        if chapter_narration_text(locked, entry.folder_name).strip():
            names.append(entry.folder_name)
    return names


def get_intro_source_folder_names(project: Project) -> list[str]:
    """Ordner, die als Intro-Quelle gelten (klassisch bestätigt / Enhanced gelockt)."""
    if project.is_without_voiceover_enhanced:
        return _enhanced_locked_chapter_names(project)
    return get_confirmed_folder_voiceover_names(project)


def missing_intro_source_folder_names(project: Project) -> list[str]:
    """Aktive Dramaturgie-Ordner ohne Intro-Quelle."""
    if project.is_without_voiceover_enhanced:
        from otio_app.services.without_voiceover_enhanced.script_lock_service import (
            load_locked_script,
        )

        locked = load_locked_script(project)
        if locked is None:
            return get_active_dramaturgy_folder_names(project)
        present = set(_enhanced_locked_chapter_names(project))
        return [
            name
            for name in get_active_dramaturgy_folder_names(project)
            if name not in present
        ]
    return missing_confirmed_folder_names(project)


def intro_source_ready(project: Project) -> bool:
    active = get_active_dramaturgy_folder_names(project)
    return bool(active) and not missing_intro_source_folder_names(project)


def folder_drafts_from_locked_enhanced_script(
    project: Project,
) -> list[FolderVoiceoverDraft]:
    """Mapped gesperrte Enhanced-Kapitel auf FolderVoiceoverDraft für den Intro-Prompt."""
    from otio_app.services.without_voiceover_enhanced.script_author_service import (
        chapter_narration_text,
        list_enabled_dramaturgy_folders,
        segments_for_folder,
    )
    from otio_app.services.without_voiceover_enhanced.script_lock_service import (
        load_locked_script,
    )

    locked = load_locked_script(project)
    if locked is None:
        return []

    drafts: list[FolderVoiceoverDraft] = []
    for entry in list_enabled_dramaturgy_folders(project):
        narration = chapter_narration_text(locked, entry.folder_name).strip()
        if not narration:
            continue
        segments = segments_for_folder(locked, entry.folder_name)
        sentence_items = [
            SentenceItem(
                sentence_id=segment.segment_id,
                beat_id=segment.segment_id,
                text=segment.text,
                visual_intent=segment.semantic_function or "",
            )
            for segment in segments
            if segment.text.strip()
        ]
        drafts.append(
            FolderVoiceoverDraft(
                project_id=project.id,
                folder_name=entry.folder_name,
                order_index=entry.order_index,
                language=project.language,
                voiceover_text_full=narration,
                word_count=_count_words(narration),
                sentence_items=sentence_items,
                status="confirmed",
            )
        )
    return drafts


def load_intro_source_folder_drafts(project: Project) -> list[FolderVoiceoverDraft]:
    """Folder-Drafts für den Intro-Prompt (klassisch confirmed / Enhanced locked)."""
    active = set(get_active_dramaturgy_folder_names(project))
    if project.is_without_voiceover_enhanced:
        drafts = folder_drafts_from_locked_enhanced_script(project)
    else:
        document = load_folder_voiceovers_confirmed(project)
        drafts = list(document.items)
    return sorted(
        (item for item in drafts if item.folder_name in active),
        key=lambda item: item.order_index,
    )


def parse_intro_hook_response(raw_text: str) -> dict[str, Any]:
    payload = _extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Intro-Hook-Antwort ist kein JSON-Objekt.")
    return payload


def _parse_visual_beats(raw_items: Any) -> list[IntroHookVisualBeat]:
    beats: list[IntroHookVisualBeat] = []
    if not isinstance(raw_items, list):
        return beats
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        try:
            confidence = float(raw.get("asset_confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        beats.append(
            IntroHookVisualBeat(
                hook_beat_id=str(raw.get("hook_beat_id") or f"hook_beat_{index:03d}"),
                text=str(raw.get("text", "")),
                visual_intent=str(raw.get("visual_intent", "")),
                source_folder_name=str(raw.get("source_folder_name", "")),
                source_sentence_id=str(raw.get("source_sentence_id", "")),
                primary_asset_id=str(raw.get("primary_asset_id") or "").strip(),
                backup_asset_ids=as_str_list(raw.get("backup_asset_ids")),
                asset_match_reason=str(raw.get("asset_match_reason", "")),
                asset_confidence=confidence,
                needs_supplement_asset=bool(raw.get("needs_supplement_asset", False)),
                supplement_reason=str(raw.get("supplement_reason", "")),
            )
        )
    return beats


def _sanitize_visual_beats(
    beats: list[IntroHookVisualBeat], valid_asset_ids: set[str]
) -> list[IntroHookVisualBeat]:
    """Entfernt ausschließlich halluzinierte Asset-IDs — analog zur
    Folder-Voice-over-Sanitisierung aus Phase 4."""
    sanitized: list[IntroHookVisualBeat] = []
    for beat in beats:
        primary = beat.primary_asset_id if beat.primary_asset_id in valid_asset_ids else ""
        backups = [asset_id for asset_id in beat.backup_asset_ids if asset_id in valid_asset_ids]
        sanitized.append(beat.model_copy(update={"primary_asset_id": primary, "backup_asset_ids": backups}))
    return sanitized


def _parse_candidate(raw: Any, *, index: int) -> IntroHookCandidate | None:
    if not isinstance(raw, dict):
        return None
    hook_text = str(raw.get("hook_text", ""))
    hook_type = str(raw.get("hook_type", "")).strip().lower()
    if hook_type not in INTRO_HOOK_TYPES:
        hook_type = INTRO_HOOK_TYPE_CINEMATIC_PROMISE
    try:
        score = float(raw.get("hook_potential_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return IntroHookCandidate(
        hook_id=str(raw.get("hook_id") or f"hook_{index:03d}"),
        hook_text=hook_text,
        word_count=_count_words(hook_text),
        hook_type=hook_type,
        used_folders=as_str_list(raw.get("used_folders")),
        used_sentence_ids=as_str_list(raw.get("used_sentence_ids")),
        visual_beats=_parse_visual_beats(raw.get("visual_beats", [])),
        hook_potential_score=score,
        reason=str(raw.get("reason", "")),
        risks=as_str_list(raw.get("risks")),
    )


def validate_intro_hook_candidate(
    candidate: IntroHookCandidate,
    *,
    confirmed_folder_names: set[str],
    valid_sentence_ids_by_folder: dict[str, set[str]],
    valid_asset_ids: set[str],
    settings: IntroHookSettings,
) -> list[str]:
    """Deterministische Prüfung eines Intro-Hook-Kandidaten (Phase 5 §6).

    Läuft unabhängig von einer vorherigen Sanitisierung — prüft IMMER gegen
    die aktuellen bestätigten Ordner/Sätze/Assets."""
    risks: list[str] = []
    all_valid_sentence_ids = {
        sentence_id for ids in valid_sentence_ids_by_folder.values() for sentence_id in ids
    }

    for folder_name in candidate.used_folders:
        if folder_name not in confirmed_folder_names:
            risks.append(
                f"{VO_ERROR_INVALID_FOLDER_REFERENCE}: '{folder_name}' ist kein bestätigter "
                "aktiver Ordner."
            )

    for sentence_id in candidate.used_sentence_ids:
        if sentence_id not in all_valid_sentence_ids:
            risks.append(
                f"{VO_ERROR_INVALID_SENTENCE_REFERENCE}: sentence_id '{sentence_id}' existiert "
                "in keinem bestätigten Ordner."
            )

    for beat in candidate.visual_beats:
        if beat.source_folder_name and beat.source_folder_name not in confirmed_folder_names:
            risks.append(
                f"{VO_ERROR_INVALID_FOLDER_REFERENCE}: visual_beat '{beat.hook_beat_id}' "
                f"verweist auf unbekannten Ordner '{beat.source_folder_name}'."
            )
        if beat.source_sentence_id and beat.source_sentence_id not in all_valid_sentence_ids:
            risks.append(
                f"{VO_ERROR_INVALID_SENTENCE_REFERENCE}: visual_beat '{beat.hook_beat_id}' "
                f"verweist auf unbekannte sentence_id '{beat.source_sentence_id}'."
            )
        if beat.primary_asset_id and beat.primary_asset_id not in valid_asset_ids:
            risks.append(
                f"{VO_ERROR_INVALID_ASSET_ID}: primary_asset_id '{beat.primary_asset_id}' "
                "existiert nicht im Inventory."
            )
        for backup_id in beat.backup_asset_ids:
            if backup_id not in valid_asset_ids:
                risks.append(
                    f"{VO_ERROR_INVALID_ASSET_ID}: backup_asset_id '{backup_id}' existiert "
                    "nicht im Inventory."
                )
        if not beat.primary_asset_id and not beat.needs_supplement_asset:
            risks.append(
                f"{VO_ERROR_MISSING_ASSET_MAPPING}: visual_beat '{beat.hook_beat_id}' hat weder "
                "Asset noch needs_supplement_asset."
            )
        if beat.needs_supplement_asset and not beat.supplement_reason.strip():
            risks.append(
                f"{VO_ERROR_MISSING_SUPPLEMENT_REASON}: visual_beat '{beat.hook_beat_id}' "
                "braucht eine Begründung."
            )
        if beat.primary_asset_id:
            if not (0.0 <= beat.asset_confidence <= 1.0):
                risks.append(
                    f"{VO_ERROR_WEAK_ASSET_MATCH}: asset_confidence {beat.asset_confidence} "
                    "liegt außerhalb von 0.0-1.0."
                )
            elif beat.asset_confidence < WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD:
                risks.append(
                    f"{VO_ERROR_WEAK_ASSET_MATCH}: asset_confidence {beat.asset_confidence} "
                    f"liegt unter dem Schwellenwert {WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD}."
                )

    if settings.min_words and candidate.word_count < settings.min_words:
        risks.append(
            f"{VO_ERROR_WORD_COUNT_OUT_OF_RANGE}: Wortanzahl {candidate.word_count} liegt "
            f"unter dem Minimum {settings.min_words}."
        )
    elif settings.max_words and candidate.word_count > settings.max_words:
        risks.append(
            f"{VO_ERROR_WORD_COUNT_OUT_OF_RANGE}: Wortanzahl {candidate.word_count} liegt "
            f"über dem Maximum {settings.max_words}."
        )

    forbidden_phrases = list(settings.forbidden_phrases) + list(settings.must_avoid)
    lowered_text = candidate.hook_text.lower()
    for phrase in forbidden_phrases:
        cleaned = phrase.strip().lower()
        if cleaned and cleaned in lowered_text:
            risks.append(
                f"{VO_ERROR_FORBIDDEN_TERM_USED}: Verbotene Formulierung gefunden: "
                f"'{phrase.strip()}'."
            )

    return risks


def load_intro_hook_candidates(project: Project) -> IntroHookCandidatesDocument | None:
    path = get_intro_hook_candidates_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IntroHookCandidatesDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_intro_hook_candidates(
    project: Project, candidates: IntroHookCandidatesDocument
) -> IntroHookCandidatesDocument:
    normalized = candidates.model_copy(update={"project_id": project.id})
    path = get_intro_hook_candidates_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_confirmed_intro_hook(project: Project) -> ConfirmedIntroHook | None:
    path = get_intro_hook_confirmed_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ConfirmedIntroHook.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_confirmed_intro_hook(project: Project, hook: ConfirmedIntroHook) -> ConfirmedIntroHook:
    normalized = hook.model_copy(update={"project_id": project.id})
    path = get_intro_hook_confirmed_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def confirm_intro_hook(
    project: Project, hook_id: str, edited_hook_text: str | None = None
) -> ConfirmedIntroHook:
    """Explizite Nutzerbestätigung — überschreibt einen bestehenden bestätigten
    Hook nur, weil dieser Aufruf selbst explizit erfolgt (§9)."""
    document = load_intro_hook_candidates(project)
    if document is None:
        raise ValueError("Keine Intro-Hook-Kandidaten vorhanden.")
    candidate = next((c for c in document.candidates if c.hook_id == hook_id), None)
    if candidate is None:
        raise ValueError(f"Hook-Kandidat '{hook_id}' nicht gefunden.")

    hook_text = edited_hook_text if edited_hook_text is not None else candidate.hook_text
    confirmed = ConfirmedIntroHook(
        project_id=project.id,
        language=document.language,
        hook_id=candidate.hook_id,
        hook_text=hook_text,
        word_count=_count_words(hook_text),
        hook_type=candidate.hook_type,
        used_folders=candidate.used_folders,
        used_sentence_ids=candidate.used_sentence_ids,
        visual_beats=candidate.visual_beats,
        hook_potential_score=candidate.hook_potential_score,
        reason=candidate.reason,
        llm_run_id=document.llm_run_id,
        risks=candidate.risks,
    )
    return save_confirmed_intro_hook(project, confirmed)


def unconfirm_intro_hook(project: Project) -> None:
    """Nimmt die Intro-Hook-Bestätigung zurück (löscht intro_hook.confirmed.json)."""
    path = get_intro_hook_confirmed_path(project.language_work_dir_path)
    path.unlink(missing_ok=True)


def update_intro_hook_candidate(
    project: Project, hook_id: str, edited_fields: dict[str, Any]
) -> IntroHookCandidatesDocument:
    """Übernimmt manuelle Bearbeitungen (z. B. hook_text) in EINEN Kandidaten
    und speichert das Dokument erneut — andere Kandidaten bleiben unverändert."""
    document = load_intro_hook_candidates(project)
    if document is None:
        raise ValueError("Keine Intro-Hook-Kandidaten vorhanden.")

    updated_candidates: list[IntroHookCandidate] = []
    found = False
    for candidate in document.candidates:
        if candidate.hook_id != hook_id:
            updated_candidates.append(candidate)
            continue
        found = True
        updates = dict(edited_fields)
        if "hook_text" in updates:
            updates["word_count"] = _count_words(str(updates["hook_text"]))
        updated_candidates.append(candidate.model_copy(update=updates))
    if not found:
        raise ValueError(f"Hook-Kandidat '{hook_id}' nicht gefunden.")

    updated_document = document.model_copy(update={"candidates": updated_candidates})
    return save_intro_hook_candidates(project, updated_document)


@dataclass
class IntroHookBuildResult:
    status: str  # PASS | FAIL | PARSE_FAILED (Generierungs-Status)
    document: IntroHookCandidatesDocument | None
    error: str | None
    llm_run_id: str
    provider: str
    model: str


def build_intro_hook_candidates(
    project: Project,
    *,
    provider: str,
    model: str,
) -> IntroHookBuildResult:
    """Erzeugt genau 5 Intro-Hook-Kandidaten.

    Klassisch: alle aktiven Ordner mit bestätigtem Folder-Voice-over.
    Enhanced: Script Lock + Kapitel-Skript für jeden aktiven Dramaturgie-Ordner.
    """
    missing = missing_intro_source_folder_names(project)
    if missing:
        if project.is_without_voiceover_enhanced:
            from otio_app.services.without_voiceover_enhanced.script_lock_service import (
                load_locked_script,
            )

            if load_locked_script(project) is None:
                raise ValueError(
                    "Kein Script Lock — bitte unter ④ alle Kapitel erzeugen "
                    "und Script Lock setzen."
                )
            raise ValueError(
                "Nicht alle aktiven Kapitel haben ein Skript im gesperrten Draft: "
                + ", ".join(missing)
            )
        raise ValueError(
            "Nicht alle aktiven Ordner haben einen bestätigten Voice-over-Eintrag: "
            + ", ".join(missing)
        )
    active_names = get_active_dramaturgy_folder_names(project)
    if not active_names:
        raise ValueError("Keine aktiven Ordner in der bestätigten Dramaturgie.")

    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    dramaturgy_plan = load_confirmed_dramaturgy(project)
    settings = load_intro_hook_settings(project)
    confirmed_drafts = load_intro_source_folder_drafts(project)

    # Inventory nur noch für Post-Validierung der Asset-IDs — nicht im Prompt
    # (bei vielen Ordnern zu lang).
    inventory_by_folder = {
        draft.folder_name: build_inventory_asset_context(project, draft.folder_name)
        for draft in confirmed_drafts
    }

    run_id, run_dir = create_llm_run_dir(project, STAGE_INTRO_HOOK)
    from otio_app.services.voiceover_generation.style_reference_service import (
        style_context_text_for_prompts,
    )

    prompt = build_intro_hook_prompt(
        project_brief=project_brief,
        style_profile=style_profile,
        dramaturgy_plan=dramaturgy_plan,
        confirmed_folder_voiceovers=confirmed_drafts,
        settings=settings,
        style_context_text=style_context_text_for_prompts(project, for_intro=True),
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
                run_id=run_id, stage=STAGE_INTRO_HOOK, provider=provider, model=model,
                prompt_hash=prompt_hash, status=STATUS_FAIL,
            ),
        )
        return IntroHookBuildResult(
            status=STATUS_FAIL, document=None, error=str(exc), llm_run_id=run_id,
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
        payload = parse_intro_hook_response(llm_response.raw_text)
    except (ValueError, TypeError) as exc:
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id, stage=STAGE_INTRO_HOOK, provider=llm_response.provider,
                model=llm_response.model, prompt_hash=prompt_hash, status=STATUS_PARSE_FAILED,
                latency_ms=llm_response.latency_ms, token_usage=llm_response.token_usage,
            ),
        )
        return IntroHookBuildResult(
            status=STATUS_PARSE_FAILED, document=None, error=str(exc), llm_run_id=run_id,
            provider=llm_response.provider, model=llm_response.model,
        )

    confirmed_folder_names = {draft.folder_name for draft in confirmed_drafts}
    valid_sentence_ids_by_folder = {
        draft.folder_name: {item.sentence_id for item in draft.sentence_items}
        for draft in confirmed_drafts
    }
    valid_asset_ids: set[str] = set()
    for assets in inventory_by_folder.values():
        valid_asset_ids.update(asset["asset_id"] for asset in assets)

    raw_candidates = payload.get("candidates", [])
    candidates: list[IntroHookCandidate] = []
    if isinstance(raw_candidates, list):
        for index, raw in enumerate(raw_candidates, start=1):
            candidate = _parse_candidate(raw, index=index)
            if candidate is None:
                continue
            sanitized_beats = _sanitize_visual_beats(candidate.visual_beats, valid_asset_ids)
            candidate = candidate.model_copy(update={"visual_beats": sanitized_beats})
            extra_risks = validate_intro_hook_candidate(
                candidate,
                confirmed_folder_names=confirmed_folder_names,
                valid_sentence_ids_by_folder=valid_sentence_ids_by_folder,
                valid_asset_ids=valid_asset_ids,
                settings=settings,
            )
            merged_risks = list(dict.fromkeys([*candidate.risks, *extra_risks]))
            candidates.append(candidate.model_copy(update={"risks": merged_risks}))

    if not candidates:
        # Bewusste Entscheidung (§12.18): valides JSON, aber keine brauchbaren
        # Kandidaten -> wie ein Fehlschlag behandeln, bestehende Kandidaten
        # NICHT überschreiben.
        write_llm_parsed_response(
            run_dir, {"error": "No valid candidates parsed", "raw_candidates": raw_candidates}
        )
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id, stage=STAGE_INTRO_HOOK, provider=llm_response.provider,
                model=llm_response.model, prompt_hash=prompt_hash, status=STATUS_FAIL,
                latency_ms=llm_response.latency_ms, token_usage=llm_response.token_usage,
            ),
        )
        return IntroHookBuildResult(
            status=STATUS_FAIL, document=None,
            error="Keine gültigen Kandidaten in der LLM-Antwort gefunden.",
            llm_run_id=run_id, provider=llm_response.provider, model=llm_response.model,
        )

    document_risks: list[str] = []
    if len(candidates) != INTRO_HOOK_CANDIDATE_COUNT:
        document_risks.append(
            f"CANDIDATE_COUNT_MISMATCH: erwartet {INTRO_HOOK_CANDIDATE_COUNT} Kandidaten, "
            f"erhalten {len(candidates)}."
        )

    document = IntroHookCandidatesDocument(
        project_id=project.id,
        language=settings.language,
        target_words=settings.target_words,
        min_words=settings.min_words,
        max_words=settings.max_words,
        candidates=candidates,
        llm_run_id=run_id,
        status=INTRO_HOOK_STATUS_READY,
        risks=document_risks,
    )
    saved = save_intro_hook_candidates(project, document)
    write_llm_parsed_response(run_dir, saved.model_dump(mode="json"))
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id, stage=STAGE_INTRO_HOOK, provider=llm_response.provider,
            model=llm_response.model, prompt_hash=prompt_hash, status=STATUS_PASS,
            latency_ms=llm_response.latency_ms, token_usage=llm_response.token_usage,
        ),
    )

    return IntroHookBuildResult(
        status=STATUS_PASS, document=saved, error=None, llm_run_id=run_id,
        provider=llm_response.provider, model=llm_response.model,
    )


def regenerate_intro_hook_candidates(
    project: Project, *, provider: str, model: str
) -> IntroHookBuildResult:
    """Alias für build_intro_hook_candidates — überschreibt NIE einen
    bestätigten Hook (§9), nur die Kandidaten-Datei."""
    return build_intro_hook_candidates(project, provider=provider, model=model)
