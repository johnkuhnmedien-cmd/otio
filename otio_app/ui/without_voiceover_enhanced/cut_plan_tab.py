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
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    load_cut_plan_options,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    accept_supplement_candidates,
    generate_all_final_cuts,
    generate_all_rough_cuts,
    list_cut_plan_chapter_names,
    merge_and_persist_final_cuts,
    merge_and_persist_rough_cuts,
    search_supplements_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
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
    ResolvedTimelineDocument,
    RoughCutPlanDocument,
    StockSearchResultsDocument,
)
from otio_app.ui.without_voiceover_enhanced.timeline_view import render_realtime_timeline
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    final_cut_plan_path,
    narration_timeline_path,
    resolved_timeline_path,
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


def _cut_chapter_count(project) -> int:
    locked = load_locked_script(project)
    if locked is None:
        return 1
    names = list_cut_plan_chapter_names(project, locked)
    return max(1, len(names))


def _estimate_rough_cut_input_tokens(project) -> tuple[int, int]:
    """Returns (tokens_per_chapter_estimate, chapter_count)."""
    chapters = _cut_chapter_count(project)
    whole = (
        _estimate_path_tokens(script_locked_path(project))
        + _estimate_path_tokens(segment_timings_path(project))
        + 3_000  # style + dramaturgy + assets overhead
    )
    per_chapter = max(400, whole // chapters)
    return per_chapter, chapters


def _estimate_final_cut_input_tokens(project) -> tuple[int, int]:
    chapters = _cut_chapter_count(project)
    whole = (
        _estimate_path_tokens(script_locked_path(project))
        + _estimate_path_tokens(rough_cut_plan_path(project))
        + _estimate_path_tokens(narration_timeline_path(project))
        + 4_000  # pauses + assets + supplements + style overhead
    )
    per_chapter = max(400, whole // chapters)
    return per_chapter, chapters


def _render_cost_caption(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_ceiling: int,
    chapter_count: int = 1,
) -> None:
    estimate = estimate_call_cost_usd(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens_ceiling=output_ceiling,
    )
    total_input = estimate.input_cost_usd * chapter_count
    total_output = estimate.output_ceiling_cost_usd * chapter_count
    total = estimate.total_ceiling_usd * chapter_count
    st.caption(
        f"**Kostenschätzung** ({estimate.price.label}) · "
        f"{chapter_count} Kapitel-Call(s): "
        f"Input ≈ {estimate.input_tokens:,} Tok/Kap. → "
        f"{format_usd(estimate.input_cost_usd)} × {chapter_count} = "
        f"{format_usd(total_input)} · "
        f"Output-Worst-Case {estimate.output_tokens_ceiling:,} Tok/Kap. → "
        f"{format_usd(total_output)} · "
        f"**Summe-Ceiling ≈ {format_usd(total)}**"
    )
    st.caption(
        "Hinweis: Abgerechnet werden nur tatsächlich erzeugte Tokens — "
        "nicht automatisch das volle Output-Limit. "
        "Lauf 2/3: ein LLM-Call pro Dramaturgie-Kapitel."
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
    chapter_count: int = 1,
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
                "Obergrenze für die Antwortlänge pro Kapitel-Call. "
                "Du zahlst nur die tatsächlich erzeugten Output-Tokens — "
                "nicht automatisch das volle Limit."
            ),
        )
        _render_cost_caption(
            provider=updated.provider,
            model=updated.model,
            input_tokens=input_tokens,
            output_ceiling=int(max_tokens),
            chapter_count=chapter_count,
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
    rough_tokens, rough_chapters = _estimate_rough_cut_input_tokens(project)
    rough_provider, rough_model, _rough_max = _render_enhanced_cut_model(
        project,
        role_attr="enhanced_rough_cut",
        label="Modell (LLM-Lauf 2)",
        key_prefix="enh_rough",
        input_info=LLM_INPUT_INFO["enhanced_rough_cut"],
        input_tokens=rough_tokens,
        default_output_tokens=_ROUGH_CUT_OUTPUT_DEFAULT,
        chapter_count=rough_chapters,
    )
    st.caption(
        f"Lauf 2 läuft sequenziell: **ein LLM-Call pro Kapitel** "
        f"({rough_chapters} Kapitel)."
    )
    cut_options = load_cut_plan_options(project)
    frame_key = f"enh_rough_middle_frames_{project.id}"
    if frame_key not in st.session_state:
        st.session_state[frame_key] = cut_options.include_middle_frames
    include_middle_frames = st.checkbox(
        "Mittel-Frames der Asset-Analyse mitsenden (Vision)",
        key=frame_key,
        help=(
            "Optional: pro lokalem Asset das mittlere Analyse-Frame "
            "(bei 3 Frames: Mitte) an LLM-Lauf 2 senden. "
            "Hilft bei der Asset-Auswahl und visueller Abwechslung. "
            "Standard aus = bisheriger Text-Modus. "
            "Unterstützt Gemini und OpenAI (Terra/Sol)."
        ),
    )
    if include_middle_frames != cut_options.include_middle_frames:
        save_cut_plan_options(
            project,
            CutPlanOptions(
                include_middle_frames=include_middle_frames,
                max_middle_frames_per_chapter=cut_options.max_middle_frames_per_chapter,
            ),
        )
    if include_middle_frames:
        st.caption(
            "Vision aktiv: Beschreibungen + Mittel-Frames gehen in den Prompt "
            f"(max. {cut_options.max_middle_frames_per_chapter} Bilder/Kapitel). "
            "Mehr Tokens/Kosten als Text-only."
        )
    if st.button("LLM-Lauf 2 starten", type="primary", key="enh_rough_cut"):
        try:
            progress = st.empty()

            def _rough_progress(folder_name: str, index: int, total: int) -> None:
                progress.info(
                    f"LLM-Lauf 2 · Kapitel {index}/{total}: „{folder_name}“ "
                    f"({resolve_llm_model_id(rough_provider, rough_model)})…"
                )

            with st.spinner("Pausen + grober Cut — Kapitel nacheinander…"):
                results = generate_all_rough_cuts(
                    project,
                    provider=rough_provider,
                    model=rough_model,
                    progress_callback=_rough_progress,
                )
                rough, coverage = merge_and_persist_rough_cuts(project, results)
            progress.empty()
            ok = [r for r in results if r.status == "PASS"]
            fail = [r for r in results if r.status != "PASS"]
            st.success(
                f"{len(ok)}/{len(results)} Kapitel · {len(rough.shots)} Shots · "
                f"{len(rough.pause_directives)} Pausen · "
                f"{len(coverage.gaps)} Coverage Gaps."
            )
            for result in fail:
                st.error(f"„{result.folder_name}“: {result.error}")
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    timeline = load_model(narration_timeline_path(project), NarrationTimelineDocument)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    final_preview = load_model(final_cut_plan_path(project), FinalCutPlanDocument)
    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)

    render_realtime_timeline(
        narration_timeline=timeline,
        rough=rough,
        final=final_preview,
        resolved=resolved,
    )

    if rough is not None:
        with st.expander(
            f"Rough-Cut Details · {len(rough.shots)} Shots · "
            f"{len(rough.pause_directives)} Pausen",
            expanded=False,
        ):
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
        with st.expander(
            f"Coverage Gaps · {len(coverage.gaps)}",
            expanded=False,
        ):
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
            st.success(f"{len(results.candidates)} Kandidaten gefunden.")
            if results.message:
                st.warning(results.message)
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
    final_tokens, final_chapters = _estimate_final_cut_input_tokens(project)
    final_provider, final_model, _final_max = _render_enhanced_cut_model(
        project,
        role_attr="enhanced_final_cut",
        label="Modell (LLM-Lauf 3)",
        key_prefix="enh_final",
        input_info=LLM_INPUT_INFO["enhanced_final_cut"],
        input_tokens=final_tokens,
        default_output_tokens=_FINAL_CUT_OUTPUT_DEFAULT,
        chapter_count=final_chapters,
    )
    st.caption(
        f"Lauf 3 läuft sequenziell: **ein LLM-Call pro Kapitel** "
        f"({final_chapters} Kapitel), danach Python-Auflösung."
    )
    if st.button("LLM-Lauf 3 + Python-Finalisierung", type="primary", key="enh_final_cut"):
        try:
            progress = st.empty()

            def _final_progress(folder_name: str, index: int, total: int) -> None:
                progress.info(
                    f"LLM-Lauf 3 · Kapitel {index}/{total}: „{folder_name}“ "
                    f"({resolve_llm_model_id(final_provider, final_model)})…"
                )

            with st.spinner("Finaler Cut — Kapitel nacheinander…"):
                results = generate_all_final_cuts(
                    project,
                    provider=final_provider,
                    model=final_model,
                    progress_callback=_final_progress,
                )
                final = merge_and_persist_final_cuts(project, results)
            progress.empty()
            ok = [r for r in results if r.status == "PASS"]
            fail = [r for r in results if r.status != "PASS"]
            st.success(
                f"{len(ok)}/{len(results)} Kapitel · {len(final.shots)} finale Shots."
            )
            for result in fail:
                st.error(f"„{result.folder_name}“: {result.error}")
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
