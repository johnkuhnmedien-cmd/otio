"""Zentrale Seite: alle Sprach-Standards unter ``data/`` bearbeiten.

Kein Projekt nötig. Speichert dieselben Dateien wie die Buttons
„Als Standard für {Sprache} speichern“ auf den Einzel-Tabs.
"""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import (
    BRIEF_LANGUAGE_CHOICES,
    BRIEF_NEGATIVE_RULE_LABELS,
    BRIEF_TONE_TAG_CHOICES,
    DRAMATURGY_PLANNING_MODE_CHOICES,
    DRAMATURGY_PLANNING_MODE_LABELS,
    DRAMATURGY_TARGET_WORDS_INPUT_MAX,
    ELEVENLABS_DEFAULT_MODEL_ID,
    ELEVENLABS_DEFAULT_OUTPUT_FORMAT,
    ELEVENLABS_MODEL_PRESETS,
    ELEVENLABS_OUTPUT_FORMAT_PRESETS,
    INTRO_HOOK_DEFAULT_TARGET_WORDS,
    PROJECT_BRIEF_TITLE_REFERENCE_SLOTS,
    STYLE_REFERENCE_DEFAULT_SLOTS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
    VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
    VOICEOVER_GEN_MIN_FOLDER_WORDS,
    intro_word_window,
    normalize_elevenlabs_output_format,
)
from otio_app.services.voiceover_generation.dramaturgy_defaults_service import (
    load_dramaturgy_defaults,
    load_language_dramaturgy_word_defaults,
    save_dramaturgy_defaults,
    save_language_dramaturgy_word_defaults,
)
from otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service import (
    load_language_voice_defaults,
    save_language_voice_defaults,
)
from otio_app.services.voiceover_generation.intro_hook_defaults_service import (
    load_language_intro_defaults,
    save_language_intro_defaults,
)
from otio_app.services.voiceover_generation.language_defaults_catalog import (
    language_standards_dir,
    list_language_standard_files,
    list_shared_library_files,
)
from otio_app.services.voiceover_generation.language_defaults_hub_service import (
    copy_language_defaults,
    delete_language_standard,
    language_defaults_overview,
    language_has_standard,
)
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_CHOICES,
    STYLE_MODE_LABELS,
    DramaturgyWordDefaults,
    ElevenLabsLanguageVoiceDefaults,
    IntroHookLanguageDefaults,
    ProjectBriefLanguageDefaults,
    StyleReferenceLanguageDefaults,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    load_language_brief_defaults,
    save_language_brief_defaults,
    title_references_for_ui,
)
from otio_app.services.voiceover_generation.project_brief_service import (
    parse_forbidden_phrases_text,
)
from otio_app.services.voiceover_generation.style_reference_defaults_service import (
    load_language_style_defaults,
    normalize_style_reference_texts,
    save_language_style_defaults,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    normalize_style_mode,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    default_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
    load_language_cut_plan_defaults,
    save_language_cut_plan_defaults,
)
from otio_app.ui.voiceover_generation.language_standards_ui import (
    render_language_standard_path_caption,
)
from otio_app.ui.without_voiceover_enhanced.cut_plan_defaults_form import (
    render_cut_plan_defaults_form,
)

_LANG_KEY = "lang_hub_language"
_HUB_PREFIX = "lang_hub_"


def _k(lang: str, section: str, field: str) -> str:
    return f"{_HUB_PREFIX}{section}_{field}_{lang}"


def _hydrate(key: str, value: object) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _clear_hub_widgets(lang: str) -> None:
    suffix = f"_{lang}"
    stale = [
        key
        for key in list(st.session_state.keys())
        if isinstance(key, str) and key.startswith(_HUB_PREFIX) and key.endswith(suffix)
    ]
    for key in stale:
        st.session_state.pop(key, None)


def _split_csv(text: str) -> list[str]:
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def _padded(values: list[str], size: int) -> list[str]:
    padded = [str(item) for item in values[:size]]
    while len(padded) < size:
        padded.append("")
    return padded


