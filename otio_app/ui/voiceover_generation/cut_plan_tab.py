"""Phase 8.1: Grundgerüst für den Cut-Plan-Tab.

Baut noch KEINEN Cut Plan Draft — nur Status-Anzeige des bestätigten
Voice-over-Projektplans, Cut-Plan-Settings (eigene Datei, siehe
cut_plan_settings_service.py) und die künftigen Artefakt-Pfade. Die
Draft-Erzeugung (Timeline-Mathematik, Asset-Auswahl, Split/Merge,
Validierung) folgt erst in späteren Sub-Phasen (8.2ff)."""

from __future__ import annotations

from otio_app.defaults import PLAN_STATUS_READY_FOR_CUT
from otio_app.models import Project
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_draft_path,
    get_cut_plan_settings_path,
    get_cut_plan_supplement_requests_path,
    get_cut_plan_trace_path,
    get_cut_plan_validation_report_path,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import (
    load_cut_plan_settings,
    save_cut_plan_settings,
)
from otio_app.services.voiceover_generation.final_plan_service import (
    load_confirmed_voiceover_project_plan,
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
        "Diese Dateien werden ab Phase 8.2ff. erzeugt. In Phase 8.1 existieren "
        "höchstens `cut_plan_settings.json`, sofern bereits gespeichert."
    )
    st.caption(f"Cut Plan Settings: `{get_cut_plan_settings_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Draft: `{get_cut_plan_draft_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Validation Report: `{get_cut_plan_validation_report_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Confirmed: `{get_cut_plan_confirmed_path(project.work_dir_path)}`")
    st.caption(f"Cut Plan Trace: `{get_cut_plan_trace_path(project.work_dir_path)}`")
    st.caption(
        f"Supplement Requests (isoliert): `{get_cut_plan_supplement_requests_path(project.work_dir_path)}`"
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

    st.subheader("Cut Plan Draft")
    st.info("🚧 Die Erzeugung des Cut-Plan-Entwurfs folgt in Phase 8.2.")
    st.button(
        "Cut Plan Draft erzeugen",
        key=f"cut_plan_generate_draft_{project.id}",
        disabled=True,
        help="Noch nicht implementiert — folgt in Phase 8.2 (Timeline-Mathematik + Asset-Auswahl).",
    )
