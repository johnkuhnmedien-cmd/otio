"""Intro aus Ordner-Signalen — N Inhaltsvarianten einer Struktur, Bestätigung."""

from __future__ import annotations

from otio_app.defaults import INTRO_HOOK_CANDIDATE_COUNT, intro_word_window
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
    revise_all_intro_hook_candidates,
    revise_intro_hook_candidate,
    unconfirm_intro_hook,
    update_intro_hook_candidate,
)
from otio_app.services.voiceover_generation.prompts import (
    DEFAULT_INTRO_HOOK_REVISION_INSTRUCTIONS,
)
from otio_app.services.voiceover_generation.intro_hook_defaults_service import (
    apply_language_defaults_to_settings,
    load_language_intro_defaults,
    save_language_intro_defaults,
)
from otio_app.services.voiceover_generation.intro_hook_settings_service import (
    default_intro_hook_settings,
    load_intro_hook_settings,
    save_intro_hook_settings,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import IntroHookCandidate, IntroHookSettings
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.ui.project_context import render_project_selector
from otio_app.services.voiceover_generation.llm_pricing import (
    estimate_call_cost_usd,
    format_usd,
)
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_input_info,
    render_llm_model_selectbox,
    require_without_voiceover_mode,
    style_source_metric_value,
)

import streamlit as st

# Output-Token-Ceiling für Intro (Varianten + visual_beats). Unverbrauchtes
# Limit kostet nichts. Anthropic streamt oberhalb ~20k automatisch.
_INTRO_MAX_OUTPUT_TOKENS_MIN = 16_384
_INTRO_MAX_OUTPUT_TOKENS_MAX = 100_000
_INTRO_MAX_OUTPUT_TOKENS_DEFAULT = 65_536
_INTRO_MAX_OUTPUT_TOKENS_STEP = 4_096


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


def _intro_settings_keys(project_id: str) -> dict[str, str]:
    return {
        "target_words": f"vo_intro_target_words_{project_id}",
        "tolerance": f"vo_intro_tolerance_{project_id}",
        "tone": f"vo_intro_tone_{project_id}",
        "allow_questions": f"vo_intro_allow_q_{project_id}",
        "allow_strong_claim": f"vo_intro_allow_claim_{project_id}",
        "allow_direct_place_name": f"vo_intro_allow_place_{project_id}",
        "allow_tease_multiple_places": f"vo_intro_allow_tease_{project_id}",
        "freeform_rule": f"vo_intro_freeform_{project_id}",
        "forbidden_phrases": f"vo_intro_forbidden_{project_id}",
        "must_include": f"vo_intro_must_include_{project_id}",
        "must_avoid": f"vo_intro_must_avoid_{project_id}",
    }


def _pending_intro_settings_key(project_id: str) -> str:
    return f"vo_intro_settings_pending_{project_id}"


def _split_csv(text: str) -> list[str]:
    return [item.strip() for item in (text or "").split(",") if item.strip()]


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def apply_intro_settings_to_session(project_id: str, settings: IntroHookSettings) -> None:
    """Nur aufrufen, BEVOR die zugehörigen Widgets in diesem Run instanziert werden."""
    keys = _intro_settings_keys(project_id)
    st.session_state[keys["target_words"]] = settings.target_words
    st.session_state[keys["tolerance"]] = settings.word_tolerance_percent
    st.session_state[keys["tone"]] = settings.tone
    st.session_state[keys["allow_questions"]] = settings.allow_questions
    st.session_state[keys["allow_strong_claim"]] = settings.allow_strong_claim
    st.session_state[keys["allow_direct_place_name"]] = settings.allow_direct_place_name
    st.session_state[keys["allow_tease_multiple_places"]] = settings.allow_tease_multiple_places
    st.session_state[keys["freeform_rule"]] = settings.freeform_rule_for_llm
    st.session_state[keys["forbidden_phrases"]] = "\n".join(settings.forbidden_phrases)
    st.session_state[keys["must_include"]] = ", ".join(settings.must_include)
    st.session_state[keys["must_avoid"]] = ", ".join(settings.must_avoid)


def _hydrate_intro_settings(project: Project) -> None:
    pending = st.session_state.pop(_pending_intro_settings_key(project.id), None)
    if isinstance(pending, IntroHookSettings):
        apply_intro_settings_to_session(project.id, pending)
        return
    keys = _intro_settings_keys(project.id)
    if keys["target_words"] not in st.session_state:
        apply_intro_settings_to_session(project.id, load_intro_hook_settings(project))