def render_language_defaults_hub_page() -> None:
    st.header("Sprachstandards")
    st.caption(
        "Hier liegen **alle per-Sprache-Defaults** — ohne offenes Projekt. "
        f"Ordner: `{language_standards_dir()}`. "
        "Neue Projekte und Auto-Lauf lesen diese Dateien. "
        "Videotitel, Uploads, erzeugte Cuts und LLM-Rollen in "
        "`model_settings.json` bleiben projektspezifisch."
    )

    language = st.selectbox(
        "Sprache",
        options=list(BRIEF_LANGUAGE_CHOICES),
        key=_LANG_KEY,
        help="Wechsel lädt die gespeicherten Werte dieser Sprache (oder den Werkstandard).",
    )
    lang = str(language)

    _render_overview_table(lang)
    _render_copy_from(lang)

    brief_draft = _render_brief_section(lang)
    style_draft = _render_style_section(lang)
    dram_words, dram_mode = _render_dramaturgy_section(lang)
    intro_draft = _render_intro_section(lang)
    voice_draft = _render_voice_section(lang)
    cut_draft = _render_cut_plan_section(lang)

    st.divider()
    if st.button(
        f"Alle Abschnitte für {lang} speichern",
        type="primary",
        key="lang_hub_save_all",
    ):
        save_language_brief_defaults(lang, brief_draft)
        save_language_style_defaults(lang, style_draft)
        save_language_dramaturgy_word_defaults(lang, dram_words)
        save_dramaturgy_defaults(dram_mode)
        save_language_intro_defaults(lang, intro_draft)
        save_language_voice_defaults(lang, voice_draft)
        save_language_cut_plan_defaults(lang, cut_draft)
        st.success(f"Alle sechs Sprach-Standards für **{lang}** gespeichert.")
        st.rerun()

    with st.expander("Was liegt nicht pro Sprache?", expanded=False):
        st.markdown(
            "- **LLM-Rollen** (Brief/Style/Dramaturgie/Intro/Funnel/YouTube) "
            "stehen in der projektspezifischen `model_settings.json`.\n"
            "- **Karten** (Auflösung, Parallelität) sind Projekt-Settings.\n"
            "- **Style-Bibliotheken** (Raw + Profile) sind global, nicht nach Sprache keyed.\n"
            "- Erzeugte Texte, Cuts, Timing, Funnel und OTIO bleiben im Projekt."
        )
        for item in list_shared_library_files():
            st.caption(f"{item.tab}: `{item.path}`")


def _render_overview_table(active_lang: str) -> None:
    items = list_language_standard_files()
    overview = language_defaults_overview()
    header = "| Bereich | " + " | ".join(BRIEF_LANGUAGE_CHOICES) + " |"
    sep = "| --- | " + " | ".join(["---"] * len(BRIEF_LANGUAGE_CHOICES)) + " |"
    rows = [header, sep]
    for item in items:
        cells = []
        for lang in BRIEF_LANGUAGE_CHOICES:
            mark = "✓" if overview[lang][item.key] else "—"
            if lang == active_lang:
                mark = f"**{mark}**"
            cells.append(mark)
        rows.append(f"| {item.tab} | " + " | ".join(cells) + " |")
    st.markdown("\n".join(rows))
    st.caption("✓ = in `data/` für diese Sprache gespeichert. — = noch kein Eintrag (Werkstandard).")


def _render_copy_from(lang: str) -> None:
    with st.expander("Von anderer Sprache kopieren", expanded=False):
        sources = [item for item in BRIEF_LANGUAGE_CHOICES if item != lang]
        source = st.selectbox(
            "Quelle",
            options=sources,
            key="lang_hub_copy_source",
        )
        st.caption(
            f"Überschreibt gesetzte Einträge in **{lang}** mit den Werten von **{source}**. "
            "Der Dramaturgie-Planungsmodus ist global und bleibt unangetastet."
        )
        if st.button(
            f"{source} → {lang} kopieren",
            key="lang_hub_copy_btn",
        ):
            copied = copy_language_defaults(source, lang)
            _clear_hub_widgets(lang)
            if copied:
                labels = ", ".join(copied)
                st.success(f"Kopiert nach **{lang}**: {labels}")
            else:
                st.info(f"**{source}** hat noch keine gespeicherten Standards.")
            st.rerun()


def _section_header(key: str, lang: str) -> None:
    item = next(i for i in list_language_standard_files() if i.key == key)
    is_set = language_has_standard(key, lang)
    badge = "gesetzt" if is_set else "noch kein Eintrag"
    st.markdown(f"#### {item.tab}")
    st.caption(f"{item.stores}. Nicht: {item.not_stored}. Status **{lang}**: {badge}.")
    render_language_standard_path_caption(key)


