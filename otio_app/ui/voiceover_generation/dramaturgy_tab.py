"""Dramaturgieplanung über alle Ordner — Reihenfolge, Rollen, Bestätigung (Phase 3)."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import (
    DRAMATURGY_PLANNING_MODE_GEOGRAPHY,
    DRAMATURGY_PLANNING_MODE_LABELS,
    DRAMATURGY_PLANNING_MODE_VARIETY,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_dramaturgy_plan_confirmed_path,
    get_dramaturgy_plan_draft_path,
    get_folder_inventory_summaries_path,
    get_llm_run_dir,
)
from otio_app.services.inventory_loader import folder_has_usable_inventory_data
from otio_app.services.voiceover_generation.dramaturgy_service import (
    build_dramaturgy_plan,
    confirm_dramaturgy_plan,
    disable_dramaturgy_craft_flags,
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
from otio_app.services.voiceover_generation.llm_pricing import (
    estimate_call_cost_usd,
    estimate_tokens_from_text,
    format_usd,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import (
    render_llm_model_selectbox,
    require_without_voiceover_mode,
    style_source_metric_value,
)

# Output-Token-Ceiling für Dramaturgie. Unverbrauchtes Limit kostet nichts
# (Ceiling, kein Target). Anthropic streamt oberhalb ~20k automatisch.
_DRAMATURGY_MAX_OUTPUT_TOKENS_MIN = 16_384
_DRAMATURGY_MAX_OUTPUT_TOKENS_MAX = 100_000
_DRAMATURGY_MAX_OUTPUT_TOKENS_DEFAULT = 100_000
_DRAMATURGY_MAX_OUTPUT_TOKENS_STEP = 4_096


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
    from otio_app.services.voiceover_generation.style_reference_service import (
        is_raw_style_mode,
        load_style_references,
    )

    brief = load_project_brief(project)
    profile = load_style_profile(project)
    refs = load_style_references(project)
    folder_count, folders_with_inventory = _inventory_counts(project)
    style_metric = style_source_metric_value(project, profile)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Project Brief", "✓" if (brief.video_title or brief.tone_tags) else "—")
    with col2:
        st.metric("Style", style_metric)
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
    if is_raw_style_mode(refs) and not refs.raw_reference_text.strip():
        st.warning(
            "Raw-Text-Modus aktiv, aber noch kein Text gespeichert — unter "
            "„② Style References“ Raw Text speichern."
        )
    elif not is_raw_style_mode(refs) and profile is None:
        st.warning(
            "Kein Style Profile gefunden. Unter „② Style References“ ein Style "
            "Profile erstellen — oder auf Raw Text umschalten. Die Dramaturgie "
            "kann auch ohne geplant werden, nutzt dann aber keinen Stilkontext."
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


def _estimate_dramaturgy_input_tokens(project: Project) -> int:
    """Grobe Input-Schätzung aus Brief + Ordner-Summaries (falls vorhanden)."""
    parts: list[str] = []
    brief = load_project_brief(project)
    parts.append(brief.model_dump_json())
    summaries_path = get_folder_inventory_summaries_path(project.language_work_dir_path)
    if summaries_path.is_file():
        try:
            parts.append(summaries_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    # Prompt-Rahmen / Instruktionen grob dazuaddieren
    overhead = 4_000
    return estimate_tokens_from_text("\n".join(parts)) + overhead


def _render_max_tokens_slider(
    project: Project,
    *,
    provider: str,
    model: str,
) -> int:
    slider_key = f"vo_dramaturgy_max_tokens_{project.id}"
    if slider_key not in st.session_state:
        st.session_state[slider_key] = _DRAMATURGY_MAX_OUTPUT_TOKENS_DEFAULT

    max_tokens = st.slider(
        "Max. Output-Tokens (Ceiling)",
        min_value=_DRAMATURGY_MAX_OUTPUT_TOKENS_MIN,
        max_value=_DRAMATURGY_MAX_OUTPUT_TOKENS_MAX,
        step=_DRAMATURGY_MAX_OUTPUT_TOKENS_STEP,
        key=slider_key,
        help=(
            "Obergrenze für die Antwortlänge. Du zahlst nur die tatsächlich "
            "erzeugten Output-Tokens — nicht automatisch das volle Limit."
        ),
    )

    input_tokens = _estimate_dramaturgy_input_tokens(project)
    estimate = estimate_call_cost_usd(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens_ceiling=int(max_tokens),
    )
    st.caption(
        f"**Kostenschätzung** ({estimate.price.label}): "
        f"Input ≈ {estimate.input_tokens:,} Tok → {format_usd(estimate.input_cost_usd)} · "
        f"Output-Worst-Case {estimate.output_tokens_ceiling:,} Tok → "
        f"{format_usd(estimate.output_ceiling_cost_usd)} · "
        f"**Summe-Ceiling ≈ {format_usd(estimate.total_ceiling_usd)}**"
    )
    st.caption(
        "Hinweis: Bei deinem vorherigen Abbruch bei 32 768 Tokens lag die echte "
        "Antwortlänge vermutlich knapp darüber — typisch zahlst du deutlich weniger "
        "als den Worst-Case bei 100 000."
    )
    return int(max_tokens)


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
            "use_transition_from_previous": entry.use_transition_from_previous,
            "use_transition_to_next": entry.use_transition_to_next,
            "use_callback_to_previous": entry.use_callback_to_previous,
            "use_contrast_with_previous": entry.use_contrast_with_previous,
            "use_commonality_with_previous": entry.use_commonality_with_previous,
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
            "use_transition_from_previous": st.column_config.CheckboxColumn(
                "↑ Übergang von vorher"
            ),
            "use_transition_to_next": st.column_config.CheckboxColumn(
                "Übergang zum nächsten Kapitel"
            ),
            "use_callback_to_previous": st.column_config.CheckboxColumn("Rückbezug"),
            "use_contrast_with_previous": st.column_config.CheckboxColumn("Kontrast"),
            "use_commonality_with_previous": st.column_config.CheckboxColumn("Gemeinsamkeit"),
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
    max_output_tokens = _render_max_tokens_slider(
        project, provider=provider, model=model
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
            f"Aktuelles Limit: max_tokens={max_output_tokens:,}."
        )
    with col_variety:
        variety_clicked = st.button(
            DRAMATURGY_PLANNING_MODE_LABELS[DRAMATURGY_PLANNING_MODE_VARIETY],
            disabled=not can_plan,
            key=f"vo_dramaturgy_plan_variety_{project.id}",
        )
        st.caption(
            "Reihenfolge für maximale Abwechslung und Kontraste zwischen Kapiteln. "
            f"Aktuelles Limit: max_tokens={max_output_tokens:,}."
        )

    build_kwargs: dict | None = None
    if geo_clicked:
        build_kwargs = {
            "planning_mode": DRAMATURGY_PLANNING_MODE_GEOGRAPHY,
            "max_output_tokens": max_output_tokens,
        }
    elif variety_clicked:
        build_kwargs = {
            "planning_mode": DRAMATURGY_PLANNING_MODE_VARIETY,
            "max_output_tokens": max_output_tokens,
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
    if draft.craft_flags_disabled:
        st.info(
            "Craft-Flags sind deaktiviert — Übergang/Rückbezug/Kontrast/Gemeinsamkeit "
            "werden an Folder-Voice-over-Prompts nicht mehr mitgegeben."
        )
    edited_rows = _render_draft_editor(project, draft)

    col_apply, col_sort, col_reload, col_clear = st.columns(4)
    with col_apply:
        apply_clicked = st.button("Änderungen übernehmen", key=f"vo_dramaturgy_apply_{project.id}")
    with col_sort:
        sort_clicked = st.button(
            "Nach Reihenfolge sortieren", key=f"vo_dramaturgy_sort_{project.id}"
        )
    with col_reload:
        reload_clicked = st.button("Draft neu laden", key=f"vo_dramaturgy_reload_{project.id}")
    with col_clear:
        clear_craft_clicked = st.button(
            "Craft-Kästchen deaktivieren",
            key=f"vo_dramaturgy_clear_craft_{project.id}",
            help=(
                "Setzt alle Übergang-/Rückbezug-/Kontrast-/Gemeinsamkeit-Flags "
                "auf aus und entfernt sie aus späteren Folder-Voice-over-Prompts."
            ),
        )
    if clear_craft_clicked:
        disable_dramaturgy_craft_flags(project)
        st.success(
            "Alle Craft-Kästchen deaktiviert. Folder-Voice-overs bekommen diese "
            "Parameter nicht mehr mit."
        )
        st.rerun()

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

    st.caption(f"Draft-Pfad: `{get_dramaturgy_plan_draft_path(project.language_work_dir_path)}`")
    st.caption(
        f"Ordner-Zusammenfassungen: `{get_folder_inventory_summaries_path(project.language_work_dir_path)}`"
    )
