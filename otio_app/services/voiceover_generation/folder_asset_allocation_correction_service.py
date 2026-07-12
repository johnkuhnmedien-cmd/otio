"""Nutzervorgabe (Juli 2026): eigenständiger Correction-Loop für die
Asset-Allokations-Diagnose aus `folder_asset_readiness.py` (Closing Shot
fehlt/wiederholt einen der letzten zwei Sätze, Asset über dem folder-weiten
Nutzungslimit, Mindestabstand zwischen Wiederverwendungen unterschritten,
knappes Asset an einen flexibleren Satz vergeben).

Bewusst GETRENNT von `voiceover_review_service.py` (Text-/Stil-Review-
Loop): dieser Loop repariert AUSSCHLIESSLICH die Asset-Zuordnung (inkl.
Closing Shot, siehe `ClosingVisualPlan`) — der redaktionelle Text soll
dabei möglichst unverändert bleiben. Läuft ausschließlich bei explizitem
Nutzerklick (Einzel-Button im Folder-Voice-overs-Tab) — niemals automatisch
nach der Generierung oder nach dem Text-Review-Loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.defaults import MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS
from otio_app.models import Project
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_asset_readiness import (
    READINESS_STATUS_PASS,
    FolderAssetReadinessReport,
    SentenceAssetReadinessIssue,
    build_folder_asset_readiness_report,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    load_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_ASSET_ALLOCATION_CORRECTION,
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
    LlmRunManifest,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.prompts import build_asset_allocation_correction_prompt
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.services.voiceover_generation.voiceover_author_service import (
    _count_words,
    _parse_closing_visual_plan,
    _parse_sentence_items,
    _sanitize_closing_visual_plan,
    _sanitize_sentence_items,
    build_inventory_asset_context,
    get_folder_voiceover_draft,
    load_folder_voiceovers_draft,
    parse_folder_voiceover_response,
    upsert_folder_voiceover_draft_item,
)

__all__ = [
    "ASSET_ALLOCATION_CORRECTION_STATUS_PASS",
    "ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW",
    "ASSET_ALLOCATION_CORRECTION_STATUS_FAILED",
    "AssetAllocationCorrectionResult",
    "apply_asset_allocation_correction",
    "run_asset_allocation_correction",
    "run_all_asset_allocation_corrections",
]

ASSET_ALLOCATION_CORRECTION_STATUS_PASS = "PASS"
ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW = "NEEDS_USER_REVIEW"
ASSET_ALLOCATION_CORRECTION_STATUS_FAILED = "FAILED"


def _resolve_setting(project: Project, folder_name: str, draft: FolderVoiceoverDraft) -> FolderVoiceoverSetting:
    """Kleine, bewusst eigenständige Kopie von `voiceover_review_service.
    _resolve_setting` — dieses Modul soll unabhängig von der Review-Loop-
    Implementierung bleiben (siehe Moduldocstring)."""
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


@dataclass
class AssetAllocationCorrectionResult:
    """Ergebnis EINES `run_asset_allocation_correction`-Laufs — `draft` ist
    IMMER der zuletzt erfolgreich persistierte Stand (auch bei FAILED/
    NEEDS_USER_REVIEW), niemals None."""

    status: str
    draft: FolderVoiceoverDraft
    attempt_count: int = 0
    remaining_issues: list[SentenceAssetReadinessIssue] = field(default_factory=list)
    correction_run_ids: list[str] = field(default_factory=list)
    error: str = ""


def apply_asset_allocation_correction(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    raw_text: str,
    *,
    correction_run_id: str,
) -> FolderVoiceoverDraft:
    """Parst eine Asset-Allokations-Correction-Antwort, saniert Asset-IDs
    (sentence_items UND closing_visual_plan) und persistiert das Ergebnis.
    Wirft ValueError/TypeError bei ungültigem JSON — der Aufrufer entscheidet
    dann, den vorherigen Draft NICHT zu verlieren (siehe run_
    asset_allocation_correction)."""
    payload = parse_folder_voiceover_response(raw_text)
    inventory_assets = build_inventory_asset_context(project, folder_name)
    valid_asset_ids = {asset["asset_id"] for asset in inventory_assets}
    sentence_items = _sanitize_sentence_items(
        _parse_sentence_items(payload.get("sentence_items", [])), valid_asset_ids
    )
    closing_visual_plan = _sanitize_closing_visual_plan(
        _parse_closing_visual_plan(payload.get("closing_visual_plan")), valid_asset_ids
    )
    voiceover_text_full = str(payload.get("voiceover_text_full") or draft.voiceover_text_full)

    updated = draft.model_copy(
        update={
            "voiceover_text_full": voiceover_text_full,
            "word_count": _count_words(voiceover_text_full),
            "sentence_items": sentence_items,
            "closing_visual_plan": closing_visual_plan,
            "used_asset_evidence": [item.primary_asset_id for item in sentence_items if item.primary_asset_id],
            "correction_run_ids": [*draft.correction_run_ids, correction_run_id],
            "updated_at": datetime.now(timezone.utc),
        }
    )
    upsert_folder_voiceover_draft_item(project, updated)
    return updated


def _write_failed_manifest(
    run_dir, run_id: str, *, provider: str, model: str, prompt_hash: str, error: str, status: str
) -> None:
    write_llm_raw_response(run_dir, raw_text=f"ERROR: {error}", provider=provider, model=model)
    write_llm_parsed_response(run_dir, {"parse_error": error})
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id,
            stage=STAGE_ASSET_ALLOCATION_CORRECTION,
            provider=provider,
            model=model,
            prompt_hash=prompt_hash,
            status=status,
        ),
    )


def run_asset_allocation_correction(
    project: Project,
    folder_name: str,
    *,
    provider: str,
    model: str,
) -> AssetAllocationCorrectionResult:
    """Orchestriert Diagnose -> ggf. LLM-Correction -> Diagnose erneut,
    maximal MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS Durchläufe. Bricht
    sofort mit PASS ab, sobald `build_folder_asset_readiness_report` keine
    Issues mehr findet — läuft NIE automatisch, nur bei explizitem Klick.

    Wirft ValueError, wenn kein Entwurf für diesen Ordner existiert."""
    draft = get_folder_voiceover_draft(project, folder_name)
    if draft is None:
        raise ValueError(f"Kein Voice-over-Entwurf für '{folder_name}' vorhanden — bitte zuerst generieren.")

    current_draft = draft
    correction_run_ids: list[str] = []
    report: FolderAssetReadinessReport = build_folder_asset_readiness_report(project, current_draft)
    if report.status == READINESS_STATUS_PASS:
        return AssetAllocationCorrectionResult(
            status=ASSET_ALLOCATION_CORRECTION_STATUS_PASS, draft=current_draft, attempt_count=0
        )

    setting = _resolve_setting(project, folder_name, current_draft)
    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    model_id = resolve_llm_model_id(provider, model)

    attempt = 0
    while attempt < MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS:
        attempt += 1
        inventory_assets = build_inventory_asset_context(project, folder_name)
        prompt = build_asset_allocation_correction_prompt(
            project_brief=project_brief,
            style_profile=style_profile,
            setting=setting,
            draft=current_draft,
            inventory_assets=inventory_assets,
            issues=report.issues,
        )
        prompt_hash = content_hash(prompt)
        run_id, run_dir = create_llm_run_dir(project, STAGE_ASSET_ALLOCATION_CORRECTION)
        correction_run_ids.append(run_id)
        write_llm_prompt(run_dir, prompt)

        try:
            llm_response = generate_plan_text_with_metadata(prompt=prompt, model=model_id)
        except Exception as exc:  # noqa: BLE001 — jeder LLM-/SDK-/Netzwerkfehler soll als
            # kontrollierter FAILED-Status zurückkommen statt die Streamlit-Seite crashen zu
            # lassen (nicht nur der eng gefasste PlanLlmNotConfiguredError-Fall).
            _write_failed_manifest(
                run_dir, run_id, provider=provider, model=model, prompt_hash=prompt_hash,
                error=str(exc), status=STATUS_FAIL,
            )
            return AssetAllocationCorrectionResult(
                status=ASSET_ALLOCATION_CORRECTION_STATUS_FAILED,
                draft=current_draft,
                attempt_count=attempt,
                remaining_issues=report.issues,
                correction_run_ids=correction_run_ids,
                error=str(exc),
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
            current_draft = apply_asset_allocation_correction(
                project, folder_name, current_draft, llm_response.raw_text, correction_run_id=run_id
            )
        except (ValueError, TypeError) as exc:
            _write_failed_manifest(
                run_dir, run_id, provider=llm_response.provider, model=llm_response.model,
                prompt_hash=prompt_hash, error=str(exc), status=STATUS_PARSE_FAILED,
            )
            return AssetAllocationCorrectionResult(
                status=ASSET_ALLOCATION_CORRECTION_STATUS_FAILED,
                draft=current_draft,
                attempt_count=attempt,
                remaining_issues=report.issues,
                correction_run_ids=correction_run_ids,
                error=str(exc),
            )

        write_llm_parsed_response(run_dir, current_draft.model_dump(mode="json"))
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_ASSET_ALLOCATION_CORRECTION,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_PASS,
                latency_ms=llm_response.latency_ms,
                token_usage=llm_response.token_usage,
            ),
        )

        report = build_folder_asset_readiness_report(project, current_draft)
        if report.status == READINESS_STATUS_PASS:
            return AssetAllocationCorrectionResult(
                status=ASSET_ALLOCATION_CORRECTION_STATUS_PASS,
                draft=current_draft,
                attempt_count=attempt,
                correction_run_ids=correction_run_ids,
            )

    return AssetAllocationCorrectionResult(
        status=ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW,
        draft=current_draft,
        attempt_count=attempt,
        remaining_issues=report.issues,
        correction_run_ids=correction_run_ids,
    )


def run_all_asset_allocation_corrections(
    project: Project,
    *,
    provider: str,
    model: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[AssetAllocationCorrectionResult]:
    """Bulk-Variante von `run_asset_allocation_correction` für alle aktiven
    Ordner MIT Entwurf — sequenziell. Ordner ohne Auffälligkeiten enden
    sofort mit PASS (attempt_count=0), ohne LLM-Aufruf. Wirft ValueError
    ohne bestätigte Dramaturgie."""
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        raise ValueError("Keine bestätigte Dramaturgie vorhanden.")
    draft_folder_names = {item.folder_name for item in load_folder_voiceovers_draft(project).items}
    folder_names = [
        entry.folder_name
        for entry in sorted(plan.recommended_folder_order, key=lambda entry: entry.order_index)
        if entry.enabled and entry.folder_name in draft_folder_names
    ]
    results: list[AssetAllocationCorrectionResult] = []
    total = len(folder_names)
    for index, folder_name in enumerate(folder_names, start=1):
        if progress_callback is not None:
            progress_callback(folder_name, index, total)
        results.append(
            run_asset_allocation_correction(
                project, folder_name, provider=provider, model=model
            )
        )
    return results
