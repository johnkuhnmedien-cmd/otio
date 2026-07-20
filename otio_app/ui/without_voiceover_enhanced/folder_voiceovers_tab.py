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
from otio_app.services.without_voiceover_enhanced.models import EnhancedScriptDocument
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    DEFAULT_ENHANCED_SCRIPT_MAX_OUTPUT_TOKENS,
    chapter_narration_text,
    folders_present_in_script,
    generate_all_enhanced_scripts,
    generate_enhanced_script_for_folder,
    group_segments_by_folder,
    list_enabled_dramaturgy_folders,
    looks_like_asset_inventory_script,
    segments_for_folder,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    load_locked_script,
    load_script_draft,
    lock_script,
    mark_segment_text_changed,
    update_folder_chapter_narration,
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
        f"{len(present)}/{len(entries)} Kapitel haben bereits ein Skript — "
        "jedes Kapitel wird getrennt erzeugt und später einzeln vertont."
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
            _run_chapter_generation(
                project,
                selected,
                provider=provider,
                model=model,
                max_tokens=max_tokens,
            )

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


def _word_count(text: str) -> int:
    return len([part for part in text.split() if part])


def _run_chapter_generation(
    project,
    folder_name: str,
    *,
    provider: str,
    model: str,
    max_tokens: int,
) -> None:
    with st.spinner(f"Skript für „{folder_name}“ wird erzeugt…"):
        result = generate_enhanced_script_for_folder(
            project,
            folder_name,
            provider=provider,
            model=model,
            max_output_tokens=max_tokens,
        )
    if result.status == "PASS":
        # Clear cached text widgets so the new narration shows immediately.
        text_key = f"enh_chapter_text_{project.id}_{folder_name}"
        st.session_state.pop(text_key, None)
        st.session_state.pop(f"{text_key}__src", None)
        st.success(
            f"„{folder_name}“ neu erzeugt: {result.segment_count} Segmente."
        )
        if result.document and looks_like_asset_inventory_script(result.document):
            st.warning("Skript wirkt noch zu assetgebunden — bitte prüfen.")
    else:
        st.error(f"„{folder_name}“ fehlgeschlagen: {result.error}")
    st.rerun()


