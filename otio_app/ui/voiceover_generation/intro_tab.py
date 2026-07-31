"""Intro aus Ordner-Signalen — 5 Inhaltsvarianten einer Struktur, Bestätigung."""

from __future__ import annotations

from otio_app.models import Project
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.intro_hook_service import (
    build_intro_hook_candidates,
    confirm_intro_hook,
    get_active_dramaturgy_folder_names,
    get_intro_source_folder_names,
    intro_source_ready,
    load_confirmed_intro_hook,
    load_intro_hook_candidates,
    missing_intro_source_folder_names,
    regenerate_intro_hook_candidates,
    unconfirm_intro_hook,
    update_intro_hook_candidate,
)
from otio_app.services.voiceover_generation.intro_hook_settings_service import (
    default_intro_hook_settings,
    load_intro_hook_settings,
    save_intro_hook_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import IntroHookCandidate
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_model_selectbox,
    require_without_voiceover_mode,
    style_source_metric_value,
)

import streamlit as st


def _render_prerequisites(project: Project) -> bool:
    brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    confirmed_plan = load_confirmed_dramaturgy(project)
    active_names = get_active_dramaturgy_folder_names(project)
    source_names = get_intro_source_folder_names(project)
    missing_names = missing_intro_source_folder_names(project)
    enhanced = project.is_without_voiceover_enhanced

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Project Brief", "✓" if (brief.video_title or brief.tone_tags) else "—")
    with col2:
        st.metric("Style", style_source_metric_value(project, style_profile))
    with col3:
        st.metric("Dramaturgie bestätigt", "✓" if confirmed_plan is not None else "—")
    with col4:
        st.metric("Aktive Ordner", len(active_names))

    st.caption(f"Aktive Ordner laut Dramaturgie: {', '.join(active_names) or '—'}")
    if enhanced:
        st.caption(
            f"Kapitel im Script Lock: {', '.join(source_names) or '—'} "
            f"({len(source_names)}/{len(active_names)})"
        )
    else:
        st.caption(
            f"Bestätigte Voice-over-Ordner: {', '.join(source_names) or '—'} "
            f"({len(source_names)}/{len(active_names)})"
        )

    if confirmed_plan is None:
        st.warning("Bitte zuerst die Dramaturgie bestätigen.")
        return False

    if missing_names:
        if enhanced:
            from otio_app.services.without_voiceover_enhanced.script_lock_service import (
                load_locked_script,
            )

            if load_locked_script(project) is None:
                st.warning(
                    "Bitte unter **④ Folder Voice-overs** alle Kapitel-Skripte erzeugen "
                    "und **Script Lock** setzen."
                )
            else:
                st.warning(
                    "Script Lock ist gesetzt, aber noch nicht alle aktiven Kapitel "
                    "haben ein Skript. Bitte fehlende Kapitel erzeugen und erneut locken."
                )
            st.caption(f"Fehlende Kapitel: {', '.join(missing_names)}")
        else:
            st.warning("Bitte zuerst alle aktiven Folder Voice-overs bestätigen.")
            st.caption(f"Fehlende Ordner: {', '.join(missing_names)}")
        return False

    if not active_names:
        st.error("Keine aktiven Ordner in der bestätigten Dramaturgie.")
        return False

    if enhanced and intro_source_ready(project):
        st.success(
            f"Script Lock bereit — {len(source_names)} Kapitel als Intro-Quelle."
        )

    return True


