"""Gemeinsame UI-Bausteine für die Pipeline "Projekt ohne Voice-Over"."""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from otio_app.defaults import VOICEOVER_GEN_MODEL_CHOICES
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.model_settings_service import (
    combined_model_id,
    format_voiceover_gen_model_label,
    split_llm_model_id,
)
from otio_app.services.voiceover_generation.models import LlmRoleSettings, VoiceoverStyleProfile
from otio_app.ui.project_context import render_project_selector


def render_placeholder_page(
    *,
    title: str,
    phase_hint: str,
    target_path_label: str,
    target_path_fn,
) -> None:
    """Zeigt eine Platzhalterseite mit Projektauswahl und dem künftigen Zielpfad.

    target_path_fn erhält den Arbeitsordner (work_dir_path) und liefert den
    Pfad, unter dem diese Seite ihr Artefakt in einer späteren Phase speichern
    wird — so ist die Pfadstruktur schon jetzt sichtbar und testbar.
    """
    st.header(title)

    project = render_project_selector("Projekt")
    if project is None:
        return

    if project.project_mode not in (
        ProjectMode.WITHOUT_VOICEOVER,
        ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
    ):
        st.warning(
            "Dieses Projekt ist auf „Projekt mit Voice-Over“ eingestellt. "
            "Diese Seite gehört zur Pipeline „Projekt ohne Voice-Over“ und "
            "sollte für dieses Projekt nicht verwendet werden."
        )
        return

    st.info(
        f"🚧 Noch nicht implementiert — {phase_hint}. "
        "Diese Seite ist Teil des neuen Diagnose-/Generierungsworkflows "
        "„Projekt ohne Voice-Over“ und wird in einer späteren Phase befüllt."
    )
    target_path: Path = target_path_fn(project.work_dir_path)
    st.caption(f"Künftiger Zielpfad: `{target_path}`")


def get_active_voiceover_gen_project() -> Project | None:
    """Hilfsfunktion für spätere Phasen — identisch zur bestehenden Projekt-Auswahl."""
    return render_project_selector("Projekt")


def render_llm_input_info(
    text: str,
    *,
    title: str = "Was das LLM bekommt",
) -> None:
    """Kurzer Hinweis, welche Kontexte in diesen LLM-Call gehen."""
    st.caption(f"**{title}:** {text}")


