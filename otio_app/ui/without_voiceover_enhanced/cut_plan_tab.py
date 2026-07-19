"""Schritt 7 Enhanced Cut Plan MVP — drei Aktionen hintereinander."""

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
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    resolve_final_timeline,
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
            st.caption(
                f"{shot.shot_id}: {shot.narration_start_anchor.segment_id}→"
                f"{shot.narration_end_anchor.segment_id} · asset={shot.asset_id}"
            )
    if coverage is not None and coverage.gaps:
        st.write(f"Coverage Gaps: {len(coverage.gaps)}")
        for gap in coverage.gaps:
            st.caption(
                f"{gap.gap_id}: {gap.subject} · queries={gap.search_queries}"
            )

    st.divider()
    st.subheader("2. Supplements suchen und auswählen")
    if st.button("Stock suchen (5 Anbieter)", key="enh_stock_search"):
        try:
            with st.spinner("Pexels / Pixabay / Wikimedia / Openverse / Archive.org…"):
                results = search_supplements_for_gaps(project)
            st.success(
                f"{len(results.candidates)} Kandidaten. Provider: {results.provider_status}"
            )
            st.rerun()
        except CutPlanError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Fehler: {exc}")

    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    selected_ids: list[str] = []
    if results is not None:
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
            st.success(f"{len(accepted.supplements)} Supplements akzeptiert.")
            st.rerun()

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        st.info(f"Akzeptiert: {len(accepted.supplements)} Supplements")

    st.divider()
    st.subheader("3. Finalen Cut Plan erzeugen und technisch auflösen")
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
