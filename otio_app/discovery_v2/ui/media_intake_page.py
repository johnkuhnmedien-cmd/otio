"""Streamlit-Seite: Discovery V2 Media Intake (Phase 7A — nur Vorschau/Planung)."""

from __future__ import annotations

from collections import defaultdict

import streamlit as st

from otio_app.discovery_v2.application.media_intake_planning_service import (
    MediaIntakePlanningServiceError,
    can_create_intake_plan,
    create_intake_plan,
    get_current_intake_plan,
)
from otio_app.discovery_v2.domain.media_intake import IntakeAction, IntakePlanStatus
from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.discovery_v2.ui.overview import active_discovery_project


_SESSION_ERROR_KEY = "discovery_v2_intake_error"
_SESSION_INFO_KEY = "discovery_v2_intake_info"


def render_discovery_media_intake_page() -> None:
    """Media Intake — Planvorschau; erzeugt Pläne nur über expliziten Button."""
    st.title("Media Intake")
    project = active_discovery_project()
    if project is None:
        return

    st.caption(
        "Plant auf Basis der technischen Prüfung, welche Assets später "
        "kopiert, remuxt oder transkodiert werden sollten. "
        "Es werden noch keine Medien kopiert oder verändert."
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

    # Reload nach möglicher Erstellung
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
    st.info("Es wurden noch keine Medien kopiert oder verändert.")

    root = get_discovery_v2_root(project.project_root_path)
    st.caption(
        f"Artefakt: `_otio_v2/intake/plans/{plan.plan_id}.json` · "
        f"Wurzel: `{root}`"
    )

    by_group: dict[str, list] = defaultdict(list)
    for item in plan.items:
        by_group[item.source_group or "__root__"].append(item)

    st.subheader("Ergebnisse nach Quellgruppe")
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
                if item.planned_action == IntakeAction.BLOCKED:
                    st.markdown(line)
                else:
                    st.markdown(line)
                details = [item.reason_detail]
                if item.proposed_target_extension:
                    details.append(f"Ziel={item.proposed_target_extension}")
                if item.width and item.height:
                    details.append(f"{item.width}×{item.height}")
                st.caption(" · ".join(d for d in details if d))
