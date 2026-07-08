"""Style References & Style Profile — Beispielskripte, abgeleiteter Stil (Phase 2)."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import VOICEOVER_GEN_PROVIDERS
from otio_app.models import Project
from otio_app.project_layout import (
    get_llm_run_dir,
    get_voiceover_style_profile_path,
    get_voiceover_style_references_path,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import (
    LlmRoleSettings,
    VOICEOVER_GEN_ROLE_LABELS,
    VOICEOVER_GEN_ROLES,
    VoiceoverGenerationModelSettings,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import (
    build_style_profile,
    load_style_profile,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    is_allowed_upload_filename,
    load_style_references,
    save_style_references,
    truncate_upload_text,
)
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import require_without_voiceover_mode

_REF_SLOTS = 3


def _ref_key(project_id: str, kind: str, index: int) -> str:
    return f"vo_style_ref_{kind}_{index}_{project_id}"


def _padded(values: list[str], size: int = _REF_SLOTS) -> list[str]:
    padded = list(values[:size])
    while len(padded) < size:
        padded.append("")
    return padded


def _apply_refs_to_session(project_id: str, refs: VoiceoverStyleReferences) -> None:
    for index, text in enumerate(_padded(refs.intro_reference_texts)):
        st.session_state[_ref_key(project_id, "intro", index)] = text
    for index, text in enumerate(_padded(refs.segment_reference_texts)):
        st.session_state[_ref_key(project_id, "segment", index)] = text


def _render_model_settings_editor(project: Project) -> None:
    settings = load_model_settings(project)
    with st.expander("⚙️ Modell-Einstellungen (LLM pro Rolle)", expanded=False):
        st.caption(
            "In Phase 2 wird nur „Style Profile“ aktiv verwendet. Die übrigen "
            "Rollen sind bereits vorbereitet (spätere Phasen)."
        )
        updated_roles: dict[str, LlmRoleSettings] = {}
        for role in VOICEOVER_GEN_ROLES:
            role_settings = getattr(settings, role)
            label = VOICEOVER_GEN_ROLE_LABELS[role]
            col1, col2 = st.columns(2)
            with col1:
                default_index = (
                    VOICEOVER_GEN_PROVIDERS.index(role_settings.provider)
                    if role_settings.provider in VOICEOVER_GEN_PROVIDERS
                    else 0
                )
                provider = st.selectbox(
                    f"{label} — Provider",
                    options=VOICEOVER_GEN_PROVIDERS,
                    index=default_index,
                    key=f"vo_model_provider_{role}_{project.id}",
                )
            with col2:
                model = st.text_input(
                    f"{label} — Modell",
                    value=role_settings.model,
                    key=f"vo_model_name_{role}_{project.id}",
                )
            updated_roles[role] = LlmRoleSettings(provider=provider, model=model)

        if st.button(
            "Modell-Einstellungen speichern", key=f"vo_model_settings_save_{project.id}"
        ):
            save_model_settings(project, VoiceoverGenerationModelSettings(**updated_roles))
            st.success("Modell-Einstellungen gespeichert.")


def _render_style_profile_status(project: Project) -> None:
    st.subheader("Style Profile Status")
    profile = load_style_profile(project)
    last_result = st.session_state.get(f"vo_style_profile_last_result_{project.id}")

    if last_result is not None and last_result.get("status") != STATUS_PASS:
        st.error(
            f"Letzter Versuch fehlgeschlagen ({last_result.get('status')}): "
            f"{last_result.get('error')}"
        )
        run_id = last_result.get("llm_run_id")
        if run_id:
            st.caption(f"LLM-Run: `{get_llm_run_dir(project.work_dir_path, run_id)}`")

    if profile is None:
        st.info("Status: **MISSING** — noch kein Style Profile erzeugt.")
        return

    st.success("Status: **READY**")
    st.caption(f"Erzeugt: {profile.generated_at.isoformat()}")
    if profile.llm_run_id:
        st.caption(f"LLM-Run: `{get_llm_run_dir(project.work_dir_path, profile.llm_run_id)}`")
    st.write(f"**Zusammenfassung für Prompts:** {profile.style_summary_for_prompts or '—'}")

    col_do, col_dont = st.columns(2)
    with col_do:
        st.markdown("**Do**")
        st.write("\n".join(f"- {item}" for item in profile.do) or "—")
    with col_dont:
        st.markdown("**Don't**")
        st.write("\n".join(f"- {item}" for item in profile.dont) or "—")

    if profile.forbidden_phrases:
        st.markdown("**Verbotene Phrasen (Style Profile)**")
        st.write(", ".join(profile.forbidden_phrases))

    with st.expander("Vollständiges Style Profile (JSON)"):
        st.json(profile.model_dump(mode="json"))
    st.caption(f"Pfad: `{get_voiceover_style_profile_path(project.work_dir_path)}`")


def render_style_references_page() -> None:
    st.header("② Style References")
    st.info(
        "Die Referenzen werden nicht kopiert. Das Modell soll daraus nur "
        "Stilmerkmale ableiten."
    )

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    loaded_key = f"vo_style_refs_loaded_{project.id}"
    if loaded_key not in st.session_state:
        _apply_refs_to_session(project.id, load_style_references(project))
        st.session_state[loaded_key] = True

    st.subheader("Intro-Referenzen")
    intro_texts = [
        st.text_area(
            f"Beispiel-Intro {index + 1}",
            key=_ref_key(project.id, "intro", index),
            height=100,
        )
        for index in range(_REF_SLOTS)
    ]

    st.subheader("Ordner-/Segment-Voice-over-Referenzen")
    segment_texts = [
        st.text_area(
            f"Beispiel-Segment {index + 1}",
            key=_ref_key(project.id, "segment", index),
            height=100,
        )
        for index in range(_REF_SLOTS)
    ]

    st.subheader("Optional: Datei-Upload")
    st.caption("Nur .txt und .md — keine PDF/DOCX-Verarbeitung.")
    uploaded_files = st.file_uploader(
        "Referenzdateien hochladen",
        type=["txt", "md"],
        accept_multiple_files=True,
        key=f"vo_style_ref_uploads_{project.id}",
    )
    uploaded_file_names: list[str] = []
    uploaded_file_texts: list[str] = []
    if uploaded_files:
        for uploaded_file in uploaded_files:
            if not is_allowed_upload_filename(uploaded_file.name):
                st.warning(f"Übersprungen (nicht unterstützt): `{uploaded_file.name}`")
                continue
            raw_bytes = uploaded_file.getvalue()
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_bytes.decode("utf-8", errors="replace")
            truncated_text, was_truncated = truncate_upload_text(text)
            if was_truncated:
                st.warning(
                    f"`{uploaded_file.name}` ist sehr groß und wurde auf "
                    f"{len(truncated_text)} Zeichen gekürzt."
                )
            uploaded_file_names.append(uploaded_file.name)
            uploaded_file_texts.append(truncated_text)
            with st.expander(f"Vorschau: {uploaded_file.name}"):
                st.text(truncated_text[:2000])

    col_save, col_build, col_rebuild = st.columns(3)
    with col_save:
        save_clicked = st.button("Referenzen speichern", key=f"vo_style_refs_save_{project.id}")
    with col_build:
        build_clicked = st.button(
            "Style Profile erstellen", key=f"vo_style_profile_build_{project.id}"
        )
    with col_rebuild:
        rebuild_clicked = st.button(
            "Style Profile neu erstellen", key=f"vo_style_profile_rebuild_{project.id}"
        )

    current_refs = VoiceoverStyleReferences(
        project_id=project.id,
        intro_reference_texts=intro_texts,
        segment_reference_texts=segment_texts,
        uploaded_file_names=uploaded_file_names,
        uploaded_file_texts=uploaded_file_texts,
    )

    if save_clicked:
        saved = save_style_references(project, current_refs)
        st.success("Style References gespeichert.")
        st.caption(f"Pfad: `{get_voiceover_style_references_path(project.work_dir_path)}`")
        with st.expander("JSON-Vorschau"):
            st.json(saved.model_dump(mode="json"))

    if build_clicked or rebuild_clicked:
        # Aktuelle Formularwerte zuerst speichern, damit das Style Profile immer
        # aus dem tatsächlich gerade angezeigten Stand erzeugt wird.
        saved_refs = save_style_references(project, current_refs)
        st.info("Aktuelle Referenzen wurden gespeichert und für das Style Profile verwendet.")
        brief = load_project_brief(project)
        settings = load_model_settings(project)
        with st.spinner("Style Profile wird erzeugt…"):
            result = build_style_profile(
                project,
                project_brief=brief,
                style_references=saved_refs,
                provider=settings.style_profile.provider,
                model=settings.style_profile.model,
            )
        st.session_state[f"vo_style_profile_last_result_{project.id}"] = {
            "status": result.status,
            "error": result.error,
            "llm_run_id": result.llm_run_id,
        }
        if result.status == STATUS_PASS:
            st.success("Style Profile erfolgreich erstellt.")
        else:
            st.error(f"Style Profile fehlgeschlagen ({result.status}): {result.error}")

    _render_model_settings_editor(project)
    _render_style_profile_status(project)
