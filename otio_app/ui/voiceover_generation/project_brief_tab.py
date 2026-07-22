"""Project Brief — Video-Titel, Sprache, Ton, globale Negativregeln (Phase 2)."""

from __future__ import annotations

import streamlit as st

from otio_app.project_layout import get_project_brief_path
from otio_app.services.voiceover_generation.models import (
    BRIEF_LANGUAGE_CHOICES,
    BRIEF_NEGATIVE_RULE_INSTRUCTIONS,
    BRIEF_NEGATIVE_RULE_LABELS,
    BRIEF_TONE_TAG_CHOICES,
    ProjectBrief,
)
from otio_app.services.voiceover_generation.project_brief_service import (
    default_project_brief,
    load_project_brief,
    parse_forbidden_phrases_text,
    save_project_brief,
)
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import require_without_voiceover_mode


def _key(project_id: str, field_name: str) -> str:
    return f"vo_brief_{field_name}_{project_id}"


def _flag_key(project_id: str, flag: str) -> str:
    return _key(project_id, f"flag_{flag}")


def _apply_brief_to_session(project_id: str, brief: ProjectBrief) -> None:
    st.session_state[_key(project_id, "video_title")] = brief.video_title
    st.session_state[_key(project_id, "language")] = brief.language
    st.session_state[_key(project_id, "tone_tags")] = list(brief.tone_tags)
    for flag in BRIEF_NEGATIVE_RULE_LABELS:
        st.session_state[_flag_key(project_id, flag)] = bool(
            brief.negative_rule_flags.get(flag, False)
        )
    st.session_state[_key(project_id, "forbidden_phrases")] = "\n".join(brief.forbidden_phrases)
    st.session_state[_key(project_id, "negative_rules_freetext")] = brief.negative_rules_freetext
    st.session_state[_key(project_id, "global_extra_prompt")] = brief.global_extra_prompt


def render_project_brief_page() -> None:
    st.header("① Project Brief")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    title_key = _key(project.id, "video_title")
    if title_key not in st.session_state:
        _apply_brief_to_session(project.id, load_project_brief(project))

    video_title = st.text_input("Video-Titel", key=title_key)
    language = st.selectbox(
        "Sprache",
        options=BRIEF_LANGUAGE_CHOICES,
        key=_key(project.id, "language"),
    )
    tone_tags = st.multiselect(
        "Globale Charakteristik / Ton",
        options=BRIEF_TONE_TAG_CHOICES,
        key=_key(project.id, "tone_tags"),
    )

    st.subheader("Globale Negativregeln")
    st.caption(
        "Diese Regeln gelten für das GESAMTE Projekt und werden bei JEDEM LLM-Schritt "
        "mitgeschickt (Style Profile, Dramaturgie, Voice-over-Text je Ordner, Intro-Hook). "
        "Es gibt drei unabhängige Mechanismen, die sich ergänzen:\n\n"
        "1. **Standard-Regeln (Checkboxen unten)** — vordefinierte, einzeln an-/abschaltbare "
        "Regeln. Beim Draufhalten der Maus siehst du die genaue Formulierung, die ans LLM "
        "geschickt wird.\n"
        "2. **Freitext** — eigene, zusätzliche Regeln in normaler Sprache, die keiner "
        "Checkbox entsprechen.\n"
        "3. **Verbotene Wörter/Phrasen** — eine feste Liste konkreter Wörter/Ausdrücke, die "
        "nie vorkommen dürfen."
    )
    negative_rule_flags: dict[str, bool] = {}
    for flag, label in BRIEF_NEGATIVE_RULE_LABELS.items():
        negative_rule_flags[flag] = st.checkbox(
            label,
            key=_flag_key(project.id, flag),
            help=BRIEF_NEGATIVE_RULE_INSTRUCTIONS.get(flag, ""),
        )

    forbidden_phrases_text = st.text_area(
        "Verbotene Wörter / Phrasen (eine pro Zeile)",
        key=_key(project.id, "forbidden_phrases"),
        height=120,
        help="Konkrete Wörter oder Ausdrücke, die im generierten Text niemals vorkommen "
        "dürfen — unabhängig von den Standard-Regeln oben. Ein Wort/Ausdruck pro Zeile.",
    )
    negative_rules_freetext = st.text_area(
        "Globale Negativregeln — Freitext",
        key=_key(project.id, "negative_rules_freetext"),
        height=100,
        help="Zusätzliche, eigene Regeln in normaler Sprache — für alles, was durch die "
        "Standard-Regeln oben noch nicht abgedeckt ist (z. B. projektspezifische "
        "inhaltliche Einschränkungen).",
    )
    global_extra_prompt = st.text_area(
        "Globaler Zusatzprompt",
        key=_key(project.id, "global_extra_prompt"),
        height=100,
        help="Freie Zusatzanweisung an das LLM, die KEINE Verbotsregel ist (z. B. "
        "Erzählperspektive, Schwerpunktsetzung, redaktioneller Stil).",
    )

    col_save, col_reload, col_reset = st.columns(3)
    with col_save:
        save_clicked = st.button(
            "Speichern", type="primary", key=f"vo_brief_save_{project.id}"
        )
    with col_reload:
        reload_clicked = st.button("Neu laden", key=f"vo_brief_reload_{project.id}")
    with col_reset:
        reset_clicked = st.button(
            "Auf Standard zurücksetzen", key=f"vo_brief_reset_{project.id}"
        )

    if reload_clicked:
        _apply_brief_to_session(project.id, load_project_brief(project))
        st.rerun()

    if reset_clicked:
        _apply_brief_to_session(project.id, default_project_brief(project))
        st.rerun()

    if save_clicked:
        forbidden_phrases = parse_forbidden_phrases_text(forbidden_phrases_text)
        brief = ProjectBrief(
            project_id=project.id,
            video_title=video_title.strip(),
            language=language,
            tone_tags=list(tone_tags),
            negative_rule_flags=negative_rule_flags,
            negative_rules_freetext=negative_rules_freetext,
            forbidden_phrases=forbidden_phrases,
            global_extra_prompt=global_extra_prompt,
        )
        saved = save_project_brief(project, brief)
        st.success("Project Brief gespeichert.")
        st.caption(f"Pfad: `{get_project_brief_path(project.language_work_dir_path)}`")
        with st.expander("JSON-Vorschau"):
            st.json(saved.model_dump(mode="json"))
