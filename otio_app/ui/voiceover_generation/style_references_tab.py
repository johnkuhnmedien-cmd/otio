"""Style References & Style Profile — Beispielskripte, abgeleiteter Stil (Phase 2)."""

from __future__ import annotations

import streamlit as st

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
    STYLE_MODE_CHOICES,
    STYLE_MODE_LABELS,
    STYLE_MODE_PROFILE,
    STYLE_MODE_RAW_TEXT,
    VOICEOVER_GEN_ROLE_LABELS,
    VOICEOVER_GEN_ROLES,
    VoiceoverGenerationModelSettings,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.raw_style_library_service import (
    delete_raw_from_library,
    get_raw_from_library,
    get_raw_style_library_path,
    load_raw_style_library,
    save_raw_to_library,
)
from otio_app.services.voiceover_generation.style_profile_library_service import (
    delete_profile_from_library,
    get_profile_from_library,
    get_style_profile_library_path,
    load_style_profile_library,
    save_profile_to_library,
)
from otio_app.services.voiceover_generation.style_profile_service import (
    build_style_profile,
    load_style_profile,
    save_style_profile,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    is_allowed_upload_filename,
    is_raw_style_mode,
    load_style_references,
    normalize_style_mode,
    save_style_references,
    truncate_upload_text,
)
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_input_info,
    render_llm_model_selectbox,
    require_without_voiceover_mode,
)

_REF_SLOTS = 3


def _ref_key(project_id: str, kind: str, index: int) -> str:
    return f"vo_style_ref_{kind}_{index}_{project_id}"


def _mode_key(project_id: str) -> str:
    return f"vo_style_mode_{project_id}"


def _raw_key(project_id: str) -> str:
    return f"vo_style_raw_{project_id}"


def _raw_intro_key(project_id: str) -> str:
    return f"vo_style_raw_intro_{project_id}"


def _padded(values: list[str], size: int = _REF_SLOTS) -> list[str]:
    padded = list(values[:size])
    while len(padded) < size:
        padded.append("")
    return padded


def _refs_loaded_key(project_id: str) -> str:
    return f"vo_style_refs_loaded_{project_id}"


def _apply_refs_to_session(project_id: str, refs: VoiceoverStyleReferences) -> None:
    """Nur aufrufen, BEVOR die zugehörigen Widgets in diesem Run instanziert werden."""
    st.session_state[_mode_key(project_id)] = normalize_style_mode(refs.style_mode)
    st.session_state[_raw_key(project_id)] = refs.raw_reference_text or ""
    st.session_state[_raw_intro_key(project_id)] = refs.raw_intro_reference_text or ""
    for index, text in enumerate(_padded(refs.intro_reference_texts)):
        st.session_state[_ref_key(project_id, "intro", index)] = text
    for index, text in enumerate(_padded(refs.segment_reference_texts)):
        st.session_state[_ref_key(project_id, "segment", index)] = text


def _schedule_refs_reload_from_disk(project_id: str) -> None:
    """Nach Widget-Instanziierung: nicht session_state der Widget-Keys setzen.

    Stattdessen den Load-Marker löschen und per st.rerun() die Referenzen
    am Anfang des nächsten Runs aus der Datei in die Widgets laden.
    """
    st.session_state.pop(_refs_loaded_key(project_id), None)


def _render_model_settings_editor(project: Project) -> None:
    settings = load_model_settings(project)
    with st.expander("⚙️ Modell-Einstellungen (LLM pro Rolle)", expanded=False):
        st.caption(
            "In Phase 2 wird nur „Style Profile“ aktiv verwendet. Die übrigen "
            "Rollen sind bereits vorbereitet (spätere Phasen)."
        )
        updated_roles = {}
        for role in VOICEOVER_GEN_ROLES:
            label = VOICEOVER_GEN_ROLE_LABELS[role]
            # Style-Seite: nur Style-Profile aktiv; Info nur dort, um Rauschen zu vermeiden.
            info = LLM_INPUT_INFO["style_profile"] if role == "style_profile" else None
            updated_roles[role] = render_llm_model_selectbox(
                label=label,
                role_settings=getattr(settings, role),
                key=f"vo_model_{role}_{project.id}",
                input_info=info,
            )

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
            st.caption(f"LLM-Run: `{get_llm_run_dir(project.language_work_dir_path, run_id)}`")

    if profile is None:
        st.info("Status: **MISSING** — noch kein Style Profile erzeugt.")
        return

    st.success("Status: **READY**")
    if profile.library_name:
        st.caption(f"Aus Bibliothek: **{profile.library_name}**")
    st.caption(f"Erzeugt: {profile.generated_at.isoformat()}")
    if profile.llm_run_id:
        st.caption(f"LLM-Run: `{get_llm_run_dir(project.language_work_dir_path, profile.llm_run_id)}`")
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
    st.caption(f"Pfad: `{get_voiceover_style_profile_path(project.language_work_dir_path)}`")