def _settings_from_widgets(
    project: Project,
    *,
    target_words: int,
    tolerance: int,
    tone: str,
    freeform_rule: str,
    forbidden_phrases_text: str,
    allow_questions: bool,
    allow_strong_claim: bool,
    allow_direct_place_name: bool,
    allow_tease_multiple_places: bool,
    must_include_text: str,
    must_avoid_text: str,
) -> IntroHookSettings:
    return IntroHookSettings(
        project_id=project.id,
        language=normalize_brief_language(project.language),
        target_words=int(target_words),
        word_tolerance_percent=int(tolerance),
        tone=tone,
        freeform_rule_for_llm=freeform_rule,
        forbidden_phrases=_split_lines(forbidden_phrases_text),
        allow_questions=allow_questions,
        allow_strong_claim=allow_strong_claim,
        allow_direct_place_name=allow_direct_place_name,
        allow_tease_multiple_places=allow_tease_multiple_places,
        must_include=_split_csv(must_include_text),
        must_avoid=_split_csv(must_avoid_text),
    )


def _render_settings_editor(project: Project) -> None:
    lang_key = normalize_brief_language(project.language)
    has_language_default = load_language_intro_defaults(lang_key) is not None
    _hydrate_intro_settings(project)
    keys = _intro_settings_keys(project.id)

    st.subheader("Intro Settings")
    st.caption(f"Projektsprache **{lang_key}**.")

    col1, col2, col3 = st.columns(3)
    with col1:
        target_words = st.number_input(
            "Ziel-Wortanzahl",
            min_value=0,
            step=5,
            key=keys["target_words"],
        )
        tolerance = st.number_input(
            "Toleranz (%)",
            min_value=0,
            max_value=100,
            step=5,
            key=keys["tolerance"],
            help="Min/Max-Wörter = Ziel ± diese Prozentzahl. Kein separates Min/Max-Feld.",
        )
        window_min, window_max = intro_word_window(int(target_words), int(tolerance))
        st.caption(f"Wortfenster: **{window_min}–{window_max}** (Ziel ± {int(tolerance)}%).")
    with col2:
        tone = st.text_input("Tonalität", key=keys["tone"])
        allow_questions = st.checkbox("Fragen erlaubt", key=keys["allow_questions"])
        allow_strong_claim = st.checkbox(
            "Starke These erlaubt", key=keys["allow_strong_claim"]
        )
    with col3:
        allow_direct_place_name = st.checkbox(
            "Ortsname direkt nennen erlaubt", key=keys["allow_direct_place_name"]
        )
        allow_tease_multiple_places = st.checkbox(
            "Mehrere Orte anteasern erlaubt", key=keys["allow_tease_multiple_places"]
        )

    freeform_rule = st.text_area(
        "Freitext-Regel für das LLM",
        key=keys["freeform_rule"],
    )
    forbidden_phrases_text = st.text_area(
        "Verbotene Begriffe (eine pro Zeile)",
        key=keys["forbidden_phrases"],
    )
    must_include_text = st.text_input(
        "Muss enthalten (Komma-getrennt)",
        key=keys["must_include"],
    )
    must_avoid_text = st.text_input(
        "Muss vermeiden (Komma-getrennt)",
        key=keys["must_avoid"],
    )

    draft = _settings_from_widgets(
        project,
        target_words=int(target_words),
        tolerance=int(tolerance),
        tone=tone,
        freeform_rule=freeform_rule,
        forbidden_phrases_text=forbidden_phrases_text,
        allow_questions=allow_questions,
        allow_strong_claim=allow_strong_claim,
        allow_direct_place_name=allow_direct_place_name,
        allow_tease_multiple_places=allow_tease_multiple_places,
        must_include_text=must_include_text,
        must_avoid_text=must_avoid_text,
    )

    col_save, col_lang, col_reset = st.columns(3)
    with col_save:
        if st.button("Intro Settings speichern", key=f"vo_intro_settings_save_{project.id}"):
            save_intro_hook_settings(project, draft)
            st.success("Intro Settings gespeichert.")
            st.rerun()
    with col_lang:
        if st.button(
            f"Als Standard für {lang_key} speichern",
            key=f"vo_intro_settings_save_lang_{project.id}",
            help=(
                f"Wortziel, Tonalität, Freitext-Regel und Flags global für {lang_key}. "
                "Erzeugte Intro-Varianten bleiben projektspezifisch."
            ),
        ):
            save_language_intro_defaults(lang_key, draft)
            save_intro_hook_settings(project, draft)
            st.success(f"Als globaler Standard für **{lang_key}** gespeichert.")
            st.rerun()
    with col_reset:
        reset_label = (
            f"Auf {lang_key}-Standard zurück"
            if has_language_default
            else "Auf Standard zurücksetzen"
        )
        if st.button(
            reset_label,
            key=f"vo_intro_settings_reset_{project.id}",
            disabled=not has_language_default,
            help=(
                f"Lädt den globalen {lang_key}-Standard in dieses Projekt."
                if has_language_default
                else "Noch kein Sprachstandard gespeichert."
            ),
        ):
            language_defaults = load_language_intro_defaults(lang_key)
            if language_defaults is not None:
                reset = apply_language_defaults_to_settings(
                    default_intro_hook_settings(project), language_defaults
                )
                save_intro_hook_settings(project, reset)
                st.session_state[_pending_intro_settings_key(project.id)] = reset
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


