"""Schritt 4 Enhanced: Skripterzeugung pro Dramaturgie-Kapitel + Script Lock."""

from __future__ import annotations

import streamlit as st

from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    mark_audio_stale_for_changed_segments,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
    folders_present_in_script,
    generate_all_enhanced_scripts,
    generate_enhanced_script_for_folder,
    list_enabled_dramaturgy_folders,
    looks_like_asset_inventory_script,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    load_locked_script,
    load_script_draft,
    lock_script,
    mark_segment_text_changed,
)
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_model_selectbox,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project

_MAX_TOKENS_MIN = 16_384
_MAX_TOKENS_MAX = 100_000
_MAX_TOKENS_STEP = 4_096
_MAX_TOKENS_DEFAULT = DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS


def _render_model_and_tokens(project) -> tuple[str, str, int]:
    settings = load_model_settings(project)
    with st.expander("⚙️ Modell für Skripterzeugung", expanded=True):
        role_settings = render_llm_model_selectbox(
            label="Modell (LLM-Lauf 1)",
            role_settings=settings.voiceover_author,
            key=f"enh_script_model_{project.id}",
            input_info=LLM_INPUT_INFO["enhanced_script"],
        )
        if st.button("Modell speichern", key=f"enh_script_model_save_{project.id}"):
            updated = settings.model_copy(update={"voiceover_author": role_settings})
            save_model_settings(project, updated)
            st.success("Modell für Skripterzeugung gespeichert.")

    token_key = f"enh_script_max_tokens_{project.id}"
    if token_key not in st.session_state:
        st.session_state[token_key] = _MAX_TOKENS_DEFAULT
    max_tokens = st.slider(
        "Max. Output-Tokens pro Kapitel (Ceiling)",
        min_value=_MAX_TOKENS_MIN,
        max_value=_MAX_TOKENS_MAX,
        step=_MAX_TOKENS_STEP,
        key=token_key,
        help=(
            "Obergrenze pro Ordner-Call. Du zahlst nur tatsächlich erzeugte Tokens. "
            "Bei Truncation Limit erhöhen."
        ),
    )
    return role_settings.provider, role_settings.model, int(max_tokens)


def _render_generation_controls(
    project,
    *,
    provider: str,
    model: str,
    max_tokens: int,
) -> None:
    entries = list_enabled_dramaturgy_folders(project)
    if not entries:
        st.error(
            "Keine bestätigte Dramaturgie mit aktiven Ordnern. "
            "Bitte zuerst unter **③ Dramaturgie** planen und bestätigen."
        )
        return

    draft = load_script_draft(project)
    present = folders_present_in_script(draft)
    st.caption(
        f"{len(present)}/{len(entries)} Kapitel haben bereits Segmente im Draft."
    )

    folder_names = [entry.folder_name for entry in entries]
    selected = st.selectbox(
        "Kapitel / Ordner (Dramaturgie-Reihenfolge)",
        options=folder_names,
        key=f"enh_script_folder_{project.id}",
        format_func=lambda name: (
            f"{'✓' if name in present else '○'} · {name}"
        ),
    )

    col_one, col_all = st.columns(2)
    with col_one:
        if st.button(
            "Skript für ausgewähltes Kapitel erzeugen",
            type="primary",
            key=f"enh_script_one_{project.id}",
        ):
            with st.spinner(f"Skript für „{selected}“ wird erzeugt…"):
                result = generate_enhanced_script_for_folder(
                    project,
                    selected,
                    provider=provider,
                    model=model,
                    max_output_tokens=max_tokens,
                )
            if result.status == "PASS":
                st.success(
                    f"„{selected}“: {result.segment_count} Segmente "
                    f"(Draft gesamt: {len(result.document.segments) if result.document else 0})."
                )
                if result.document and looks_like_asset_inventory_script(result.document):
                    st.warning("Skript wirkt noch zu assetgebunden — bitte prüfen.")
            else:
                st.error(f"Fehlgeschlagen: {result.error}")
            st.rerun()

    with col_all:
        if st.button(
            "Alle Kapitel sequenziell erzeugen",
            key=f"enh_script_all_{project.id}",
        ):
            progress = st.empty()

            def _progress(folder_name: str, index: int, total: int) -> None:
                progress.info(f"Kapitel {index}/{total}: „{folder_name}“…")

            with st.spinner("Alle Kapitel werden nacheinander erzeugt…"):
                results = generate_all_enhanced_scripts(
                    project,
                    provider=provider,
                    model=model,
                    max_output_tokens=max_tokens,
                    progress_callback=_progress,
                )
            progress.empty()
            ok = [r for r in results if r.status == "PASS"]
            fail = [r for r in results if r.status != "PASS"]
            st.success(f"{len(ok)}/{len(results)} Kapitel erfolgreich.")
            for result in fail:
                st.error(f"„{result.folder_name}“: {result.error}")
            st.rerun()

    st.subheader("Kapitel-Status")
    for entry in entries:
        status = "fertig" if entry.folder_name in present else "offen"
        words = entry.recommended_word_count or "—"
        st.caption(
            f"{entry.order_index}. **{entry.folder_name}** · "
            f"Rolle `{entry.dramaturgy_role}` · Zielwörter {words} · {status}"
        )


