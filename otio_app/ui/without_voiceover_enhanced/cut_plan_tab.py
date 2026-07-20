"""Schritt 7 Enhanced Cut Plan MVP — drei Aktionen hintereinander (R1)."""

from __future__ import annotations

import streamlit as st

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
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


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
    render_llm_input_info(LLM_INPUT_INFO["enhanced_rough_cut"])
    if st.button("LLM-Lauf 2 starten", type="primary", key="enh_rough_cut"):
        try:
            with st.spinner("Pausen + grober Cut…"):
                rough, coverage = generate_rough_cut_and_pauses(project)
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
                st.success(
                    f"{len(results.candidates)} Kandidaten. "
                    f"Status: {results.provider_status}"
                )
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    selected_ids: list[str] = []
    if results is not None:
        if results.message:
            st.warning(results.message)
        st.write("**Provider-Status**")
        st.json(results.provider_status)
        for candidate in results.candidates:
            checked = st.checkbox(
                f"{candidate.candidate_id} · {candidate.provider} · "
                f"{candidate.title} · license={candidate.license}",
                value=candidate.selected,
                key=f"enh_stock_{project.id}_{candidate.candidate_id}",
            )
            if candidate.source_page:
                st.caption(candidate.source_page)
            if checked:
                selected_ids.append(candidate.candidate_id)
        if st.button("Auswahl akzeptieren", key="enh_accept_stock"):
            accepted = accept_supplement_candidates(project, selected_ids)
            st.success(
                f"{len(accepted.supplements)} Supplements akzeptiert "
                "(lokale Dateizuordnung noch erforderlich für Export)."
            )
            st.rerun()

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
    render_llm_input_info(LLM_INPUT_INFO["enhanced_final_cut"])
    if st.button("LLM-Lauf 3 + Python-Finalisierung", type="primary", key="enh_final_cut"):
        try:
            with st.spinner("Finaler redaktioneller Plan…"):
                final = generate_final_cut_plan(project)
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
