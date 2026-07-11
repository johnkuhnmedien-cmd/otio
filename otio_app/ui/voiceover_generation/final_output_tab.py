"""Finale Ansicht + Export der Übergabedatei für die spätere Schnittplan-Pipeline (Phase 7)."""

from __future__ import annotations

from otio_app.defaults import AUDIO_STATUS_MISSING, PLAN_STATUS_READY_FOR_CUT
from otio_app.models import Project
from otio_app.project_layout import (
    get_confirmed_voiceover_project_plan_path,
    get_voiceover_project_plan_csv_path,
    get_voiceover_project_plan_json_path,
    get_voiceover_project_plan_md_path,
)
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.final_plan_service import (
    build_confirmed_voiceover_project_plan,
    export_voiceover_project_plan_csv,
    export_voiceover_project_plan_json,
    export_voiceover_project_plan_markdown,
    is_project_plan_stale,
    load_confirmed_voiceover_project_plan,
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.intro_hook_service import (
    get_active_dramaturgy_folder_names,
    get_confirmed_folder_voiceover_names,
)
from otio_app.services.voiceover_generation.models import ConfirmedFolderPlanItem, ConfirmedVoiceoverProjectPlan
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import require_without_voiceover_mode

import streamlit as st


def _render_status_overview(project: Project, plan: ConfirmedVoiceoverProjectPlan) -> None:
    active_folders = get_active_dramaturgy_folder_names(project)
    confirmed_folders = get_confirmed_folder_voiceover_names(project)
    audio_ready_count = sum(
        1 for f in plan.folders if f.audio_status != AUDIO_STATUS_MISSING
    ) + (1 if plan.intro.audio_status != AUDIO_STATUS_MISSING else 0)
    alignment_ready_count = sum(1 for f in plan.folders if f.alignment_items) + (
        1 if plan.intro.alignment_items else 0
    )

    st.write(f"## {plan.project_title or '(ohne Titel)'}")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Sprache", plan.language)
    with col2:
        st.metric("Project Status", plan.status)
    with col3:
        st.metric("Ready for Cut", "✅ Ja" if plan.status == PLAN_STATUS_READY_FOR_CUT else "❌ Nein")
    with col4:
        st.metric("Aktive Ordner", f"{len(confirmed_folders)}/{len(active_folders)} bestätigt")

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        st.metric("Audio bereit", f"{audio_ready_count}/{len(plan.folders) + 1}")
    with col6:
        st.metric("Alignment bereit", f"{alignment_ready_count}/{len(plan.folders) + 1}")
    with col7:
        st.metric("Warnings", len(plan.warnings))
    with col8:
        st.metric("Blockers", len(plan.blockers))

    if is_project_plan_stale(project, plan):
        st.warning("Der finale Projektplan ist veraltet. Bitte aktualisieren.")


def _render_intro_card(plan: ConfirmedVoiceoverProjectPlan) -> None:
    st.subheader("Intro")
    intro = plan.intro
    if not intro.hook_text:
        st.info("Noch kein bestätigter Intro-Hook vorhanden. → Tab „Intro“")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Hook-Typ", intro.hook_type or "—")
    with col2:
        st.metric("Wortanzahl", intro.word_count)
    with col3:
        st.metric("Audio-Status", intro.audio_status)

    st.write(intro.hook_text)
    st.caption(f"Verwendete Ordner: {', '.join(intro.used_folders) or '—'}")

    if intro.audio_path:
        st.audio(intro.audio_path)
        st.caption(f"Dauer: {intro.audio_duration_sec:.1f}s")

    with st.expander("Visual Beats"):
        if not intro.visual_beats:
            st.caption("Keine visual_beats vorhanden.")
        else:
            rows = [
                {
                    "text": beat.text,
                    "visual_intent": beat.visual_intent,
                    "source_folder_name": beat.source_folder_name,
                    "primary_asset_id": beat.primary_asset_id,
                    "needs_supplement_asset": beat.needs_supplement_asset,
                }
                for beat in intro.visual_beats
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("Alignment"):
        if not intro.alignment_items:
            st.caption("Kein Alignment vorhanden.")
        else:
            rows = [
                {
                    "sentence_id": item.sentence_id,
                    "text": item.text,
                    "audio_start_sec": item.audio_start_sec,
                    "audio_end_sec": item.audio_end_sec,
                }
                for item in intro.alignment_items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_folder_row(folder: ConfirmedFolderPlanItem) -> None:
    with st.expander(f"{folder.order_index}. {folder.folder_name} — {folder.readiness_status}"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Rolle", folder.dramaturgy_role)
        with col2:
            st.metric("Wortanzahl", folder.word_count)
        with col3:
            st.metric("Validierung", folder.validation_status)
        with col4:
            st.metric("Asset-Mapping", folder.asset_mapping_status)

        col5, col6 = st.columns(2)
        with col5:
            st.metric("Audio-Status", folder.audio_status)
        with col6:
            st.metric("Audio-Dauer", f"{folder.audio_duration_sec:.1f}s")

        st.write("**Voice-over-Text:**")
        st.write(folder.voiceover_text_full)

        if folder.audio_path:
            st.audio(folder.audio_path)

        with st.expander("sentence_items"):
            if not folder.sentence_items:
                st.caption("Keine sentence_items vorhanden.")
            else:
                alignment_by_id = {item.sentence_id: item for item in folder.alignment_items}
                rows = []
                for sentence_item in folder.sentence_items:
                    alignment_item = alignment_by_id.get(sentence_item.sentence_id)
                    rows.append(
                        {
                            "sentence_id": sentence_item.sentence_id,
                            "text": sentence_item.text,
                            "primary_asset_id": sentence_item.primary_asset_id,
                            "backup_asset_ids": ", ".join(sentence_item.backup_asset_ids),
                            "needs_supplement_asset": sentence_item.needs_supplement_asset,
                            "audio_start_sec": alignment_item.audio_start_sec if alignment_item else "",
                            "audio_end_sec": alignment_item.audio_end_sec if alignment_item else "",
                        }
                    )
                st.dataframe(rows, use_container_width=True, hide_index=True)

        if folder.warnings:
            st.warning("Warnings:\n" + "\n".join(f"- {message}" for message in folder.warnings))
        if folder.blockers:
            st.error("Blockers:\n" + "\n".join(f"- {message}" for message in folder.blockers))


def render_final_output_page() -> None:
    st.header("⑦ Final Output")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    if load_confirmed_dramaturgy(project) is None:
        st.warning("Bitte zuerst die Dramaturgie bestätigen.")
        return

    st.info(
        "Diese Seite erzeugt keinen Schnittplan und keinen OTIO-Export — sie fasst nur "
        "bestätigte Artefakte zusammen. Textänderungen → Tab „Folder Voice-overs“ / "
        "„Intro“. Neu vertonen → Tab „Audio / ElevenLabs“."
    )

    existing_plan = load_confirmed_voiceover_project_plan(project)

    col_build, col_json, col_md, col_csv, col_all = st.columns(5)
    with col_build:
        build_clicked = st.button(
            "Finalen Plan erstellen/aktualisieren", key=f"vo_final_build_{project.id}", type="primary"
        )
    with col_json:
        export_json_clicked = st.button("JSON exportieren", key=f"vo_final_export_json_{project.id}")
    with col_md:
        export_md_clicked = st.button("Markdown exportieren", key=f"vo_final_export_md_{project.id}")
    with col_csv:
        export_csv_clicked = st.button("CSV exportieren", key=f"vo_final_export_csv_{project.id}")
    with col_all:
        export_all_clicked = st.button("Alle Exporte aktualisieren", key=f"vo_final_export_all_{project.id}")

    if build_clicked:
        with st.spinner("Finaler Projektplan wird erstellt…"):
            new_plan = build_confirmed_voiceover_project_plan(project)
            save_confirmed_voiceover_project_plan(project, new_plan)
        st.success("Finaler Voice-over-Projektplan aktualisiert.")
        st.rerun()

    if existing_plan is None:
        st.info("Noch kein finaler Projektplan vorhanden.")
        return

    if export_json_clicked or export_all_clicked:
        path = export_voiceover_project_plan_json(project, existing_plan)
        st.success(f"JSON exportiert: `{path}`")
    if export_md_clicked or export_all_clicked:
        path = export_voiceover_project_plan_markdown(project, existing_plan)
        st.success(f"Markdown exportiert: `{path}`")
    if export_csv_clicked or export_all_clicked:
        path = export_voiceover_project_plan_csv(project, existing_plan)
        st.success(f"CSV exportiert: `{path}`")

    _render_status_overview(project, existing_plan)
    _render_intro_card(existing_plan)

    st.subheader("Ordner (in bestätigter dramaturgischer Reihenfolge)")
    if not existing_plan.folders:
        st.info("Noch keine bestätigten Ordner im Plan.")
    for folder in sorted(existing_plan.folders, key=lambda item: item.order_index):
        _render_folder_row(folder)

    if existing_plan.blockers or existing_plan.warnings:
        st.subheader("Alle Warnungen / Blocker")
        for error in existing_plan.blockers:
            location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
            st.error(f"[{error.type}]{location}: {error.message}")
        for error in existing_plan.warnings:
            location = f" ({error.scope}: {error.folder_name})" if error.folder_name else f" ({error.scope})"
            st.warning(f"[{error.type}]{location}: {error.message}")

    with st.expander("Pfade"):
        st.caption(f"confirmed_voiceover_project_plan.json: `{get_confirmed_voiceover_project_plan_path(project.work_dir_path)}`")
        st.caption(f"voiceover_project_plan.json: `{get_voiceover_project_plan_json_path(project.work_dir_path)}`")
        st.caption(f"voiceover_project_plan.md: `{get_voiceover_project_plan_md_path(project.work_dir_path)}`")
        st.caption(f"voiceover_project_plan.csv: `{get_voiceover_project_plan_csv_path(project.work_dir_path)}`")
