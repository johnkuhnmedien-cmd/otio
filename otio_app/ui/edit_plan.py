"""Streamlit-UI: Schnittplan erstellen und freigeben."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings
from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_FALLBACK_ORDER,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
    FALLBACK_SOURCE_LABELS,
    GEMINI_MODEL_CHOICES,
)
from otio_app.services.edit_plan_builder import build_edit_plan, load_edit_plan, save_edit_plan
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    count_folder_states,
)
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    format_gemini_model_label,
    get_default_gemini_model,
    is_gemini_configured,
)
from otio_app.services.edit_plan_rules import validate_shots_against_rules
from otio_app.ui.edit_plan_rules_ui import (
    get_edit_plan_rules_for_project,
    render_edit_plan_rules_manager,
)
from otio_app.ui.project_context import (
    render_file_paths,
    render_project_selector,
    render_workflow_progress,
)


def _plan_state_key(project_id: str) -> str:
    return f"edit_plan_draft_{project_id}"


def _get_draft(project_id: str) -> EditPlanDocument | None:
    raw = st.session_state.get(_plan_state_key(project_id))
    if not raw:
        return None
    return EditPlanDocument.model_validate(raw)


def _set_draft(document: EditPlanDocument) -> None:
    st.session_state[_plan_state_key(document.project_id)] = document.model_dump(mode="json")


def render_edit_plan_page() -> None:
    st.header("③ Schnittplan")

    project = render_project_selector()
    if project is None:
        return

    render_workflow_progress(project, current_step="edit_plan")

    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        st.warning("Bitte zuerst unter „② Zuordnung“ die Voice-over-Zuordnung bestätigen.")
        render_file_paths(project)
        return

    mapped_folders = sorted(
        {entry.folder for entry in mapping.entries if entry.folder and entry.confirmed}
    )
    saved = load_edit_plan(project)
    if saved is not None and saved.confirmed:
        st.success(f"Schnittplan bestätigt: `{project.edit_plan_path}`")

    tab_settings, tab_generate, tab_review = st.tabs(
        ["⚙️ Regeln", "▶️ Vorschlag", "✅ Prüfen & Speichern"]
    )

    with tab_settings:
        rules_doc = render_edit_plan_rules_manager(project)
        st.divider()
        st.markdown("**Timing & Gemini**")
        col1, col2, col3 = st.columns(3)
        with col1:
            shot_min = st.number_input(
                "Min. Shot (Sek.)",
                value=float(DEFAULT_SHOT_MIN_SEC),
                min_value=1.0,
                max_value=30.0,
                step=0.5,
                key=f"plan_min_{project.id}",
            )
        with col2:
            shot_max = st.number_input(
                "Max. Shot (Sek.)",
                value=float(DEFAULT_SHOT_MAX_SEC),
                min_value=1.0,
                max_value=60.0,
                step=0.5,
                key=f"plan_max_{project.id}",
            )
        with col3:
            audio_offset = st.number_input(
                "Audio-Start (+Sek.)",
                value=float(DEFAULT_AUDIO_OFFSET_SEC),
                min_value=0.0,
                max_value=10.0,
                step=0.5,
                key=f"plan_offset_{project.id}",
            )

        splitters = st.text_input(
            "Text-Trenner (kommagetrennt)",
            value=", und ,, , und ",
            key=f"plan_split_{project.id}",
        )
        st.caption("Fallback-Reihenfolge (Adobe Stock / Pexels / KI folgen später):")
        for source in DEFAULT_FALLBACK_ORDER:
            st.write(f"- {FALLBACK_SOURCE_LABELS.get(source, source)}")

        default_model = get_default_gemini_model()
        gemini_model = st.selectbox(
            "Gemini-Modell (Motiv → Asset)",
            options=list(GEMINI_MODEL_CHOICES),
            index=list(GEMINI_MODEL_CHOICES).index(default_model),
            format_func=format_gemini_model_label,
            key=f"plan_gemini_{project.id}",
        )
        api_confirmed = st.checkbox(
            "Kostenpflichtige Gemini-Aufrufe für Schnittplan bestätigen",
            key=f"plan_api_{project.id}",
        )

        folder_filter = st.multiselect(
            "Nur diese Ordner planen (leer = alle zugeordneten)",
            options=mapped_folders,
            key=f"plan_folders_{project.id}",
        )

    with tab_generate:
        st.markdown(
            "Gemini erhält **nur Text** (Whisper) + **Asset-Beschreibungen** und schlägt Shots vor. "
            "Mehrere Orte in einem Satz werden in mehrere Shots zerlegt."
        )
        if not is_gemini_configured():
            st.warning("Ohne GEMINI_API_KEY wird nur eine einfache Text-Trennung genutzt.")

        if st.button("Schnittplan vorschlagen", key=f"build_plan_{project.id}", type="primary"):
            use_gemini = api_confirmed and is_gemini_configured()
            if is_gemini_configured() and not api_confirmed:
                st.warning("Bitte Gemini-Aufrufe bestätigen — sonst nur Text-Trennung.")
            else:
                settings = EditPlanSettings(
                    shot_min_sec=float(shot_min),
                    shot_max_sec=float(shot_max),
                    audio_offset_sec=float(audio_offset),
                    text_splitters=[
                        piece.strip()
                        for piece in splitters.split(",")
                        if piece.strip()
                    ],
                    fallback_order=list(DEFAULT_FALLBACK_ORDER),
                    gemini_model=gemini_model,
                )
                try:
                    with st.spinner("Schnittplan wird erstellt…"):
                        document = build_edit_plan(
                            project,
                            settings,
                            use_api=use_gemini,
                            folder_names=folder_filter or None,
                        )
                    _set_draft(document)
                    st.success(f"{len(document.shots)} Shots vorgeschlagen.")
                    st.rerun()
                except (GeminiNotConfiguredError, ValueError, FileNotFoundError) as exc:
                    st.error(str(exc))

        draft = _get_draft(project.id) or saved
        if draft is not None:
            missing = sum(1 for shot in draft.shots if shot.asset_source == "missing")
            violations = validate_shots_against_rules(draft.shots, rules_doc)
            st.caption(
                f"{len(draft.shots)} Shots · {missing} ohne passendes lokales Asset"
            )
            if violations:
                st.warning("Regelverletzungen — ggf. unter „Regeln“ anpassen und neu generieren:")
                for line in violations[:15]:
                    st.caption(f"• {line}")
                if len(violations) > 15:
                    st.caption(f"… und {len(violations) - 15} weitere")

    with tab_review:
        draft = _get_draft(project.id) or saved
        if draft is None or not draft.shots:
            st.info("Noch kein Vorschlag — zuerst unter „Vorschlag“ generieren.")
            render_file_paths(project)
            return

        st.markdown(f"**{len(draft.shots)} Shots** — Audio-Offset: {draft.settings.audio_offset_sec}s")
        rules_doc = get_edit_plan_rules_for_project(project)
        violations = validate_shots_against_rules(draft.shots, rules_doc)
        if violations:
            st.warning(f"{len(violations)} Regelverletzung(en) im aktuellen Vorschlag.")
        for index, shot in enumerate(draft.shots):
            icon = "🟢" if shot.asset_path else "🟡"
            with st.expander(
                f"{icon} Shot {index + 1} · {shot.folder} · {shot.duration_sec:.1f}s",
                expanded=index < 2,
            ):
                st.write(f"**Motiv:** {shot.motif or '—'}")
                st.write(f"**Voice:** {shot.voice_start_sec:.1f}–{shot.voice_end_sec:.1f}s")
                st.caption(shot.passage_text)
                if shot.asset_path:
                    st.write(f"**Asset:** `{Path(shot.asset_path).name}`")
                else:
                    st.warning("Kein lokales Asset — Fallback folgt später (Adobe Stock / Pexels / KI).")

        confirm = st.checkbox(
            "Schnittplan geprüft und bestätigt",
            key=f"confirm_plan_{project.id}",
        )
        if st.button("Schnittplan speichern", key=f"save_plan_{project.id}", type="primary"):
            if not confirm:
                st.warning("Bitte bestätigen.")
            else:
                confirmed = draft.model_copy(update={"confirmed": True})
                for shot in confirmed.shots:
                    if not shot.asset_path:
                        shot.asset_source = "missing"
                save_edit_plan(project, confirmed)
                _set_draft(confirmed)
                st.success(f"Gespeichert: `{project.edit_plan_path}`")
                st.rerun()

        with st.expander("JSON-Vorschau", expanded=False):
            st.code(draft.model_dump_json(indent=2)[:6000])

    render_file_paths(project)