def _render_max_tokens_slider(
    project: Project,
    *,
    provider: str,
    model: str,
) -> int:
    slider_key = f"vo_intro_max_tokens_{project.id}"
    if slider_key not in st.session_state:
        st.session_state[slider_key] = _INTRO_MAX_OUTPUT_TOKENS_DEFAULT

    max_tokens = st.slider(
        "Max. Output-Tokens (Ceiling)",
        min_value=_INTRO_MAX_OUTPUT_TOKENS_MIN,
        max_value=_INTRO_MAX_OUTPUT_TOKENS_MAX,
        step=_INTRO_MAX_OUTPUT_TOKENS_STEP,
        key=slider_key,
        help=(
            f"Obergrenze für die Antwortlänge ({INTRO_HOOK_CANDIDATE_COUNT} "
            "Intro-Varianten + visual_beats). "
            "Du zahlst nur die tatsächlich erzeugten Output-Tokens — nicht "
            "automatisch das volle Limit. Bei Truncation höher stellen."
        ),
    )
    estimate = estimate_call_cost_usd(
        provider=provider,
        model=model,
        input_tokens=0,
        output_tokens_ceiling=int(max_tokens),
    )
    st.caption(
        f"**Output-Worst-Case** ({estimate.price.label}): "
        f"{estimate.output_tokens_ceiling:,} Tok → "
        f"{format_usd(estimate.output_ceiling_cost_usd)}. "
        f"Aktuelles Limit: max_tokens={int(max_tokens):,}."
    )
    return int(max_tokens)


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
    _render_settings_editor(project)
    if not can_generate:
        return

    provider, model = _render_model_settings(project)

    confirmed_hook = load_confirmed_intro_hook(project)
    candidates_document = load_intro_hook_candidates(project)

    st.subheader("Intro-Varianten generieren")
    st.caption(
        f"Eine Intro-Struktur (Raw-Intro-Referenz) × {INTRO_HOOK_CANDIDATE_COUNT} "
        f"unterschiedliche Inhalte. Nicht {INTRO_HOOK_CANDIDATE_COUNT} verschiedene "
        "Hook-Strategien."
    )
    render_llm_input_info(LLM_INPUT_INFO["intro"])
    max_output_tokens = _render_max_tokens_slider(
        project, provider=provider, model=model
    )
    if confirmed_hook is not None:
        st.info(
            "Es gibt bereits ein bestätigtes Intro. Neue Varianten "
            "ersetzen es nicht automatisch."
        )

    label = (
        "Intro-Varianten neu generieren"
        if candidates_document is not None
        else f"{INTRO_HOOK_CANDIDATE_COUNT} Intro-Varianten generieren"
    )
    if st.button(label, key=f"vo_intro_generate_{project.id}", type="primary"):
        with st.spinner("Intro-Varianten werden generiert…"):
            if candidates_document is not None:
                result = regenerate_intro_hook_candidates(
                    project,
                    provider=provider,
                    model=model,
                    max_output_tokens=max_output_tokens,
                )
            else:
                result = build_intro_hook_candidates(
                    project,
                    provider=provider,
                    model=model,
                    max_output_tokens=max_output_tokens,
                )
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

    _render_intro_revision_section(
        project,
        candidates_document,
        provider=provider,
        model=model,
        max_output_tokens=max_output_tokens,
    )

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


