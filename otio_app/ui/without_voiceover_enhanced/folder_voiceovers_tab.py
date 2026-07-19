"""Schritt 4 Enhanced: freiere Skripterzeugung + Script Lock."""

from __future__ import annotations

import streamlit as st

from otio_app.services.without_voiceover_enhanced.script_author_service import (
    generate_enhanced_script,
    looks_like_asset_inventory_script,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    load_locked_script,
    load_script_draft,
    lock_script,
    mark_segment_text_changed,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    mark_audio_stale_for_changed_segments,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def render_enhanced_folder_voiceovers_page() -> None:
    st.header("④ Folder Voice-overs / Skripterzeugung (Enhanced)")
    st.caption(
        "Redaktionelle Narration zu Geschichte, Besonderheiten und Atmosphäre — "
        "Assets sind visuelle Ressource, keine Inhaltsgrenze."
    )
    project = get_enhanced_project()
    if project is None:
        return

    draft = load_script_draft(project)
    locked = load_locked_script(project)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Skript erzeugen (LLM-Lauf 1)", type="primary"):
            try:
                with st.spinner("Skript wird erzeugt…"):
                    draft = generate_enhanced_script(project)
                st.success(
                    f"{len(draft.segments)} Segmente, "
                    f"{len(draft.visual_intents)} Visual Intents."
                )
                if draft.forbidden_phrases_found:
                    st.warning(
                        "Verbotene Bildbeschreibungs-Phrasen gefunden: "
                        + ", ".join(draft.forbidden_phrases_found)
                    )
                if looks_like_asset_inventory_script(draft):
                    st.warning("Skript wirkt noch zu assetgebunden — bitte prüfen.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Skripterzeugung fehlgeschlagen: {exc}")
    with col2:
        if st.button("Script Lock", disabled=draft is None):
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
    elif draft is not None:
        st.warning("Skript ist noch nicht gesperrt — ElevenLabs benötigt Script Lock.")

    show = locked or draft
    if show is None:
        st.info("Noch kein Skript vorhanden.")
        return

    st.subheader("Gesprochene Narration")
    st.write(show.narration_full)

    st.subheader("Segmente")
    for segment in show.segments:
        with st.expander(
            f"{segment.segment_id} · {segment.semantic_function}"
            + (" · fact_check_required" if segment.fact_check_required else "")
        ):
            new_text = st.text_area(
                "Text",
                value=segment.text,
                key=f"enh_seg_{project.id}_{segment.segment_id}",
            )
            if st.button(
                "Text speichern (macht Audio stale)",
                key=f"enh_seg_save_{project.id}_{segment.segment_id}",
            ):
                try:
                    mark_segment_text_changed(project, segment.segment_id, new_text)
                    mark_audio_stale_for_changed_segments(project)
                    st.warning(
                        "Segment geändert — Script Lock aufgehoben, Audio als stale markiert."
                    )
                    st.rerun()
                except ScriptLockError as exc:
                    st.error(str(exc))
            st.caption(
                f"Visual Intents: {', '.join(segment.visual_intent_ids) or '—'}"
            )

    st.subheader("Visual Intents (getrennt vom gesprochenen Text)")
    for intent in show.visual_intents:
        st.markdown(f"**{intent.intent_id}** — {intent.description}")

    if show.fact_check_hints:
        st.subheader("Fact-Check-Hinweise")
        for hint in show.fact_check_hints:
            st.warning(f"{hint.hint_id}: {hint.claim} ({hint.status})")

    if show.coverage_needs:
        st.subheader("Vorläufige Coverage Needs")
        for need in show.coverage_needs:
            st.caption(f"{need.need_id}: {need.subject} — {need.reason}")
