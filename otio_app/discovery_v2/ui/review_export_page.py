"""Streamlit page: Discovery V2 Review & Export."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.editorial_approval_service import (
    create_editorial_approval,
    get_review_export_view,
)
from otio_app.discovery_v2.application.export_validation_service import start_export_validation_run
from otio_app.discovery_v2.application.otio_export_service import start_otio_export_run
from otio_app.discovery_v2.domain.export import AcceptedExportRisk
from otio_app.discovery_v2.ui.overview import active_discovery_project


def render_discovery_review_export_page() -> None:
    st.title("Review & Export")
    project = active_discovery_project()
    if project is None:
        return
    st.info(
        "Phase 13 ist MANUAL: keine automatische Freigabe, keine echten Provider, "
        "kein Premiere/DaVinci/FCP-Export. OTIO wird nur nach explizitem Button erzeugt."
    )
    view = get_review_export_view(project)
    if not view.ok:
        st.warning(view.message or "Review-&-Export-Ansicht nicht verfuegbar.")
        return
    _render_preview(view)
    _render_approval(project, view)
    _render_validation(project, view)
    _render_export(project, view)


def _render_preview(view) -> None:
    st.subheader("Editorial Review")
    preview = view.preview
    if preview is None or not preview.ok:
        st.warning(f"Export-Gate blockiert: {', '.join(preview.blockers if preview else ['unknown'])}")
        return
    context = preview.context
    st.write(
        {
            "preview_fingerprint": preview.fingerprint,
            "visual_edit_plan_id": context.visual_bundle.plan.plan_id if context else None,
            "narration_timeline_id": context.narration_timeline.timeline_id if context else None,
            "humanity_review_id": context.humanity_bundle.review.review_id if context else None,
            "feasibility_report_id": context.feasibility_bundle.report.report_id if context else None,
            "expected_otio_tracks": "V1 Video + A1 Audio",
        }
    )
    if context is not None:
        st.dataframe(
            [
                {
                    "Ordinal": shot.ordinal,
                    "Shot": shot.shot_id,
                    "Strategy": shot.media_strategy,
                    "Start": shot.timeline_start_frame,
                    "End": shot.timeline_end_frame,
                    "Entries": ", ".join(shot.narration_entry_ids),
                }
                for shot in context.visual_bundle.shots
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_approval(project, view) -> None:
    st.subheader("Finale Editorial-Freigabe")
    approval = view.current_approval
    if approval is not None:
        st.write(
            {
                "approval_id": approval.approval_id,
                "status": approval.status.value,
                "revision": approval.revision,
                "fingerprint": approval.input_fingerprint,
            }
        )
    checked = st.checkbox(
        "Ich habe den aktuellen Plan und alle sichtbaren Risiken geprueft.",
        value=False,
        key="discovery_v2_export_approval_checked",
    )
    comment = st.text_area("Kommentar", key="discovery_v2_export_approval_comment")
    if st.button(
        "Finale Editorial-Freigabe erteilen",
        disabled=not view.can_approve,
        key="discovery_v2_export_approval_submit",
    ):
        risks = view.preview.context.visible_risks if view.preview and view.preview.context else []
        result = create_editorial_approval(
            project,
            confirmation_checked=checked,
            user_decision="approved",
            user_comment=comment,
            accepted_risks=risks,
            confirmed_fingerprint=view.preview.fingerprint if view.preview else None,
        )
        st.success(result.message) if result.ok else st.warning(result.message)
    if st.button(
        "Editorial-Freigabe ablehnen",
        disabled=not view.can_approve,
        key="discovery_v2_export_approval_reject",
    ):
        result = create_editorial_approval(
            project,
            confirmation_checked=False,
            user_decision="rejected",
            user_comment=comment,
            accepted_risks=[],
            confirmed_fingerprint=view.preview.fingerprint if view.preview else None,
        )
        st.success(result.message) if result.ok else st.warning(result.message)


def _render_validation(project, view) -> None:
    st.subheader("Export Validation")
    report = view.validation_report
    if report is not None:
        st.write(
            {
                "report_id": report.report_id,
                "status": report.status.value,
                "timebase": report.timebase,
                "issues": len(report.issues),
            }
        )
        if report.issues:
            st.dataframe(
                [
                    {
                        "Code": issue.error_code,
                        "Severity": issue.severity.value,
                        "Shot": issue.shot_id,
                        "Blocks": issue.blocks_export,
                        "Details": issue.technical_details,
                    }
                    for issue in report.issues
                ],
                hide_index=True,
                use_container_width=True,
            )
    if st.button(
        "Export validieren",
        disabled=not view.can_validate,
        key="discovery_v2_export_validate",
    ):
        result = start_export_validation_run(project, sync=True)
        st.success(result.message) if result.ok else st.warning(result.message)


def _render_export(project, view) -> None:
    st.subheader("OTIO Export")
    if view.active_export_run is not None:
        st.caption(
            f"Aktiver Export-Run: `{view.active_export_run.run_id}` "
            f"({view.active_export_run.status.value})"
        )
    if view.export_run is not None:
        st.write(
            {
                "run_id": view.export_run.run_id,
                "status": view.export_run.status.value,
                "output_relative_path": view.export_run.output_relative_path,
                "sha256": view.export_run.otio_sha256,
            }
        )
    if view.artifact is not None:
        st.write(
            {
                "artifact_id": view.artifact.artifact_id,
                "path": view.artifact.relative_path,
                "tracks": view.artifact.track_count,
                "clips": view.artifact.clip_count,
                "frames": view.artifact.total_frames,
                "timebase": view.artifact.timebase,
            }
        )
    if view.reparse_report is not None:
        st.write(
            {
                "reparse_report_id": view.reparse_report.report_id,
                "status": view.reparse_report.status.value,
                "semantically_equivalent": view.reparse_report.semantically_equivalent,
                "deviations": view.reparse_report.deviations,
            }
        )
    if st.button(
        "OTIO erzeugen",
        disabled=not view.can_export,
        key="discovery_v2_export_otio",
    ):
        result = start_otio_export_run(project, sync=False)
        st.success(result.message) if result.started else st.warning(result.message)


__all__ = ["render_discovery_review_export_page"]
