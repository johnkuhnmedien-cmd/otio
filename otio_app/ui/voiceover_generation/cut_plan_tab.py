"""Phase 8.4/8.5: Cut-Plan-Tab mit vollständiger Validierung + Visual Coverage.

Baut auf Phase 8.2 (Timeline-/Audio-Platzierung) und Phase 8.3 (Asset-
Auswahl, Fallback, Dauer-/Split-/Merge) auf und ergänzt die vollständige
Cut-Plan-Validierung (siehe cut_plan_validator.py). Phase 8.5 ergänzt den
Visual-Coverage-Fix (siehe cut_plan_visual_coverage.py), der bereits
während der Asset-Auswahl automatisch angewendet wird, damit initialer
Audio-Vorlauf und Sektions-Pausen nicht zu Schwarzbild führen. Noch KEIN
Confirm/Lock, kein EditPlanDocument, kein OTIO-Export, keine Supplement-
Suche/-Beschaffung, kein LLM-Konfliktlöser. Diese Schritte folgen in
späteren Sub-Phasen (8.6ff)."""

from __future__ import annotations

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_BLOCKED,
    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_VALIDATION_STATUS_BLOCKED,
    CUT_PLAN_VALIDATION_STATUS_PASS,
    CUT_PLAN_VALIDATION_STATUS_WARNING,
    PLAN_STATUS_READY_FOR_CUT,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_draft_path,
    get_cut_plan_settings_path,
    get_cut_plan_supplement_requests_path,
    get_cut_plan_trace_path,
    get_cut_plan_validation_report_path,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    is_cut_plan_draft_stale,
    is_cut_plan_settings_stale,
    load_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
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
from otio_app.services.voiceover_generation.cut_plan_validator import load_cut_plan_validation_report
from otio_app.services.voiceover_generation.final_plan_service import (
    load_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
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

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        st.metric("PRIMARY_USED", status_counts[CUT_PLAN_ASSET_SELECTION_PRIMARY_USED])
    with col6:
        st.metric("BACKUP_USED", status_counts[CUT_PLAN_ASSET_SELECTION_BACKUP_USED])
    with col7:
        st.metric("SUPPLEMENT_REQUIRED", status_counts[CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED])
    with col8:
        st.metric("BLOCKED", status_counts[CUT_PLAN_ASSET_SELECTION_BLOCKED])

    col9, col10 = st.columns(2)
    with col9:
        st.metric("Warnings", len(draft.warnings))
    with col10:
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

    current_hash = content_hash_of_model(draft)
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
        "🚧 Supplement-Suche/-Beschaffung sowie Confirm/Lock folgen in späteren Sub-Phasen (8.6ff)."
    )

    if existing_draft is not None:
        st.divider()
        _render_cut_plan_draft(project, existing_draft)
    elif source_plan is not None:
        st.info("Noch kein Cut Plan Draft vorhanden.")
    else:
        st.caption(
            f"Beim Klick würde standardmäßig hier gespeichert: "
            f"`{get_cut_plan_draft_path(project.work_dir_path)}`"
        )