def _render_settings_editor(project: Project) -> None:
    settings = load_intro_hook_settings(project)
    st.subheader("Intro Settings")

    col1, col2, col3 = st.columns(3)
    with col1:
        target_words = st.number_input(
            "Ziel-Wortanzahl", min_value=0, step=5, value=settings.target_words,
            key=f"vo_intro_target_words_{project.id}",
        )
        min_words = st.number_input(
            "Min. Wörter", min_value=0, step=5, value=settings.min_words,
            key=f"vo_intro_min_words_{project.id}",
        )
        max_words = st.number_input(
            "Max. Wörter", min_value=0, step=5, value=settings.max_words,
            key=f"vo_intro_max_words_{project.id}",
        )
    with col2:
        tolerance = st.number_input(
            "Toleranz (%)", min_value=0, max_value=100, step=5,
            value=settings.word_tolerance_percent, key=f"vo_intro_tolerance_{project.id}",
        )
        tone = st.text_input("Tonalität", value=settings.tone, key=f"vo_intro_tone_{project.id}")
        allow_questions = st.checkbox(
            "Fragen erlaubt", value=settings.allow_questions, key=f"vo_intro_allow_q_{project.id}"
        )
        allow_strong_claim = st.checkbox(
            "Starke These erlaubt", value=settings.allow_strong_claim,
            key=f"vo_intro_allow_claim_{project.id}",
        )
    with col3:
        allow_direct_place_name = st.checkbox(
            "Ortsname direkt nennen erlaubt", value=settings.allow_direct_place_name,
            key=f"vo_intro_allow_place_{project.id}",
        )
        allow_tease_multiple_places = st.checkbox(
            "Mehrere Orte anteasern erlaubt", value=settings.allow_tease_multiple_places,
            key=f"vo_intro_allow_tease_{project.id}",
        )

    freeform_rule = st.text_area(
        "Freitext-Regel für das LLM", value=settings.freeform_rule_for_llm,
        key=f"vo_intro_freeform_{project.id}",
    )
    forbidden_phrases_text = st.text_area(
        "Verbotene Begriffe (eine pro Zeile)",
        value="\n".join(settings.forbidden_phrases),
        key=f"vo_intro_forbidden_{project.id}",
    )
    must_include_text = st.text_input(
        "Muss enthalten (Komma-getrennt)", value=", ".join(settings.must_include),
        key=f"vo_intro_must_include_{project.id}",
    )
    must_avoid_text = st.text_input(
        "Muss vermeiden (Komma-getrennt)", value=", ".join(settings.must_avoid),
        key=f"vo_intro_must_avoid_{project.id}",
    )

    col_save, col_defaults = st.columns(2)
    with col_save:
        if st.button("Intro Settings speichern", key=f"vo_intro_settings_save_{project.id}"):
            updated = settings.model_copy(
                update={
                    "target_words": int(target_words),
                    "min_words": int(min_words),
                    "max_words": int(max_words),
                    "word_tolerance_percent": int(tolerance),
                    "tone": tone,
                    "freeform_rule_for_llm": freeform_rule,
                    "forbidden_phrases": [
                        line.strip() for line in forbidden_phrases_text.splitlines() if line.strip()
                    ],
                    "allow_questions": allow_questions,
                    "allow_strong_claim": allow_strong_claim,
                    "allow_direct_place_name": allow_direct_place_name,
                    "allow_tease_multiple_places": allow_tease_multiple_places,
                    "must_include": [
                        item.strip() for item in must_include_text.split(",") if item.strip()
                    ],
                    "must_avoid": [
                        item.strip() for item in must_avoid_text.split(",") if item.strip()
                    ],
                }
            )
            save_intro_hook_settings(project, updated)
            st.success("Intro Settings gespeichert.")
            st.rerun()
    with col_defaults:
        if st.button("Defaults aus Project Brief laden", key=f"vo_intro_settings_defaults_{project.id}"):
            save_intro_hook_settings(project, default_intro_hook_settings(project))
            st.success("Defaults aus Project Brief übernommen.")
            st.rerun()


def _render_model_settings(project: Project) -> tuple[str, str]:
    settings = load_model_settings(project)
    with st.expander("⚙️ Modell für Intro", expanded=False):
        role_settings = render_llm_model_selectbox(
            label="Modell",
            role_settings=settings.intro,
            key=f"vo_intro_model_{project.id}",
            input_info=LLM_INPUT_INFO["intro"],
        )
        if st.button("Speichern", key=f"vo_intro_model_save_{project.id}"):
            updated = settings.model_copy(update={"intro": role_settings})
            save_model_settings(project, updated)
            st.success("Modell-Einstellung für Intro gespeichert.")
    return role_settings.provider, role_settings.model


