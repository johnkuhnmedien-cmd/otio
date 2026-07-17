"""Streamlit-Seite: Discovery V2 Media Intake (Plan + Copy + Remux)."""

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
from otio_app.discovery_v2.application.remux_intake_service import (
    RemuxIntakeServiceError,
    can_start_remux_intake,
    get_remux_intake_status,
    list_open_remux_plan_items,
    start_remux_intake,
)
from otio_app.discovery_v2.application.video_transcode_service import (
    VideoTranscodeServiceError,
    can_start_video_transcode_intake,
    format_rotation_display,
    get_video_transcode_status,
    list_video_transcode_plan_item_views,
    start_video_transcode_intake,
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
    """Media Intake — Plan + Copy/Remux nur über explizite Buttons."""
    st.title("Media Intake")
    project = active_discovery_project()
    if project is None:
        return

    st.caption(
        "Plant die technische Übernahme. Copy übernimmt bytegenau; "
        "Remux ändert nur den Container per Stream Copy; "
        "Video-Transkodierung wandelt nach H.264/yuv420p/8-Bit um. "
        "Bildkonvertierung ist hier nicht enthalten."
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

    remux_run, _, _, _ = get_remux_intake_status(project)
    vt_run, _, _, _ = get_video_transcode_status(project)
    any_active = (
        (run is not None and run.status in ACTIVE_INTAKE_RUN_STATUSES)
        or (remux_run is not None and remux_run.status in ACTIVE_INTAKE_RUN_STATUSES)
        or (vt_run is not None and vt_run.status in ACTIVE_INTAKE_RUN_STATUSES)
    )
    can_click_copy = copy_ok and not any_active and not is_stale

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
        st.markdown(f"**Copy Working Media ({len(working)})**")
        for wm in working[:50]:
            st.caption(
                f"`{wm.source_relative_path}` → `{wm.working_relative_path}` "
                f"(sha256={wm.output_sha256[:12]}…)"
            )

    # --- Remux-Intake (Phase 7C1) -------------------------------------------
    st.subheader("Remux")
    st.caption(
        "Beim Remux wird nur der Container geändert. Video und unterstütztes "
        "Audio werden nicht neu codiert."
    )
    remux_ok, remux_msg, remux_ctx = can_start_remux_intake(project)
    remux_items = list_open_remux_plan_items(project)
    remux_run, remux_assets, remux_working, remux_status_err = get_remux_intake_status(
        project
    )
    if remux_status_err:
        st.warning(remux_status_err)

    if remux_ctx is not None:
        st.write(
            f"**Offene Remux-Items:** {remux_ctx.get('remux_item_count', 0)} · "
            f"**Gate OK:** {remux_ctx.get('eligible_remux_item_count', 0)}"
        )

    if remux_items:
        st.markdown("**Offene Remux-Positionen**")
        for item in remux_items:
            tc = item.embedded_timecode or "null"
            st.markdown(
                f"`{item.source_relative_path}` · Gruppe=`{item.source_group}` · "
                f"Container=`{item.container_format or item.extension}` · "
                f"codec=`{item.video_codec}` · pix=`{item.pixel_format}` · "
                f"bit=`{item.bit_depth}` · audio=`{item.audio_codec or '—'}` · "
                f"tc=`{tc}`"
            )
            st.caption(f"{item.reason_code}: {item.reason_detail}")
    else:
        st.caption("Keine offenen Remux-Items im aktuellen Plan.")

    remux_active = (
        remux_run is not None and remux_run.status in ACTIVE_INTAKE_RUN_STATUSES
    )
    copy_active = run is not None and run.status in ACTIVE_INTAKE_RUN_STATUSES
    vt_active = vt_run is not None and vt_run.status in ACTIVE_INTAKE_RUN_STATUSES
    can_click_remux = (
        remux_ok and not remux_active and not copy_active and not vt_active and not is_stale
    )

    if can_click_remux:
        if st.button(
            "Remux-Intake starten",
            type="primary",
            key="discovery_v2_remux_intake_start_btn",
        ):
            try:
                result = start_remux_intake(project, sync=False)
            except RemuxIntakeServiceError as exc:
                st.session_state[_SESSION_ERROR_KEY] = str(exc)
            else:
                if result.started:
                    st.session_state[_SESSION_INFO_KEY] = result.message
                else:
                    st.session_state[_SESSION_ERROR_KEY] = result.message
            st.rerun()
    else:
        st.caption(remux_msg or "Remux-Intake derzeit nicht startbar.")

    if remux_run is not None:
        st.write(f"**Remux-Run-ID:** `{remux_run.run_id}`")
        st.write(f"**Scope:** `{remux_run.scope}` · **Status:** `{remux_run.status.value}`")
        st.write(
            f"**Fortschritt:** {remux_run.processed_assets} / {remux_run.total_assets} · "
            f"OK={remux_run.succeeded_assets} · Skip={remux_run.skipped_assets} · "
            f"Fehler={remux_run.failed_assets}"
        )
        if remux_run.error_summary:
            st.error(remux_run.error_summary)
        if remux_run.status in ACTIVE_INTAKE_RUN_STATUSES:
            st.info(
                "Remux-Intake läuft … Seite neu laden, um den Fortschritt zu aktualisieren."
            )
        st.caption(f"Bericht: `_otio_v2/intake/runs/{remux_run.run_id}.json`")
        if remux_assets:
            st.markdown("**Remux-Ergebnisse**")
            for item in remux_assets:
                path = item.working_relative_path or "—"
                line = (
                    f"`{item.source_relative_path}` — **{item.status.value}** · "
                    f"working=`{path}`"
                )
                st.markdown(line)
                if item.error_code or item.error_message:
                    st.caption(
                        f"{item.error_code or ''}: {item.error_message or ''}".strip(": ")
                    )

    if remux_working:
        st.markdown(f"**Remux Working Media ({len(remux_working)})**")
        for wm in remux_working[:50]:
            st.caption(
                f"`{wm.source_relative_path}` → `{wm.working_relative_path}` "
                f"(sha256={wm.output_sha256[:12]}…)"
            )

    # --- Video-Transkodierung (Phase 7C2) -----------------------------------
    st.subheader("Video-Transkodierung")
    st.caption(
        "Video wird technisch nach H.264/yuv420p/8-Bit umgewandelt. "
        "Auflösung und Seitenverhältnis bleiben erhalten. Es erfolgt kein "
        "Crop, Zoom oder automatisches 16:9."
    )
    vt_ok, vt_msg, vt_ctx = can_start_video_transcode_intake(project)
    vt_views = list_video_transcode_plan_item_views(project)
    vt_run, vt_assets, vt_working, vt_status_err = get_video_transcode_status(project)
    if vt_status_err:
        st.warning(vt_status_err)
    if vt_ctx is not None:
        st.write(
            f"**Offene Video-Transcode-Items:** "
            f"{vt_ctx.get('video_transcode_item_count', 0)}"
        )
    if vt_views:
        st.markdown("**Offene Video-Transcode-Positionen**")
        for view in vt_views:
            item = view.item
            fps = None
            if item.frame_rate_numerator and item.frame_rate_denominator:
                fps = f"{item.frame_rate_numerator}/{item.frame_rate_denominator}"
            audio_streams = (
                str(view.audio_stream_count)
                if view.audio_stream_count is not None
                else "—"
            )
            channels = (
                str(view.audio_channels) if view.audio_channels is not None else "—"
            )
            rotation = format_rotation_display(view.rotation_degrees)
            st.markdown(
                f"`{item.source_relative_path}` · "
                f"Container=`{item.container_format or item.extension}` · "
                f"codec=`{item.video_codec}` · pix=`{item.pixel_format}` · "
                f"bit=`{item.bit_depth}` · "
                f"{item.width}×{item.height} · fps=`{fps or '—'}` · "
                f"audio_codec=`{item.audio_codec or '—'}` · "
                f"Audio: {audio_streams} Stream"
                f"{'' if audio_streams == '1' else 's'}, "
                f"{channels} Kanäle · "
                f"tc=`{item.embedded_timecode or 'null'}` · "
                f"Rotation: {rotation}"
            )
            st.caption(
                f"Block-/Planungsgrund: {item.reason_code}: {item.reason_detail}"
            )
    else:
        st.caption("Keine offenen Video-Transcode-Items im aktuellen Plan.")

    can_click_vt = vt_ok and not any_active and not is_stale
    if can_click_vt:
        if st.button(
            "Video-Transkodierung starten",
            type="primary",
            key="discovery_v2_video_transcode_start_btn",
        ):
            try:
                result = start_video_transcode_intake(project, sync=False)
            except VideoTranscodeServiceError as exc:
                st.session_state[_SESSION_ERROR_KEY] = str(exc)
            else:
                if result.started:
                    st.session_state[_SESSION_INFO_KEY] = result.message
                else:
                    st.session_state[_SESSION_ERROR_KEY] = result.message
            st.rerun()
    else:
        st.caption(vt_msg or "Video-Transkodierung derzeit nicht startbar.")

    if vt_run is not None:
        st.write(f"**Video-Transcode-Run-ID:** `{vt_run.run_id}`")
        st.write(
            f"**Scope:** `{vt_run.scope}` · **Status:** `{vt_run.status.value}`"
        )
        st.write(
            f"**Fortschritt:** {vt_run.processed_assets} / {vt_run.total_assets} · "
            f"transcoded={vt_run.transcoded_assets} · "
            f"reused={vt_run.reused_assets} · "
            f"remuxed={vt_run.remuxed_assets} · "
            f"Skip={vt_run.skipped_assets} · "
            f"Fehler={vt_run.failed_assets}"
        )
        if vt_run.error_summary:
            st.error(vt_run.error_summary)
        if vt_run.status in ACTIVE_INTAKE_RUN_STATUSES:
            st.info(
                "Video-Transkodierung läuft … Seite neu laden, um den Fortschritt "
                "zu aktualisieren."
            )
        st.caption(f"Bericht: `_otio_v2/intake/runs/{vt_run.run_id}.json`")
        if vt_assets:
            st.markdown("**Video-Transcode-Ergebnisse**")
            for item in vt_assets:
                path = item.working_relative_path or "—"
                st.markdown(
                    f"`{item.source_relative_path}` — **{item.status.value}** · "
                    f"working=`{path}`"
                )
                if item.error_code or item.error_message:
                    st.caption(
                        f"{item.error_code or ''}: {item.error_message or ''}".strip(
                            ": "
                        )
                    )
    if vt_working:
        st.markdown(f"**Video-Transcode Working Media ({len(vt_working)})**")
        for wm in vt_working[:50]:
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