# Kurze, UI-taugliche Beschreibungen der Prompt-Inputs je LLM-Rolle/Schritt.
LLM_INPUT_INFO = {
    "style_profile": (
        "Project Brief (Titel, Ton, Zusatzprompt) · Negative Rules · "
        "Forbidden Phrases · Intro-/Segment-/Upload-Referenzen "
        "(nur Stil ableiten, nicht wörtlich kopieren)."
    ),
    "dramaturgy": (
        "Project Brief · Negative Rules · Style Profile/Raw-Text · "
        "Kapitel-Summaries (Ordnername, Themen, Scores, Asset-Anzahl, Risiken) — "
        "keine einzelnen Asset-Beschreibungen · Planning-Mode-Hinweise."
    ),
    "voiceover_author": (
        "Brief (Ton, Negative Rules, Forbidden) · Style · Dramaturgie-Kapitel "
        "(Rolle, Reason, Nachbarn, Craft) · Folder-Settings (Wortziel, Energy, "
        "Factuality, Must-Include/Avoid, Zusatzprompt) · volles lokales "
        "Asset-Inventar (IDs, Dauer, Beschreibungen, Frames)."
    ),
    "voiceover_review": (
        "Style · Forbidden/Negative Rules · Factuality-Modus · "
        "kompletter VO-Text · Satz-Breakdown. "
        "Bei Blockern folgt Correction mit Originaltext + Fehlerliste "
        "(ohne volles Inventar)."
    ),
    "asset_allocation": (
        "Style · lokales Inventar · bestehender VO-Text/Sätze/Closing · "
        "Readiness-Issues — Text bleibt, nur Asset-Zuordnung wird repariert."
    ),
    "project_brief": (
        "Land/Region des Projekts · Sprache · drei Titel-Referenzen "
        "(nur Inspiration, keine starre Vorlage) · optional Ton-Tags."
    ),
    "intro": (
        "Intro-Settings · Brief/Negative Rules/Forbidden · Style "
        "(Raw Intro Text oder Style Profile als Struktur-Template) · "
        "Dramaturgie (Arc/Promise) · "
        "nur kurze Kapitel-Signale (Name, Rolle, Reason, Scores) — "
        "kein Fließtext/Skript, keine Folder-VO-Sätze, kein Inventory."
    ),
    "intro_revision": (
        "Nur dein Freitext + der fertige Intro-Text der Variante "
        "(inkl. [pause N seconds]-Marker). "
        "Kein Brief, keine Dramaturgie, kein Style, kein Inventory."
    ),
    "enhanced_script": (
        "Project Brief (ohne Titel-Referenzen, kompakt) · Film-Kontext · "
        "Kapitel-Dramaturgie (Rolle, Reason, Wortziel) · "
        "FILM CHAPTER MAP (Überschriften + Rolle, ohne Reasons der anderen Kapitel) · "
        "Satzanfang-Inventar (letzte 4 Sätze + Keys; Stem max. 2× filmweit) · "
        "Rhetoric-Slot-Ledger (filmweit, kompakt ohne Beispielsätze) · "
        "ab Kapitel 3: erster/letzter Satz der zwei Vorgänger (Opening-Varianz) · "
        "Kontrast/Gemeinsamkeit/Übergänge nur bei Dramaturgie-Brief · "
        "Vorgänger-/Nachfolger-Kapitel · Style Profile · verifizierte "
        "Fakten/Metadaten · am letzten Kapitel optional Serie-Brücke zum "
        "anderen Film (keine YouTube-CTA). LLM liefert Skript + rhetoric_usage. "
        "Ein Call = nur dieses Kapitel — kein Inventar, keine Asset-Zuordnung."
    ),
    "enhanced_script_revision": (
        "Nur dein Freitext + das bereits erzeugte Kapitel-Skript "
        "(inkl. [pause N seconds]-Marker). "
        "Kein Brief, keine Dramaturgie, kein Style, kein Inventory."
    ),
    "enhanced_rough_cut": (
        "Pro Kapitel (sequenziell): Kapitel-Skript · Kapitel-Timings · "
        "Ordner-Assets · Style · Kapitel-Dramaturgie → Pausen + grober Cut "
        "mit Editorial-Ankern (keine Sekunden) + Coverage Gaps. "
        "Ein LLM-Call pro Dramaturgie-Kapitel. "
        "Optional: Mittel-Frames (Vision) zur Asset-Auswahl."
    ),
    "enhanced_final_cut": (
        "Pro Kapitel (sequenziell): Kapitel-Skript · Timeline-Slice · "
        "Kapitel-Rough-Cut · Ordner-Assets · akzeptierte Supplements · Style → "
        "finaler Cut Plan. Ein LLM-Call pro Dramaturgie-Kapitel."
    ),
    "enhanced_sfx_planner": (
        "Resolved Visual Timeline (nur verwendete Shots) · Locked Script · "
        "echte ElevenLabs Word-Timestamps → optionaler SFX-Plan "
        "(0–max Effekte). Unabhängig vom Cut-Modell."
    ),
    "enhanced_supplement_funnel": (
        "Pro offenem Coverage Gap: Text-Ranking der Stock-Metadaten · "
        "Thumbnail-Vision in Batches · Finalisten-Vergleich. "
        "Nur Gemini-Modelle (Vision). Günstig starten: Flash Lite."
    ),
    "cut_plan_supplement_query": (
        "Ordnername · VO-Satz · Visual Intent · Reason · optionaler "
        "Search-Hint → bis zu 3 englische Stock-Suchqueries."
    ),
    "youtube_publish": (
        "Sprache · Titel · Dauer · Kapitelüberschriften + Timestamps "
        "(keine Folder-Skripte) → YouTube-Titel, Videotitel "
        "(Die Wunder von + Land/Region in der Videosprache), "
        "Beschreibung/Hashtags bzw. Quiz."
    ),
    "analysis_assets": (
        "Frame-Bilder des jeweiligen Mediums + kurzer Kontext "
        "(Dateiname, Ordner, Sprache) → visuelle Beschreibung."
    ),
    "analysis_voice_gemini": (
        "Voice-over-Audio + Transkriptions-Schema → Segmente/Text "
        "(nur wenn Gemini als Voice-Engine gewählt)."
    ),
    "edit_plan": (
        "Alle Whisper-Segmente des Ordners · alle Asset-Beschreibungen · "
        "Timing-/Asset-Regeln · Editor-/Gemini-Freitext · optional "
        "Korrektur-Hinweise — in einem gesamtheitlichen Call."
    ),
    "supplement_auto_resolve": (
        "Pro Kandidat: Frame-Bilder + VO-Passage · Visual Intent · "
        "Must-Show/Avoid · Ort — Gemini prüft Passung (PASS = übernehmen)."
    ),
}


