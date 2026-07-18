"""Streamlit page: Discovery V2 Visual Edit."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.feasibility_service import start_feasibility_check_run
from otio_app.discovery_v2.application.humanity_review_service import start_humanity_review_run
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    get_visual_edit_view,
    start_visual_edit_plan_run,
)
from otio_app.discovery_v2.application.visual_edit_repair_service import (
    apply_selected_repair_proposals,
    propose_editorial_repairs,
)
from otio_app.discovery_v2.ui.flash import discovery_ui_flash_and_rerun
from otio_app.discovery_v2.ui.overview import active_discovery_project


def render_discovery_visual_edit_page() -> None:
    st.title("Visual Edit")
    project = active_discovery_project()
    if project is None:
        return
    st.info(
        "Phase 12 nutzt ausschliesslich den lokalen FakeTextAdapter. "
        "Es wird kein OTIO erzeugt und kein echter Provider verwendet."
    )
    view = get_visual_edit_view(project)
    if not view.ok:
        st.warning(view.message or "Visual-Edit-Ansicht nicht verfuegbar.")
        return
    _render_input_status(view)
    _render_actions(project, view)
    _render_plan(view)
    _render_humanity(view)
    _render_feasibility(view)
    _render_repairs(view)


def _render_input_status(view) -> None:
    st.subheader("Inputstatus")
    gate = view.input_gate
    if gate is None:
        st.warning("Wirksamer Script Lock oder completed Narration Timeline fehlt.")
        return
    st.write(
        {
            "script_lock_id": gate.script_lock_id,
            "narration_timeline_id": gate.narration_timeline_id,
            "duration_seconds": round(gate.total_duration_seconds, 3),
            "frames": gate.total_frames,
            "input_fingerprint": gate.input_fingerprint,
        }
    )
    if view.active_run is not None:
        st.caption(
            f"Aktiver Visual-Edit-Run: `{view.active_run.run_id}` "
            f"({view.active_run.scope}/{view.active_run.status.value})"
        )


def _render_actions(project, view) -> None:
    st.subheader("Aktionen")
    if st.button(
        "Visual Edit Plan erzeugen",
        disabled=not view.can_start_plan,
        key="discovery_v2_visual_edit_start_plan",
    ):
        result = start_visual_edit_plan_run(project, sync=False)
        if result.started:
            discovery_ui_flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
    if st.button(
        "Humanity & Authenticity pruefen",
        disabled=not view.can_start_humanity,
        key="discovery_v2_visual_edit_start_humanity",
    ):
        result = start_humanity_review_run(project, sync=False)
        if result.ok:
            discovery_ui_flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
    if st.button(
        "Technische Machbarkeit pruefen",
        disabled=not view.can_start_feasibility,
        key="discovery_v2_visual_edit_start_feasibility",
    ):
        result = start_feasibility_check_run(project, sync=False)
        if result.ok:
            discovery_ui_flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
    if st.button(
        "Repair Proposals erzeugen",
        disabled=view.current_bundle is None,
        key="discovery_v2_visual_edit_propose_repairs",
    ):
        result = propose_editorial_repairs(project)
        if result.ok:
            discovery_ui_flash_and_rerun(result.message)
        else:
            st.warning(result.message)
    selected = [
        proposal.proposal_id
        for proposal in view.repair_proposals
        if getattr(proposal, "user_status", "") == "selected"
    ]
    if st.button(
        "Ausgewaehlte Reparaturen anwenden",
        disabled=not selected,
        key="discovery_v2_visual_edit_apply_repairs",
    ):
        result = apply_selected_repair_proposals(project, selected_proposal_ids=selected)
        if result.ok:
            discovery_ui_flash_and_rerun(result.message)
        else:
            st.warning(result.message)


def _render_plan(view) -> None:
    st.subheader("Visual Edit Plan")
    bundle = view.current_bundle
    if bundle is None:
        st.caption("Noch kein Visual Edit Plan.")
        return
    st.write(
        {
            "plan_id": bundle.plan.plan_id,
            "version": bundle.plan.plan_version,
            "status": bundle.plan.status,
            "shots": bundle.plan.total_shot_count,
        }
    )
    st.dataframe(
        [
            {
                "Ordinal": shot.ordinal,
                "Shot": shot.shot_id,
                "Function": shot.shot_function,
                "Strategy": shot.media_strategy,
                "Start": round(shot.timeline_start_seconds, 3),
                "End": round(shot.timeline_end_seconds, 3),
                "Sentences": ", ".join(shot.sentence_ids),
                "Entries": ", ".join(shot.narration_entry_ids),
                "Status": shot.status,
            }
            for shot in bundle.shots
        ],
        hide_index=True,
        use_container_width=True,
    )
    if bundle.assignments:
        st.markdown("**Assignments**")
        st.dataframe(
            [
                {
                    "Assignment": item.assignment_id,
                    "Shot": item.shot_id,
                    "Asset": item.asset_id,
                    "Working Media": item.working_media_id,
                    "Technical Shot": item.technical_shot_id,
                    "In": item.technical_source_in_seconds,
                    "Out": item.technical_source_out_seconds,
                    "Status": item.status,
                }
                for item in bundle.assignments
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_humanity(view) -> None:
    st.subheader("Humanity")
    if view.humanity_review is None:
        st.caption("Noch kein Humanity Review.")
        return
    st.write(
        {
            "review_id": view.humanity_review.review_id,
            "status": view.humanity_review.status,
            "judgment": view.humanity_review.overall_judgment,
        }
    )
    if view.humanity_findings:
        st.dataframe(
            [
                {
                    "Finding": finding.finding_id,
                    "Category": finding.category,
                    "Severity": finding.severity,
                    "Status": finding.user_status,
                    "Action": finding.recommended_action,
                }
                for finding in view.humanity_findings
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_feasibility(view) -> None:
    st.subheader("Feasibility")
    if view.feasibility_report is None:
        st.caption("Noch kein Feasibility Report.")
        return
    st.write(
        {
            "report_id": view.feasibility_report.report_id,
            "status": view.feasibility_report.status,
            "assessment": view.feasibility_report.overall_technical_assessment,
        }
    )
    if view.feasibility_issues:
        st.dataframe(
            [
                {
                    "Issue": issue.issue_id,
                    "Code": issue.error_code,
                    "Severity": issue.severity,
                    "Shot": issue.shot_id,
                    "Assignment": issue.assignment_id,
                    "Blocks": issue.blocks_phase_13,
                }
                for issue in view.feasibility_issues
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_repairs(view) -> None:
    st.subheader("Repair")
    if not view.repair_proposals:
        st.caption("Keine Repair Proposals.")
        return
    st.dataframe(
        [
            {
                "Proposal": proposal.proposal_id,
                "Source": proposal.source,
                "Type": proposal.repair_type,
                "Status": proposal.user_status,
                "Affected": ", ".join(proposal.affected_ids),
            }
            for proposal in view.repair_proposals
        ],
        hide_index=True,
        use_container_width=True,
    )


__all__ = ["render_discovery_visual_edit_page"]
