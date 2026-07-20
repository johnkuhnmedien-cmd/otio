"""Schritt 7 Enhanced Cut Plan MVP — drei Aktionen hintereinander (R1)."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import (
    ENHANCED_CUT_LLM_MODEL_CHOICES,
    ENHANCED_CUT_LLM_MODEL_LABELS,
)
from otio_app.services.voiceover_generation.llm_pricing import (
    estimate_call_cost_usd,
    estimate_tokens_from_text,
    format_usd,
)
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    resolve_llm_model_id,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import LlmRoleSettings
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    accept_supplement_candidates,
    generate_final_cut_plan,
    generate_rough_cut_and_pauses,
    search_supplements_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    LocalMediaError,
    assign_local_media_path,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGapsDocument,
    FinalCutPlanDocument,
    NarrationTimelineDocument,
    RoughCutPlanDocument,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    final_cut_plan_path,
    narration_timeline_path,
    rough_cut_plan_path,
    script_locked_path,
    segment_timings_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    PROVIDER_UI_LABELS,
    SUPPORTED_STOCK_PROVIDERS,
    load_stock_providers_config,
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    resolve_final_timeline,
)
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_input_info,
    render_llm_model_selectbox,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project

_ROUGH_CUT_OUTPUT_DEFAULT = 16_384
_FINAL_CUT_OUTPUT_DEFAULT = 16_384
_OUTPUT_TOKENS_MIN = 2_048
_OUTPUT_TOKENS_MAX = 65_536
_OUTPUT_TOKENS_STEP = 1_024


def _estimate_path_tokens(path) -> int:
    if path is None or not path.is_file():
        return 0
    try:
        return estimate_tokens_from_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return 0


def _estimate_rough_cut_input_tokens(project) -> int:
    return (
        _estimate_path_tokens(script_locked_path(project))
        + _estimate_path_tokens(segment_timings_path(project))
        + 3_000  # style + dramaturgy + assets overhead
    )


def _estimate_final_cut_input_tokens(project) -> int:
    return (
        _estimate_path_tokens(script_locked_path(project))
        + _estimate_path_tokens(rough_cut_plan_path(project))
        + _estimate_path_tokens(narration_timeline_path(project))
        + 4_000  # pauses + assets + supplements + style overhead
    )


def _render_cost_caption(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_ceiling: int,
) -> None:
    estimate = estimate_call_cost_usd(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens_ceiling=output_ceiling,
    )
    st.caption(
        f"**Kostenschätzung** ({estimate.price.label}): "
        f"Input ≈ {estimate.input_tokens:,} Tok → {format_usd(estimate.input_cost_usd)} · "
        f"Output-Worst-Case {estimate.output_tokens_ceiling:,} Tok → "
        f"{format_usd(estimate.output_ceiling_cost_usd)} · "
        f"**Summe-Ceiling ≈ {format_usd(estimate.total_ceiling_usd)}**"
    )
    st.caption(
        "Hinweis: Abgerechnet werden nur tatsächlich erzeugte Tokens — "
        "nicht automatisch das volle Output-Limit."
    )


def _render_enhanced_cut_model(
    project,
    *,
    role_attr: str,
    label: str,
    key_prefix: str,
    input_info: str,
    input_tokens: int,
    default_output_tokens: int,
) -> tuple[str, str, int]:
    settings = load_model_settings(project)
    role_settings: LlmRoleSettings = getattr(settings, role_attr)
    with st.expander(f"⚙️ {label}", expanded=True):
        updated = render_llm_model_selectbox(
            label=label,
            role_settings=role_settings,
            key=f"{key_prefix}_model_{project.id}",
            input_info=input_info,
            options=ENHANCED_CUT_LLM_MODEL_CHOICES,
            labels=ENHANCED_CUT_LLM_MODEL_LABELS,
            show_estimated_costs=True,
        )
        if st.button("Modell speichern", key=f"{key_prefix}_model_save_{project.id}"):
            save_model_settings(
                project, settings.model_copy(update={role_attr: updated})
            )
            st.success(f"{label} gespeichert.")

        token_key = f"{key_prefix}_max_tokens_{project.id}"
        if token_key not in st.session_state:
            st.session_state[token_key] = default_output_tokens
        max_tokens = st.slider(
            "Max. Output-Tokens (Ceiling)",
            min_value=_OUTPUT_TOKENS_MIN,
            max_value=_OUTPUT_TOKENS_MAX,
            step=_OUTPUT_TOKENS_STEP,
            key=token_key,
            help=(
                "Obergrenze für die Antwortlänge. Du zahlst nur die tatsächlich "
                "erzeugten Output-Tokens — nicht automatisch das volle Limit."
            ),
        )
        _render_cost_caption(
            provider=updated.provider,
            model=updated.model,
            input_tokens=input_tokens,
            output_ceiling=int(max_tokens),
        )
    return updated.provider, updated.model, int(max_tokens)


def render_enhanced_cut_plan_page() -> None:
    st.header("⑦ Cut Plan (Enhanced MVP)")
    st.caption(
        "1) Grober Cut Plan + Pausen · 2) Supplements suchen/auswählen · "
        "3) Finaler Cut Plan + technische Auflösung. "
        "Kein Satz = ein Asset."
    )
    project = get_enhanced_project()
    if project is None:
        return

    st.subheader("1. Groben Cut Plan und Pausen erzeugen")
    rough_provider, rough_model, _rough_max = _render_enhanced_cut_model(
        project,
        role_attr="enhanced_rough_cut",
        label="Modell (LLM-Lauf 2)",
        key_prefix="enh_rough",
        input_info=LLM_INPUT_INFO["enhanced_rough_cut"],
        input_tokens=_estimate_rough_cut_input_tokens(project),
        default_output_tokens=_ROUGH_CUT_OUTPUT_DEFAULT,
    )
    if st.button("LLM-Lauf 2 starten", type="primary", key="enh_rough_cut"):
        try:
            with st.spinner(
                f"Pausen + grober Cut ({resolve_llm_model_id(rough_provider, rough_model)})…"
            ):
                rough, coverage = generate_rough_cut_and_pauses(
                    project,
                    provider=rough_provider,
                    model=rough_model,
                )
            st.success(
                f"{len(rough.shots)} Shots, {len(rough.pause_directives)} Pausen, "
                f"{len(coverage.gaps)} Coverage Gaps."
            )
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    timeline = load_model(narration_timeline_path(project), NarrationTimelineDocument)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if timeline is not None:
        st.write(
            f"Narrationstimeline: {timeline.total_duration_seconds:.2f}s · "
            f"{len(timeline.entries)} Segmente"
        )
        for entry in timeline.entries:
            st.caption(
                f"{entry.segment_id}: {entry.start_seconds:.2f}–{entry.end_seconds:.2f} "
                f"+ pause {entry.pause_after_seconds:.2f}s"
            )
    if rough is not None:
        st.write(f"Grober Plan: {len(rough.shots)} Shots")
        for shot in rough.shots:
            start = shot.start_anchor
            end = shot.end_anchor
            start_label = (
                f"pause after {start.after_segment_id}@{start.position}"
                if start.type == "pause"
                else f"{start.segment_id}@{start.position}"
            )
            end_label = (
                f"pause after {end.after_segment_id}@{end.position}"
                if end.type == "pause"
                else f"{end.segment_id}@{end.position}"
            )
            st.caption(
                f"{shot.shot_id}: {start_label}→{end_label} · "
                f"asset={shot.local_asset_id or shot.asset_id} · "
                f"fit={shot.asset_fit}"
            )
    if coverage is not None and coverage.gaps:
        st.write(f"Coverage Gaps: {len(coverage.gaps)}")
        for gap in coverage.gaps:
            queries = gap.search_concepts or gap.search_queries
            st.caption(
                f"{gap.gap_id}: {gap.needed_visual or gap.subject} · "
                f"queries={queries}"
            )

    st.divider()
    st.subheader("2. Supplements suchen und auswählen")

    st.markdown("**Stockanbieter verwenden:**")
    config = load_stock_providers_config(project)
    enabled_draft: dict[str, bool] = {}
    cols = st.columns(len(SUPPORTED_STOCK_PROVIDERS))
    for index, provider_name in enumerate(SUPPORTED_STOCK_PROVIDERS):
        current = config.providers[provider_name].enabled
        widget_key = f"enh_provider_{project.id}_{provider_name}"
        # Seed session_state once from disk. Do NOT pass value= on every rerun —
        # otherwise Save would re-apply the old disk value and discard the UI toggle.
        if widget_key not in st.session_state:
            st.session_state[widget_key] = current
        with cols[index]:
            enabled_draft[provider_name] = st.checkbox(
                PROVIDER_UI_LABELS[provider_name],
                key=widget_key,
            )
    if st.button("Anbieterauswahl speichern", key="enh_save_providers"):
        saved = save_stock_providers_config(project, enabled_draft)
        # Do not write checkbox keys here — Streamlit forbids mutating a
        # widget's session_state after the widget was instantiated. The
        # checkboxes already hold the saved values; persist + rerun is enough.
        st.success(
            "Anbieterauswahl gespeichert: "
            + ", ".join(
                f"{PROVIDER_UI_LABELS[n]}="
                f"{'an' if saved.providers[n].enabled else 'aus'}"
                for n in SUPPORTED_STOCK_PROVIDERS
            )
        )
        st.rerun()

    if st.button("Stock suchen", key="enh_stock_search"):
        try:
            with st.spinner("Aktive Stockanbieter werden abgefragt…"):
                results = search_supplements_for_gaps(project)
            if results.message:
                st.warning(results.message)
            else:
                st.success(f"{len(results.candidates)} Kandidaten gefunden.")
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if results is not None:
        if results.provider_status:
            st.caption(
                "Provider-Status: "
                + ", ".join(f"{k}={v}" for k, v in results.provider_status.items())
            )
        selected_ids: list[str] = []
        for candidate in results.candidates:
            checked = st.checkbox(
                f"{candidate.provider}: {candidate.title or candidate.candidate_id} "
                f"({candidate.media_type}, license={candidate.license})",
                value=candidate.selected,
                key=f"enh_stock_{project.id}_{candidate.candidate_id}",
            )
            if checked:
                selected_ids.append(candidate.candidate_id)
        if st.button("Auswahl akzeptieren", key="enh_accept_stock"):
            try:
                accepted = accept_supplement_candidates(project, selected_ids)
                st.success(f"{len(accepted.supplements)} Supplements akzeptiert.")
                st.rerun()
            except CutPlanError as exc:
                st.error(str(exc))

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        st.info(f"Akzeptiert: {len(accepted.supplements)} Supplements")
        st.markdown("**Lokale Dateizuordnung (manuell)**")
        for supplement in accepted.supplements:
            refreshed = refresh_supplement_validation(supplement)
            st.write(
                f"`{refreshed.candidate_id}` · status=`{refreshed.media_validation_status}`"
            )
            if refreshed.media_validation_error:
                st.caption(refreshed.media_validation_error)
            path_value = st.text_input(
                f"local_media_path für {refreshed.candidate_id}",
                value=refreshed.local_media_path or "",
                key=f"enh_local_{project.id}_{refreshed.candidate_id}",
                help="Lokaler Dateipfad — keine http(s)-URL.",
            )
            if st.button(
                f"Lokale Datei zuordnen & validieren ({refreshed.candidate_id})",
                key=f"enh_assign_{project.id}_{refreshed.candidate_id}",
            ):
                try:
                    updated = assign_local_media_path(
                        project, refreshed.candidate_id, path_value
                    )
                    st.success(
                        f"{updated.candidate_id} → {updated.media_validation_status}"
                    )
                    st.rerun()
                except LocalMediaError as exc:
                    st.error(str(exc))

    st.divider()
    st.subheader("3. Finalen Cut Plan erzeugen und technisch auflösen")
    final_provider, final_model, _final_max = _render_enhanced_cut_model(
        project,
        role_attr="enhanced_final_cut",
        label="Modell (LLM-Lauf 3)",
        key_prefix="enh_final",
        input_info=LLM_INPUT_INFO["enhanced_final_cut"],
        input_tokens=_estimate_final_cut_input_tokens(project),
        default_output_tokens=_FINAL_CUT_OUTPUT_DEFAULT,
    )
    if st.button("LLM-Lauf 3 + Python-Finalisierung", type="primary", key="enh_final_cut"):
        try:
            with st.spinner(
                f"Finaler redaktioneller Plan "
                f"({resolve_llm_model_id(final_provider, final_model)})…"
            ):
                final = generate_final_cut_plan(
                    project,
                    provider=final_provider,
                    model=final_model,
                )
            st.success(f"{len(final.shots)} finale Shots.")
            with st.spinner("Technische Auflösung…"):
                resolved = resolve_final_timeline(project)
            st.success(
                f"Timeline {resolved.total_duration_seconds:.2f}s · "
                f"{len(resolved.shots)} Shots · Reparaturen: {len(resolved.repairs)}"
            )
            if resolved.repairs:
                for repair in resolved.repairs:
                    st.caption(repair)
            st.rerun()
        except (CutPlanError, TimelineResolveError) as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    final = load_model(final_cut_plan_path(project), FinalCutPlanDocument)
    if final is not None:
        st.write(f"Finaler Plan: {len(final.shots)} Shots")
        for shot in final.shots:
            st.caption(
                f"{shot.shot_id}: {shot.asset_id} · "
                f"{shot.narration_start_anchor.segment_id}→"
                f"{shot.narration_end_anchor.segment_id}"
            )