def _render_style_profile_library(project: Project) -> None:
    st.subheader("Style Profile Bibliothek (projektübergreifend)")
    st.caption(
        "Style Profiles in dieser Bibliothek sind NICHT an ein Projekt gebunden — "
        "sie werden zentral gespeichert und stehen in jedem Projekt zur Auswahl."
    )
    current_profile = load_style_profile(project)
    library = load_style_profile_library()

    col_save, col_apply = st.columns(2)
    with col_save:
        st.markdown("**Aktuelles Style Profile in Bibliothek speichern**")
        if current_profile is None:
            st.caption("Für dieses Projekt existiert noch kein Style Profile.")
        else:
            library_name = st.text_input(
                "Name in der Bibliothek",
                value=current_profile.library_name,
                key=f"vo_style_lib_save_name_{project.id}",
                help="Frei wählbarer Name, unter dem dieses Style Profile wiedergefunden wird.",
            )
            if st.button("In Bibliothek speichern", key=f"vo_style_lib_save_{project.id}"):
                cleaned_name = library_name.strip()
                if not cleaned_name:
                    st.warning("Bitte einen Namen angeben.")
                else:
                    named_profile = current_profile.model_copy(update={"library_name": cleaned_name})
                    save_profile_to_library(cleaned_name, named_profile)
                    # Projekt-eigene Kopie ebenfalls mit dem Namen verknüpfen, damit
                    # er z. B. bei den Voraussetzungen in Dramaturgie/Intro/Folder
                    # Voice-overs statt eines Häkchens angezeigt wird.
                    save_style_profile(project, named_profile)
                    st.success(f"Style Profile als „{cleaned_name}“ in der Bibliothek gespeichert.")
                    st.rerun()

    with col_apply:
        st.markdown("**Style Profile aus Bibliothek in dieses Projekt übernehmen**")
        if not library.entries:
            st.caption("Bibliothek ist noch leer.")
        else:
            names = [entry.name for entry in library.entries]
            selected_name = st.selectbox(
                "Gespeichertes Style Profile",
                options=names,
                key=f"vo_style_lib_select_{project.id}",
            )
            col_apply_btn, col_delete_btn = st.columns(2)
            with col_apply_btn:
                if st.button("In dieses Projekt laden", key=f"vo_style_lib_load_{project.id}"):
                    entry_profile = get_profile_from_library(selected_name)
                    if entry_profile is not None:
                        named_profile = entry_profile.model_copy(
                            update={"library_name": selected_name}
                        )
                        save_style_profile(project, named_profile)
                        st.success(f"„{selected_name}“ wurde in dieses Projekt übernommen.")
                        st.rerun()
            with col_delete_btn:
                if st.button("Aus Bibliothek löschen", key=f"vo_style_lib_delete_{project.id}"):
                    delete_profile_from_library(selected_name)
                    st.success(f"„{selected_name}“ wurde aus der Bibliothek gelöscht.")
                    st.rerun()

    with st.expander("Alle Bibliothekseinträge (JSON)"):
        st.json(library.model_dump(mode="json"))
    st.caption(f"Pfad: `{get_style_profile_library_path()}`")


