"""Streamlit-Seite: Discovery V2 Media Intake (Planung + Copy-Intake Phase 7B)."""

from __future__ import annotations

from collections import defaultdict

import streamlit as st

from otio_app.discovery_v2.application.copy_intake_service import (
    CopyIntakeServiceError,
    can_start_copy_intake,
    get_copy_intake_status,
    start_copy_intake,
)
from otio_app.discovery_v2.application.media_intake_planning_service import (
    MediaIntakePlanningServiceError,
    can_create_intake_plan,
    create_intake_plan,
    get_current_intake_plan,
)
from otio_app.discovery_v2.domain.media_intake import (
    ACTIVE_INTAKE_RUN_STATUSES,
    IntakeAction,
    IntakePlanStatus,
    IntakeRunAssetStatus,
)
from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.discovery_v2.ui.overview import active_discovery_project


_SESSION_ERROR_KEY = "discovery_v2_intake_error"
_SESSION_INFO_KEY = "discovery_v2_intake_info"


def render_discovery_media_intake_page() -> None:
    """Media Intake — Plan + Copy-Start nur über explizite Buttons."""
    st.title("Media Intake")
    project = active_discovery_project()
    if project is None:
        return

    st.caption(
        "Plant die technische Übernahme und kopiert ausschließlich als "
        "`copy` geplante Assets bytegenau nach Working Media. "
        "Remux und Transkodierung sind in dieser Phase nicht enthalten."
    )

    error = st.session_state.pop(_SESSION_ERROR_KEY, None)
    info = st.session_state.pop(_SESSION_INFO_KEY, None)
    if error:
        st.error(error)
    if info:
        st.info(info)

    ok, block_msg, ctx = can_create_intake_plan(project)
    plan, is_stale, plan_warn = get_current_intake_plan(project)
    if plan_warn:
        st.warning(plan_warn)

    st.subheader("Planungsbasis")
    if ctx is not None:
        st.write(f"**Import-ID:** `{ctx['import_id']}`")
        st.write(f"**Selection-ID:** `{ctx['selection_id']}`")
        st.write(f"**Scan-ID:** `{ctx['scan_id']}`")
        st.write(f"**Validation-Run-ID:** `{ctx['validation_run_id']}`")
        st.write(f"**Assets:** {ctx['asset_count']}")
        st.write(
            f"**Technisch erfolgreich:** {ctx['successful_assets']} · "
            f"**Technisch blockiert:** {ctx['blocked_assets']}"
        )
    else:
        st.warning(
            block_msg
            or "Keine gültige technische Validation für die aktuelle Auswahl."
        )

    if ok:
        if st.button(
            "Media-Intake-Plan erstellen",
            type="primary",
            key="discovery_v2_intake_plan_btn",
        ):
            try:
                result = create_intake_plan(project)
            except MediaIntakePlanningServiceError as exc:
                st.session_state[_SESSION_ERROR_KEY] = str(exc)
            else:
                if result.created:
                    st.session_state[_SESSION_INFO_KEY] = result.message
                else:
                    st.session_state[_SESSION_ERROR_KEY] = result.message
            st.rerun()
    else:
        st.caption(block_msg or "Planung derzeit nicht möglich.")

    plan, is_stale, _ = get_current_intake_plan(project)
    if plan is None:
        st.info("Noch kein Media-Intake-Plan vorhanden.")
        return

    if is_stale or plan.status == IntakePlanStatus.STALE:
        st.warning(
            "Der gespeicherte Plan ist veraltet (Selection, Import oder "
            "Validation-Run haben sich geändert). Historischer Plan bleibt "
            "erhalten — bitte bewusst einen neuen Plan erstellen."
        )

    st.subheader("Planvorschau")
    st.write(f"**Plan-ID:** `{plan.plan_id}`")
    st.write(f"**Status:** `{plan.status.value}`")
    st.write(f"**Validation-Run:** `{plan.validation_run_id}`")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Copy", plan.copy_count)
    with c2:
        st.metric("Remux", plan.remux_count)
    with c3:
        st.metric("Transcode", plan.transcode_count)
    with c4:
        st.metric("Blocked", plan.blocked_count)

    st.write(f"**Dublettenhinweise:** {plan.duplicate_warning_count}")

    root = get_discovery_v2_root(project.project_root_path)
    st.caption(
        f"Artefakt: `_otio_v2/intake/plans/{plan.plan_id}.json` · "
        f"Wurzel: `{root}`"
    )

    # --- Copy-Intake (Phase 7B) ---------------------------------------------
    st.subheader("Copy-Intake (Working Media)")
    copy_ok, copy_msg, copy_ctx = can_start_copy_intake(project)
    run, run_assets, working, status_err = get_copy_intake_status(project)
    if status_err:
        st.warning(status_err)

    if copy_ctx is not None:
        st.write(f"**Geplante Copy-Assets:** {copy_ctx.get('copy_item_count', 0)}")

    active = run is not None and run.status in ACTIVE_INTAKE_RUN_STATUSES
    can_click_copy = copy_ok and not active and not is_stale

    if can_click_copy:
        if st.button(
            "Copy-Intake starten",
            type="primary",
            key="discovery_v2_copy_intake_start_btn",
        ):
            try:
                result = start_copy_intake(project, sync=False)
            except CopyIntakeServiceError as exc:
                st.session_state[_SESSION_ERROR_KEY] = str(exc)
            else:
                if result.started:
                    st.session_state[_SESSION_INFO_KEY] = result.message
                else:
                    st.session_state[_SESSION_ERROR_KEY] = result.message
            st.rerun()
    else:
        st.caption(copy_msg or "Copy-Intake derzeit nicht startbar.")

    # Keine Remux-/Transcode-Startbuttons in Phase 7B.
    run, run_assets, working, _ = get_copy_intake_status(project)
    if run is None:
        st.info(
            "Noch kein Copy-Intake ausgeführt. "
            "Originalquellen bleiben unverändert, bis Copy-Intake bewusst gestartet wird."
        )
    else:
        st.write(f"**Copy-Run-ID:** `{run.run_id}`")
        st.write(f"**Status:** `{run.status.value}`")
        st.write(
            f"**Fortschritt:** {run.processed_assets} / {run.total_assets} · "
            f"OK={run.succeeded_assets} · Skip={run.skipped_assets} · "
            f"Fehler={run.failed_assets}"
        )
        if run.error_summary:
            st.error(run.error_summary)
        if run.status in ACTIVE_INTAKE_RUN_STATUSES:
            st.info(
                "Copy-Intake läuft … Seite neu laden, um den Fortschritt zu aktualisieren."
            )
        st.caption(
            f"Bericht: `_otio_v2/intake/runs/{run.run_id}.json` · "
            "Working Media: `_otio_v2/media/working/`"
        )

        if run_assets:
            st.markdown("**Copy-Ergebnisse**")
            for item in run_assets:
                label = item.status.value
                path = item.working_relative_path or "—"
                line = (
                    f"`{item.source_relative_path}` — **{label}** · "
                    f"working=`{path}`"
                )
                if item.status == IntakeRunAssetStatus.FAILED:
                    st.markdown(line)
                    if item.error_message:
                        st.caption(item.error_message)
                else:
                    st.markdown(line)

    if working:
        st.markdown(f"**Working Media ({len(working)})**")
        for wm in working[:50]:
            st.caption(
                f"`{wm.source_relative_path}` → `{wm.working_relative_path}` "
                f"(sha256={wm.output_sha256[:12]}…)"
            )

    by_group: dict[str, list] = defaultdict(list)
    for item in plan.items:
        by_group[item.source_group or "__root__"].append(item)

    st.subheader("Planpositionen nach Quellgruppe")
    if not plan.items:
        st.caption("Keine Planpositionen.")
        return

    for group_name in sorted(by_group.keys()):
        items = by_group[group_name]
        with st.expander(f"{group_name} ({len(items)})", expanded=False):
            for item in items:
                action = item.planned_action.value
                dup = ""
                if item.duplicate_group_id:
                    dup = " · Dublette (Hinweis)"
                line = (
                    f"`{item.source_relative_path}` — **{action}** "
                    f"({item.reason_code}){dup}"
                )
                st.markdown(line)
                details = [item.reason_detail]
                if item.video_codec:
                    details.append(f"codec={item.video_codec}")
                if item.pixel_format:
                    details.append(f"pix_fmt={item.pixel_format}")
                elif (item.media_kind or "").lower() == "video":
                    details.append("pix_fmt=null")
                if item.bit_depth is not None:
                    details.append(f"bit_depth={item.bit_depth}")
                elif (item.media_kind or "").lower() == "video":
                    details.append("bit_depth=null")
                if item.proposed_target_extension:
                    details.append(f"Ziel={item.proposed_target_extension}")
                if item.width and item.height:
                    details.append(f"{item.width}×{item.height}")
                st.caption(" · ".join(d for d in details if d))
