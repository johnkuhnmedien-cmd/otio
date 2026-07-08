"""Phase 8.4-9.1: Cut-Plan-Tab — Validierung, Visual Coverage, Supplement Bridge,
Confirm+Trace, isolierte EditPlan-Bridge.

Baut auf Phase 8.2 (Timeline-/Audio-Platzierung) und Phase 8.3 (Asset-
Auswahl, Fallback, Dauer-/Split-/Merge) auf und ergänzt die vollständige
Cut-Plan-Validierung (siehe cut_plan_validator.py). Phase 8.5 ergänzt den
Visual-Coverage-Fix (siehe cut_plan_visual_coverage.py), der bereits
während der Asset-Auswahl automatisch angewendet wird, damit initialer
Audio-Vorlauf und Sektions-Pausen nicht zu Schwarzbild führen. Phase 8.6
ergänzt eine isolierte Supplement Bridge (siehe cut_plan_supplement_bridge.py):
Kandidatensuche und Downloads laufen AUSSCHLIESSLICH bei explizitem
Nutzerklick, niemals automatisch. Phase 8.7 ergänzt Confirm + Trace (siehe
cut_plan_confirm_service.py/cut_plan_trace_service.py): ein bereits
validierter Draft kann explizit als unveränderlicher Snapshot bestätigt
werden. Phase 9.1 ergänzt eine isolierte EditPlan-Bridge (siehe
cut_plan_edit_plan_bridge.py/cut_plan_edit_plan_trace.py): der bestätigte
Cut Plan wird deterministisch in einen EditPlanDocument-kompatiblen Draft
übersetzt — komplett getrennt von der bestehenden Produktions-EditPlan-
Pipeline. Phase 9.2 härtet diese Bridge: Boundary-Chaining pro Track
verhindert 1-Frame-Gaps/-Overlaps, eine strukturierte bridge_audio_plan.json
ersetzt das Rätselraten am TimelineItem-Sondertyp 'voiceover_audio', und die
Bridge-Validierung wurde verschärft. Phase 9.3 ergänzt Confirm/Freeze (siehe
cut_plan_edit_plan_confirm_service.py): ein bereits validierter Bridge-Draft
kann explizit als unveränderlicher Snapshot eingefroren werden — weiterhin
KEIN Produktions-EditPlan, KEIN locked Produktionsplan, KEIN OTIO-Export,
kein Render, keine neue LLM-Planung."""