def render_llm_model_selectbox(
    *,
    label: str,
    role_settings: LlmRoleSettings,
    key: str,
    input_info: str | None = None,
    options: list[str] | tuple[str, ...] | None = None,
    labels: dict[str, str] | None = None,
    show_estimated_costs: bool = False,
    fallback_if_unknown: str | None = None,
) -> LlmRoleSettings:
    """Ein einziges Dropdown aus VOICEOVER_GEN_MODEL_CHOICES statt Provider-
    Selectbox + Modell-Freitext.

    Verhindert Tippfehler und ungültige Provider/Modell-Kombinationen, weil
    nur konkrete, bekannt funktionierende Modelle wählbar sind. Liefert die
    Auswahl direkt als LlmRoleSettings(provider, model) zurück — das
    Speicherformat bleibt dadurch unverändert (Rückwärtskompatibilität mit
    bereits gespeicherten model_settings.json).

    Optional ``input_info``: kurzer Hinweis, was dieser LLM-Call mitbekommt.
    Optional ``options``/``labels``: eingeschränkte Auswahl (z. B. Enhanced Cut).
    Optional ``show_estimated_costs``: Preis-/1M-Hinweis in den Optionslabels.
    Optional ``fallback_if_unknown``: statt einer toten gespeicherten ID (z. B.
    ``gemini-1.5-flash``) an die Liste anzuhängen, auf diese kuratierte ID
    zurückfallen. Session-State wird *vor* dem Widget bereinigt.
    """
    from otio_app.services.voiceover_generation.llm_pricing import (
        format_model_price_suffix,
    )

    current_id = combined_model_id(role_settings)
    choice_options = list(options) if options is not None else list(VOICEOVER_GEN_MODEL_CHOICES)
    if fallback_if_unknown is not None:
        safe_fallback = (
            fallback_if_unknown
            if fallback_if_unknown in choice_options
            else choice_options[0]
        )
        # Streamlit nutzt session_state[key] und ignoriert index nach dem
        # ersten Render. Eine tote ID dort würde den Job starten, obwohl das
        # Dropdown nur 3.x anzeigt.
        if key in st.session_state and st.session_state[key] not in choice_options:
            st.session_state[key] = safe_fallback
        if current_id not in choice_options:
            current_id = safe_fallback
    elif current_id not in choice_options:
        # Bewahrt einen bereits gespeicherten, nicht (mehr) kuratierten Wert,
        # anstatt ihn beim Öffnen der Seite stillschweigend zu überschreiben.
        choice_options = [current_id] + choice_options

    def _format(model_id: str) -> str:
        if labels is not None and model_id in labels:
            base = labels[model_id]
        else:
            base = format_voiceover_gen_model_label(model_id)
        if not show_estimated_costs:
            return base
        provider, model = split_llm_model_id(model_id)
        return f"{base}{format_model_price_suffix(provider, model)}"

    selected = st.selectbox(
        label,
        options=choice_options,
        index=choice_options.index(current_id),
        format_func=_format,
        key=key,
    )
    if input_info:
        render_llm_input_info(input_info)
    provider, model = split_llm_model_id(selected)
    return LlmRoleSettings(provider=provider, model=model)


def style_profile_metric_value(profile: VoiceoverStyleProfile | None) -> str:
    """Wert für die 'Style Profile'-Kennzahl in den Voraussetzungen-Zeilen.

    Zeigt den Namen des Bibliothekseintrags an, aus dem das aktuelle Style
    Profile geladen wurde (statt eines nicht-identifizierenden Häkchens) —
    Nutzerfeedback: 'Können wir das geladene Profil anzeigen, also den Namen
    anstatt einem Haken?'. Für direkt im Projekt erzeugte, nie mit einem
    Bibliothekseintrag verknüpfte Profile bleibt es beim Häkchen, da es dort
    keinen Namen gibt."""
    if profile is None:
        return "—"
    return profile.library_name or "✓"