def _save_row(key: str, lang: str) -> tuple[bool, bool]:
    is_set = language_has_standard(key, lang)
    col_save, col_del = st.columns(2)
    with col_save:
        save_clicked = st.button(
            f"{lang}-Standard speichern",
            key=f"lang_hub_save_{key}_{lang}",
            type="primary",
        )
    with col_del:
        delete_clicked = st.button(
            f"{lang}-Eintrag löschen",
            key=f"lang_hub_del_{key}_{lang}",
            disabled=not is_set,
        )
    return save_clicked, delete_clicked


def _render_brief_section(lang: str) -> ProjectBriefLanguageDefaults:
    with st.expander("① Project Brief", expanded=True):
        _section_header("project_brief", lang)
        current = load_language_brief_defaults(lang) or ProjectBriefLanguageDefaults()
        tone_key = _k(lang, "brief", "tone")
        tone_tags_initial = [
            tag for tag in current.tone_tags if tag in BRIEF_TONE_TAG_CHOICES
        ]
        _hydrate(tone_key, tone_tags_initial)
        tone_tags = st.multiselect(
            "Ton-Tags",
            options=list(BRIEF_TONE_TAG_CHOICES),
            key=tone_key,
        )
        negative_rule_flags: dict[str, bool] = {}
        for flag, label in BRIEF_NEGATIVE_RULE_LABELS.items():
            flag_key = _k(lang, "brief", f"flag_{flag}")
            _hydrate(flag_key, bool(current.negative_rule_flags.get(flag, False)))
            negative_rule_flags[flag] = st.checkbox(label, key=flag_key)
        phrases_key = _k(lang, "brief", "forbidden")
        _hydrate(phrases_key, "\n".join(current.forbidden_phrases))
        forbidden_text = st.text_area(
            "Verbotene Wörter / Phrasen (eine pro Zeile)",
            key=phrases_key,
            height=100,
        )
        free_key = _k(lang, "brief", "freetext")
        _hydrate(free_key, current.negative_rules_freetext)
        negative_rules_freetext = st.text_area(
            "Globale Negativregeln — Freitext",
            key=free_key,
            height=80,
        )
        extra_key = _k(lang, "brief", "extra")
        _hydrate(extra_key, current.global_extra_prompt)
        global_extra_prompt = st.text_area(
            "Globaler Zusatzprompt",
            key=extra_key,
            height=80,
        )
        title_refs: list[str] = []
        for index, text in enumerate(title_references_for_ui(current.title_references)):
            ref_key = _k(lang, "brief", f"ref_{index}")
            _hydrate(ref_key, text)
            title_refs.append(
                st.text_input(
                    f"Titel-Referenz {index + 1}",
                    key=ref_key,
                )
            )
        draft = ProjectBriefLanguageDefaults(
            tone_tags=list(tone_tags),
            negative_rule_flags=negative_rule_flags,
            negative_rules_freetext=str(negative_rules_freetext or ""),
            forbidden_phrases=parse_forbidden_phrases_text(str(forbidden_text or "")),
            global_extra_prompt=str(global_extra_prompt or ""),
            title_references=[item for item in title_refs if str(item).strip()][
                :PROJECT_BRIEF_TITLE_REFERENCE_SLOTS
            ],
        )
        save_clicked, delete_clicked = _save_row("project_brief", lang)
        if save_clicked:
            save_language_brief_defaults(lang, draft)
            st.success(f"Project Brief-Standard für **{lang}** gespeichert.")
            st.rerun()
        if delete_clicked:
            delete_language_standard("project_brief", lang)
            _clear_hub_widgets(lang)
            st.success(f"Project Brief-Standard für **{lang}** gelöscht.")
            st.rerun()
        return draft


