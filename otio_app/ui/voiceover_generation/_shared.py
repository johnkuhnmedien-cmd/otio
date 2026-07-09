"""Gemeinsame UI-Bausteine für die Pipeline "Projekt ohne Voice-Over"."""

from __future__ import annotations

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

    if project.project_mode != ProjectMode.WITHOUT_VOICEOVER:
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


def render_llm_model_selectbox(
    *,
    label: str,
    role_settings: LlmRoleSettings,
    key: str,
) -> LlmRoleSettings:
    """Ein einziges Dropdown aus VOICEOVER_GEN_MODEL_CHOICES statt Provider-
    Selectbox + Modell-Freitext.

    Verhindert Tippfehler und ungültige Provider/Modell-Kombinationen, weil
    nur konkrete, bekannt funktionierende Modelle wählbar sind. Liefert die
    Auswahl direkt als LlmRoleSettings(provider, model) zurück — das
    Speicherformat bleibt dadurch unverändert (Rückwärtskompatibilität mit
    bereits gespeicherten model_settings.json)."""
    current_id = combined_model_id(role_settings)
    options = list(VOICEOVER_GEN_MODEL_CHOICES)
    if current_id not in options:
        # Bewahrt einen bereits gespeicherten, nicht (mehr) kuratierten Wert,
        # anstatt ihn beim Öffnen der Seite stillschweigend zu überschreiben.
        options = [current_id] + options
    selected = st.selectbox(
        label,
        options=options,
        index=options.index(current_id),
        format_func=format_voiceover_gen_model_label,
        key=key,
    )
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


def require_without_voiceover_mode(project: Project) -> bool:
    """Zeigt eine Warnung und liefert False, wenn das Projekt nicht im Modus
    "ohne Voice-Over" ist. Aufrufer dürfen dann nichts schreiben."""
    if project.project_mode != ProjectMode.WITHOUT_VOICEOVER:
        st.warning(
            "Dieses Projekt ist auf „Projekt mit Voice-Over“ eingestellt. "
            "Diese Seite gehört zur Pipeline „Projekt ohne Voice-Over“ und "
            "speichert hier nichts."
        )
        return False
    return True