def _render_raw_style_library(
    project: Project,
    *,
    raw_text: str,
    raw_intro_text: str,
) -> None:
    st.subheader("Raw-Text Bibliothek (projektübergreifend)")
    st.caption(
        "Raw-Texte (allgemein + Intro) hier speichern und in anderen Projekten laden. "
        f"Ablage: `{get_raw_style_library_path()}`"
    )
    library = load_raw_style_library()
    existing = load_style_references(project)

    col_save, col_apply = st.columns(2)
    with col_save:
        st.markdown("**Aktuelle Raw-Texte in Bibliothek speichern**")
        default_name = existing.raw_library_name or ""
        library_name = st.text_input(
            "Name in der Bibliothek",
            value=default_name,
            key=f"vo_raw_lib_save_name_{project.id}",
        )
        if st.button("In Bibliothek speichern", key=f"vo_raw_lib_save_{project.id}"):
            cleaned = library_name.strip()
            if not cleaned:
                st.warning("Bitte einen Namen angeben.")
            elif not raw_text.strip() and not raw_intro_text.strip():
                st.warning("Beide Raw-Text-Felder sind leer.")
            else:
                save_raw_to_library(
                    cleaned,
                    raw_reference_text=raw_text,
                    raw_intro_reference_text=raw_intro_text,
                )
                save_style_references(
                    project,
                    VoiceoverStyleReferences(
                        project_id=project.id,
                        style_mode=STYLE_MODE_RAW_TEXT,
                        raw_reference_text=raw_text,
                        raw_intro_reference_text=raw_intro_text,
                        raw_library_name=cleaned,
                    ),
                )
                st.success(f"Raw-Texte als „{cleaned}“ gespeichert.")
                st.rerun()

    with col_apply:
        st.markdown("**Raw-Texte aus Bibliothek laden**")
        if not library.entries:
            st.caption("Bibliothek ist noch leer.")
        else:
            names = [entry.name for entry in library.entries]
            selected_name = st.selectbox(
                "Gespeicherter Raw-Text-Satz",
                options=names,
                key=f"vo_raw_lib_select_{project.id}",
            )
            col_load, col_delete = st.columns(2)
            with col_load:
                if st.button("In dieses Projekt laden", key=f"vo_raw_lib_load_{project.id}"):
                    entry = get_raw_from_library(selected_name)
                    if entry is not None:
                        save_style_references(
                            project,
                            VoiceoverStyleReferences(
                                project_id=project.id,
                                style_mode=STYLE_MODE_RAW_TEXT,
                                raw_reference_text=entry.raw_reference_text,
                                raw_intro_reference_text=entry.raw_intro_reference_text,
                                raw_library_name=selected_name,
                            ),
                        )
                        # Widgets (mode/raw textareas) sind in diesem Run schon da —
                        # session_state der Keys hier nicht setzen (StreamlitAPIException).
                        _schedule_refs_reload_from_disk(project.id)
                        st.success(f"„{selected_name}“ übernommen.")
                        st.rerun()
            with col_delete:
                if st.button("Aus Bibliothek löschen", key=f"vo_raw_lib_delete_{project.id}"):
                    delete_raw_from_library(selected_name)
                    st.success(f"„{selected_name}“ gelöscht.")
                    st.rerun()

    with st.expander("Alle Raw-Bibliothekseinträge (JSON)"):
        st.json(library.model_dump(mode="json"))


def _collect_uploads(project: Project) -> tuple[list[str], list[str]]:
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
    return uploaded_file_names, uploaded_file_texts