def _render_chapter_scripts(
    project,
    document: EnhancedScriptDocument,
    *,
    editable: bool,
    provider: str,
    model: str,
    max_tokens: int,
) -> None:
    entries = list_enabled_dramaturgy_folders(project)
    folder_order = [entry.folder_name for entry in entries]
    role_by_folder = {
        entry.folder_name: entry.dramaturgy_role for entry in entries
    }
    order_by_folder = {entry.folder_name: entry.order_index for entry in entries}
    groups = group_segments_by_folder(document, folder_order=folder_order)
    present = {name for name, _ in groups if name}

    # Auch Dramaturgie-Kapitel ohne Skript anzeigen (zum ersten Erzeugen).
    for entry in entries:
        if entry.folder_name not in present:
            groups.append((entry.folder_name, []))

    if not groups and not entries:
        st.info("Noch kein Kapitel-Skript vorhanden.")
        return

    unassigned = [name for name, _ in groups if not name]
    if unassigned:
        st.warning(
            "Einige Segmente haben keine Kapitelzuordnung (älterer Monolith-Draft). "
            "Bitte Kapitel neu erzeugen — dann erscheinen sie getrennt wie im "
            "klassischen Without-Voice-Over-Modus."
        )

    st.subheader("Kapitel-Skripte")
    st.caption(
        "Ein Skript pro Dramaturgie-Kapitel — analog zur klassischen Folder-VO-Pipeline. "
        "Jedes Kapitel kann hier einzeln neu erzeugt werden."
    )

    for folder_name, segments in groups:
        label = folder_name or "(ohne Kapitelzuordnung)"
        role = role_by_folder.get(folder_name, "—")
        order_index = (
            order_by_folder.get(folder_name)
            if folder_name in order_by_folder
            else (segments[0].folder_order_index if segments else "—")
        )
        narration = (
            chapter_narration_text(document, folder_name)
            if folder_name and segments
            else (" ".join(seg.text for seg in segments) if segments else "")
        )
        words = _word_count(narration)
        status = "offen" if not segments else f"{words} Wörter · {len(segments)} Segment(e)"
        with st.expander(
            f"{order_index} · {label} · Rolle `{role}` · {status}",
            expanded=bool(folder_name and segments),
        ):
            if folder_name:
                col_regen, col_meta = st.columns([1, 3])
                with col_regen:
                    regen_label = (
                        "Kapitel neu erzeugen"
                        if segments
                        else "Kapitel erzeugen"
                    )
                    if st.button(
                        regen_label,
                        type="primary" if not segments else "secondary",
                        key=f"enh_chapter_regen_{project.id}_{folder_name}",
                    ):
                        _run_chapter_generation(
                            project,
                            folder_name,
                            provider=provider,
                            model=model,
                            max_tokens=max_tokens,
                        )
                with col_meta:
                    st.caption(
                        "Ersetzt nur dieses Kapitel (LLM-Call). "
                        "Andere Kapitel bleiben unverändert. Script Lock wird aufgehoben."
                    )

            if not segments:
                st.info("Noch kein Skript für dieses Kapitel.")
                continue

            if folder_name and editable:
                text_key = f"enh_chapter_text_{project.id}_{folder_name}"
                if text_key not in st.session_state:
                    st.session_state[text_key] = narration
                if st.session_state.get(f"{text_key}__src") != narration:
                    st.session_state[text_key] = narration
                    st.session_state[f"{text_key}__src"] = narration

                new_text = st.text_area(
                    "Voice-over-Text (Kapitel)",
                    key=text_key,
                    height=220,
                )
                if st.button(
                    "Kapitel-Text speichern",
                    key=f"enh_chapter_save_{project.id}_{folder_name}",
                ):
                    try:
                        update_folder_chapter_narration(
                            project, folder_name, new_text
                        )
                        mark_audio_stale_for_changed_segments(project)
                        st.success(
                            "Kapitel gespeichert — Script Lock aufgehoben "
                            "(ein Segment für dieses Kapitel)."
                        )
                        st.rerun()
                    except ScriptLockError as exc:
                        st.error(str(exc))
                st.caption(
                    f"{words} Wörter · {len(segments_for_folder(document, folder_name))} "
                    "Segment(e) · Vertonung unter ⑥ Audio pro Kapitel"
                )
            else:
                st.write(narration)
                st.caption(f"{words} Wörter · {len(segments)} Segment(e)")

            with st.expander("Segmente (Cut-Plan / Feinbearbeitung)", expanded=False):
                for segment in segments:
                    st.markdown(
                        f"**{segment.sequence_index}. `{segment.segment_id}`** "
                        f"({segment.semantic_function})"
                    )
                    if editable and folder_name:
                        seg_key = f"enh_seg_{project.id}_{segment.segment_id}"
                        seg_text = st.text_area(
                            "Segment-Text",
                            value=segment.text,
                            key=seg_key,
                            height=100,
                        )
                        if st.button(
                            "Segment speichern",
                            key=f"enh_seg_save_{project.id}_{segment.segment_id}",
                        ):
                            try:
                                mark_segment_text_changed(
                                    project, segment.segment_id, seg_text
                                )
                                mark_audio_stale_for_changed_segments(project)
                                st.success(
                                    "Segment aktualisiert — Script Lock aufgehoben."
                                )
                                st.rerun()
                            except ScriptLockError as exc:
                                st.error(str(exc))
                    else:
                        st.write(segment.text)

            chapter_intents = [
                intent
                for intent in document.visual_intents
                if intent.folder_name == folder_name
                or (
                    not intent.folder_name
                    and any(
                        intent.intent_id in seg.visual_intent_ids for seg in segments
                    )
                )
            ]
            if chapter_intents:
                st.markdown("**Visual Intents**")
                for intent in chapter_intents:
                    st.caption(
                        f"`{intent.intent_id}`: {intent.description} "
                        f"({intent.preferred_media_type})"
                    )


def render_enhanced_folder_voiceovers_page() -> None:
    st.header("④ Folder Voice-overs / Skripterzeugung (Enhanced)")
    st.caption(
        "Redaktionelle Narration pro Dramaturgie-Kapitel — "
        "ein LLM-Call und ein Skript pro Kapitel (wie in der klassischen Pipeline). "
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

    show = locked or draft or EnhancedScriptDocument(script_status="draft")

    if show.forbidden_phrases_found:
        real = [
            phrase
            for phrase in show.forbidden_phrases_found
            if not phrase.startswith("PARTIAL_FAIL:")
        ]
        if real:
            st.warning("Verbotene Phrasen: " + ", ".join(real))

    _render_chapter_scripts(
        project,
        show,
        editable=True,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
    )

    if show.segments:
        with st.expander(
            "Gesamtfilm (nur abgeleitet, nicht die Arbeitsansicht)",
            expanded=False,
        ):
            st.caption(
                "Zusammengehängte Narration aller Kapitel — nur zur Kontrolle. "
                "Arbeits- und Vertonungseinheit ist das einzelne Kapitel."
            )
            st.write(show.narration_full)
