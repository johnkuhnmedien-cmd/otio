"""Review-/Correction-Loop für Folder-Voice-overs (Phase 4).

Ablauf: deterministische Python-Checks + ein LLM-Review-Call für weiche
Kriterien; bei Blocker-Fehlern automatischer Correction-Versuch; maximal
MAX_VOICEOVER_REVIEW_ATTEMPTS Durchläufe; Ergebnis PASS oder
NEEDS_USER_REVIEW. Schreibt niemals EditPlanDocuments, löst nie OTIO-Export
aus und bestätigt niemals automatisch (PASS != CONFIRMED).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from otio_app.defaults import (
    MAX_VOICEOVER_REVIEW_ATTEMPTS,
    VO_ERROR_FORBIDDEN_TERM_USED,
    VO_ERROR_MISSING_CONTRAST_OR_COMMONALITY,
    VO_ERROR_MISSING_TRANSITION,
    VO_ERROR_TYPES_LLM_REVIEW,
    VO_ERROR_UNKNOWN_LLM_REVIEW_ERROR,
    VO_ERROR_WORD_COUNT_OUT_OF_RANGE,
    VOICEOVER_STATUS_CONFIRMED,
    VOICEOVER_STATUS_NEEDS_USER_REVIEW,
    VOICEOVER_STATUS_PASS,
)
from otio_app.models import Project
from otio_app.project_layout import get_folder_voiceover_validation_report_path
from otio_app.services.plan_llm_client import (
    PlanLlmNotConfiguredError,
    generate_plan_text_with_metadata,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    load_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_VOICEOVER_CORRECTION,
    STAGE_VOICEOVER_REVIEW,
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
    FolderVoiceoverDraft,
    FolderVoiceoverSetting,
    FolderVoiceoverValidationReport,
    FolderVoiceoverValidationReportsDocument,
    LlmRunManifest,
    ValidationError,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.prompts import (
    _combined_forbidden_phrases,
    build_voiceover_correction_prompt,
    build_voiceover_review_prompt,
)
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.services.voiceover_generation.voiceover_author_service import (
    _count_words,
    _parse_sentence_items,
    _sanitize_sentence_items,
    build_inventory_asset_context,
    get_folder_voiceover_draft,
    load_folder_voiceovers_confirmed,
    parse_folder_voiceover_response,
    save_folder_voiceovers_confirmed,
    upsert_folder_voiceover_draft_item,
    validate_asset_ids_against_inventory,
)

__all__ = [
    "run_deterministic_checks",
    "review_folder_voiceover",
    "build_correction_prompt",
    "apply_corrected_voiceover",
    "run_folder_voiceover_review_loop",
    "confirm_folder_voiceover",
    "unconfirm_folder_voiceover",
    "load_validation_reports",
    "save_validation_report",
]


def _resolve_setting(project: Project, folder_name: str, draft: FolderVoiceoverDraft) -> FolderVoiceoverSetting:
    settings_doc = load_folder_voiceover_settings(project)
    if settings_doc is not None:
        found = next((s for s in settings_doc.settings if s.folder_name == folder_name), None)
        if found is not None:
            return found
    return FolderVoiceoverSetting(
        folder_name=folder_name,
        target_words=draft.target_words,
        min_words=draft.min_words,
        max_words=draft.max_words,
    )


def run_deterministic_checks(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    setting: FolderVoiceoverSetting,
) -> list[ValidationError]:
    """Alle Python-Checks (Phase 4 §8) — Wortanzahl, verbotene Begriffe,
    Übergang/Kontrast-Nutzung, plus die Asset-ID-Validierung aus §7."""
    errors: list[ValidationError] = []

    if setting.min_words and draft.word_count < setting.min_words:
        errors.append(
            ValidationError(
                type=VO_ERROR_WORD_COUNT_OUT_OF_RANGE,
                severity="BLOCKER",
                folder_name=folder_name,
                message=f"Wortanzahl {draft.word_count} liegt unter dem Minimum {setting.min_words}.",
                fix_hint="Text verlängern oder mehr Details ergänzen.",
            )
        )
    elif setting.max_words and draft.word_count > setting.max_words:
        errors.append(
            ValidationError(
                type=VO_ERROR_WORD_COUNT_OUT_OF_RANGE,
                severity="BLOCKER",
                folder_name=folder_name,
                message=f"Wortanzahl {draft.word_count} liegt über dem Maximum {setting.max_words}.",
                fix_hint="Text kürzen.",
            )
        )

    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    forbidden_phrases = _combined_forbidden_phrases(project_brief, style_profile, setting)
    lowered_text = draft.voiceover_text_full.lower()
    for phrase in forbidden_phrases:
        cleaned = phrase.strip().lower()
        if cleaned and cleaned in lowered_text:
            errors.append(
                ValidationError(
                    type=VO_ERROR_FORBIDDEN_TERM_USED,
                    severity="BLOCKER",
                    folder_name=folder_name,
                    message=f"Verbotene Formulierung gefunden: '{phrase.strip()}'.",
                    fix_hint="Formulierung entfernen oder ersetzen.",
                )
            )

    if setting.transition_from_previous and not draft.transition_from_previous_used:
        errors.append(
            ValidationError(
                type=VO_ERROR_MISSING_TRANSITION,
                severity="WARNING",
                folder_name=folder_name,
                message="Übergang vom vorherigen Ort wurde angefordert, aber nicht verwendet.",
                fix_hint="Übergang zu Beginn des Textes ergänzen.",
            )
        )
    if (
        setting.use_contrast_with_previous or setting.use_commonality_with_previous
    ) and not draft.contrast_or_commonality_used:
        errors.append(
            ValidationError(
                type=VO_ERROR_MISSING_CONTRAST_OR_COMMONALITY,
                severity="WARNING",
                folder_name=folder_name,
                message="Kontrast/Gemeinsamkeit zum vorherigen Ort wurde angefordert, aber nicht verwendet.",
                fix_hint="Kontrast oder Gemeinsamkeit zum vorherigen Ort ergänzen.",
            )
        )

    errors.extend(validate_asset_ids_against_inventory(project, folder_name, draft))
    return errors


def _parse_llm_review_errors(raw_errors: Any, folder_name: str) -> list[ValidationError]:
    """Parst die Fehlerliste aus dem Review-LLM.

    Unbekannte Fehlertypen werden NICHT stillschweigend verworfen (Hardening
    nach Phase 4) — sie werden als UNKNOWN_LLM_REVIEW_ERROR mit Severity
    WARNING gespeichert, damit sie in der UI sichtbar bleiben und kein
    Modellverhalten unbemerkt verloren geht."""
    errors: list[ValidationError] = []
    if not isinstance(raw_errors, list):
        return errors
    for raw in raw_errors:
        if not isinstance(raw, dict):
            continue
        raw_type = str(raw.get("type", "")).strip()
        error_type = raw_type.upper()
        severity = str(raw.get("severity", "WARNING")).strip().upper()
        if severity not in {"WARNING", "BLOCKER"}:
            severity = "WARNING"
        sentence_id = str(raw.get("sentence_id", ""))
        fix_hint = str(raw.get("fix_hint", ""))

        if error_type not in VO_ERROR_TYPES_LLM_REVIEW:
            errors.append(
                ValidationError(
                    type=VO_ERROR_UNKNOWN_LLM_REVIEW_ERROR,
                    severity="WARNING",
                    folder_name=folder_name,
                    sentence_id=sentence_id,
                    message=f"LLM returned unknown review error type: {raw_type or '(empty)'}",
                    fix_hint=fix_hint,
                    retryable=False,
                )
            )
            continue

        errors.append(
            ValidationError(
                type=error_type,
                severity=severity,
                folder_name=folder_name,
                sentence_id=sentence_id,
                message=str(raw.get("message", "")),
                fix_hint=fix_hint,
            )
        )
    return errors


def review_folder_voiceover(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    setting: FolderVoiceoverSetting,
    *,
    provider: str,
    model: str,
) -> tuple[list[ValidationError], str]:
    """Ein LLM-Review-Call für die weichen Kriterien (Phase 4 §8). Harte
    Kriterien laufen separat über run_deterministic_checks()."""
    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)

    run_id, run_dir = create_llm_run_dir(project, STAGE_VOICEOVER_REVIEW)
    prompt = build_voiceover_review_prompt(
        project_brief=project_brief, style_profile=style_profile, setting=setting, draft=draft
    )
    prompt_hash = content_hash(prompt)
    write_llm_prompt(run_dir, prompt)
    model_id = resolve_llm_model_id(provider, model)

    try:
        llm_response = generate_plan_text_with_metadata(prompt=prompt, model=model_id)
    except PlanLlmNotConfiguredError as exc:
        write_llm_raw_response(run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model)
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id, stage=STAGE_VOICEOVER_REVIEW, provider=provider, model=model,
                prompt_hash=prompt_hash, status=STATUS_FAIL,
            ),
        )
        # Fehlender API-Key blockiert weiche Kriterien nicht hart — nur als
        # nicht-retryable Warnung sichtbar machen; deterministische Checks
        # laufen unabhängig davon weiter.
        return (
            [
                ValidationError(
                    type="LLM_REVIEW_UNAVAILABLE",
                    severity="WARNING",
                    folder_name=folder_name,
                    message=str(exc),
                    retryable=False,
                )
            ],
            run_id,
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
                run_id=run_id, stage=STAGE_VOICEOVER_REVIEW, provider=llm_response.provider,
                model=llm_response.model, prompt_hash=prompt_hash, status=STATUS_PARSE_FAILED,
                latency_ms=llm_response.latency_ms, token_usage=llm_response.token_usage,
            ),
        )
        return (
            [
                ValidationError(
                    type="LLM_REVIEW_PARSE_FAILED",
                    severity="WARNING",
                    folder_name=folder_name,
                    message=str(exc),
                    retryable=True,
                )
            ],
            run_id,
        )

    errors = _parse_llm_review_errors(payload.get("errors", []), folder_name)
    write_llm_parsed_response(run_dir, {"errors": [error.model_dump(mode="json") for error in errors]})
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id, stage=STAGE_VOICEOVER_REVIEW, provider=llm_response.provider,
            model=llm_response.model, prompt_hash=prompt_hash, status=STATUS_PASS,
            latency_ms=llm_response.latency_ms, token_usage=llm_response.token_usage,
        ),
    )
    return errors, run_id


def build_correction_prompt(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    errors: list[ValidationError],
) -> str:
    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    setting = _resolve_setting(project, folder_name, draft)
    return build_voiceover_correction_prompt(
        project_brief=project_brief,
        style_profile=style_profile,
        setting=setting,
        draft=draft,
        errors=errors,
    )


def apply_corrected_voiceover(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    raw_text: str,
    *,
    correction_run_id: str,
) -> FolderVoiceoverDraft:
    """Parst eine Correction-Antwort, saniert Asset-IDs und persistiert das
    Ergebnis. Wirft ValueError bei ungültigem JSON — der Aufrufer entscheidet
    dann, den vorherigen Draft NICHT zu verlieren."""
    payload = parse_folder_voiceover_response(raw_text)
    inventory_assets = build_inventory_asset_context(project, folder_name)
    valid_asset_ids = {asset["asset_id"] for asset in inventory_assets}
    sentence_items = _sanitize_sentence_items(
        _parse_sentence_items(payload.get("sentence_items", [])), valid_asset_ids
    )
    voiceover_text_full = str(payload.get("voiceover_text_full", ""))

    updated = draft.model_copy(
        update={
            "voiceover_text_full": voiceover_text_full,
            "word_count": _count_words(voiceover_text_full),
            "sentence_items": sentence_items,
            "transition_from_previous_used": bool(
                payload.get("transition_from_previous_used", draft.transition_from_previous_used)
            ),
            "callback_to_previous_used": bool(
                payload.get("callback_to_previous_used", draft.callback_to_previous_used)
            ),
            "contrast_or_commonality_used": bool(
                payload.get("contrast_or_commonality_used", draft.contrast_or_commonality_used)
            ),
            "used_asset_evidence": [
                item.primary_asset_id for item in sentence_items if item.primary_asset_id
            ],
            "correction_run_ids": [*draft.correction_run_ids, correction_run_id],
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_folder_voiceover_draft_item(project, updated)
    return updated


def run_folder_voiceover_review_loop(
    project: Project,
    folder_name: str,
    *,
    provider: str,
    model: str,
) -> FolderVoiceoverValidationReport:
    """Orchestriert Checks -> LLM-Review -> ggf. Correction, maximal
    MAX_VOICEOVER_REVIEW_ATTEMPTS Durchläufe. Setzt NIE automatisch CONFIRMED."""
    draft = get_folder_voiceover_draft(project, folder_name)
    if draft is None:
        raise ValueError(
            f"Kein Voice-over-Entwurf für '{folder_name}' vorhanden — bitte zuerst generieren."
        )

    setting = _resolve_setting(project, folder_name, draft)
    author_run_ids = [draft.author_run_id] if draft.author_run_id else []
    review_run_ids: list[str] = []
    correction_run_ids: list[str] = list(draft.correction_run_ids)
    current_draft = draft
    attempt = 0
    final_errors: list[ValidationError] = []
    final_warnings: list[ValidationError] = []
    status = VOICEOVER_STATUS_NEEDS_USER_REVIEW

    while attempt < MAX_VOICEOVER_REVIEW_ATTEMPTS:
        attempt += 1
        deterministic_errors = run_deterministic_checks(project, folder_name, current_draft, setting)
        llm_errors, review_run_id = review_folder_voiceover(
            project, folder_name, current_draft, setting, provider=provider, model=model
        )
        review_run_ids.append(review_run_id)

        combined = deterministic_errors + llm_errors
        blockers = [error for error in combined if error.severity == "BLOCKER"]
        warnings = [error for error in combined if error.severity != "BLOCKER"]
        final_errors, final_warnings = blockers, warnings

        if not blockers:
            status = VOICEOVER_STATUS_PASS
            break

        if attempt >= MAX_VOICEOVER_REVIEW_ATTEMPTS:
            status = VOICEOVER_STATUS_NEEDS_USER_REVIEW
            break

        correction_prompt = build_correction_prompt(project, folder_name, current_draft, blockers)
        correction_run_id, correction_run_dir = create_llm_run_dir(project, STAGE_VOICEOVER_CORRECTION)
        correction_run_ids.append(correction_run_id)
        prompt_hash = content_hash(correction_prompt)
        write_llm_prompt(correction_run_dir, correction_prompt)
        model_id = resolve_llm_model_id(provider, model)

        try:
            llm_response = generate_plan_text_with_metadata(prompt=correction_prompt, model=model_id)
        except PlanLlmNotConfiguredError as exc:
            write_llm_raw_response(
                correction_run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model
            )
            write_llm_parsed_response(correction_run_dir, {"parse_error": str(exc)})
            write_llm_manifest(
                correction_run_dir,
                LlmRunManifest(
                    run_id=correction_run_id, stage=STAGE_VOICEOVER_CORRECTION, provider=provider,
                    model=model, prompt_hash=prompt_hash, status=STATUS_FAIL,
                ),
            )
            status = VOICEOVER_STATUS_NEEDS_USER_REVIEW
            break

        write_llm_raw_response(
            correction_run_dir,
            raw_text=llm_response.raw_text,
            provider=llm_response.provider,
            model=llm_response.model,
            latency_ms=llm_response.latency_ms,
            token_usage=llm_response.token_usage,
        )

        try:
            current_draft = apply_corrected_voiceover(
                project, folder_name, current_draft, llm_response.raw_text,
                correction_run_id=correction_run_id,
            )
        except (ValueError, TypeError) as exc:
            write_llm_parsed_response(correction_run_dir, {"parse_error": str(exc)})
            write_llm_manifest(
                correction_run_dir,
                LlmRunManifest(
                    run_id=correction_run_id, stage=STAGE_VOICEOVER_CORRECTION,
                    provider=llm_response.provider, model=llm_response.model,
                    prompt_hash=prompt_hash, status=STATUS_PARSE_FAILED,
                    latency_ms=llm_response.latency_ms, token_usage=llm_response.token_usage,
                ),
            )
            status = VOICEOVER_STATUS_NEEDS_USER_REVIEW
            break

        write_llm_parsed_response(correction_run_dir, current_draft.model_dump(mode="json"))
        write_llm_manifest(
            correction_run_dir,
            LlmRunManifest(
                run_id=correction_run_id, stage=STAGE_VOICEOVER_CORRECTION,
                provider=llm_response.provider, model=llm_response.model,
                prompt_hash=prompt_hash, status=STATUS_PASS,
                latency_ms=llm_response.latency_ms, token_usage=llm_response.token_usage,
            ),
        )

    final_draft = current_draft.model_copy(
        update={
            "status": status,
            "review_run_id": review_run_ids[-1] if review_run_ids else current_draft.review_run_id,
            "correction_run_ids": correction_run_ids,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_folder_voiceover_draft_item(project, final_draft)

    report = FolderVoiceoverValidationReport(
        project_id=project.id,
        folder_name=folder_name,
        attempt_count=attempt,
        status=status,
        errors=final_errors,
        warnings=final_warnings,
        author_run_ids=author_run_ids,
        review_run_ids=review_run_ids,
        correction_run_ids=correction_run_ids,
    )
    save_validation_report(project, report)
    return report


def load_validation_reports(project: Project) -> FolderVoiceoverValidationReportsDocument:
    path = get_folder_voiceover_validation_report_path(project.work_dir_path)
    if not path.is_file():
        return FolderVoiceoverValidationReportsDocument(project_id=project.id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FolderVoiceoverValidationReportsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return FolderVoiceoverValidationReportsDocument(project_id=project.id)


def save_validation_report(
    project: Project, report: FolderVoiceoverValidationReport
) -> FolderVoiceoverValidationReportsDocument:
    document = load_validation_reports(project)
    reports = dict(document.reports)
    reports[report.folder_name] = report
    updated = document.model_copy(update={"project_id": project.id, "reports": reports})
    path = get_folder_voiceover_validation_report_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    return updated


def confirm_folder_voiceover(project: Project, folder_name: str) -> FolderVoiceoverDraft:
    """Explizite Nutzerbestätigung — wird NIE automatisch nach PASS ausgelöst."""
    draft = get_folder_voiceover_draft(project, folder_name)
    if draft is None:
        raise ValueError(f"Kein Voice-over-Entwurf für '{folder_name}' vorhanden.")

    confirmed_item = draft.model_copy(
        update={
            "status": VOICEOVER_STATUS_CONFIRMED,
            "confirmed_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_folder_voiceover_draft_item(project, confirmed_item)

    confirmed_document = load_folder_voiceovers_confirmed(project)
    items = [item for item in confirmed_document.items if item.folder_name != folder_name]
    items.append(confirmed_item)
    save_folder_voiceovers_confirmed(project, confirmed_document.model_copy(update={"items": items}))
    return confirmed_item


def unconfirm_folder_voiceover(project: Project, folder_name: str) -> FolderVoiceoverDraft:
    """Nimmt eine Bestätigung zurück — entfernt den Ordner aus der confirmed-Datei."""
    confirmed_document = load_folder_voiceovers_confirmed(project)
    items = [item for item in confirmed_document.items if item.folder_name != folder_name]
    save_folder_voiceovers_confirmed(project, confirmed_document.model_copy(update={"items": items}))

    draft = get_folder_voiceover_draft(project, folder_name)
    if draft is None:
        raise ValueError(f"Kein Voice-over-Entwurf für '{folder_name}' vorhanden.")
    reverted = draft.model_copy(
        update={
            "status": VOICEOVER_STATUS_PASS,
            "confirmed_at": None,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_folder_voiceover_draft_item(project, reverted)
    return reverted