def _render_style_section(lang: str) -> StyleReferenceLanguageDefaults:
    with st.expander("② Style References", expanded=False):
        _section_header("style_references", lang)
        current = load_language_style_defaults(lang) or StyleReferenceLanguageDefaults()
        mode_key = _k(lang, "style", "mode")
        _hydrate(mode_key, normalize_style_mode(current.style_mode))
        style_mode = st.radio(
            "Modus",
            options=list(STYLE_MODE_CHOICES),
            format_func=lambda m: STYLE_MODE_LABELS.get(m, m),
            key=mode_key,
            horizontal=True,
        )
        raw_key = _k(lang, "style", "raw")
        _hydrate(raw_key, current.raw_reference_text or "")
        raw_reference_text = st.text_area(
            "Raw-Text (Kapitel)",
            key=raw_key,
            height=160,
        )
        raw_intro_key = _k(lang, "style", "raw_intro")
        _hydrate(raw_intro_key, current.raw_intro_reference_text or "")
        raw_intro_reference_text = st.text_area(
            "Raw-Text Intro (optional)",
            key=raw_intro_key,
            height=100,
        )
        intro_texts: list[str] = []
        for index, text in enumerate(
            _padded(list(current.intro_reference_texts), STYLE_REFERENCE_DEFAULT_SLOTS)
        ):
            ref_key = _k(lang, "style", f"intro_{index}")
            _hydrate(ref_key, text)
            intro_texts.append(
                st.text_area(f"Intro-Referenz {index + 1}", key=ref_key, height=80)
            )
        segment_texts: list[str] = []
        for index, text in enumerate(
            _padded(list(current.segment_reference_texts), STYLE_REFERENCE_DEFAULT_SLOTS)
        ):
            ref_key = _k(lang, "style", f"seg_{index}")
            _hydrate(ref_key, text)
            segment_texts.append(
                st.text_area(f"Segment-Referenz {index + 1}", key=ref_key, height=80)
            )
        if current.style_profile is not None:
            st.caption("Es liegt ein Style-Profile-Snapshot in diesem Sprachstandard.")
        draft = StyleReferenceLanguageDefaults(
            style_mode=normalize_style_mode(str(style_mode)),
            raw_reference_text=str(raw_reference_text or ""),
            raw_intro_reference_text=str(raw_intro_reference_text or ""),
            raw_library_name=current.raw_library_name,
            intro_reference_texts=normalize_style_reference_texts(intro_texts),
            segment_reference_texts=normalize_style_reference_texts(segment_texts),
            style_profile=current.style_profile,
        )
        save_clicked, delete_clicked = _save_row("style_references", lang)
        if save_clicked:
            save_language_style_defaults(lang, draft)
            st.success(f"Style-Standard für **{lang}** gespeichert.")
            st.rerun()
        if delete_clicked:
            delete_language_standard("style_references", lang)
            _clear_hub_widgets(lang)
            st.success(f"Style-Standard für **{lang}** gelöscht.")
            st.rerun()
        return draft


def _render_dramaturgy_section(lang: str) -> tuple[DramaturgyWordDefaults, str]:
    with st.expander("③ Dramaturgie", expanded=False):
        _section_header("dramaturgy", lang)
        document = load_dramaturgy_defaults()
        current = load_language_dramaturgy_word_defaults(lang) or DramaturgyWordDefaults()
        mode_options = list(DRAMATURGY_PLANNING_MODE_CHOICES)
        mode_key = _k(lang, "dram", "planning_mode")
        _hydrate(mode_key, document.planning_mode)
        planning_mode = st.radio(
            "Planungsmodus (global, alle Sprachen)",
            options=mode_options,
            format_func=lambda m: DRAMATURGY_PLANNING_MODE_LABELS.get(m, m),
            key=mode_key,
            help="Dieser Wert gilt für den Auto-Lauf unabhängig von der Sprache oben.",
        )
        target_key = _k(lang, "dram", "target")
        tol_key = _k(lang, "dram", "tol")
        _hydrate(target_key, int(current.target_words))
        _hydrate(tol_key, int(current.word_tolerance_percent))
        col_t, col_p = st.columns(2)
        with col_t:
            target_words = st.number_input(
                "Ziel-Wortanzahl",
                min_value=VOICEOVER_GEN_MIN_FOLDER_WORDS,
                max_value=DRAMATURGY_TARGET_WORDS_INPUT_MAX,
                step=5,
                key=target_key,
            )
        with col_p:
            tolerance = st.number_input(
                "Toleranz (%)",
                min_value=0,
                max_value=100,
                step=5,
                key=tol_key,
            )
        window_min, window_max = intro_word_window(int(target_words), int(tolerance))
        st.caption(f"Wortfenster: **{window_min}–{window_max}**.")
        words = DramaturgyWordDefaults(
            target_words=int(target_words or VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS),
            word_tolerance_percent=int(
                tolerance if tolerance is not None else VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT
            ),
        )
        save_clicked, delete_clicked = _save_row("dramaturgy", lang)
        if save_clicked:
            save_language_dramaturgy_word_defaults(lang, words)
            save_dramaturgy_defaults(str(planning_mode))
            st.success(f"Dramaturgie-Standard für **{lang}** gespeichert.")
            st.rerun()
        if delete_clicked:
            delete_language_standard("dramaturgy", lang)
            _clear_hub_widgets(lang)
            st.success(f"Wortziel für **{lang}** gelöscht (Planungsmodus bleibt global).")
            st.rerun()
        return words, str(planning_mode)


