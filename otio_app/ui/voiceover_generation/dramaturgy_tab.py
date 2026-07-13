"""Dramaturgieplanung über alle Ordner — Reihenfolge, Rollen, Bestätigung (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.defaults import (
    CHAPTER_MAP_IMAGE_SIZE_CHOICES,
    CHAPTER_MAP_IMAGE_SIZE_DEFAULT,
    CHAPTER_MAP_MODEL_CHOICES,
    CHAPTER_MAP_MODEL_DEFAULT,
    CHAPTER_MAP_MODEL_LABELS,
    CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_CHOICES,
    CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_DEFAULT,
    CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_LABELS,
    CHAPTER_MAP_OPENROUTER_UPSCALE_RESOLUTION_CHOICES,
    CHAPTER_MAP_OPENROUTER_UPSCALE_RESOLUTION_DEFAULT,
    CHAPTER_MAP_STATUS_PASS,
    CHAPTER_MAP_UPSCALER_CHOICES,
    CHAPTER_MAP_UPSCALER_DEFAULT,
    CHAPTER_MAP_UPSCALER_LABELS,
    CHAPTER_MAP_UPSCALER_OPENROUTER,
    DRAMATURGY_PLANNING_MODE_GEOGRAPHY,
    DRAMATURGY_PLANNING_MODE_LABELS,
    DRAMATURGY_PLANNING_MODE_VARIETY,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_chapter_maps_manifest_path,
    get_chapter_maps_style_refs_dir,
    get_dramaturgy_plan_confirmed_path,
    get_dramaturgy_plan_draft_path,
    get_folder_inventory_summaries_path,
    get_llm_run_dir,
)
from otio_app.services.inventory_loader import folder_has_usable_inventory_data
from otio_app.services.voiceover_generation.chapter_map_service import (
    delete_all_chapter_maps,
    delete_chapter_map,
    display_chapter_number,
    generate_all_chapter_maps,
    generate_single_chapter_map,
    import_style_examples_from_folder,
    load_chapter_map_manifest,
    load_chapter_map_settings,
    save_chapter_map_settings,
)
from otio_app.services.voiceover_generation.dramaturgy_service import (
    build_dramaturgy_plan,
    confirm_dramaturgy_plan,
    load_confirmed_dramaturgy,
    load_dramaturgy_draft,
    update_dramaturgy_order,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import DRAMATURGY_ROLES
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import (
    render_llm_model_selectbox,
    require_without_voiceover_mode,
    style_profile_metric_value,
)

# Höher als plan_llm_client.DEFAULT_MAX_OUTPUT_TOKENS — genug Spielraum für
# ~40 Kapitel-JSON, aber unter der alten 70k-Marke, die bei OpenAI/Gemini
# lange Idle-Verbindungen begünstigte. Unverbrauchtes Limit ändert die
# Antwortqualität nicht (Ceiling, kein Target). Anthropic-Calls darüber
# streamen automatisch (SDK-10-Minuten-Regel).
_DRAMATURGY_HIGH_MAX_OUTPUT_TOKENS = 32768


def _inventory_counts(project: Project) -> tuple[int, int]:
    """(Anzahl Ordner, Anzahl Ordner mit Inventory).

    Nutzt folder_has_usable_inventory_data() statt einer reinen Datei-
    Existenzprüfung — diese erkennt auch Ordner, deren flache Inventar-JSON
    von sync_folder_inventory_with_status() wieder gelöscht wurde, weil nicht
    ALLE Assets im Ordner als "grün" gelten, obwohl bereits erfolgreich
    analysierte Daten im Cache vorliegen (genau das, was die Dramaturgie-
    Planung selbst ohnehin verwendet)."""
    folders = project.selected_asset_subdirs
    with_inventory = sum(
        1 for name in folders if folder_has_usable_inventory_data(project, name)
    )
    return len(folders), with_inventory


def _render_prerequisites(project: Project) -> bool:
    brief = load_project_brief(project)
    profile = load_style_profile(project)
    folder_count, folders_with_inventory = _inventory_counts(project)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Project Brief", "✓" if (brief.video_title or brief.tone_tags) else "—")
    with col2:
        st.metric("Style Profile", style_profile_metric_value(profile))
    with col3:
        st.metric("Ordner erkannt", folder_count)
    with col4:
        st.metric("Mit Inventory", folders_with_inventory)

    if not brief.video_title and not brief.tone_tags:
        st.warning(
            "Kein Project Brief gefunden. Bitte zuerst unter „① Project Brief“ "
            "Titel/Ton/Regeln festlegen — die Dramaturgie funktioniert auch ohne, "
            "aber mit weniger Kontext."
        )
    if profile is None:
        st.warning(
            "Kein Style Profile gefunden. Bitte zuerst unter „② Style References“ "
            "ein Style Profile erstellen — die Dramaturgie kann auch ohne geplant "
            "werden, nutzt dann aber keinen abgeleiteten Stil."
        )

    can_plan = True
    if folder_count == 0:
        st.error("Keine Asset-Ordner im Projekt ausgewählt.")
        can_plan = False
    elif folders_with_inventory == 0:
        st.error(
            "Für keinen Ordner liegt ein Inventory vor. Bitte zuerst unter "
            "„① Analysen“ die Asset-Analyse ausführen."
        )
        can_plan = False
    elif folders_with_inventory < folder_count:
        st.warning(
            f"Nur {folders_with_inventory} von {folder_count} Ordnern haben ein "
            "Inventory — die übrigen werden ohne Analyse-Daten geplant."
        )
    return can_plan


def _render_model_settings(project: Project) -> tuple[str, str]:
    settings = load_model_settings(project)
    with st.expander("⚙️ Modell für Dramaturgie", expanded=False):
        role_settings = render_llm_model_selectbox(
            label="Modell",
            role_settings=settings.dramaturgy,
            key=f"vo_dramaturgy_model_{project.id}",
        )
        if st.button("Speichern", key=f"vo_dramaturgy_model_save_{project.id}"):
            updated = settings.model_copy(update={"dramaturgy": role_settings})
            save_model_settings(project, updated)
            st.success("Modell-Einstellung für Dramaturgie gespeichert.")
    return role_settings.provider, role_settings.model


def _plan_to_rows(plan) -> list[dict]:
    return [
        {
            "order_index": entry.order_index,
            "enabled": entry.enabled,
            "folder_name": entry.folder_name,
            "dramaturgy_role": entry.dramaturgy_role,
            "reason": entry.reason,
            "visual_strength_score": entry.visual_strength_score,
            "asset_diversity_score": entry.asset_diversity_score,
            "hook_potential_score": entry.hook_potential_score,
            "recommended_word_count": entry.recommended_word_count,
            "recommended_min_words": entry.recommended_min_words,
            "recommended_max_words": entry.recommended_max_words,
            "transition_goal_to_next": entry.transition_goal_to_next,
            "risks": ", ".join(entry.risks),
        }
        for entry in sorted(plan.recommended_folder_order, key=lambda entry: entry.order_index)
    ]


def _render_draft_editor(project: Project, draft) -> list[dict]:
    st.caption(f"Erzeugt: {draft.generated_at.isoformat()} · LLM-Run: `{draft.llm_run_id}`")
    if draft.core_promise:
        st.write(f"**Kernversprechen:** {draft.core_promise}")
    if draft.narrative_arc:
        st.write(f"**Erzählbogen:** {draft.narrative_arc}")
    if draft.global_transition_strategy:
        st.write(f"**Übergangsstrategie:** {draft.global_transition_strategy}")
    if draft.risks:
        st.caption(f"Risiken: {', '.join(draft.risks)}")

    rows = _plan_to_rows(draft)
    return st.data_editor(
        rows,
        key=f"vo_dramaturgy_editor_{project.id}",
        num_rows="fixed",
        use_container_width=True,
        column_config={
            "order_index": st.column_config.NumberColumn("Reihenfolge", min_value=1, step=1),
            "enabled": st.column_config.CheckboxColumn("Aktiv"),
            "folder_name": st.column_config.TextColumn("Ordner", disabled=True),
            "dramaturgy_role": st.column_config.SelectboxColumn(
                "Rolle", options=list(DRAMATURGY_ROLES)
            ),
            "reason": st.column_config.TextColumn("Begründung"),
            "visual_strength_score": st.column_config.NumberColumn(
                "Visuelle Stärke", disabled=True, format="%.2f"
            ),
            "asset_diversity_score": st.column_config.NumberColumn(
                "Vielfalt", disabled=True, format="%.2f"
            ),
            "hook_potential_score": st.column_config.NumberColumn(
                "Hook-Potenzial", disabled=True, format="%.2f"
            ),
            "recommended_word_count": st.column_config.NumberColumn(
                "Ziel-Wortanzahl", min_value=0, step=5
            ),
            "recommended_min_words": st.column_config.NumberColumn(
                "Min. Wörter", min_value=0, step=5
            ),
            "recommended_max_words": st.column_config.NumberColumn(
                "Max. Wörter", min_value=0, step=5
            ),
            "transition_goal_to_next": st.column_config.TextColumn("Übergang zum nächsten Ort"),
            "risks": st.column_config.TextColumn("Risiken", disabled=True),
        },
    )


def render_dramaturgy_page() -> None:
    st.header("③ Dramaturgie")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    st.subheader("Voraussetzungen")
    can_plan = _render_prerequisites(project)

    provider, model = _render_model_settings(project)

    confirmed = load_confirmed_dramaturgy(project)
    draft = load_dramaturgy_draft(project)

    if confirmed is not None:
        confirmed_at = confirmed.confirmed_at.isoformat() if confirmed.confirmed_at else "—"
        st.info(f"Es gibt bereits eine **bestätigte** Dramaturgie (bestätigt: {confirmed_at}).")

    st.subheader("Dramaturgie planen")
    st.caption(
        "Das LLM erhält nur die **Kapitel** (Ordnernamen + kurze Kapitel-Signale), "
        "keine einzelnen Asset-Beschreibungen."
    )
    col_geo, col_variety = st.columns(2)
    with col_geo:
        geo_clicked = st.button(
            DRAMATURGY_PLANNING_MODE_LABELS[DRAMATURGY_PLANNING_MODE_GEOGRAPHY],
            disabled=not can_plan,
            key=f"vo_dramaturgy_plan_geography_{project.id}",
        )
        st.caption(
            "Reihenfolge primär nach Geographie / sinnvollem Reiseverlauf. "
            f"max_tokens={_DRAMATURGY_HIGH_MAX_OUTPUT_TOKENS:,}."
        )
    with col_variety:
        variety_clicked = st.button(
            DRAMATURGY_PLANNING_MODE_LABELS[DRAMATURGY_PLANNING_MODE_VARIETY],
            disabled=not can_plan,
            key=f"vo_dramaturgy_plan_variety_{project.id}",
        )
        st.caption(
            "Reihenfolge für maximale Abwechslung und Kontraste zwischen Kapiteln. "
            f"max_tokens={_DRAMATURGY_HIGH_MAX_OUTPUT_TOKENS:,}."
        )

    build_kwargs: dict | None = None
    if geo_clicked:
        build_kwargs = {
            "planning_mode": DRAMATURGY_PLANNING_MODE_GEOGRAPHY,
            "max_output_tokens": _DRAMATURGY_HIGH_MAX_OUTPUT_TOKENS,
        }
    elif variety_clicked:
        build_kwargs = {
            "planning_mode": DRAMATURGY_PLANNING_MODE_VARIETY,
            "max_output_tokens": _DRAMATURGY_HIGH_MAX_OUTPUT_TOKENS,
        }

    if build_kwargs is not None:
        if confirmed is not None:
            st.info(
                "Es gibt bereits eine bestätigte Dramaturgie. Neuplanung erzeugt "
                "nur einen neuen Draft — der bestätigte Plan bleibt unverändert, "
                "bis du ihn explizit ersetzt."
            )
        with st.spinner("Dramaturgie wird geplant…"):
            result = build_dramaturgy_plan(project, provider=provider, model=model, **build_kwargs)
        st.session_state[f"vo_dramaturgy_last_result_{project.id}"] = {
            "status": result.status,
            "error": result.error,
            "llm_run_id": result.llm_run_id,
        }
        if result.status == STATUS_PASS:
            st.success("Dramaturgie-Draft erstellt.")
        else:
            st.error(f"Dramaturgie-Planung fehlgeschlagen ({result.status}): {result.error}")
        st.rerun()

    last_result = st.session_state.get(f"vo_dramaturgy_last_result_{project.id}")
    if last_result is not None and last_result.get("status") != STATUS_PASS:
        st.error(
            f"Letzter Versuch fehlgeschlagen ({last_result.get('status')}): "
            f"{last_result.get('error')}"
        )
        run_id = last_result.get("llm_run_id")
        if run_id:
            st.caption(f"LLM-Run: `{get_llm_run_dir(project.language_work_dir_path, run_id)}`")

    if draft is None:
        st.info("Noch kein Dramaturgie-Draft vorhanden.")
        st.caption(
            f"Ordner-Zusammenfassungen: `{get_folder_inventory_summaries_path(project.language_work_dir_path)}`"
        )
        return

    st.subheader("Dramaturgie-Draft")
    edited_rows = _render_draft_editor(project, draft)

    col_apply, col_sort, col_reload = st.columns(3)
    with col_apply:
        apply_clicked = st.button("Änderungen übernehmen", key=f"vo_dramaturgy_apply_{project.id}")
    with col_sort:
        sort_clicked = st.button(
            "Nach Reihenfolge sortieren", key=f"vo_dramaturgy_sort_{project.id}"
        )
    with col_reload:
        reload_clicked = st.button("Draft neu laden", key=f"vo_dramaturgy_reload_{project.id}")

    if reload_clicked:
        st.rerun()

    if sort_clicked:
        update_dramaturgy_order(
            project, sorted(edited_rows, key=lambda row: row["order_index"])
        )
        st.success("Reihenfolge sortiert und gespeichert.")
        st.rerun()

    if apply_clicked:
        update_dramaturgy_order(project, edited_rows)
        st.success("Änderungen übernommen.")
        st.rerun()

    st.subheader("Bestätigen")
    if confirmed is not None:
        st.warning(
            "Es gibt bereits eine bestätigte Dramaturgie. Neuplanung erzeugt nur "
            "einen neuen Draft."
        )
        confirm_label = "Neuen Draft bestätigen und bisherigen bestätigten Plan ersetzen"
    else:
        confirm_label = "Dramaturgie bestätigen"

    if st.button(confirm_label, type="primary", key=f"vo_dramaturgy_confirm_{project.id}"):
        updated_draft = update_dramaturgy_order(project, edited_rows)
        confirmed_plan = confirm_dramaturgy_plan(project, updated_draft)
        st.success("Dramaturgie bestätigt.")
        st.caption(f"Pfad: `{get_dramaturgy_plan_confirmed_path(project.language_work_dir_path)}`")
        with st.expander("Bestätigter Plan (JSON)"):
            st.json(confirmed_plan.model_dump(mode="json"))

    _render_chapter_maps_section(project)

    st.caption(f"Draft-Pfad: `{get_dramaturgy_plan_draft_path(project.language_work_dir_path)}`")
    st.caption(
        f"Ordner-Zusammenfassungen: `{get_folder_inventory_summaries_path(project.language_work_dir_path)}`"
    )


def _render_chapter_maps_section(project: Project) -> None:
    """Kapitel-Karten: Bulk + Einzelgenerierung nach bestätigter Dramaturgie."""
    st.subheader("Kapitel-Karten (Nano Banana)")
    confirmed = load_confirmed_dramaturgy(project)
    if confirmed is None:
        st.info(
            "Kapitel-Karten können erst nach **bestätigter** Dramaturgie erzeugt werden "
            "(stabile Reihenfolge nötig)."
        )
        return

    settings = load_chapter_map_settings(project)
    style_dir = get_chapter_maps_style_refs_dir(project.language_work_dir_path)

    with st.expander("Style-Referenzen & Einstellungen", expanded=True):
        st.caption(
            "Benötigt `EN_MAP_EXAMPLE_1.png` (erstes Kapitel, ein Pin) und "
            "`EN_MAP_EXAMPLE_2.png` (Folgekapitel mit Verbindungslinie). "
            "Ausgabe immer **16:9**. Text in der Projektsprache."
        )
        source_folder = st.text_input(
            "Map_example-Ordner (optional importieren)",
            value="",
            key=f"vo_chapter_maps_import_folder_{project.id}",
            placeholder="/Users/…/Map_example",
        )
        col_import, col_paths = st.columns([1, 2])
        with col_import:
            if st.button("Examples importieren", key=f"vo_chapter_maps_import_{project.id}"):
                if not source_folder.strip():
                    st.error("Bitte einen Ordnerpfad angeben.")
                else:
                    try:
                        settings = import_style_examples_from_folder(
                            project, Path(source_folder.strip())
                        )
                        st.success("Style-Examples importiert.")
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
        with col_paths:
            st.caption(f"Ablage: `{style_dir}`")
            st.caption(f"Example 1: `{settings.style_example_1_path or '—'}`")
            st.caption(f"Example 2: `{settings.style_example_2_path or '—'}`")

        uploaded = st.file_uploader(
            "Oder beide Examples hochladen (Reihenfolge: Example 1, dann Example 2)",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"vo_chapter_maps_upload_{project.id}",
        )
        if uploaded and len(uploaded) >= 2:
            if st.button("Uploads speichern", key=f"vo_chapter_maps_save_uploads_{project.id}"):
                from otio_app.defaults import (
                    CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME,
                    CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME,
                )

                style_dir.mkdir(parents=True, exist_ok=True)
                dest_1 = style_dir / CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME
                dest_2 = style_dir / CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME
                dest_1.write_bytes(uploaded[0].getvalue())
                dest_2.write_bytes(uploaded[1].getvalue())
                settings = save_chapter_map_settings(
                    project,
                    settings.model_copy(
                        update={
                            "style_example_1_path": str(dest_1),
                            "style_example_2_path": str(dest_2),
                        }
                    ),
                )
                st.success("Uploads gespeichert.")

        model_options = list(CHAPTER_MAP_MODEL_CHOICES)
        current_model = settings.model if settings.model in model_options else CHAPTER_MAP_MODEL_DEFAULT
        model_value = st.selectbox(
            "Gemini Image Modell",
            options=model_options,
            index=model_options.index(current_model),
            format_func=lambda value: CHAPTER_MAP_MODEL_LABELS.get(value, value),
            key=f"vo_chapter_maps_model_{project.id}",
            help=(
                "Standard: gemini-3.1-flash-image, danach Upscale über separate API. "
                "Pro nur wenn Pin-/Textqualität kritisch ist."
            ),
        )
        size_options = list(CHAPTER_MAP_IMAGE_SIZE_CHOICES)
        current_size = (
            settings.image_size if settings.image_size in size_options else CHAPTER_MAP_IMAGE_SIZE_DEFAULT
        )
        image_size_value = st.selectbox(
            "Bildqualität / Größe (Gemini)",
            options=size_options,
            index=size_options.index(current_size),
            key=f"vo_chapter_maps_image_size_{project.id}",
            help="2K = schärfere Gemini-Ausgabe vor dem Upscale. 1K = schneller/günstiger.",
        )
        upscaler_options = list(CHAPTER_MAP_UPSCALER_CHOICES)
        current_upscaler = (
            settings.upscaler if settings.upscaler in upscaler_options else CHAPTER_MAP_UPSCALER_DEFAULT
        )
        upscaler_value = st.selectbox(
            "Upscaler (nach Gemini)",
            options=upscaler_options,
            index=upscaler_options.index(current_upscaler),
            format_func=lambda value: CHAPTER_MAP_UPSCALER_LABELS.get(value, value),
            key=f"vo_chapter_maps_upscaler_{project.id}",
            help=(
                "OpenRouter: Image-to-Image @ 2K/4K (braucht OPENROUTER_API_KEY). "
                "OpenRouter hat kein reines ESRGAN — Upscale läuft als i2i-Enhancement."
            ),
        )
        openrouter_model_value = settings.openrouter_upscale_model
        openrouter_resolution_value = settings.openrouter_upscale_resolution
        if upscaler_value == CHAPTER_MAP_UPSCALER_OPENROUTER:
            or_model_options = list(CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_CHOICES)
            current_or_model = (
                settings.openrouter_upscale_model
                if settings.openrouter_upscale_model in or_model_options
                else CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_DEFAULT
            )
            openrouter_model_value = st.selectbox(
                "OpenRouter Upscale-Modell",
                options=or_model_options,
                index=or_model_options.index(current_or_model),
                format_func=lambda value: CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_LABELS.get(
                    value, value
                ),
                key=f"vo_chapter_maps_or_upscale_model_{project.id}",
            )
            or_res_options = list(CHAPTER_MAP_OPENROUTER_UPSCALE_RESOLUTION_CHOICES)
            current_or_res = (
                settings.openrouter_upscale_resolution
                if settings.openrouter_upscale_resolution in or_res_options
                else CHAPTER_MAP_OPENROUTER_UPSCALE_RESOLUTION_DEFAULT
            )
            openrouter_resolution_value = st.selectbox(
                "OpenRouter Ziel-Auflösung",
                options=or_res_options,
                index=or_res_options.index(current_or_res),
                key=f"vo_chapter_maps_or_upscale_res_{project.id}",
                help="2K reicht für 1920×1080 Timeline. 4K nur wenn das Modell es unterstützt.",
            )
        if st.button("Einstellungen speichern", key=f"vo_chapter_maps_save_settings_{project.id}"):
            settings = save_chapter_map_settings(
                project,
                settings.model_copy(
                    update={
                        "model": model_value.strip(),
                        "image_size": image_size_value,
                        "upscaler": upscaler_value,
                        "openrouter_upscale_model": openrouter_model_value.strip(),
                        "openrouter_upscale_resolution": openrouter_resolution_value.strip(),
                    }
                ),
            )
            st.success("Einstellungen gespeichert.")

    enabled = sorted(
        (entry for entry in confirmed.recommended_folder_order if entry.enabled),
        key=lambda entry: entry.order_index,
    )
    if not enabled:
        st.warning("Keine aktiven Kapitel in der bestätigten Dramaturgie.")
        return

    col_bulk, col_from, col_delete_all = st.columns([2, 1, 1])
    with col_from:
        start_index = st.number_input(
            "Bulk ab Index",
            min_value=1,
            max_value=max(entry.order_index for entry in enabled),
            value=1,
            step=1,
            key=f"vo_chapter_maps_start_{project.id}",
        )
    with col_bulk:
        bulk_clicked = st.button(
            "Alle Kapitel-Karten erzeugen (Bulk)",
            type="primary",
            key=f"vo_chapter_maps_bulk_{project.id}",
        )
        st.caption(
            "Sequentiell: jedes Bild nutzt das zuvor generierte als Referenz "
            "(außer Kapitel 1 → Example 1). Pro Kapitel nur der Sprung "
            "vorheriger Ort → neuer Ort (2 Pins). "
            "Anzeigezahl rückwärts: erstes Kapitel = N, letztes = 1."
        )
    with col_delete_all:
        delete_all_clicked = st.button(
            "Alle Karten löschen",
            key=f"vo_chapter_maps_delete_all_{project.id}",
        )

    if delete_all_clicked:
        delete_all_chapter_maps(project)
        st.success("Alle Kapitel-Karten gelöscht.")
        st.rerun()

    if bulk_clicked:
        progress = st.progress(0.0, text="Kapitel-Karten werden vorbereitet…")
        status_box = st.empty()

        def _on_progress(done: int, total: int, message: str) -> None:
            fraction = 0.0 if total <= 0 else min(1.0, done / total)
            progress.progress(fraction, text=f"{done}/{total} — {message}")
            status_box.caption(message)

        result = generate_all_chapter_maps(
            project,
            start_order_index=int(start_index),
            stop_on_error=True,
            progress_callback=_on_progress,
        )
        progress.progress(1.0, text="Bulk abgeschlossen.")
        if result.status == CHAPTER_MAP_STATUS_PASS:
            st.success(f"Bulk OK — {result.generated} Karte(n) erzeugt.")
        else:
            st.error(
                f"Bulk mit Fehlern — erzeugt: {result.generated}, fehlgeschlagen: {result.failed}."
            )
            for err in result.errors:
                st.caption(err)
        st.rerun()

    manifest = load_chapter_map_manifest(project)
    status_by_index = {entry.order_index: entry for entry in manifest.entries}

    st.markdown("**Einzelne Kapitel**")
    total_chapters = len(enabled)
    for entry in enabled:
        map_entry = status_by_index.get(entry.order_index)
        status_label = map_entry.status if map_entry is not None else "MISSING"
        shown = (
            map_entry.display_number
            if map_entry is not None and map_entry.display_number
            else display_chapter_number(
                order_index=entry.order_index, total_chapters=total_chapters
            )
        )
        cols = st.columns([3, 1, 1, 1])
        with cols[0]:
            st.write(
                f"{entry.order_index}. **{entry.folder_name}** — Karten-Zahl **{shown}** — `{status_label}`"
            )
            if map_entry is not None and map_entry.relative_path:
                st.caption(map_entry.relative_path)
            if map_entry is not None and map_entry.error:
                st.caption(f"Fehler: {map_entry.error}")
        with cols[1]:
            if (
                map_entry is not None
                and map_entry.status == CHAPTER_MAP_STATUS_PASS
                and map_entry.absolute_path
                and Path(map_entry.absolute_path).is_file()
            ):
                st.image(map_entry.absolute_path, use_container_width=True)
        with cols[2]:
            if st.button(
                "Nur dieses",
                key=f"vo_chapter_maps_single_{project.id}_{entry.order_index}",
            ):
                with st.spinner(f"Karte für {entry.folder_name}…"):
                    single = generate_single_chapter_map(
                        project, order_index=entry.order_index, invalidate_following=True
                    )
                if single.status == CHAPTER_MAP_STATUS_PASS:
                    st.success(f"Kapitel {entry.order_index} erzeugt.")
                    if entry.order_index < max(e.order_index for e in enabled):
                        st.info(
                            "Folgende Karten wurden als veraltet markiert — "
                            "bitte Bulk ab dem nächsten Index ausführen."
                        )
                else:
                    st.error(single.error or "Generierung fehlgeschlagen.")
                st.rerun()
        with cols[3]:
            if st.button(
                "Löschen",
                key=f"vo_chapter_maps_delete_{project.id}_{entry.order_index}",
            ):
                delete_chapter_map(
                    project, order_index=entry.order_index, invalidate_following=True
                )
                st.success(f"Kapitel {entry.order_index} gelöscht.")
                st.rerun()

    st.caption(f"Manifest: `{get_chapter_maps_manifest_path(project.language_work_dir_path)}`")