def _render_intro_revision_section(
    project: Project,
    candidates_document,
    *,
    provider: str,
    model: str,
    max_output_tokens: int,
) -> None:
    st.subheader("Intro mit Freitext nachbearbeiten")
    render_llm_input_info(LLM_INPUT_INFO["intro_revision"])
    hook_ids = [c.hook_id for c in candidates_document.candidates]
    if not hook_ids:
        st.info("Keine Varianten zum Nachbearbeiten.")
        return

    selected = st.selectbox(
        "Variante für Nachbearbeitung",
        options=hook_ids,
        format_func=lambda hook_id: next(
            (
                f"{c.hook_id} — {c.hook_type}"
                for c in candidates_document.candidates
                if c.hook_id == hook_id
            ),
            hook_id,
        ),
        key=f"vo_intro_revise_hook_{project.id}",
    )
    prompt_key = f"vo_intro_revise_prompt_{project.id}"
    if prompt_key not in st.session_state:
        st.session_state[prompt_key] = DEFAULT_INTRO_HOOK_REVISION_INSTRUCTIONS
    instructions = st.text_area(
        "Freitext-Anweisung an das LLM",
        key=prompt_key,
        height=140,
        help=(
            "Nur dieser Text und der aktuelle Intro-Text "
            "(inkl. [pause N seconds]-Marker) gehen an das LLM."
        ),
    )
    selected_candidate = next(
        (c for c in candidates_document.candidates if c.hook_id == selected),
        None,
    )
    with st.expander("Aktuelles Intro (wird mitgeschickt)", expanded=False):
        st.write(
            (selected_candidate.hook_text if selected_candidate else "") or "(leer)"
        )

    col_one, col_all = st.columns(2)
    with col_one:
        if st.button(
            "Ausgewählte Variante nachbearbeiten",
            type="primary",
            key=f"vo_intro_revise_one_{project.id}",
        ):
            if not (instructions or "").strip():
                st.warning("Bitte zuerst eine Freitext-Anweisung eingeben.")
            else:
                with st.spinner(f"„{selected}“ wird nachbearbeitet…"):
                    result = revise_intro_hook_candidate(
                        project,
                        selected,
                        editor_instructions=instructions,
                        provider=provider,
                        model=model,
                        max_output_tokens=max_output_tokens,
                    )
                if result.status == STATUS_PASS:
                    # Clear text-area session so UI reloads revised text.
                    st.session_state.pop(
                        f"vo_intro_hook_text_{selected}_{project.id}", None
                    )
                    st.success(f"„{selected}“ nachbearbeitet.")
                else:
                    st.error(result.error or "Fehlgeschlagen.")
                st.rerun()
    with col_all:
        if st.button(
            f"Alle {len(hook_ids)} Varianten nachbearbeiten",
            key=f"vo_intro_revise_all_{project.id}",
        ):
            if not (instructions or "").strip():
                st.warning("Bitte zuerst eine Freitext-Anweisung eingeben.")
            else:
                progress = st.empty()

                def _progress(hook_id: str, index: int, total: int) -> None:
                    progress.info(f"Variante {index}/{total}: „{hook_id}“…")

                with st.spinner("Alle Intro-Varianten werden nachbearbeitet…"):
                    results = revise_all_intro_hook_candidates(
                        project,
                        editor_instructions=instructions,
                        provider=provider,
                        model=model,
                        max_output_tokens=max_output_tokens,
                        progress_callback=_progress,
                    )
                progress.empty()
                ok = [r for r in results if r.status == STATUS_PASS]
                fail = [r for r in results if r.status != STATUS_PASS]
                for candidate in candidates_document.candidates:
                    st.session_state.pop(
                        f"vo_intro_hook_text_{candidate.hook_id}_{project.id}",
                        None,
                    )
                st.success(f"{len(ok)}/{len(results)} Varianten nachbearbeitet.")
                for result in fail:
                    st.error(f"„{result.hook_id}“: {result.error}")
                st.rerun()