def _render_intro_section(lang: str) -> IntroHookLanguageDefaults:
    with st.expander("⑤ Intro", expanded=False):
        _section_header("intro", lang)
        current = load_language_intro_defaults(lang) or IntroHookLanguageDefaults()
        target_key = _k(lang, "intro", "target")
        tol_key = _k(lang, "intro", "tol")
        tone_key = _k(lang, "intro", "tone")
        _hydrate(target_key, int(current.target_words or INTRO_HOOK_DEFAULT_TARGET_WORDS))
        _hydrate(tol_key, int(current.word_tolerance_percent))
        _hydrate(tone_key, current.tone or "cinematic")
        col1, col2, col3 = st.columns(3)
        with col1:
            target_words = st.number_input(
                "Ziel-Wortanzahl",
                min_value=1,
                max_value=500,
                step=5,
                key=target_key,
            )
            tolerance = st.number_input(
                "Toleranz (%)",
                min_value=0,
                max_value=100,
                step=5,
                key=tol_key,
            )
            window_min, window_max = intro_word_window(int(target_words), int(tolerance))
            st.caption(f"Wortfenster: **{window_min}–{window_max}**.")
        with col2:
            tone = st.text_input("Tonalität", key=tone_key)
            q_key = _k(lang, "intro", "questions")
            claim_key = _k(lang, "intro", "claim")
            _hydrate(q_key, bool(current.allow_questions))
            _hydrate(claim_key, bool(current.allow_strong_claim))
            allow_questions = st.checkbox("Fragen erlaubt", key=q_key)
            allow_strong_claim = st.checkbox("Starke These erlaubt", key=claim_key)
        with col3:
            place_key = _k(lang, "intro", "place")
            tease_key = _k(lang, "intro", "tease")
            _hydrate(place_key, bool(current.allow_direct_place_name))
            _hydrate(tease_key, bool(current.allow_tease_multiple_places))
            allow_direct_place_name = st.checkbox(
                "Ortsname direkt nennen erlaubt", key=place_key
            )
            allow_tease_multiple_places = st.checkbox(
                "Mehrere Orte anteasern erlaubt", key=tease_key
            )
        free_key = _k(lang, "intro", "freeform")
        _hydrate(free_key, current.freeform_rule_for_llm)
        freeform_rule = st.text_area("Freitext-Regel für das LLM", key=free_key)
        forb_key = _k(lang, "intro", "forbidden")
        _hydrate(forb_key, "\n".join(current.forbidden_phrases))
        forbidden_text = st.text_area(
            "Verbotene Begriffe (eine pro Zeile)", key=forb_key
        )
        inc_key = _k(lang, "intro", "include")
        av_key = _k(lang, "intro", "avoid")
        _hydrate(inc_key, ", ".join(current.must_include))
        _hydrate(av_key, ", ".join(current.must_avoid))
        must_include_text = st.text_input("Muss enthalten (Komma-getrennt)", key=inc_key)
        must_avoid_text = st.text_input("Muss vermeiden (Komma-getrennt)", key=av_key)
        draft = IntroHookLanguageDefaults(
            target_words=int(target_words),
            word_tolerance_percent=int(tolerance),
            tone=str(tone or "").strip() or "cinematic",
            freeform_rule_for_llm=str(freeform_rule or ""),
            forbidden_phrases=parse_forbidden_phrases_text(str(forbidden_text or "")),
            allow_questions=bool(allow_questions),
            allow_strong_claim=bool(allow_strong_claim),
            allow_direct_place_name=bool(allow_direct_place_name),
            allow_tease_multiple_places=bool(allow_tease_multiple_places),
            must_include=_split_csv(str(must_include_text or "")),
            must_avoid=_split_csv(str(must_avoid_text or "")),
        )
        save_clicked, delete_clicked = _save_row("intro", lang)
        if save_clicked:
            save_language_intro_defaults(lang, draft)
            st.success(f"Intro-Standard für **{lang}** gespeichert.")
            st.rerun()
        if delete_clicked:
            delete_language_standard("intro", lang)
            _clear_hub_widgets(lang)
            st.success(f"Intro-Standard für **{lang}** gelöscht.")
            st.rerun()
        return draft