def render_enhanced_folder_voiceovers_page() -> None:
    st.header("④ Folder Voice-overs / Skripterzeugung (Enhanced)")
    st.caption(
        "Redaktionelle Narration zu Geschichte, Besonderheiten und Atmosphäre — "
        "ein LLM-Call pro Dramaturgie-Kapitel (wie in der klassischen Pipeline). "
        "Assets sind visuelle Ressource, keine Inhaltsgrenze."
    )
    project = get_enhanced_project()
    if project is None:
        return

    confirmed = load_confirmed_dramaturgy(project)
    if confirmed is None:
        st.warning(
            "Bitte zuerst die Dramaturgie unter **③ Dramaturgie** bestätigen — "
            "die Skripte folgen der bestätigten Kapitel-Reihenfolge."
        )

    provider, model, max_tokens = _render_model_and_tokens(project)
    st.divider()
    _render_generation_controls(
        project, provider=provider, model=model, max_tokens=max_tokens
    )

    draft = load_script_draft(project)
    locked = load_locked_script(project)

    st.divider()
    col_lock, _ = st.columns(2)
    with col_lock:
        if st.button("Script Lock", disabled=draft is None or not draft.segments):
            try:
                locked = lock_script(project, draft)
                st.success(
                    f"Skript gesperrt: {locked.script_version} "
                    f"(status={locked.script_status})"
                )
                st.rerun()
            except ScriptLockError as exc:
                st.error(str(exc))

    if locked is not None:
        st.info(f"Gesperrt: `{locked.script_version}` · status=`{locked.script_status}`")
    elif draft is not None and draft.segments:
        st.warning("Skript ist noch nicht gesperrt — ElevenLabs benötigt Script Lock.")

    show = locked or draft
    if show is None or not show.segments:
        st.info("Noch kein Skript vorhanden — Kapitel oben erzeugen.")
        return

    st.subheader("Gesprochene Narration")
    st.write(show.narration_full)
    if show.forbidden_phrases_found:
        real = [p for p in show.forbidden_phrases_found if not p.startswith("PARTIAL_FAIL:")]
        if real:
            st.warning("Verbotene Phrasen: " + ", ".join(real))

    st.subheader("Segmente")
    for segment in show.segments:
        folder_label = f" · {segment.folder_name}" if segment.folder_name else ""
        with st.expander(
            f"{segment.sequence_index}. {segment.segment_id}{folder_label} "
            f"({segment.semantic_function})"
        ):
            new_text = st.text_area(
                "Text",
                value=segment.text,
                key=f"enh_seg_{project.id}_{segment.segment_id}",
                height=120,
            )
            if st.button(
                "Text speichern",
                key=f"enh_seg_save_{project.id}_{segment.segment_id}",
            ):
                try:
                    updated = mark_segment_text_changed(
                        project, segment.segment_id, new_text
                    )
                    mark_audio_stale_for_changed_segments(project, updated)
                    st.success("Segment aktualisiert — Script Lock aufgehoben.")
                    st.rerun()
                except ScriptLockError as exc:
                    st.error(str(exc))

    st.subheader("Visual Intents")
    for intent in show.visual_intents:
        folder_bit = f" · {intent.folder_name}" if intent.folder_name else ""
        st.caption(
            f"`{intent.intent_id}`{folder_bit}: {intent.description} "
            f"({intent.preferred_media_type})"
        )