def _render_visual_beats_table(candidate: IntroHookCandidate) -> None:
    if not candidate.visual_beats:
        st.caption("Keine visual_beats vorhanden.")
        return
    rows = [
        {
            "text": beat.text,
            "visual_intent": beat.visual_intent,
            "source_folder_name": beat.source_folder_name,
            "source_sentence_id": beat.source_sentence_id,
            "primary_asset_id": beat.primary_asset_id,
            "backup_asset_ids": ", ".join(beat.backup_asset_ids),
            "asset_confidence": beat.asset_confidence,
            "needs_supplement_asset": beat.needs_supplement_asset,
            "supplement_reason": beat.supplement_reason,
        }
        for beat in candidate.visual_beats
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_candidate(project: Project, candidate: IntroHookCandidate, *, is_confirmed: bool) -> None:
    with st.expander(
        f"{candidate.hook_id} — {candidate.hook_type} (Score: {candidate.hook_potential_score:.2f})"
        + (" ✅ bestätigt" if is_confirmed else "")
    ):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Wortanzahl", candidate.word_count)
        with col2:
            st.metric("Inhalt / Fokus", candidate.hook_type)
        with col3:
            st.metric("Score", f"{candidate.hook_potential_score:.2f}")

        st.caption(f"Verwendete Ordner: {', '.join(candidate.used_folders) or '—'}")

        text_key = f"vo_intro_hook_text_{candidate.hook_id}_{project.id}"
        if text_key not in st.session_state:
            st.session_state[text_key] = candidate.hook_text
        hook_text = st.text_area("Intro-Text", key=text_key, height=160)

        if candidate.reason:
            st.write(f"**Begründung:** {candidate.reason}")
        if candidate.risks:
            st.warning("Risiken:\n" + "\n".join(f"- {risk}" for risk in candidate.risks))

        st.markdown("**Visuelle Zuordnung (visual_beats)**")
        _render_visual_beats_table(candidate)

        col_save, col_confirm = st.columns(2)
        with col_save:
            if st.button("Änderungen speichern", key=f"vo_intro_save_{candidate.hook_id}_{project.id}"):
                update_intro_hook_candidate(project, candidate.hook_id, {"hook_text": hook_text})
                st.success("Änderungen gespeichert.")
                st.rerun()
        with col_confirm:
            if st.button(
                "Diese Variante wählen / bestätigen",
                key=f"vo_intro_confirm_{candidate.hook_id}_{project.id}",
                type="primary",
            ):
                update_intro_hook_candidate(project, candidate.hook_id, {"hook_text": hook_text})
                confirm_intro_hook(project, candidate.hook_id, edited_hook_text=hook_text)
                st.success("Intro bestätigt.")
                st.rerun()


def render_intro_page() -> None:
    st.header("⑤ Intro")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    st.subheader("Voraussetzungen")
    can_generate = _render_prerequisites(project)
    if not can_generate:
        return

    _render_settings_editor(project)
    provider, model = _render_model_settings(project)

    confirmed_hook = load_confirmed_intro_hook(project)
    candidates_document = load_intro_hook_candidates(project)

    st.subheader("Intro-Varianten generieren")
    st.caption(
        "Eine Intro-Struktur (Raw-Intro-Referenz) × fünf unterschiedliche Inhalte. "
        "Nicht fünf verschiedene Hook-Strategien."
    )
    if confirmed_hook is not None:
        st.info(
            "Es gibt bereits ein bestätigtes Intro. Neue Varianten "
            "ersetzen es nicht automatisch."
        )

    label = (
        "Intro-Varianten neu generieren"
        if candidates_document is not None
        else "5 Intro-Varianten generieren"
    )
    if st.button(label, key=f"vo_intro_generate_{project.id}", type="primary"):
        with st.spinner("Intro-Varianten werden generiert…"):
            if candidates_document is not None:
                result = regenerate_intro_hook_candidates(project, provider=provider, model=model)
            else:
                result = build_intro_hook_candidates(project, provider=provider, model=model)
        st.session_state[f"vo_intro_last_result_{project.id}"] = {
            "status": result.status, "error": result.error, "llm_run_id": result.llm_run_id,
        }
        if result.status == STATUS_PASS:
            st.success(f"{len(result.document.candidates)} Varianten erzeugt.")
        else:
            st.error(f"Fehlgeschlagen ({result.status}): {result.error}")
        st.rerun()

    last_result = st.session_state.get(f"vo_intro_last_result_{project.id}")
    if last_result is not None and last_result.get("status") != STATUS_PASS:
        st.error(
            f"Letzter Versuch fehlgeschlagen ({last_result.get('status')}): "
            f"{last_result.get('error')}"
        )

    if candidates_document is None:
        st.info("Noch keine Intro-Varianten vorhanden.")
        return

    if candidates_document.risks:
        st.warning("Hinweise zum letzten Lauf:\n" + "\n".join(f"- {risk}" for risk in candidates_document.risks))

    st.subheader("Varianten")
    confirmed_hook_id = confirmed_hook.hook_id if confirmed_hook is not None else None
    for candidate in candidates_document.candidates:
        _render_candidate(project, candidate, is_confirmed=candidate.hook_id == confirmed_hook_id)

    st.subheader("Bestätigtes Intro")
    if confirmed_hook is None:
        st.info("Noch kein Intro bestätigt.")
        return

    st.success(f"Bestätigt: `{confirmed_hook.hook_id}` ({confirmed_hook.confirmed_at.isoformat()})")
    st.write(confirmed_hook.hook_text)
    st.caption(
        f"Wortanzahl: {confirmed_hook.word_count} · Inhalt / Fokus: {confirmed_hook.hook_type}"
    )
    if confirmed_hook.visual_beats:
        st.markdown("**Visuelle Zuordnung**")
        rows = [
            {
                "text": beat.text,
                "primary_asset_id": beat.primary_asset_id,
                "source_folder_name": beat.source_folder_name,
                "needs_supplement_asset": beat.needs_supplement_asset,
            }
            for beat in confirmed_hook.visual_beats
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    if st.button("Bestätigung zurücknehmen", key=f"vo_intro_unconfirm_{project.id}"):
        unconfirm_intro_hook(project)
        st.info("Bestätigung zurückgenommen.")
        st.rerun()