def _render_voice_section(lang: str) -> ElevenLabsLanguageVoiceDefaults:
    with st.expander("⑥ Audio / ElevenLabs", expanded=False):
        _section_header("elevenlabs_voice", lang)
        current = load_language_voice_defaults(lang) or ElevenLabsLanguageVoiceDefaults()
        voice_key = _k(lang, "voice", "id")
        _hydrate(voice_key, current.voice_id)
        voice_id = st.text_input("Voice-ID", key=voice_key)
        model_options = list(ELEVENLABS_MODEL_PRESETS)
        model_id = str(current.model_id or ELEVENLABS_DEFAULT_MODEL_ID)
        if model_id not in model_options:
            model_options = [model_id, *model_options]
        model_key = _k(lang, "voice", "model")
        _hydrate(model_key, model_id)
        model_id = st.selectbox("Modell", options=model_options, key=model_key)
        format_options = list(ELEVENLABS_OUTPUT_FORMAT_PRESETS)
        output_format = normalize_elevenlabs_output_format(
            current.output_format or ELEVENLABS_DEFAULT_OUTPUT_FORMAT
        )
        if output_format not in format_options:
            format_options = [output_format, *format_options]
        format_key = _k(lang, "voice", "format")
        _hydrate(format_key, output_format)
        output_format = st.selectbox("Output-Format", options=format_options, key=format_key)
        col1, col2 = st.columns(2)
        with col1:
            stab_key = _k(lang, "voice", "stability")
            sim_key = _k(lang, "voice", "similarity")
            _hydrate(stab_key, float(current.stability))
            _hydrate(sim_key, float(current.similarity_boost))
            stability = st.slider("Stability", 0.0, 1.0, key=stab_key)
            similarity_boost = st.slider("Similarity Boost", 0.0, 1.0, key=sim_key)
        with col2:
            style_key = _k(lang, "voice", "style")
            boost_key = _k(lang, "voice", "boost")
            speed_key = _k(lang, "voice", "speed")
            _hydrate(style_key, float(current.style))
            _hydrate(boost_key, bool(current.use_speaker_boost))
            _hydrate(speed_key, float(current.speed))
            style = st.slider("Style", 0.0, 1.0, key=style_key)
            use_speaker_boost = st.checkbox("Speaker Boost", key=boost_key)
            speed = st.slider("Speed", 0.25, 4.0, key=speed_key)
        code_key = _k(lang, "voice", "lang_code")
        _hydrate(code_key, current.language_code)
        language_code = st.text_input(
            "Language Code (optional — nur senden, wenn ausgefüllt)",
            key=code_key,
        )
        draft = ElevenLabsLanguageVoiceDefaults(
            voice_id=str(voice_id or "").strip(),
            model_id=str(model_id or ELEVENLABS_DEFAULT_MODEL_ID),
            output_format=normalize_elevenlabs_output_format(str(output_format)),
            stability=float(stability),
            similarity_boost=float(similarity_boost),
            style=float(style),
            use_speaker_boost=bool(use_speaker_boost),
            speed=float(speed),
            language_code=str(language_code or "").strip(),
        )
        save_clicked, delete_clicked = _save_row("elevenlabs_voice", lang)
        if save_clicked:
            save_language_voice_defaults(lang, draft)
            st.success(f"ElevenLabs-Standard für **{lang}** gespeichert.")
            st.rerun()
        if delete_clicked:
            delete_language_standard("elevenlabs_voice", lang)
            _clear_hub_widgets(lang)
            st.success(f"ElevenLabs-Standard für **{lang}** gelöscht.")
            st.rerun()
        return draft


def _render_cut_plan_section(lang: str):
    with st.expander("⑦ Cut Plan", expanded=False):
        _section_header("cut_plan_options", lang)
        current = load_language_cut_plan_defaults(lang) or default_cut_plan_options()
        draft = render_cut_plan_defaults_form(current, key_suffix=lang)
        save_clicked, delete_clicked = _save_row("cut_plan_options", lang)
        if save_clicked:
            save_language_cut_plan_defaults(lang, draft)
            st.success(f"Cut-Plan-Standard für **{lang}** gespeichert.")
            st.rerun()
        if delete_clicked:
            delete_language_standard("cut_plan_options", lang)
            _clear_hub_widgets(lang)
            st.success(f"Cut-Plan-Standard für **{lang}** gelöscht.")
            st.rerun()
        return draft