def style_source_metric_value(
    project: Project,
    profile: VoiceoverStyleProfile | None = None,
) -> str:
    """Style-Kennzahl inkl. Raw-Text-Modus (ohne Style Profile)."""
    from otio_app.services.voiceover_generation.style_reference_service import (
        is_raw_style_mode,
        load_style_references,
    )

    refs = load_style_references(project)
    if is_raw_style_mode(refs):
        if refs.raw_library_name:
            return f"Raw: {refs.raw_library_name}"
        has_any = bool(
            refs.raw_reference_text.strip() or refs.raw_intro_reference_text.strip()
        )
        return "Raw text" if has_any else "—"
    if profile is None:
        from otio_app.services.voiceover_generation.style_profile_service import (
            load_style_profile,
        )

        profile = load_style_profile(project)
    return style_profile_metric_value(profile)


_GREEN_BUTTON_BACKGROUND = "#1e8e3e"
_GREEN_BUTTON_HOVER_BACKGROUND = "#17703a"


def render_new_feature_button(
    label: str,
    *,
    key: str,
    help: str | None = None,
    disabled: bool = False,
    use_container_width: bool = False,
) -> bool:
    """Rendert einen `st.button` in Grün — ausschließlich für NEU
    hinzugefügte Funktionen dieser Pipeline (Nutzerwunsch, Juli 2026: 'Ich
    will alle neu hinzugefügten Buttons in grün haben, damit ich die
    Neuerungen sofort sehe'). Bestehende Buttons bleiben unverändert grau/
    orange (Streamlit-Standard) — nur Buttons, die explizit über diesen
    Helper gerendert werden, sind grün.

    Nutzt die von Streamlit dokumentierte Kopplung `key` -> CSS-Klasse
    `st-key-<sanitized key>` (siehe st.button-Dokumentation: 'if key is
    provided, it will be used as a CSS class name prefixed with st-key-'),
    um AUSSCHLIESSLICH diesen einen Button einzufärben, nicht andere
    Widgets mit anderen keys."""
    css_class_suffix = re.sub(r"[^a-zA-Z0-9_-]", "-", key.strip())
    st.markdown(
        f"""
        <style>
        .st-key-{css_class_suffix} button {{
            background-color: {_GREEN_BUTTON_BACKGROUND};
            border-color: {_GREEN_BUTTON_BACKGROUND};
            color: white;
        }}
        .st-key-{css_class_suffix} button:hover,
        .st-key-{css_class_suffix} button:focus:not(:active) {{
            background-color: {_GREEN_BUTTON_HOVER_BACKGROUND};
            border-color: {_GREEN_BUTTON_HOVER_BACKGROUND};
            color: white;
        }}
        .st-key-{css_class_suffix} button:disabled {{
            background-color: #a8d5b5;
            border-color: #a8d5b5;
            color: #f0f0f0;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    return st.button(
        label,
        key=key,
        help=help,
        disabled=disabled,
        use_container_width=use_container_width,
    )


def require_without_voiceover_mode(project: Project) -> bool:
    """Zeigt eine Warnung und liefert False, wenn das Projekt nicht im Modus
    "ohne Voice-Over" (klassisch oder Enhanced-MVP) ist. Aufrufer dürfen dann
    nichts schreiben.

    Additive Erweiterung: ``without_voiceover_enhanced`` darf dieselben frühen
    Pipeline-Seiten (Brief/Style/Dramaturgie/Intro) nutzen; Fachverhalten von
    ``without_voiceover`` bleibt unverändert.
    """
    if project.project_mode not in (
        ProjectMode.WITHOUT_VOICEOVER,
        ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
    ):
        st.warning(
            "Dieses Projekt ist auf „Projekt mit Voice-Over“ eingestellt. "
            "Diese Seite gehört zur Pipeline „Projekt ohne Voice-Over“ und "
            "speichert hier nichts."
        )
        return False
    return True