def render_style_references_page() -> None:
    st.header("② Style References")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    loaded_key = _refs_loaded_key(project.id)
    if loaded_key not in st.session_state:
        _apply_refs_to_session(project.id, load_style_references(project))
        st.session_state[loaded_key] = True

    st.subheader("Stil-Quelle")
    selected_mode = st.radio(
        "Wie soll der Stil an die späteren LLM-Schritte gehen?",
        options=list(STYLE_MODE_CHOICES),
        format_func=lambda mode: STYLE_MODE_LABELS[mode],
        key=_mode_key(project.id),
        horizontal=False,
    )
    selected_mode = normalize_style_mode(selected_mode)

    existing_refs = load_style_references(project)
    intro_texts = _padded(existing_refs.intro_reference_texts)
    segment_texts = _padded(existing_refs.segment_reference_texts)
    raw_text = st.session_state.get(
        _raw_key(project.id), existing_refs.raw_reference_text or ""
    )
    raw_intro_text = st.session_state.get(
        _raw_intro_key(project.id), existing_refs.raw_intro_reference_text or ""
    )
    uploaded_file_names: list[str] = list(existing_refs.uploaded_file_names)
    uploaded_file_texts: list[str] = list(existing_refs.uploaded_file_texts)

    if selected_mode == STYLE_MODE_RAW_TEXT:
        st.info(
            "Ohne Style Profile: Raw-Texte gehen als Stil-Referenz an die LLMs — "
            "nicht wörtlich kopieren. "
            "**Raw Text** → Kapitel-VOs u. a.; **Raw Text Intro** → nur Intro (⑤)."
        )
        raw_text = st.text_area(
            "Raw Text (Kapitel / allgemein)",
            key=_raw_key(project.id),
            height=220,
            help=(
                "Stil-Beispiel für Folder Voice-overs, Dramaturgie, Cut Plan usw. "
                "Nicht wörtlich kopieren."
            ),
        )
        raw_intro_text = st.text_area(
            "Raw Text Intro",
            key=_raw_intro_key(project.id),
            height=220,
            help=(
                "Stil-Beispiel nur für den Intro-Hook (Schritt ⑤). "
                "Leer = Fallback auf den allgemeinen Raw Text."
            ),
        )
        st.caption(
            "Style Profile ist in diesem Modus nicht nötig. "
            "Gespeicherte Profile bleiben unberührt."
        )
        save_clicked = st.button(
            "Raw-Texte speichern",
            key=f"vo_style_refs_save_{project.id}",
        )
        build_clicked = False
        _render_raw_style_library(
            project, raw_text=raw_text, raw_intro_text=raw_intro_text
        )
    else:
        st.info(
            "Die Referenzen werden nicht kopiert. Das Modell soll daraus nur "
            "Stilmerkmale ableiten."
        )
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

        uploaded_file_names, uploaded_file_texts = _collect_uploads(project)

        # Nur EIN Erzeugen-Button, je nach Zustand unterschiedlich benannt — vorher
        # gab es zwei Buttons ("erstellen"/"neu erstellen") mit identischer
        # Funktion, was Verwirrung stiftete ("Was ist der Unterschied zwischen den
        # beiden Buttons?"). Beide haben immer schon dasselbe getan: ein neues
        # Style Profile per LLM erzeugen und das bestehende (falls vorhanden)
        # ersetzen.
        existing_profile_before_click = load_style_profile(project)
        build_label = (
            "Style Profile neu erstellen"
            if existing_profile_before_click is not None
            else "Style Profile erstellen"
        )
        col_save, col_build = st.columns(2)
        with col_save:
            save_clicked = st.button(
                "Referenzen speichern", key=f"vo_style_refs_save_{project.id}"
            )
        with col_build:
            build_clicked = st.button(build_label, key=f"vo_style_profile_build_{project.id}")
            render_llm_input_info(LLM_INPUT_INFO["style_profile"])
            if existing_profile_before_click is not None:
                st.caption("Ersetzt das aktuell gespeicherte Style Profile dieses Projekts.")

    current_refs = VoiceoverStyleReferences(
        project_id=project.id,
        style_mode=selected_mode,
        raw_reference_text=raw_text,
        raw_intro_reference_text=raw_intro_text,
        raw_library_name=existing_refs.raw_library_name,
        intro_reference_texts=intro_texts,
        segment_reference_texts=segment_texts,
        uploaded_file_names=uploaded_file_names,
        uploaded_file_texts=uploaded_file_texts,
    )

    if save_clicked:
        saved = save_style_references(project, current_refs)
        if is_raw_style_mode(saved):
            st.success(
                "Raw-Texte gespeichert — Kapitel-LLMs nutzen Raw Text, "
                "Intro nutzt Raw Text Intro (Fallback: Raw Text)."
            )
        else:
            st.success("Style References gespeichert.")
        st.caption(
            f"Pfad: `{get_voiceover_style_references_path(project.language_work_dir_path)}`"
        )
        with st.expander("JSON-Vorschau"):
            st.json(saved.model_dump(mode="json"))

    if build_clicked:
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

    if selected_mode == STYLE_MODE_RAW_TEXT:
        refs_on_disk = load_style_references(project)
        has_general = bool(refs_on_disk.raw_reference_text.strip())
        has_intro = bool(refs_on_disk.raw_intro_reference_text.strip())
        if is_raw_style_mode(refs_on_disk) and (has_general or has_intro):
            bits = []
            if has_general:
                bits.append("Raw Text")
            if has_intro:
                bits.append("Raw Text Intro")
            lib = (
                f" · Bibliothek „{refs_on_disk.raw_library_name}“"
                if refs_on_disk.raw_library_name
                else ""
            )
            st.success(
                f"Status: **RAW TEXT READY** ({', '.join(bits)}){lib} — "
                "Style Profile wird übersprungen."
            )
            with st.expander("Gespeicherte Raw-Texte"):
                st.markdown("**Raw Text (Kapitel / allgemein)**")
                st.text(refs_on_disk.raw_reference_text or "(leer)")
                st.markdown("**Raw Text Intro**")
                st.text(refs_on_disk.raw_intro_reference_text or "(leer — Fallback Raw Text)")
        else:
            st.warning("Raw-Texte noch nicht gespeichert oder leer.")
    else:
        _render_model_settings_editor(project)
        _render_style_profile_status(project)
        _render_style_profile_library(project)
