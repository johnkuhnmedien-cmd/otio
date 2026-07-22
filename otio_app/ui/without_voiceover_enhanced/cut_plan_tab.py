"""Schritt 7 Enhanced Cut Plan MVP — drei Aktionen hintereinander (R1)."""

from __future__ import annotations

import time

import streamlit as st

from otio_app.defaults import (
    ENHANCED_CUT_LLM_MODEL_CHOICES,
    ENHANCED_CUT_LLM_MODEL_LABELS,
    ENHANCED_FUNNEL_LLM_MODEL_CHOICES,
    ENHANCED_FUNNEL_LLM_MODEL_LABELS,
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
from otio_app.services.without_voiceover_enhanced.supplement_funnel_job import (
    JobStatus as FunnelJobStatus,
    get_supplement_funnel_job_manager,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    list_open_funnel_gap_ids,
)
from otio_app.ui.polling import poll_while_running
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
    StockCandidate,
    StockSearchResultsDocument,
    SupplementFunnelReport,
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
    supplement_funnel_report_path,
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
_STOCK_PASSAGE_LABEL_LEN = 110


def _gap_passage_map(coverage: CoverageGapsDocument | None) -> dict[str, str]:
    if coverage is None:
        return {}
    out: dict[str, str] = {}
    for gap in coverage.gaps:
        text = (
            (gap.needed_visual or "").strip()
            or (gap.subject or "").strip()
            or (gap.reason or "").strip()
        )
        out[gap.gap_id] = text
    return out


def _stock_candidate_checkbox_label(
    candidate: StockCandidate,
    gap_passages: dict[str, str],
    *,
    max_passage_len: int = _STOCK_PASSAGE_LABEL_LEN,
) -> str:
    """Vorschau: Textpassage + Gap statt Link/Titel-URL."""
    gap_id = (candidate.gap_id or "").strip()
    passage = (gap_passages.get(gap_id) or "").strip()
    if not passage:
        title = (candidate.title or "").strip()
        # URLs als Titel sind unbrauchbar — dann nur Gap zeigen.
        if title and not title.startswith(("http://", "https://")):
            passage = title
    if len(passage) > max_passage_len:
        passage = passage[: max_passage_len - 1].rstrip() + "…"
    gap_part = f"Gap {gap_id}" if gap_id else "Gap ?"
    passage_part = passage or "(keine Textpassage)"
    license_label = candidate.license or "unknown"
    return (
        f"{candidate.provider}: {passage_part} · {gap_part} "
        f"({candidate.media_type}, license={license_label})"
    )


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


def _render_lightweight_funnel_monitor(project) -> None:
    """Schlanke Seite während der Funnel läuft — Abbrechen ohne schweren Rerun."""
    mgr = get_supplement_funnel_job_manager()
    state = mgr.get_state(project.id)
    if state is None or state.status != FunnelJobStatus.RUNNING:
        return

    st.subheader("Supplement-Funnel läuft")
    st.progress(
        min(1.0, max(0.0, float(state.fraction))),
        text=(state.message or "Funnel läuft…")[:120],
    )
    st.info(state.message or "Funnel läuft im Hintergrund…")
    if state.model:
        st.caption(f"Modell: `{state.model}`")
    if state.gap_total:
        st.caption(
            f"Gap {state.gap_index}/{state.gap_total}"
            + (f" · `{state.gap_id}`" if state.gap_id else "")
        )

    if state.cancel_requested:
        st.warning(
            "Abbruch angefordert. Der aktuelle Gemini-/Download-Schritt "
            "(oft Thumbnail-Batch mit bis zu 10 Bildern) wird noch beendet — "
            "danach stoppt der Funnel. Bereits erfüllte Gaps bleiben."
        )
    else:
        st.caption(
            "Abbrechen wirkt nach dem laufenden LLM-/Download-Schritt, "
            "nicht mitten im API-Call."
        )

    cols = st.columns(2)
    with cols[0]:
        if st.button(
            "⏹ Funnel abbrechen",
            key=f"enh_funnel_cancel_lite_{project.id}",
            disabled=state.cancel_requested,
            type="primary",
        ):
            mgr.request_cancel(project.id)
            st.rerun()
    with cols[1]:
        if st.button(
            "🔄 Aktualisieren",
            key=f"enh_funnel_refresh_lite_{project.id}",
        ):
            st.rerun()

    if state.log_lines:
        with st.expander("Letzte Fortschrittszeilen", expanded=False):
            st.caption("\n".join(state.log_lines[-20:]))

    st.caption(
        "Leichte Ansicht während der Funnel läuft "
        "(Cut-Plan-Details ausgeblendet, damit Abbrechen schnell reagiert)."
    )
    # Kurzes Auto-Refresh, damit Stop ohne manuelles Klicken sichtbar wird.
    time.sleep(1.5)
    st.rerun()


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

    # Während der Funnel läuft: keine schweren JSON-/Widget-Reruns.
    funnel_mgr_early = get_supplement_funnel_job_manager()
    if funnel_mgr_early.is_running(project.id):
        _render_lightweight_funnel_monitor(project)
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
                max_candidates_per_gap=cut_options.max_candidates_per_gap,
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
    open_gap_ids_overview = list_open_funnel_gap_ids(project)
    total_gaps = len(coverage.gaps) if coverage is not None else 0
    open_gaps_count = len(open_gap_ids_overview)
    filled_gaps_count = max(0, total_gaps - open_gaps_count)
    if total_gaps > 0:
        st.info(
            f"Gaps: **offen {open_gaps_count}** · "
            f"**erfüllt {filled_gaps_count}** · "
            f"**gesamt {total_gaps}**"
        )

    if coverage is not None and coverage.gaps:
        with st.expander(
            (
                f"Coverage Gaps · gesamt {total_gaps} · "
                f"offen {open_gaps_count} · erfüllt {filled_gaps_count}"
            ),
            expanded=False,
        ):
            for gap in coverage.gaps:
                queries = gap.search_concepts or gap.search_queries
                is_open = gap.gap_id in open_gap_ids_overview
                status = "offen" if is_open else "erfüllt"
                st.caption(
                    f"{gap.gap_id}: {gap.needed_visual or gap.subject} · "
                    f"Status: {status} · queries={queries}"
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
            progress_bar = st.progress(0.0, text="Stocksuche startet…")
            status_box = st.empty()

            def _search_progress(fraction: float, message: str) -> None:
                progress_bar.progress(
                    min(1.0, max(0.0, float(fraction))),
                    text=(message or "Stocksuche…")[:120],
                )
                status_box.info(message)

            results = search_supplements_for_gaps(
                project,
                progress_callback=_search_progress,
            )
            progress_bar.progress(
                1.0,
                text=f"{len(results.candidates)} Kandidaten gefunden",
            )
            status_box.success(f"{len(results.candidates)} Kandidaten gefunden.")
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

        # Funnel-Auswahl VOR der langen Kandidaten-Checkboxliste — kompakt und
        # zuverlässig bedienbar (st.pills; st.multiselect ist in 1.59/BaseWeb
        # für echten Browser-Smoke ungeeignet).
        st.markdown("**Coverage Gaps automatisch auflösen**")
        open_gap_ids = list(open_gap_ids_overview)
        gap_by_id = {g.gap_id: g for g in (coverage.gaps if coverage else [])}
        select_key = f"enh_funnel_gap_multiselect_{project.id}"
        st.caption(
            f"Aktuell: offen **{len(open_gap_ids)}** · "
            f"erfüllt **{filled_gaps_count}** · "
            f"gesamt **{total_gaps}**"
        )

        funnel_settings = load_model_settings(project)
        funnel_role = funnel_settings.enhanced_supplement_funnel
        with st.expander("⚙️ Funnel-Modell (Text + Thumbnail)", expanded=False):
            funnel_updated = render_llm_model_selectbox(
                label="Funnel-Modell",
                role_settings=funnel_role,
                key=f"enh_funnel_model_{project.id}",
                input_info=LLM_INPUT_INFO["enhanced_supplement_funnel"],
                options=ENHANCED_FUNNEL_LLM_MODEL_CHOICES,
                labels=ENHANCED_FUNNEL_LLM_MODEL_LABELS,
                show_estimated_costs=True,
            )
            if st.button(
                "Funnel-Modell speichern",
                key=f"enh_funnel_model_save_{project.id}",
            ):
                save_model_settings(
                    project,
                    funnel_settings.model_copy(
                        update={"enhanced_supplement_funnel": funnel_updated}
                    ),
                )
                st.success("Funnel-Modell gespeichert.")
            st.caption(
                f"Aktiv: **{funnel_updated.model}** · "
                "Für günstige Tests: Gemini 3.1 Flash Lite."
            )
        funnel_model_id = funnel_updated.model

        # Veraltete R3-Checkbox-Keys bereinigen (einmalig / bei Reruns).
        for gap_id in list(gap_by_id):
            legacy_key = f"enh_funnel_gap_select_{project.id}_{gap_id}"
            if legacy_key in st.session_state:
                del st.session_state[legacy_key]

        selected_open_ids: list[str] = []
        if open_gap_ids:
            # Auswahl erst NACH Job-Ende bereinigen — nicht nach Widget-Erzeugung
            # (Streamlit verbietet session_state-Schreiben auf existierende Keys).
            pending_deselect_key = f"enh_funnel_pending_deselect_{project.id}"
            pending_filled = st.session_state.pop(pending_deselect_key, None)
            if pending_filled and select_key in st.session_state:
                filled = set(pending_filled)
                current_sel = st.session_state.get(select_key) or []
                if isinstance(current_sel, list):
                    st.session_state[select_key] = [
                        gid for gid in current_sel if gid not in filled
                    ]

            def _format_open_gap(gap_id: str) -> str:
                gap = gap_by_id.get(gap_id)
                visual = ""
                if gap is not None:
                    visual = (gap.needed_visual or gap.subject or "").strip()
                visual = visual or "—"
                if len(visual) > 80:
                    visual = visual[:77] + "…"
                return f"{gap_id} · {visual}"

            with st.expander(
                f"Offene Coverage Gaps auswählen · {len(open_gap_ids)}",
                expanded=False,
            ):
                selected_raw = st.pills(
                    "Offene Coverage Gaps auswählen",
                    options=open_gap_ids,
                    selection_mode="multi",
                    format_func=_format_open_gap,
                    key=select_key,
                    help="Nur offene Gaps. Erfüllte Gaps erscheinen nicht.",
                    label_visibility="collapsed",
                )
                open_set = set(open_gap_ids)
                if isinstance(selected_raw, list):
                    selected_open_ids = [
                        gid for gid in selected_raw if gid in open_set
                    ]
                elif selected_raw and str(selected_raw) in open_set:
                    selected_open_ids = [str(selected_raw)]
                else:
                    selected_open_ids = []
                if selected_open_ids:
                    st.caption(
                        "Ausgewählt: " + ", ".join(selected_open_ids[:12])
                        + ("…" if len(selected_open_ids) > 12 else "")
                    )
        else:
            st.info("Keine offenen Coverage Gaps.")
            if select_key in st.session_state:
                del st.session_state[select_key]

        funnel_job_mgr = get_supplement_funnel_job_manager()
        funnel_running = funnel_job_mgr.is_running(project.id)

        def _start_funnel_job(gap_ids: list[str]) -> None:
            started = funnel_job_mgr.start(
                project,
                gap_ids=gap_ids,
                model=funnel_model_id,
            )
            if not started:
                st.warning("Funnel läuft bereits — bitte Abbrechen oder warten.")
            st.rerun()

        def _render_funnel_job_panel() -> None:
            state = funnel_job_mgr.get_state(project.id)
            if state is None:
                return
            if state.status == FunnelJobStatus.RUNNING:
                st.progress(
                    min(1.0, max(0.0, float(state.fraction))),
                    text=(state.message or "Funnel läuft…")[:120],
                )
                st.info(state.message or "Funnel läuft im Hintergrund…")
                if state.model:
                    st.caption(f"Modell: `{state.model}`")
                if state.cancel_requested:
                    st.warning(
                        "Abbruch angefordert — aktueller LLM-/Download-Schritt "
                        "wird noch beendet, danach stoppt der Funnel."
                    )
                if st.button(
                    "⏹ Funnel abbrechen",
                    key=f"enh_funnel_cancel_{project.id}",
                    disabled=state.cancel_requested,
                    type="primary",
                ):
                    funnel_job_mgr.request_cancel(project.id)
                    st.rerun()
                if state.log_lines:
                    st.caption("\n".join(state.log_lines[-14:]))
                return

            if state.status == FunnelJobStatus.CANCELLED:
                st.warning(
                    state.message
                    or "Funnel abgebrochen. Bereits erfüllte Gaps bleiben erhalten."
                )
            elif state.status == FunnelJobStatus.FAILED:
                st.error(state.error or "Funnel fehlgeschlagen.")
            elif state.status == FunnelJobStatus.COMPLETED:
                st.success(state.message or "Funnel abgeschlossen.")

            # Erfüllte Gaps vorm nächsten Pills-Render bereinigen (nicht hier).
            if state.report is not None:
                st.session_state[f"enh_funnel_pending_deselect_{project.id}"] = list(
                    state.report.filled_gap_ids or []
                )
            if st.button(
                "Hinweis schließen",
                key=f"enh_funnel_dismiss_{project.id}",
            ):
                funnel_job_mgr.dismiss(project.id)
                st.rerun()

        funnel_state = funnel_job_mgr.get_state(project.id)
        if funnel_state is not None and funnel_state.status == FunnelJobStatus.RUNNING:
            poll_while_running(
                _render_funnel_job_panel,
                lambda: funnel_job_mgr.is_running(project.id),
                refresh_key=f"enh_funnel_poll_{project.id}",
            )
        elif funnel_state is not None:
            _render_funnel_job_panel()

        cols_funnel = st.columns(2)
        with cols_funnel[0]:
            all_disabled = (not open_gap_ids) or funnel_running
            if st.button(
                "Alle offenen Gaps automatisch auflösen",
                type="primary",
                key="enh_funnel_all_open",
                disabled=all_disabled,
                help=(
                    "Verarbeitet alle aktuell offenen Coverage Gaps sequenziell. "
                    "Mehrfachauswahl wird ignoriert. Läuft im Hintergrund — "
                    "Abbrechen möglich."
                ),
            ):
                # Auswahl bewusst ignorieren — frische Open-Liste.
                _start_funnel_job(list_open_funnel_gap_ids(project))
        with cols_funnel[1]:
            selected_disabled = (not selected_open_ids) or funnel_running
            if st.button(
                "Ausgewählte Gaps automatisch auflösen",
                key="enh_funnel_selected",
                disabled=selected_disabled,
                help=(
                    "Verarbeitet nur ausgewählte offene Gaps. "
                    "Gleicher Funnel-Service wie „Alle“. Läuft im Hintergrund."
                ),
            ):
                # Nur noch gültige offene IDs (Session kann veraltet sein).
                current_open = set(list_open_funnel_gap_ids(project))
                valid_selected = [
                    gid for gid in selected_open_ids if gid in current_open
                ]
                if not valid_selected:
                    st.warning("Keine gültige Gap-Auswahl.")
                else:
                    _start_funnel_job(valid_selected)

        candidate_count = len(results.candidates)
        # Wichtig: st.expander führt den Body trotzdem bei JEDEM Rerun aus.
        # 2000+ Checkboxen deshalb nur nach explizitem Opt-in rendern.
        show_manual_key = f"enh_show_manual_candidates_{project.id}"
        st.checkbox(
            f"Kandidaten manuell prüfen laden ({candidate_count})",
            key=show_manual_key,
            help=(
                "Standard aus — sonst werden bei jedem Modellwechsel "
                "alle Stock-Checkboxen neu gebaut (sehr langsam)."
            ),
        )
        if st.session_state.get(show_manual_key):
            with st.expander(
                f"Kandidaten manuell prüfen · {candidate_count}",
                expanded=True,
            ):
                gap_passages = _gap_passage_map(coverage)
                selected_ids: list[str] = []
                for index, candidate in enumerate(results.candidates):
                    checked = st.checkbox(
                        _stock_candidate_checkbox_label(candidate, gap_passages),
                        value=candidate.selected,
                        key=(
                            f"enh_stock_{project.id}_{index}_"
                            f"{candidate.candidate_id}"
                        ),
                    )
                    if checked:
                        selected_ids.append(candidate.candidate_id)

                cols_stock = st.columns(2)
                with cols_stock[0]:
                    if st.button("Auswahl akzeptieren", key="enh_accept_stock"):
                        try:
                            accepted = accept_supplement_candidates(
                                project, selected_ids
                            )
                            st.success(
                                f"{len(accepted.supplements)} Supplements "
                                "akzeptiert (manuell — ohne Download/LLM). "
                                "Lokale Datei ggf. darunter zuordnen."
                            )
                            st.rerun()
                        except CutPlanError as exc:
                            st.error(str(exc))
        else:
            st.caption(
                f"Manuelle Kandidatenliste ausgeblendet ({candidate_count}). "
                "Zum Prüfen Checkbox oben aktivieren."
            )

        funnel_report = load_model(
            supplement_funnel_report_path(project),
            SupplementFunnelReport,
        )
        if funnel_report is not None:
            filled_n = len(funnel_report.filled_gap_ids)
            open_n = len(funnel_report.open_gap_ids)
            show_report_key = f"enh_show_funnel_report_{project.id}"
            st.checkbox(
                (
                    f"Funnel-Abschlussdetails laden · erfüllt {filled_n} · "
                    f"offen {open_n}"
                ),
                key=show_report_key,
                help="Detailzeilen pro Gap nur bei Bedarf laden.",
            )
            if st.session_state.get(show_report_key):
                with st.expander("Funnel-Abschluss", expanded=True):
                    st.caption(funnel_report.message)
                    if funnel_report.llm_model:
                        st.caption(f"Modell: `{funnel_report.llm_model}`")
                    st.write(
                        f"Angefordert: **{len(funnel_report.requested_gap_ids)}** · "
                        f"erfüllt: **{filled_n}** · "
                        f"offen: **{open_n}** · "
                        f"Voll-Downloads: **{funnel_report.full_download_count}** · "
                        f"technisch ungültig: "
                        f"**{funnel_report.technically_invalid_count}** · "
                        f"Fallbacks: **{funnel_report.fallback_used_count}**"
                    )
                    for gap_rep in funnel_report.gaps:
                        ready = gap_rep.export_ready_candidate_id
                        license_label = ""
                        if gap_rep.license_metadata_status == "complete":
                            license_label = " · Lizenzdaten vollständig"
                        elif gap_rep.license_metadata_status == "partial":
                            license_label = " · Lizenzdaten teilweise vorhanden"
                        elif gap_rep.license_metadata_status == "missing":
                            license_label = " · Keine Lizenzmetadaten geliefert"
                        pool_label = ""
                        counts = (
                            getattr(gap_rep, "provider_candidate_counts", None) or {}
                        )
                        if counts:
                            parts = [
                                f"{name} {counts[name]}"
                                for name in sorted(counts)
                            ]
                            pool_label = (
                                f" · Pool {sum(int(v) for v in counts.values())}"
                                f"/{getattr(gap_rep, 'candidate_pool_limit', 20)}"
                                f" ({' · '.join(parts)})"
                            )
                        st.write(
                            f"`{gap_rep.gap_id}` · "
                            + (
                                f"export_ready `{ready}`{license_label}{pool_label}"
                                if ready
                                else ((gap_rep.message or "offen") + pool_label)
                            )
                        )
                    if funnel_report.open_gap_ids:
                        st.warning(
                            "Offene Gaps: "
                            + ", ".join(funnel_report.open_gap_ids[:12])
                        )

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        st.info(f"Akzeptiert: {len(accepted.supplements)} Supplements")
        show_local_key = f"enh_show_local_assign_{project.id}"
        st.checkbox(
            f"Lokale Dateizuordnung laden ({len(accepted.supplements)})",
            key=show_local_key,
            help="Optional — Funnel-Downloads sind meist schon export_ready.",
        )
        if st.session_state.get(show_local_key):
            with st.expander(
                f"Lokale Dateizuordnung (manuell) · {len(accepted.supplements)}",
                expanded=True,
            ):
                for supplement in accepted.supplements:
                    refreshed = refresh_supplement_validation(supplement)
                    st.write(
                        f"`{refreshed.candidate_id}` · "
                        f"status=`{refreshed.media_validation_status}`"
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
                                f"{updated.candidate_id} → "
                                f"{updated.media_validation_status}"
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