from __future__ import annotations

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_BLOCKED,
    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
    CUT_PLAN_VALIDATION_STATUS_BLOCKED,
    CUT_PLAN_VALIDATION_STATUS_PASS,
    CUT_PLAN_VALIDATION_STATUS_WARNING,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_WARNING,
    PLAN_STATUS_READY_FOR_CUT,
    PRODUCTION_EDIT_PLAN_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_STATUS_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_STATUS_STAGED,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_draft_path,
    get_cut_plan_edit_plan_bridge_audio_plan_path,
    get_cut_plan_edit_plan_bridge_draft_path,
    get_cut_plan_settings_path,
    get_cut_plan_supplement_requests_path,
    get_cut_plan_trace_path,
    get_cut_plan_validation_report_path,
    get_folder_edit_plan_path,
    get_production_edit_plan_mapping_trace_path,
    get_production_edit_plan_package_path,
    get_production_edit_plan_validation_report_path,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.supplement_search import build_keyword_query
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    is_cut_plan_draft_stale,
    is_cut_plan_settings_stale,
    load_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import (
    can_confirm_cut_plan,
    confirm_cut_plan,
    is_confirmed_cut_plan_stale,
    load_confirmed_cut_plan,
    unconfirm_cut_plan,
)
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanSettings,
    CutPlanValidationReport,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import (
    load_cut_plan_settings,
    save_cut_plan_settings,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    accept_cut_plan_supplement_candidate,
    build_supplement_requests_from_cut_plan,
    load_cut_plan_supplement_candidates_for_request,
    load_cut_plan_supplement_requests,
    save_cut_plan_supplement_requests,
    search_candidates_for_cut_plan_request,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    build_bridge_audio_plan_from_confirmed_cut_plan,
    build_edit_plan_draft_from_confirmed_cut_plan,
    is_edit_plan_bridge_stale,
    load_bridge_audio_plan,
    load_edit_plan_bridge_draft,
    load_edit_plan_bridge_validation_report,
    save_bridge_audio_plan,
    save_edit_plan_bridge_draft,
    validate_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service import (
    can_confirm_edit_plan_bridge,
    confirm_edit_plan_bridge,
    is_confirmed_edit_plan_bridge_stale,
    load_confirmed_bridge_audio_plan,
    load_confirmed_bridge_trace,
    load_confirmed_edit_plan_bridge,
    load_edit_plan_bridge_confirm_manifest,
    unconfirm_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import (
    build_edit_plan_bridge_trace,
    load_edit_plan_bridge_trace,
    save_edit_plan_bridge_trace,
)
from otio_app.services.voiceover_generation.cut_plan_trace_service import load_cut_plan_trace
from otio_app.services.voiceover_generation.cut_plan_validator import (
    content_hash_of_cut_plan_content,
    load_cut_plan_validation_report,
)
from otio_app.services.voiceover_generation.final_plan_service import (
    load_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    build_and_save_production_edit_plan_staging,
    can_build_production_edit_plan_staging,
    is_production_edit_plan_staging_stale,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_trace import (
    load_production_edit_plan_mapping_trace,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    is_production_edit_plan_validation_report_stale,
    load_production_edit_plan_validation_report,
    validate_production_edit_plan_staging,
)
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import require_without_voiceover_mode

import streamlit as st


def _render_source_plan_status(project: Project) -> None:
    st.subheader("Voraussetzung: bestätigter Voice-over-Projektplan")
    plan = load_confirmed_voiceover_project_plan(project)

    if plan is None:
        st.warning(
            "Noch kein `confirmed_voiceover_project_plan.json` vorhanden. "
            "Bitte zuerst im Tab „⑦ Final Output“ einen finalen Plan erstellen."
        )
        return

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Projekt", plan.project_title or "(ohne Titel)")
    with col2:
        st.metric("Sprache", plan.language)
    with col3:
        st.metric("Project Plan Status", plan.status)
    with col4:
        st.metric("Ready for Cut", "✅ Ja" if plan.status == PLAN_STATUS_READY_FOR_CUT else "❌ Nein")

    if plan.status != PLAN_STATUS_READY_FOR_CUT:
        st.info(
            "Der Voice-over-Projektplan ist noch nicht vollständig schnittbereit. "
            "Ein Cut-Plan-Entwurf wird in einer späteren Phase auch aus einem "
            "noch nicht vollständig bereiten Plan erzeugbar sein (zur Diagnose), "
            "eine Bestätigung des Cut Plans wird aber erst möglich sein, wenn "
            "„Ready for Cut“ hier ja ist."
        )


def _render_settings_editor(project: Project) -> CutPlanSettings:
    st.subheader("Cut Plan Settings")
    st.caption(
        "Eigenständige Einstellungen für diese Pipeline — unabhängig von den "
        "Schnittplan-Regeln des „Projekt mit Voice-Over“-Workflows."
    )
    settings = load_cut_plan_settings(project)

    col1, col2, col3 = st.columns(3)
    with col1:
        initial_audio_offset_sec = st.number_input(
            "Initial Audio Offset (s)",
            min_value=0.0, max_value=10.0, value=settings.initial_audio_offset_sec, step=0.1,
            key=f"cut_plan_initial_offset_{project.id}",
            help="Gilt einmalig am Anfang des gesamten Videos, nicht pro Sektion.",
        )
        pause_between_sections_sec = st.number_input(
            "Pause zwischen Sektionen (s)",
            min_value=0.0, max_value=5.0, value=settings.pause_between_sections_sec, step=0.05,
            key=f"cut_plan_pause_{project.id}",
            help="Pause zwischen Intro und Foldern bzw. zwischen Foldern. "
            "Kein Schwarzbild — das letzte Visual der vorherigen Sektion wird gehalten.",
        )
        section_visual_preroll_sec = st.number_input(
            "Visueller Vorlauf je Sektion (s)",
            min_value=0.0, max_value=5.0, value=settings.section_visual_preroll_sec, step=0.1,
            key=f"cut_plan_preroll_{project.id}",
        )
        video_head_trim_sec = st.number_input(
            "Video Head Trim (s) — nur Video, nie Bild/Audio",
            min_value=0.0, max_value=5.0, value=settings.video_head_trim_sec, step=0.1,
            key=f"cut_plan_head_trim_{project.id}",
        )
    with col2:
        shot_min_sec = st.number_input(
            "Shot Min (s)", min_value=0.5, max_value=30.0, value=settings.shot_min_sec, step=0.5,
            key=f"cut_plan_shot_min_{project.id}",
        )
        shot_max_sec = st.number_input(
            "Shot Max (s)", min_value=0.5, max_value=60.0, value=settings.shot_max_sec, step=0.5,
            key=f"cut_plan_shot_max_{project.id}",
        )
        max_asset_usage = st.number_input(
            "Max Asset Usage (global, Intro zählt mit)",
            min_value=1, max_value=20, value=settings.max_asset_usage, step=1,
            key=f"cut_plan_max_usage_{project.id}",
        )
        min_asset_reuse_distance_shots = st.number_input(
            "Min. Wiederverwendungsabstand (Shots)",
            min_value=0, max_value=50, value=settings.min_asset_reuse_distance_shots, step=1,
            key=f"cut_plan_min_reuse_distance_{project.id}",
        )
    with col3:
        timeline_fps = st.number_input(
            "Timeline FPS", min_value=1, max_value=120, value=settings.timeline_fps, step=1,
            key=f"cut_plan_fps_{project.id}",
        )
        timeline_width = st.number_input(
            "Timeline Breite (px)", min_value=1, max_value=10000, value=settings.timeline_width, step=1,
            key=f"cut_plan_width_{project.id}",
        )
        timeline_height = st.number_input(
            "Timeline Höhe (px)", min_value=1, max_value=10000, value=settings.timeline_height, step=1,
            key=f"cut_plan_height_{project.id}",
        )

    updated = settings.model_copy(
        update={
            "initial_audio_offset_sec": float(initial_audio_offset_sec),
            "pause_between_sections_sec": float(pause_between_sections_sec),
            "section_visual_preroll_sec": float(section_visual_preroll_sec),
            "video_head_trim_sec": float(video_head_trim_sec),
            "shot_min_sec": float(shot_min_sec),
            "shot_max_sec": float(shot_max_sec),
            "max_asset_usage": int(max_asset_usage),
            "min_asset_reuse_distance_shots": int(min_asset_reuse_distance_shots),
            "timeline_fps": int(timeline_fps),
            "timeline_width": int(timeline_width),
            "timeline_height": int(timeline_height),
        }
    )

    col_save, col_reload = st.columns(2)
    with col_save:
        if st.button("Cut Plan Settings speichern", key=f"cut_plan_settings_save_{project.id}"):
            save_cut_plan_settings(project, updated)
            st.success("Cut Plan Settings gespeichert.")
            st.rerun()
    with col_reload:
        if st.button("Neu laden", key=f"cut_plan_settings_reload_{project.id}"):
            st.rerun()

    return updated


def _render_future_artifact_paths(project: Project) -> None:
    st.subheader("Künftige Artefakt-Pfade")
    st.caption(
        "cut_plan_settings.json und cut_plan.draft.json existieren bereits, sobald "
        "gespeichert bzw. erzeugt. Die übrigen Dateien folgen in späteren Sub-Phasen."
    )
    st.caption(f"Cut Plan Settings: `{get_cut_plan_settings_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Draft: `{get_cut_plan_draft_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Validation Report: `{get_cut_plan_validation_report_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Confirmed: `{get_cut_plan_confirmed_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Trace: `{get_cut_plan_trace_path(project.work_dir_path)}`")
    st.caption(
        f"Supplement Requests (isoliert): `{get_cut_plan_supplement_requests_path(project.work_dir_path)}`"
    )


def _render_visual_segments(item) -> None:
    if not item.planned_visual_segments:
        st.caption("Keine VisualSegments geplant.")
        return
    rows = [
        {
            "segment_id": segment.segment_id,
            "timeline_in_sec": segment.timeline_in_sec,
            "timeline_out_sec": segment.timeline_out_sec,
            "duration_sec": segment.duration_sec,
            "asset_id": segment.asset_id,
            "asset_type": segment.asset_type,
            "source_in_sec": segment.source_in_sec,
            "source_out_sec": segment.source_out_sec,
            "reason": segment.reason,
        }
        for segment in item.planned_visual_segments
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_cut_plan_draft(project: Project, draft: CutPlanDocument) -> None:
    st.subheader("Cut Plan Draft")

    if is_cut_plan_draft_stale(project, draft):
        st.warning("Der Cut Plan Draft ist veraltet. Bitte neu erzeugen.")
    if is_cut_plan_settings_stale(project, draft):
        st.warning("Die Cut-Plan-Settings wurden seit Draft-Erzeugung geändert. Bitte Draft neu erzeugen.")

    status_counts = {
        CUT_PLAN_ASSET_SELECTION_PRIMARY_USED: 0,
        CUT_PLAN_ASSET_SELECTION_BACKUP_USED: 0,
        CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED: 0,
        CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED: 0,
        CUT_PLAN_ASSET_SELECTION_BLOCKED: 0,
    }
    for item in draft.items:
        if item.asset_selection_status in status_counts:
            status_counts[item.asset_selection_status] += 1
    total_segments = sum(len(item.planned_visual_segments) for item in draft.items)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Status", draft.status)
    with col2:
        st.metric("Audio Items", len(draft.audio_items))
    with col3:
        st.metric("Cut Plan Items", len(draft.items))
    with col4:
        st.metric("VisualSegments", total_segments)

    col5, col6, col7, col8, col9 = st.columns(5)
    with col5:
        st.metric("PRIMARY_USED", status_counts[CUT_PLAN_ASSET_SELECTION_PRIMARY_USED])
    with col6:
        st.metric("BACKUP_USED", status_counts[CUT_PLAN_ASSET_SELECTION_BACKUP_USED])
    with col7:
        st.metric("SUPPLEMENT_REQUIRED", status_counts[CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED])
    with col8:
        st.metric("SUPPLEMENT_USED", status_counts[CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED])
    with col9:
        st.metric("BLOCKED", status_counts[CUT_PLAN_ASSET_SELECTION_BLOCKED])

    col10, col11 = st.columns(2)
    with col10:
        st.metric("Warnings", len(draft.warnings))
    with col11:
        st.metric("Blocker", len(draft.blockers))

    with st.expander("Audio Items (A1)", expanded=False):
        if not draft.audio_items:
            st.caption("Keine Audio Items vorhanden.")
        else:
            rows = [
                {
                    "scope": item.scope,
                    "folder_name": item.folder_name,
                    "audio_path": item.audio_path,
                    "timeline_start_sec": item.timeline_start_sec,
                    "timeline_end_sec": item.timeline_end_sec,
                    "duration_sec": item.duration_sec,
                    "track": item.track,
                }
                for item in draft.audio_items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("Cut Plan Items", expanded=True):
        if not draft.items:
            st.caption("Keine Cut Plan Items vorhanden.")
        else:
            rows = [
                {
                    "cut_item_id": item.cut_item_id,
                    "scope": item.source_scope,
                    "folder_name": item.folder_name,
                    "text": item.text,
                    "timeline_start_sec": item.timeline_start_sec,
                    "timeline_end_sec": item.timeline_end_sec,
                    "primary_asset_id": item.primary_asset_id,
                    "chosen_asset_id": item.chosen_asset_id or "—",
                    "asset_selection_status": item.asset_selection_status,
                    "duration_strategy": item.duration_strategy or "—",
                    "number_of_visual_segments": len(item.planned_visual_segments),
                    "asset_selection_reason": item.asset_selection_reason or "—",
                    "fallback_reason": item.fallback_reason or "—",
                    "warnings": ", ".join(item.warnings) or "—",
                    "blockers": ", ".join(item.blockers) or "—",
                }
                for item in draft.items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

        with st.expander("VisualSegments je Item", expanded=False):
            for item in draft.items:
                st.caption(f"**{item.cut_item_id}** — {item.text[:80]}")
                _render_visual_segments(item)

    if draft.asset_usage_summary:
        with st.expander("Asset Usage Summary", expanded=False):
            rows = [
                {"asset_id": asset_id, "usage_count": count}
                for asset_id, count in sorted(draft.asset_usage_summary.items())
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    if draft.blockers or draft.warnings:
        with st.expander("Warnungen / Blocker", expanded=bool(draft.blockers)):
            for error in draft.blockers:
                location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
                st.error(f"[{error.type}]{location}: {error.message}")
            for error in draft.warnings:
                location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
                st.warning(f"[{error.type}]{location}: {error.message}")

    st.divider()
    _render_validation_report(project, draft)
    st.caption("🚧 Confirm/Lock, EditPlanDocument-Übersetzung, OTIO-Export folgen in späteren Sub-Phasen.")


def _render_validation_report(project: Project, draft: CutPlanDocument) -> None:
    st.subheader("Validation Report")
    report: CutPlanValidationReport | None = load_cut_plan_validation_report(project)

    if report is None:
        st.info("Noch kein Validation Report vorhanden. Bitte „Cut Plan validieren“ klicken.")
        return

    current_hash = content_hash_of_cut_plan_content(draft)
    if report.cut_plan_hash != current_hash:
        st.warning(
            "Der Validation Report ist veraltet — der Cut Plan hat sich seit der letzten "
            "Validierung geändert (z. B. durch erneute Asset-Auswahl). Bitte erneut validieren."
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        if report.status == CUT_PLAN_VALIDATION_STATUS_PASS:
            st.metric("Validation Status", "✅ PASS")
        elif report.status == CUT_PLAN_VALIDATION_STATUS_WARNING:
            st.metric("Validation Status", "⚠️ WARNING")
        elif report.status == CUT_PLAN_VALIDATION_STATUS_BLOCKED:
            st.metric("Validation Status", "❌ BLOCKED")
        else:
            st.metric("Validation Status", report.status)
    with col2:
        st.metric("Warnings", len(report.warnings))
    with col3:
        st.metric("Blockers", len(report.blockers))

    if not report.errors:
        st.success("Keine Warnungen oder Blocker.")
        return

    rows = [
        {
            "type": error.type,
            "severity": error.severity,
            "scope": error.scope,
            "cut_item_id": error.cut_item_id or "—",
            "folder_name": error.folder_name or "—",
            "message": error.message,
            "fix_hint": error.fix_hint or "—",
            "must_be_fixed_by": error.must_be_fixed_by,
            "is_retryable_by_llm": error.is_retryable_by_llm,
        }
        for error in report.errors
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_supplement_requests(project: Project, draft: CutPlanDocument) -> None:
    """Phase 8.6: isolierte Supplement Bridge. Externe Provider-Suche und
    Downloads laufen ausschließlich bei explizitem Klick auf „Kandidaten
    suchen“ bzw. „Akzeptieren“ — niemals automatisch beim Laden dieser Seite."""
    st.subheader("Supplement Requests")
    st.caption(
        "Isolierte Supplement Requests aus dem Cut Plan — getrennt von der "
        "produktionsseitigen Supplement-Pipeline (`_otio/supplement/`). Suche und "
        "Download laufen ausschließlich bei explizitem Klick, nie automatisch."
    )

    requests_document = load_cut_plan_supplement_requests(project)
    current_hash = content_hash_of_model(draft)

    if requests_document is None:
        if st.button(
            "Supplement Requests aus Cut Plan erzeugen",
            key=f"cut_plan_supplement_build_requests_{project.id}",
        ):
            new_document = build_supplement_requests_from_cut_plan(project, draft)
            save_cut_plan_supplement_requests(project, new_document)
            st.success(f"{len(new_document.requests)} Supplement Request(s) erzeugt.")
            st.rerun()
        return

    if requests_document.source_cut_plan_hash != current_hash:
        st.warning(
            "Die Supplement Requests wurden aus einer älteren Version des Cut Plans erzeugt. "
            "Bitte bei Bedarf neu erzeugen."
        )
    if st.button(
        "Supplement Requests neu erzeugen",
        key=f"cut_plan_supplement_rebuild_requests_{project.id}",
    ):
        new_document = build_supplement_requests_from_cut_plan(project, draft)
        save_cut_plan_supplement_requests(project, new_document)
        st.success(f"{len(new_document.requests)} Supplement Request(s) erzeugt.")
        st.rerun()

    if not requests_document.requests:
        st.info("Keine Supplement Requests — kein Item benötigt aktuell ein Supplement-Asset.")
        return

    rows = [
        {
            "request_id": request.request_id,
            "cut_item_id": request.cut_item_id,
            "source_scope": request.source_scope,
            "folder_name": request.folder_name or "—",
            "text": request.text,
            "visual_intent": request.visual_intent or "—",
            "needed_duration_sec": request.needed_duration_sec,
            "reason": request.reason,
            "status": request.status,
        }
        for request in requests_document.requests
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    pexels_ready = bool(get_api_key("PEXELS_API_KEY"))
    if not pexels_ready:
        st.warning("PEXELS_API_KEY fehlt — die Kandidatensuche liefert ohne Key keine Treffer.")

    for request in requests_document.requests:
        with st.expander(f"{request.request_id} — {request.status}", expanded=False):
            st.write(f"**Cut Item:** {request.cut_item_id} ({request.source_scope}, {request.folder_name or '—'})")
            st.write(f"**Text:** {request.text}")
            st.write(f"**Visual Intent:** {request.visual_intent or '—'}")
            st.write(f"**Needed duration:** {request.needed_duration_sec:.2f}s")
            st.write(f"**Reason:** {request.reason}")
            if request.accepted_asset_id:
                st.success(
                    f"Akzeptiertes Asset: `{request.accepted_asset_id}` "
                    f"(`{request.accepted_asset_path}`)."
                )

            query_preview = build_keyword_query(
                folder_name=request.folder_name,
                visual_requirement=request.visual_intent or request.reason,
                passage_text=request.text,
            )
            st.caption(f"Provider: {SUPPLEMENT_SOURCE_PEXELS} · Query-Vorschau: `{query_preview}`")
            st.caption(
                "⚠️ Die Suche kann API-Kontingent beim Provider verbrauchen. Wird nur bei Klick ausgelöst."
            )

            if st.button(
                "Supplement-Kandidaten suchen",
                key=f"cut_plan_supplement_search_{project.id}_{request.request_id}",
                disabled=not pexels_ready,
            ):
                with st.spinner("Kandidaten werden gesucht…"):
                    candidates_document = search_candidates_for_cut_plan_request(
                        project, request.request_id, {"provider": SUPPLEMENT_SOURCE_PEXELS}
                    )
                if candidates_document.status == "FAILED":
                    st.error(f"Suche fehlgeschlagen: {candidates_document.error_message}")
                elif not candidates_document.candidates:
                    st.info("Keine Kandidaten gefunden.")
                else:
                    st.success(f"{len(candidates_document.candidates)} Kandidat(en) gefunden.")
                st.rerun()

            candidates_document = load_cut_plan_supplement_candidates_for_request(project, request.request_id)
            if candidates_document is None:
                st.caption("Noch keine Kandidatensuche durchgeführt.")
                continue
            if candidates_document.status == "FAILED":
                st.error(f"Letzte Suche fehlgeschlagen: {candidates_document.error_message}")
                continue
            if not candidates_document.candidates:
                st.caption("Letzte Suche ergab keine Kandidaten.")
                continue

            is_already_accepted = request.status == CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED
            for candidate in candidates_document.candidates:
                candidate_cols = st.columns([3, 1])
                with candidate_cols[0]:
                    st.write(f"**{candidate.title or candidate.candidate_id}** ({candidate.provider})")
                    st.caption(
                        f"asset_type={candidate.asset_type} · duration={candidate.duration_sec:.2f}s · "
                        f"{candidate.width}x{candidate.height} · score={candidate.score:.2f} · "
                        f"license={candidate.license or '—'}"
                    )
                    if candidate.preview_url:
                        st.caption(f"Preview: {candidate.preview_url}")
                    if candidate.source_url:
                        st.caption(f"Quelle: {candidate.source_url}")
                    if candidate.risks:
                        st.warning(f"Risiken: {', '.join(candidate.risks)}")
                    if is_already_accepted and candidate.candidate_id == request.accepted_candidate_id:
                        st.caption("✅ Dies ist der aktuell akzeptierte Kandidat.")
                with candidate_cols[1]:
                    if is_already_accepted:
                        # Vorab-Hardening (Phase 8.7): ein bereits akzeptierter
                        # Request darf nicht still durch einen zweiten Klick
                        # überschrieben werden — nur ein expliziter,
                        # klar gewarnter „Ersetzen“-Button ist erlaubt.
                        st.warning("Bereits akzeptiert.")
                        if st.button(
                            "Akzeptierten Candidate ersetzen",
                            key=(
                                f"cut_plan_supplement_replace_{project.id}_"
                                f"{request.request_id}_{candidate.candidate_id}"
                            ),
                            help="Ersetzt bewusst das bereits akzeptierte Supplement-Asset dieses Requests.",
                        ):
                            try:
                                with st.spinner("Kandidat wird ersetzt…"):
                                    accept_cut_plan_supplement_candidate(
                                        project, request.request_id, candidate.candidate_id, force_replace=True
                                    )
                                st.success(
                                    "Supplement-Asset ersetzt. Bitte Cut Plan erneut validieren."
                                )
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
                    elif st.button(
                        "Akzeptieren",
                        key=f"cut_plan_supplement_accept_{project.id}_{request.request_id}_{candidate.candidate_id}",
                    ):
                        try:
                            with st.spinner("Kandidat wird übernommen…"):
                                accept_cut_plan_supplement_candidate(
                                    project, request.request_id, candidate.candidate_id
                                )
                            st.success(
                                "Supplement-Asset übernommen. Bitte Cut Plan erneut validieren."
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))


def _render_confirmed_cut_plan(project: Project, draft: CutPlanDocument) -> None:
    """Phase 8.7: Confirm + Trace. Kein Rebuild, keine erneute Asset-Auswahl,
    keine erneute Validierung — nur der bereits validierte Draft wird als
    unveränderlicher Snapshot übernommen. Ein bestehender bestätigter Cut
    Plan wird NIEMALS automatisch durch Draft-/Asset-Auswahl-/Validierungs-
    Änderungen ersetzt, nur durch einen expliziten, klar gewarnten Klick."""
    st.subheader("Bestätigter Cut Plan")

    confirmed = load_confirmed_cut_plan(project)
    trace = load_cut_plan_trace(project)

    if confirmed is not None:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Status", confirmed.status)
        with col2:
            st.metric("Cut Plan Items", len(confirmed.items))
        with col3:
            st.metric("Audio Items", len(confirmed.audio_items))
        with col4:
            total_segments = sum(len(item.planned_visual_segments) for item in confirmed.items)
            st.metric("VisualSegments", total_segments)

        st.caption(f"generated_at: `{confirmed.generated_at}` · confirmed_at: `{confirmed.confirmed_at or '—'}`")
        st.caption(f"source_plan_hash: `{confirmed.source_plan_hash}`")
        st.caption(f"Trace vorhanden: {'✅ Ja' if trace is not None else '❌ Nein'}")

        if is_confirmed_cut_plan_stale(project, confirmed):
            st.warning(
                "Es gibt einen neueren/anderen Draft (der bestätigte Voice-over-Projektplan oder die "
                "Cut-Plan-Settings haben sich seit der Bestätigung geändert). Confirmed Plan bleibt "
                "unverändert."
            )
        st.info(
            "Es gibt bereits einen bestätigten Cut Plan. Änderungen am Draft ersetzen ihn nicht automatisch."
        )

        if st.button("Bestätigung zurücknehmen", key=f"cut_plan_unconfirm_{project.id}"):
            unconfirm_cut_plan(project)
            st.success("Bestätigung zurückgenommen — cut_plan.confirmed.json entfernt.")
            st.rerun()
    else:
        st.info("Noch kein bestätigter Cut Plan vorhanden.")

    st.divider()
    st.subheader("Cut Plan bestätigen")

    report = load_cut_plan_validation_report(project)
    eligible, reasons = can_confirm_cut_plan(project, draft, report)

    if eligible:
        st.success("Alle Confirm-Bedingungen sind erfüllt.")
    else:
        st.warning("Confirm-Bedingungen sind noch nicht erfüllt:")
        for reason in reasons:
            st.write(f"❌ {reason}")

    if confirmed is not None:
        st.warning(
            "Achtung: Dies ersetzt den bisherigen bestätigten Cut Plan unwiderruflich mit dem aktuellen "
            "Draft-Stand."
        )
        button_label = "Aktuellen Draft bestätigen und bestätigten Cut Plan ersetzen"
    else:
        button_label = "Cut Plan bestätigen"

    if st.button(
        button_label,
        key=f"cut_plan_confirm_{project.id}",
        disabled=not eligible,
        type="primary",
        help="Übernimmt den bereits validierten Draft unverändert als cut_plan.confirmed.json und "
        "schreibt cut_plan.trace.json. Kein Rebuild, keine erneute Asset-Auswahl, keine erneute "
        "Validierung. Kein EditPlanDocument, kein OTIO-Export.",
    ):
        try:
            with st.spinner("Cut Plan wird bestätigt…"):
                confirm_cut_plan(project)
            st.success("Cut Plan bestätigt — cut_plan.confirmed.json und cut_plan.trace.json wurden geschrieben.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    if trace is not None and trace.entries:
        st.divider()
        st.subheader("Trace")
        rows = []
        for entry in trace.entries:
            first_ref = entry.source_refs[0] if entry.source_refs else None
            rows.append(
                {
                    "cut_item_id": entry.cut_item_id,
                    "source_scope": first_ref.source_scope if first_ref else "—",
                    "folder_name": first_ref.folder_name if first_ref else "—",
                    "chosen_asset_id": entry.chosen_asset_id or "—",
                    "fallback_used": entry.fallback_used,
                    "used_supplement_asset": entry.used_supplement_asset,
                    "duration_strategy": entry.duration_strategy or "—",
                    "visual_segment_count": entry.visual_segment_count,
                    "validation_warnings": ", ".join(entry.validation_warnings) or "—",
                    "validation_blockers": ", ".join(entry.validation_blockers) or "—",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.caption("Die isolierte EditPlan-Bridge (Phase 9.1) folgt im nächsten Bereich unten.")


def _render_edit_plan_bridge(project: Project) -> None:
    """Phase 9.1/9.2: isolierte EditPlan-Bridge. Übersetzt ausschließlich einen
    bereits BESTÄTIGTEN Cut Plan deterministisch in einen EditPlanDocument-
    kompatiblen Draft — kein Rebuild, keine Asset-Auswahl, keine Supplement-
    Suche, keine LLM-Aufrufe. Kein locked EditPlan, kein OTIO-Export."""
    st.subheader("EditPlan Bridge")
    st.caption(
        "Übersetzt den bestätigten Cut Plan deterministisch in einen isolierten, "
        "EditPlanDocument-kompatiblen Draft — getrennt von `_otio/edit_plan/` und "
        "`_otio/exports/`. Keine neue Asset-Auswahl, keine Supplement-Suche, kein LLM-Aufruf."
    )
    st.warning(
        "Dieser Bridge-Draft ist noch kein locked Produktions-EditPlan und nicht OTIO-exportbereit."
    )

    confirmed = load_confirmed_cut_plan(project)
    if confirmed is None:
        st.info("Noch kein bestätigter Cut Plan vorhanden — bitte zuerst im Bereich oben bestätigen.")
        return

    if is_confirmed_cut_plan_stale(project, confirmed):
        st.warning(
            "Der bestätigte Cut Plan ist veraltet (Voice-over-Projektplan oder Cut-Plan-Settings haben "
            "sich seit der Bestätigung geändert). Die Bridge kann trotzdem erzeugt werden, spiegelt dann "
            "aber einen veralteten Stand wider."
        )

    existing_bridge_draft = load_edit_plan_bridge_draft(project)
    if existing_bridge_draft is not None and is_edit_plan_bridge_stale(project, existing_bridge_draft):
        st.warning(
            "Der EditPlan-Bridge-Draft basiert auf einem älteren bestätigten Cut Plan. Bitte bei Bedarf "
            "neu erzeugen."
        )

    button_label = (
        "EditPlan Draft aus bestätigtem Cut Plan erzeugen"
        if existing_bridge_draft is None
        else "EditPlan Draft neu erzeugen"
    )
    if st.button(button_label, key=f"cut_plan_edit_plan_bridge_build_{project.id}", type="primary"):
        try:
            with st.spinner("EditPlan Bridge Draft wird erstellt…"):
                edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
                edit_plan = save_edit_plan_bridge_draft(project, edit_plan)
                audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
                audio_plan = save_bridge_audio_plan(project, audio_plan)
                trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
                save_edit_plan_bridge_trace(project, trace)
                report = validate_edit_plan_bridge(project, edit_plan)
            audio_count = sum(1 for item in edit_plan.timeline_items if item.track == "A1")
            visual_count = len(edit_plan.timeline_items) - audio_count
            st.success(
                f"EditPlan Bridge Draft erzeugt: {len(edit_plan.timeline_items)} TimelineItems "
                f"({audio_count} Audio, {visual_count} Visual), {len(audio_plan.items)} BridgeAudioPlanItem(s), "
                f"{len(report.warnings)} Warnings, {len(report.blockers)} Blocker."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    bridge_draft = load_edit_plan_bridge_draft(project)
    if bridge_draft is None:
        return

    audio_items = [item for item in bridge_draft.timeline_items if item.track == "A1"]
    visual_items = [item for item in bridge_draft.timeline_items if item.track != "A1"]

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("TimelineItems", len(bridge_draft.timeline_items))
    with col2:
        st.metric("Audio Items", len(audio_items))
    with col3:
        st.metric("Visual Items", len(visual_items))

    st.caption(f"Bridge Draft Pfad: `{get_cut_plan_edit_plan_bridge_draft_path(project.work_dir_path)}`")

    audio_plan = load_bridge_audio_plan(project)
    trace = load_edit_plan_bridge_trace(project)
    rounded_count = sum(1 for entry in trace.entries if entry.frame_rounded) if trace is not None else 0
    boundary_chained_count = (
        sum(1 for entry in trace.entries if entry.boundary_chained) if trace is not None else 0
    )

    col7, col8, col9, col10 = st.columns(4)
    with col7:
        st.metric("Bridge Audio Plan", "✅ Ja" if audio_plan is not None else "❌ Nein")
    with col8:
        st.metric("AudioPlanItems", len(audio_plan.items) if audio_plan is not None else 0)
    with col9:
        st.metric("Frame-gerundete Items", rounded_count)
    with col10:
        st.metric("Boundary-gechainte Items", boundary_chained_count)
    st.caption(
        "Boundary-Chaining angewendet: "
        + ("✅ Ja" if boundary_chained_count > 0 else "— Nein (keine Anpassung nötig)")
    )
    if audio_plan is not None:
        st.caption(f"Bridge Audio Plan Pfad: `{get_cut_plan_edit_plan_bridge_audio_plan_path(project.work_dir_path)}`")

    report = load_edit_plan_bridge_validation_report(project)
    if report is not None:
        col4, col5, col6 = st.columns(3)
        with col4:
            if report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS:
                st.metric("Validation Status", "✅ PASS")
            elif report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_WARNING:
                st.metric("Validation Status", "⚠️ WARNING")
            elif report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED:
                st.metric("Validation Status", "❌ BLOCKED")
            else:
                st.metric("Validation Status", report.status)
        with col5:
            st.metric("Warnings", len(report.warnings))
        with col6:
            st.metric("Blockers", len(report.blockers))

        if report.warnings or report.blockers:
            rows = [
                {
                    "type": error.type,
                    "severity": error.severity,
                    "scope": error.scope,
                    "cut_item_id": error.cut_item_id or "—",
                    "visual_segment_id": error.visual_segment_id or "—",
                    "timeline_item_id": error.timeline_item_id or "—",
                    "message": error.message,
                    "fix_hint": error.fix_hint or "—",
                }
                for error in (report.blockers + report.warnings)
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.success("Keine Warnungen oder Blocker.")
    else:
        st.info("Noch kein Bridge Validation Report vorhanden.")

    if trace is not None and trace.entries:
        with st.expander("EditPlan Bridge Trace", expanded=False):
            rows = [
                {
                    "cut_item_id": entry.cut_item_id or "—",
                    "visual_segment_id": entry.visual_segment_id or "—",
                    "source_scope": entry.source_scope or "—",
                    "folder_name": entry.folder_name or "—",
                    "timeline_item_id": entry.timeline_item_id,
                    "timeline_item_type": entry.timeline_item_type,
                    "track": entry.track,
                    "asset_id": entry.asset_id or "—",
                    "original_timeline_in_sec": entry.original_timeline_in_sec,
                    "original_timeline_out_sec": entry.original_timeline_out_sec,
                    "rounded_timeline_in_sec": entry.rounded_timeline_in_sec,
                    "rounded_timeline_out_sec": entry.rounded_timeline_out_sec,
                    "timeline_in_sec": entry.timeline_in_sec,
                    "timeline_out_sec": entry.timeline_out_sec,
                    "source_in_sec": entry.source_in_sec,
                    "source_out_sec": entry.source_out_sec,
                    "frame_rounded": entry.frame_rounded,
                    "frame_rounding_delta_sec": entry.frame_rounding_delta_sec,
                    "boundary_chained": entry.boundary_chained,
                    "boundary_chain_delta_sec": entry.boundary_chain_delta_sec,
                    "source_duration_adjusted": entry.source_duration_adjusted,
                    "source_duration_delta_sec": entry.source_duration_delta_sec,
                    "reason": entry.reason or "—",
                }
                for entry in trace.entries
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    if audio_plan is not None and audio_plan.items:
        with st.expander("Bridge Audio Plan", expanded=False):
            rows = [
                {
                    "scope": item.scope,
                    "folder_name": item.folder_name or "—",
                    "timeline_item_id": item.timeline_item_id,
                    "timeline_in_sec": item.timeline_in_sec,
                    "timeline_out_sec": item.timeline_out_sec,
                    "source_in_sec": item.source_in_sec,
                    "source_out_sec": item.source_out_sec,
                    "duration_sec": item.duration_sec,
                    "track": item.track,
                    "source_cut_plan_audio_index": item.source_cut_plan_audio_index,
                }
                for item in audio_plan.items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    _render_edit_plan_bridge_confirm(project)

    st.caption("Kein OTIO-Button, kein Lock-Button — diese Schritte folgen erst in einer späteren Sub-Phase.")


def _render_edit_plan_bridge_confirm(project: Project) -> None:
    """Phase 9.3: EditPlan Bridge Confirm/Freeze. Übernimmt ausschließlich
    bereits validierte Bridge-Dateien unverändert als Snapshot — kein
    Rebuild, keine erneute Übersetzung, keine erneute Validierung. Weiterhin
    ein isolierter Bridge-Snapshot: KEIN Produktions-EditPlan, KEIN locked
    Produktionsplan, KEIN OTIO-Export."""
    st.subheader("EditPlan Bridge bestätigen")

    manifest = load_edit_plan_bridge_confirm_manifest(project)

    if manifest is not None:
        confirmed_edit_plan = load_confirmed_edit_plan_bridge(project)
        confirmed_audio_plan = load_confirmed_bridge_audio_plan(project)
        confirmed_trace = load_confirmed_bridge_trace(project)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Status", manifest.status)
        with col2:
            st.metric("TimelineItems", len(confirmed_edit_plan.timeline_items) if confirmed_edit_plan else 0)
        with col3:
            st.metric("AudioPlanItems", len(confirmed_audio_plan.items) if confirmed_audio_plan else 0)
        with col4:
            st.metric("TraceEntries", len(confirmed_trace.entries) if confirmed_trace else 0)

        st.caption(f"confirmed_at: `{manifest.confirmed_at}`")
        st.caption(f"source_cut_plan_hash: `{manifest.source_cut_plan_hash}`")
        st.caption(f"edit_plan_hash: `{manifest.edit_plan_hash}`")
        st.caption(f"bridge_audio_plan_hash: `{manifest.bridge_audio_plan_hash}`")
        for label, path_str in manifest.confirmed_files.items():
            st.caption(f"{label}: `{path_str}`")

        if is_confirmed_edit_plan_bridge_stale(project):
            st.warning("Der bestätigte Bridge-Snapshot ist veraltet. Bitte neu bestätigen.")
        st.info(
            "Es gibt einen bestätigten Bridge-Snapshot. Aktuelle Draft-Änderungen ersetzen ihn nicht automatisch."
        )

        if st.button("Bridge-Bestätigung zurücknehmen", key=f"cut_plan_edit_plan_bridge_unconfirm_{project.id}"):
            unconfirm_edit_plan_bridge(project)
            st.success("Bridge-Bestätigung zurückgenommen — confirmed-Dateien entfernt.")
            st.rerun()
    else:
        st.info("Noch kein bestätigter EditPlan-Bridge-Snapshot vorhanden.")

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    if eligible:
        st.success("Alle Confirm-Bedingungen sind erfüllt.")
    else:
        st.warning("Confirm-Bedingungen sind noch nicht erfüllt:")
        for reason in reasons:
            st.write(f"❌ {reason}")

    if manifest is not None:
        st.warning(
            "Achtung: Dies ersetzt den bisherigen bestätigten Bridge-Snapshot unwiderruflich mit dem "
            "aktuellen Draft-Stand."
        )
        button_label = "Aktuellen Bridge Draft bestätigen und bestehenden Snapshot ersetzen"
    else:
        button_label = "EditPlan Bridge bestätigen"

    if st.button(
        button_label,
        key=f"cut_plan_edit_plan_bridge_confirm_{project.id}",
        disabled=not eligible,
        type="primary",
        help="Übernimmt die bereits validierten Bridge-Dateien unverändert als Snapshot. Kein Rebuild, "
        "keine erneute Übersetzung/Validierung. Kein Produktions-EditPlan, kein OTIO-Export.",
    ):
        try:
            with st.spinner("EditPlan Bridge wird bestätigt…"):
                confirm_edit_plan_bridge(project)
            st.success(
                "EditPlan Bridge bestätigt — edit_plan_from_cut_plan.confirmed.json, "
                "bridge_audio_plan.confirmed.json, edit_plan_bridge_trace.confirmed.json und "
                "edit_plan_bridge_confirm_manifest.json wurden geschrieben."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.caption(
        "Dieser Bridge-Snapshot ist noch kein Produktions-EditPlan und nicht OTIO-exportbereit."
    )


def _render_staged_edit_plan_preview(project: Project, section: object) -> None:
    """Phase 10.4: Vorschau EINES gestagten EditPlanDocument — rein lesend,
    lädt nur bereits vorhandene Staging-Dateien."""
    document = load_staged_edit_plan(project, section.staging_section_id)
    if document is None:
        st.caption("staged edit_plan.json konnte nicht geladen werden.")
        return

    if document.voiceover is not None:
        voiceover = document.voiceover
        st.write("**Voiceover**")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.caption(f"path: `{voiceover.path}`")
            st.caption(f"timeline_start_sec: {voiceover.timeline_start_sec:.3f}")
        with col2:
            st.caption(f"timeline_end_sec: {voiceover.timeline_end_sec:.3f}")
            st.caption(f"duration_sec: {voiceover.duration_sec:.3f}")
        with col3:
            st.caption(f"duration_source: `{voiceover.duration_source}`")
            st.caption(f"trim_policy: `{voiceover.trim_policy}`")
    else:
        st.caption("Kein Voiceover in diesem gestagten EditPlanDocument.")

    st.write("**TimelineItems**")
    if not document.timeline_items:
        st.caption("Keine TimelineItems.")
    else:
        rows = [
            {
                "timeline_item_id": item.timeline_item_id,
                "type": item.type,
                "section_id": item.section_id,
                "folder_name": item.folder_name,
                "asset_id": item.asset_id or "—",
                "asset_path": item.resolved_media_path or "—",
                "timeline_in_sec": item.timeline_in_sec,
                "timeline_out_sec": item.timeline_out_sec,
                "source_in_sec": item.source_in_sec,
                "source_out_sec": item.source_out_sec,
                "selection_reason": item.selection_reason or "—",
            }
            for item in document.timeline_items
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.write("**Shots**")
    if not document.shots:
        st.caption("Keine Shots.")
    else:
        rows = [
            {
                "shot_id": getattr(shot, "shot_id", "") or "—",
                "asset_id": shot.asset_id or "—",
                "asset_path": shot.asset_path or "—",
                "duration_sec": shot.duration_sec,
                "voice_start_sec": shot.voice_start_sec,
                "voice_end_sec": shot.voice_end_sec,
                "beat_id": shot.beat_id or "—",
                "motif": shot.motif or "—",
                "passage_text": shot.passage_text or "—",
            }
            for shot in document.shots
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_production_plan_readonly_hint(project: Project, section: object) -> None:
    """Phase 10.4 §10: rein lesender Hinweis, ob für diese Sektion bereits ein
    Produktionsplan existiert — kein Blockieren, kein Merge, kein Schreiben."""
    if section.is_intro:
        st.caption("Intro ist eine synthetische Staging-Sektion und hat noch keinen Produktions-Zielpfad.")
        return
    existing_path = get_folder_edit_plan_path(project.work_dir_path, section.folder_name)
    exists = existing_path.is_file()
    st.caption(
        f"Produktionsplan existiert bereits: {'✅ Ja' if exists else '❌ Nein'} (`{existing_path}`)"
    )


def _render_production_edit_plan_staging(project: Project) -> None:
    """Phase 10.4: UI für das isolierte Production-EditPlan-Staging (Phase
    10.1-10.3). Rein anzeigend/erzeugend/validierend — kein Promote nach
    `_otio/edit_plan/`, kein Lock, kein OTIO-Export, kein Render, kein Aufruf
    der Save- oder Build-Funktionen der bestehenden Produktions-EditPlan-
    Pipeline, keine Produktions-Dateien werden überschrieben."""
    st.subheader("Production EditPlan Staging")
    st.warning(
        "Dieses Staging-Paket ist noch kein Produktions-EditPlan. Es wird nicht nach "
        "`_otio/edit_plan/` geschrieben und ist noch nicht OTIO-exportbereit."
    )
    st.caption("Promote nach `_otio/edit_plan/` erfolgt erst in einer späteren Phase.")

    st.markdown("**Voraussetzungen**")
    eligible, reasons = can_build_production_edit_plan_staging(project)
    if eligible:
        st.success("Alle Voraussetzungen sind erfüllt.")
    else:
        st.warning("Voraussetzungen sind noch nicht erfüllt:")
        for reason in reasons:
            st.write(f"❌ {reason}")

    package = load_production_edit_plan_staging_package(project)

    col_build, col_validate = st.columns(2)
    with col_build:
        if st.button(
            "Production EditPlan Staging erzeugen",
            key=f"production_edit_plan_staging_build_{project.id}",
            disabled=not eligible,
            type="primary",
            help="Übersetzt den bestätigten EditPlan-Bridge-Snapshot in ein isoliertes "
            "Staging-Paket (Package + gestagte EditPlanDocuments + Mapping-Trace). Kein "
            "Rebuild der Bridge, kein Promote, kein OTIO-Export. Führt keine automatische "
            "Validierung durch — dafür bitte separat unten „validieren“ klicken.",
        ):
            try:
                with st.spinner("Production EditPlan Staging wird erzeugt…"):
                    new_package = build_and_save_production_edit_plan_staging(project)
                total_timeline_items = sum(section.timeline_item_count for section in new_package.sections)
                st.success(
                    f"Staging erzeugt: {len(new_package.sections)} Sektion(en), "
                    f"{len(new_package.sections)} gestagte(s) EditPlanDocument(e), "
                    f"{total_timeline_items} TimelineItem(s) gesamt, Package-Status "
                    f"{new_package.status}. Pfad: "
                    f"`{get_production_edit_plan_package_path(project.work_dir_path)}`"
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with col_validate:
        if st.button(
            "Production EditPlan Staging validieren",
            key=f"production_edit_plan_staging_validate_{project.id}",
            disabled=package is None,
            help="Führt die vollständige Revalidierung des Staging-Pakets durch (Phase "
            "10.3) und speichert production_edit_plan_validation_report.json. Rein "
            "prüfend — keine automatische Reparatur, kein Promote, kein OTIO-Export. "
            "Verändert production_edit_plan_package.json nicht.",
        ):
            try:
                with st.spinner("Staging wird validiert…"):
                    validation_report = validate_production_edit_plan_staging(project)
                if validation_report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS:
                    st.success("Validierung: PASS.")
                elif validation_report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING:
                    st.warning(f"Validierung: WARNING ({len(validation_report.warnings)} Warnings).")
                else:
                    st.error(f"Validierung: BLOCKED ({len(validation_report.blockers)} Blocker).")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if package is None:
        st.info("Noch kein Production EditPlan Staging erzeugt.")
        return

    package_stale = is_production_edit_plan_staging_stale(project, package)
    report = load_production_edit_plan_validation_report(project)
    report_stale = report is not None and is_production_edit_plan_validation_report_stale(project, report)

    # --- §11 Status-Logik ---
    if report is None:
        st.info("Staging erzeugt, aber noch nicht validiert.")
    elif report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS:
        st.success("Staging ist validiert und bereit für spätere Promote-Planung.")
    elif report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING:
        st.warning("Staging ist validiert, enthält aber Warnungen.")
    elif report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED:
        st.error("Staging ist blockiert. Bitte Fehler prüfen.")
    if package_stale:
        st.warning("Das Staging-Paket ist veraltet. Bitte neu erzeugen.")
    if report_stale:
        st.warning("Der Validation Report ist veraltet. Bitte erneut validieren.")

    # --- §6 Package-Anzeige ---
    st.markdown("**Package**")
    total_timeline_items = sum(section.timeline_item_count for section in package.sections)
    total_shots = sum(section.shot_count for section in package.sections)
    sections_with_voiceover = sum(1 for section in package.sections if section.has_voiceover)

    package_status_label = {
        PRODUCTION_EDIT_PLAN_STATUS_STAGED: "✅ STAGED",
        PRODUCTION_EDIT_PLAN_STATUS_NEEDS_REVIEW: "⚠️ NEEDS_REVIEW",
        PRODUCTION_EDIT_PLAN_STATUS_BLOCKED: "❌ BLOCKED",
    }.get(package.status, package.status)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Package Status", package_status_label)
    with col2:
        st.metric("Sektionen", len(package.sections))
    with col3:
        st.metric("TimelineItems gesamt", total_timeline_items)
    with col4:
        st.metric("Shots gesamt", total_shots)

    col5, col6, col7 = st.columns(3)
    with col5:
        st.metric("Sektionen mit Voiceover", sections_with_voiceover)
    with col6:
        st.metric("Staging veraltet", "⚠️ Ja" if package_stale else "✅ Nein")
    with col7:
        st.caption(f"source_bridge_manifest_hash: `{package.source_bridge_manifest_hash}`")
        st.caption(f"source_cut_plan_hash: `{package.source_cut_plan_hash}`")

    section_rows = [
        {
            "staging_section_id": section.staging_section_id,
            "production_section_id": section.production_section_id,
            "folder_name": section.folder_name,
            "is_intro": section.is_intro,
            "shot_count": section.shot_count,
            "timeline_item_count": section.timeline_item_count,
            "has_voiceover": section.has_voiceover,
            "staged_edit_plan_hash": section.staged_edit_plan_hash,
            "warnings": len(section.warnings),
            "blockers": len(section.blockers),
            "staged_edit_plan_path": section.staged_edit_plan_path,
        }
        for section in package.sections
    ]
    st.dataframe(section_rows, use_container_width=True, hide_index=True)

    # --- §7 Staged EditPlan Preview + §10 Read-only Produktionsplan-Hinweis ---
    st.markdown("**Gestagte EditPlanDocuments**")
    for section in package.sections:
        with st.expander(
            f"{section.staging_section_id} — {section.folder_name or 'Intro'}", expanded=False
        ):
            _render_production_plan_readonly_hint(project, section)
            _render_staged_edit_plan_preview(project, section)

    # --- §8 Validation Report Anzeige ---
    st.markdown("**Validation Report**")
    if report is None:
        st.info("Noch kein Validation Report vorhanden.")
    else:
        report_status_label = {
            PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS: "✅ PASS",
            PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING: "⚠️ WARNING",
            PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED: "❌ BLOCKED",
        }.get(report.status, report.status)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Validation Status", report_status_label)
        with col2:
            st.metric("Warnings", len(report.warnings))
        with col3:
            st.metric("Blockers", len(report.blockers))
        st.caption(f"generated_at: `{report.generated_at}`")
        st.caption(f"package_hash: `{report.package_hash}`")
        st.caption(f"source_bridge_manifest_hash: `{report.source_bridge_manifest_hash}`")
        st.caption(
            f"Validation Report Pfad: `{get_production_edit_plan_validation_report_path(project.work_dir_path)}`"
        )

        if report.warnings or report.blockers:
            rows = [
                {
                    "type": error.type,
                    "severity": error.severity,
                    "scope": error.scope,
                    "staging_section_id": error.staging_section_id or "—",
                    "production_section_id": error.production_section_id or "—",
                    "timeline_item_id": error.timeline_item_id or "—",
                    "message": error.message,
                    "fix_hint": error.fix_hint or "—",
                }
                for error in (report.blockers + report.warnings)
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.success("Keine Warnungen oder Blocker.")

        if report_stale:
            st.warning("Der Validation Report ist veraltet. Bitte Production EditPlan Staging erneut validieren.")

    # --- §9 Mapping Trace Anzeige ---
    trace = load_production_edit_plan_mapping_trace(project)
    if trace is not None and trace.entries:
        with st.expander("Mapping Trace", expanded=False):
            rows = [
                {
                    "trace_id": entry.trace_id,
                    "source_bridge_timeline_item_id": entry.source_bridge_timeline_item_id or "—",
                    "source_bridge_audio_plan_index": (
                        entry.source_bridge_audio_plan_index
                        if entry.source_bridge_audio_plan_index is not None
                        else "—"
                    ),
                    "source_cut_item_id": entry.source_cut_item_id or "—",
                    "source_visual_segment_id": entry.source_visual_segment_id or "—",
                    "resulting_staging_section_id": entry.resulting_staging_section_id,
                    "resulting_production_section_id": entry.resulting_production_section_id,
                    "resulting_timeline_item_id": entry.resulting_timeline_item_id or "—",
                    "folder_name": entry.folder_name,
                    "is_intro": entry.is_intro,
                    "original_timeline_in_sec": entry.original_timeline_in_sec,
                    "original_timeline_out_sec": entry.original_timeline_out_sec,
                    "local_timeline_in_sec": entry.local_timeline_in_sec,
                    "local_timeline_out_sec": entry.local_timeline_out_sec,
                    "asset_id": entry.asset_id or "—",
                    "asset_path": entry.asset_path or "—",
                    "mapping_reason": entry.mapping_reason,
                    "fields_defaulted": ", ".join(entry.fields_defaulted) or "—",
                    "fields_dropped": ", ".join(entry.fields_dropped) or "—",
                    "warnings": ", ".join(entry.warnings) or "—",
                }
                for entry in trace.entries
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
    elif get_production_edit_plan_mapping_trace_path(project.work_dir_path).is_file():
        st.caption("Mapping Trace existiert, enthält aber keine Einträge.")

    st.caption(
        "Kein Promote-Button, kein OTIO-Button, kein Lock-Button — diese Schritte folgen erst "
        "in einer späteren Phase."
    )


def render_cut_plan_page() -> None:
    st.header("⑧ Cut Plan")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    st.info(
        "Dieser Schritt baut aus dem bestätigten Voice-over-Projektplan einen "
        "technischen Cut-Plan-Entwurf. Er erzeugt kein EditPlanDocument, keinen "
        "gesperrten Plan und keinen OTIO-Export. Der bestätigte Voice-over-"
        "Projektplan bleibt die redaktionelle Quelle der Wahrheit — der Cut Plan "
        "übersetzt ihn nur in eine technische Struktur, ohne redaktionell "
        "neu zu planen."
    )

    _render_source_plan_status(project)
    st.divider()
    _render_settings_editor(project)
    st.divider()
    _render_future_artifact_paths(project)
    st.divider()

    source_plan = load_confirmed_voiceover_project_plan(project)
    existing_draft = load_cut_plan_draft(project)
    settings_stale = existing_draft is not None and is_cut_plan_settings_stale(project, existing_draft)

    col_generate, col_asset_selection, col_validate = st.columns(3)
    with col_generate:
        button_label = "Cut Plan Draft erzeugen" if existing_draft is None else "Cut Plan Draft neu erzeugen"
        if st.button(
            button_label,
            key=f"cut_plan_generate_draft_{project.id}",
            disabled=source_plan is None,
            type="primary",
            help="Baut Timeline-/Audio-Platzierung und Cut-Plan-Item-Skelette aus dem "
            "bestätigten Voice-over-Projektplan neu (Asset-Auswahl geht dabei verloren).",
        ):
            try:
                with st.spinner("Cut Plan Draft wird erstellt…"):
                    new_draft = build_cut_plan_draft(project)
                    save_cut_plan_draft(project, new_draft)
                st.success("Cut Plan Draft erstellt.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with col_asset_selection:
        if st.button(
            "Asset-Auswahl anwenden",
            key=f"cut_plan_apply_asset_selection_{project.id}",
            disabled=existing_draft is None or settings_stale,
            help="Wendet Asset-Auswahl, Fallback-Logik sowie Dauer-/Split-/Merge-"
            "Strategie auf den bestehenden Draft an (verwendet cut_plan.settings_snapshot). "
            "Noch keine Supplement-Suche, keine vollständige Validierung, kein Confirm/Lock."
            + (" Deaktiviert, da die Cut-Plan-Settings seit Draft-Erzeugung geändert wurden." if settings_stale else ""),
        ):
            try:
                with st.spinner("Asset-Auswahl wird angewendet…"):
                    updated_draft = apply_asset_selection_to_draft(project)
                status_counts = {
                    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED: 0,
                    CUT_PLAN_ASSET_SELECTION_BACKUP_USED: 0,
                    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED: 0,
                    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED: 0,
                    CUT_PLAN_ASSET_SELECTION_BLOCKED: 0,
                }
                for item in updated_draft.items:
                    if item.asset_selection_status in status_counts:
                        status_counts[item.asset_selection_status] += 1
                total_segments = sum(len(item.planned_visual_segments) for item in updated_draft.items)
                st.success(
                    f"Asset-Auswahl angewendet: {len(updated_draft.items)} Items, "
                    f"{total_segments} VisualSegments, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_PRIMARY_USED]} PRIMARY_USED, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_BACKUP_USED]} BACKUP_USED, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED]} SUPPLEMENT_REQUIRED, "
                    f"{status_counts[CUT_PLAN_ASSET_SELECTION_BLOCKED]} BLOCKED, "
                    f"{len(updated_draft.warnings)} Warnings, {len(updated_draft.blockers)} Blocker."
                )
                coverage_extended_count = sum(
                    1
                    for item in updated_draft.items
                    for segment in item.planned_visual_segments
                    if "initial_preroll_extension" in segment.reason.split("+")
                    or "section_pause_hold" in segment.reason.split("+")
                )
                if coverage_extended_count:
                    st.info(
                        "Visuelle Coverage für Start-Offset/Pausen wurde angewendet "
                        f"({coverage_extended_count} VisualSegment(s) verlängert, um Schwarzbild während "
                        "initial_audio_offset_sec/pause_between_sections_sec zu vermeiden)."
                    )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with col_validate:
        if st.button(
            "Cut Plan validieren",
            key=f"cut_plan_validate_{project.id}",
            disabled=existing_draft is None,
            help="Führt die vollständige Cut-Plan-Validierung durch (Phase 8.4) und "
            "speichert cut_plan.validation_report.json. Kein Confirm/Lock, kein OTIO.",
        ):
            try:
                with st.spinner("Cut Plan wird validiert…"):
                    validated_draft, report = validate_cut_plan_draft(project)
                if report.status == CUT_PLAN_VALIDATION_STATUS_PASS:
                    st.success(f"Validierung: PASS — Status {validated_draft.status}.")
                elif report.status == CUT_PLAN_VALIDATION_STATUS_WARNING:
                    st.warning(
                        f"Validierung: WARNING ({len(report.warnings)} Warnings) — Status {validated_draft.status}."
                    )
                else:
                    st.error(
                        f"Validierung: BLOCKED ({len(report.blockers)} Blocker) — Status {validated_draft.status}."
                    )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.caption(
        "🚧 Locked EditPlan, Render und OTIO-Export folgen erst in einer späteren Sub-Phase (9.2ff)."
    )

    if existing_draft is not None:
        st.divider()
        _render_supplement_requests(project, existing_draft)
        st.divider()
        _render_cut_plan_draft(project, existing_draft)
        st.divider()
        _render_confirmed_cut_plan(project, existing_draft)
        st.divider()
        _render_edit_plan_bridge(project)
        st.divider()
        _render_production_edit_plan_staging(project)
    elif source_plan is not None:
        st.info("Noch kein Cut Plan Draft vorhanden.")
    else:
        st.caption(
            f"Beim Klick würde standardmäßig hier gespeichert: "
            f"`{get_cut_plan_draft_path(project.work_dir_path)}`"
        )
