"""Project Brief — Video-Titel, Sprache, Ton, globale Negativregeln (Phase 2)."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import PROJECT_BRIEF_TITLE_REFERENCE_SLOTS
from otio_app.models import Project
from otio_app.project_layout import get_project_brief_path
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import (
    BRIEF_LANGUAGE_CHOICES,
    BRIEF_NEGATIVE_RULE_INSTRUCTIONS,
    BRIEF_NEGATIVE_RULE_LABELS,
    BRIEF_TONE_TAG_CHOICES,
    ProjectBrief,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    apply_language_defaults_to_brief,
    load_language_brief_defaults,
    normalize_brief_language,
    normalize_title_references,
    save_language_brief_defaults,
    title_references_for_ui,
)
from otio_app.services.voiceover_generation.project_brief_service import (
    default_project_brief,
    load_project_brief,
    parse_forbidden_phrases_text,
    save_project_brief,
)
from otio_app.services.voiceover_generation.video_title_service import generate_video_title
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_input_info,
    render_llm_model_selectbox,
    require_without_voiceover_mode,
)
from otio_app.ui.voiceover_generation.language_standards_ui import (
    render_language_standard_path_caption,
    render_language_standards_expander,
)


def _key(project_id: str, field_name: str) -> str:
    return f"vo_brief_{field_name}_{project_id}"


def _flag_key(project_id: str, flag: str) -> str:
    return _key(project_id, f"flag_{flag}")


def _ref_key(project_id: str, index: int) -> str:
    return _key(project_id, f"title_ref_{index}")


def _pending_title_key(project_id: str) -> str:
    return _key(project_id, "pending_title")


def _pending_brief_key(project_id: str) -> str:
    return _key(project_id, "pending_brief")


def _flash_key(project_id: str) -> str:
    return _key(project_id, "flash")


def _queue_pending_title(project_id: str, title: str, *, flash: str | None = None) -> None:
    """Queue a title write for the next run — never after the text_input exists."""
    st.session_state[_pending_title_key(project_id)] = title
    if flash:
        st.session_state[_flash_key(project_id)] = ("success", flash)


def _queue_pending_brief(project_id: str, brief: ProjectBrief) -> None:
    st.session_state[_pending_brief_key(project_id)] = brief


def _hydrate_brief_session(project: Project) -> None:
    """Apply queued widget updates before any brief widgets are instantiated.

    Streamlit forbids writing a widget-bound session_state key after that
    widget exists in the same run (``Videotitel erzeugen``, Neu laden, Reset).
    """
    pending_brief = st.session_state.pop(_pending_brief_key(project.id), None)
    if isinstance(pending_brief, ProjectBrief):
        _apply_brief_to_session(project.id, pending_brief)
    elif _key(project.id, "video_title") not in st.session_state:
        _apply_brief_to_session(project.id, load_project_brief(project))

    pending_title = st.session_state.pop(_pending_title_key(project.id), None)
    if pending_title is not None:
        st.session_state[_key(project.id, "video_title")] = str(pending_title)

    flash = st.session_state.pop(_flash_key(project.id), None)
    if flash:
        level, text = flash if isinstance(flash, tuple) else ("success", flash)
        if level == "error":
            st.error(text)
        else:
            st.success(text)


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
    st.session_state[_key(project_id, "series_bridge_enabled")] = bool(
        brief.series_bridge_enabled
    )
    st.session_state[_key(project_id, "series_bridge_destination")] = (
        brief.series_bridge_destination
    )
    st.session_state[_key(project_id, "series_bridge_angle")] = brief.series_bridge_angle
    st.session_state[_key(project_id, "series_bridge_hook_facts")] = (
        brief.series_bridge_hook_facts
    )
    for index, text in enumerate(title_references_for_ui(brief.title_references)):
        st.session_state[_ref_key(project_id, index)] = text


def _brief_from_widgets(
    project: Project,
    *,
    video_title: str,
    language: str,
    tone_tags: list[str],
    negative_rule_flags: dict[str, bool],
    forbidden_phrases_text: str,
    negative_rules_freetext: str,
    global_extra_prompt: str,
    title_references: list[str],
    series_bridge_enabled: bool = False,
    series_bridge_destination: str = "",
    series_bridge_hook_facts: str = "",
    series_bridge_angle: str = "",
) -> ProjectBrief:
    return ProjectBrief(
        project_id=project.id,
        video_title=video_title.strip(),
        language=language,
        tone_tags=list(tone_tags),
        negative_rule_flags=negative_rule_flags,
        negative_rules_freetext=negative_rules_freetext,
        forbidden_phrases=parse_forbidden_phrases_text(forbidden_phrases_text),
        global_extra_prompt=global_extra_prompt,
        title_references=normalize_title_references(title_references),
        series_bridge_enabled=bool(series_bridge_enabled),
        series_bridge_destination=series_bridge_destination.strip(),
        series_bridge_hook_facts=series_bridge_hook_facts.strip(),
        series_bridge_angle=series_bridge_angle.strip(),
    )


def _render_model_picker(project: Project) -> tuple[str, str]:
    settings = load_model_settings(project)
    with st.expander("⚙️ Modell für Videotitel", expanded=False):
        role_settings = render_llm_model_selectbox(
            label="Modell (Titel erzeugen)",
            role_settings=settings.project_brief,
            key=f"vo_brief_model_{project.id}",
            input_info=LLM_INPUT_INFO["project_brief"],
        )
        if st.button("Modell speichern", key=f"vo_brief_model_save_{project.id}"):
            save_model_settings(
                project, settings.model_copy(update={"project_brief": role_settings})
            )
            st.success("Modell für Videotitel gespeichert.")
    return role_settings.provider, role_settings.model


def render_project_brief_page() -> None:
    st.header("① Project Brief")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    render_language_standards_expander()

    title_key = _key(project.id, "video_title")
    _hydrate_brief_session(project)

    video_place = (project.video_place or "").strip()
    if video_place:
        st.info(f"Land / Region des Projekts: **{video_place}**")
    else:
        st.warning(
            "Kein Land/Region am Projekt. Unter **Gespeicherte Projekte** nachtragen — "
            "ohne das kann der Titel-LLM nicht arbeiten."
        )

    provider, model = _render_model_picker(project)

    video_title = st.text_input("Video-Titel", key=title_key)
    language = st.selectbox(
        "Sprache",
        options=BRIEF_LANGUAGE_CHOICES,
        key=_key(project.id, "language"),
    )
    lang_key = normalize_brief_language(language)
    has_language_default = load_language_brief_defaults(lang_key) is not None

    st.subheader("Titel-Referenzen (Inspiration)")
    st.caption(
        "Drei Beispieltitel aus der Serie. Das LLM nutzt sie als **Inspiration**, "
        "nicht als starre Vorlage — es soll einen neuen Titel für "
        f"**{video_place or 'dieses Land/diese Region'}** in **{lang_key}** erfinden."
    )
    render_llm_input_info(LLM_INPUT_INFO["project_brief"])
    title_references: list[str] = []
    for index in range(PROJECT_BRIEF_TITLE_REFERENCE_SLOTS):
        title_references.append(
            st.text_input(
                f"Referenz-Titel {index + 1}",
                key=_ref_key(project.id, index),
            )
        )

    tone_tags = st.multiselect(
        "Globale Charakteristik / Ton",
        options=BRIEF_TONE_TAG_CHOICES,
        key=_key(project.id, "tone_tags"),
    )

    generate_disabled = not video_place or not normalize_title_references(title_references)
    if st.button(
        "Videotitel erzeugen",
        type="primary",
        key=f"vo_brief_generate_title_{project.id}",
        disabled=generate_disabled,
        help="Braucht Land/Region am Projekt und mindestens eine Referenz.",
    ):
        with st.spinner("Videotitel wird erzeugt…"):
            result = generate_video_title(
                project,
                language=lang_key,
                video_place=video_place,
                title_references=title_references,
                tone_tags=list(tone_tags),
                provider=provider,
                model=model,
            )
        if result.status == STATUS_PASS and result.title:
            _queue_pending_title(
                project.id,
                result.title,
                flash=f"Titel: {result.title}",
            )
            st.rerun()
        else:
            st.error(result.error or "Titel-Erzeugung fehlgeschlagen.")

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

    st.subheader("Abschluss-Überleitung zu einem anderen Video")
    st.caption(
        "Nur das **letzte Kapitel** bekommt 1–3 Sätze am Ende, die vom hiesigen Ort "
        "aus neugierig auf einen **anderen Film der Serie** machen — dokumentarisch, "
        "kein „schau dir jetzt mein Video an“."
    )
    series_bridge_enabled = st.checkbox(
        "Letztes Skript mit Serie-Brücke schließen",
        key=_key(project.id, "series_bridge_enabled"),
        help="Wirkt erst, wenn das letzte Kapitel neu erzeugt wird (Auto-Lauf Skript "
        "oder einzelnes Kapitel neu generieren).",
    )
    series_bridge_destination = st.text_input(
        "Land / Region des anderen Videos",
        key=_key(project.id, "series_bridge_destination"),
        placeholder="z. B. Griechenland",
        disabled=not series_bridge_enabled,
    )
    series_bridge_angle = st.text_input(
        "Redaktioneller Winkel (optional, wird nicht vorgelesen)",
        key=_key(project.id, "series_bridge_angle"),
        placeholder="z. B. Adria als Schwelle — gleiches Meer, anderes Licht",
        disabled=not series_bridge_enabled,
    )
    series_bridge_hook_facts = st.text_area(
        "Belegte Fakten / Bilder für die Neugier (nur diese darf das LLM über das andere Land sagen)",
        key=_key(project.id, "series_bridge_hook_facts"),
        height=120,
        disabled=not series_bridge_enabled,
        help="Ohne Fakten nennt das Modell nur den Ortsnamen und erfindet nichts. "
        "Beispiel: Die Adria verbindet beide Küsten. In Griechenland stehen antike "
        "Theater noch im Alltag. Olympia liegt eine Seereise südlich.",
    )
    if series_bridge_enabled and not (series_bridge_destination or "").strip():
        st.warning("Bitte das Land/die Region des anderen Videos eintragen — sonst bleibt die Brücke aus.")

    col_save, col_lang, col_reload, col_reset = st.columns(4)
    with col_save:
        save_clicked = st.button(
            "Speichern", type="primary", key=f"vo_brief_save_{project.id}"
        )
    with col_lang:
        lang_save_clicked = st.button(
            f"Als Standard für {lang_key} speichern",
            key=f"vo_brief_save_lang_{project.id}",
            help=(
                f"Ton, Regeln, Zusatzprompt und Titel-Referenzen global für {lang_key}. "
                "Der Videotitel bleibt projektspezifisch."
            ),
        )
    with col_reload:
        reload_clicked = st.button("Neu laden", key=f"vo_brief_reload_{project.id}")
    with col_reset:
        reset_label = (
            f"Auf {lang_key}-Standard zurück"
            if has_language_default
            else "Auf Standard zurücksetzen"
        )
        reset_clicked = st.button(reset_label, key=f"vo_brief_reset_{project.id}")

    render_language_standard_path_caption("project_brief")

    if reload_clicked:
        _queue_pending_brief(project.id, load_project_brief(project))
        st.rerun()

    if reset_clicked:
        current_title = str(st.session_state.get(title_key) or "")
        reset_brief = default_project_brief(project)
        reset_brief = reset_brief.model_copy(
            update={"language": lang_key, "video_title": current_title}
        )
        language_defaults = load_language_brief_defaults(lang_key)
        if language_defaults is not None:
            reset_brief = apply_language_defaults_to_brief(
                reset_brief, language_defaults, keep_title=True
            )
        _queue_pending_brief(project.id, reset_brief)
        st.rerun()

    draft = _brief_from_widgets(
        project,
        video_title=video_title,
        language=language,
        tone_tags=list(tone_tags),
        negative_rule_flags=negative_rule_flags,
        forbidden_phrases_text=forbidden_phrases_text,
        negative_rules_freetext=negative_rules_freetext,
        global_extra_prompt=global_extra_prompt,
        title_references=title_references,
        series_bridge_enabled=series_bridge_enabled,
        series_bridge_destination=series_bridge_destination,
        series_bridge_hook_facts=series_bridge_hook_facts,
        series_bridge_angle=series_bridge_angle,
    )

    if save_clicked:
        saved = save_project_brief(project, draft)
        st.success("Project Brief gespeichert.")
        st.caption(f"Pfad: `{get_project_brief_path(project.language_work_dir_path)}`")
        with st.expander("JSON-Vorschau"):
            st.json(saved.model_dump(mode="json"))

    if lang_save_clicked:
        save_language_brief_defaults(lang_key, draft)
        save_project_brief(project, draft)
        st.success(
            f"Als globaler Standard für **{lang_key}** gespeichert "
            "(Titel-Referenzen, Ton, Regeln — nicht der Videotitel)."
        )
        st.rerun()
