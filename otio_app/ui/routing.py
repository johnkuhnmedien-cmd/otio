"""Streamlit-Navigation — echte Seitenisolierung gegen Ghost-Widgets."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from otio_app.services.job_registry import reconcile_all_jobs
from otio_app.build_info import expected_feature_markers, format_build_label
from otio_app.ui.activity import record_script_run, render_activity_panel
from otio_app.ui.analysis_jobs_ui import render_analysis_jobs_banner
from otio_app.ui.clean_media import render_clean_media_page
from otio_app.ui.edit_plan import render_edit_plan_page
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY, PAGE_ANALYSIS, PAGE_MAPPING, PAGE_SUPPLEMENT
from otio_app.ui.page_state import clear_page_widget_state
from otio_app.ui.project_workbench import render_project_workbench
from otio_app.ui.supplement_assets import render_supplement_assets_page
from otio_app.ui.system_status import render_system_status_page
from otio_app.ui.voice_folder_mapping import render_voice_folder_mapping


_CURRENT_PAGE_KEY = "_otio_current_page"


def _wrap_page(
    page_id: str,
    render_fn: Callable[[], None],
    *,
    show_jobs_banner: bool = False,
    purge_mapping_on_enter: bool = False,
) -> Callable[[], None]:
    def wrapped() -> None:
        previous_page = st.session_state.get(_CURRENT_PAGE_KEY)
        if previous_page != page_id:
            if purge_mapping_on_enter:
                clear_page_widget_state(PAGE_MAPPING)
            st.session_state[_CURRENT_PAGE_KEY] = page_id

        reconcile_all_jobs()
        record_script_run(page_id)
        if show_jobs_banner:
            project_id = st.session_state.get(ACTIVE_PROJECT_KEY)
            if project_id:
                render_analysis_jobs_banner(project_id)
        render_fn()

    wrapped.__name__ = f"page_{page_id.replace(' ', '_')}"
    return wrapped


def run_app_navigation(
    *,
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> None:
    """Startet st.navigation — nur die aktive Seite wird gerendert."""
    from otio_app.ui.navigation import (
        PAGE_CLEAN_MEDIA,
        PAGE_EDIT_PLAN,
        PAGE_LIST,
        PAGE_MAPPING,
        PAGE_NEW,
        PAGE_STATUS,
        PAGE_SUPPLEMENT,
    )
        _run_legacy_pages(
            render_new_project=render_new_project,
            render_project_list=render_project_list,
        )
        return

    pages = [
        st.Page(render_new_project, title=PAGE_NEW, url_path="neues-projekt", default=True),
        st.Page(render_project_list, title=PAGE_LIST, url_path="projekte"),
        st.Page(
            _wrap_page(PAGE_CLEAN_MEDIA, render_clean_media_page, show_jobs_banner=True),
            title=PAGE_CLEAN_MEDIA,
            url_path="clean-media",
        ),
        st.Page(
            _wrap_page(PAGE_ANALYSIS, render_project_workbench),
            title=PAGE_ANALYSIS,
            url_path="analysen",
        ),
        st.Page(
            _wrap_page(PAGE_MAPPING, render_voice_folder_mapping, show_jobs_banner=True),
            title=PAGE_MAPPING,
            url_path="zuordnung",
        ),
        st.Page(
            _wrap_page(PAGE_SUPPLEMENT, render_supplement_assets_page, show_jobs_banner=True),
            title=PAGE_SUPPLEMENT,
            url_path="supplement-assets",
        ),
        st.Page(
            _wrap_page(
                PAGE_EDIT_PLAN,
                render_edit_plan_page,
                show_jobs_banner=True,
                purge_mapping_on_enter=True,
            ),
            title=PAGE_EDIT_PLAN,
            url_path="schnittplan",
        ),
        st.Page(render_system_status_page, title=PAGE_STATUS, url_path="systemstatus"),
    ]

    with st.sidebar:
        st.caption(f"Build: **{format_build_label()}**")
        st.caption("Workflow: ⓪ → ① → ② → ②½ → ③ · Diagnose unter Systemstatus")
        render_activity_panel()

    navigation = st.navigation(pages, position="sidebar")
    navigation.run()


def _run_legacy_pages(
    *,
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> None:
    """Fallback ohne st.navigation (ältere Streamlit-Version)."""
    from otio_app.shutdown import is_shutting_down
    from otio_app.ui.navigation import (
        LAST_NAV_PAGE_KEY,
        NAVIGATION_OPTIONS,
        PAGE_CLEAN_MEDIA,
        PAGE_EDIT_PLAN,
        PAGE_LIST,
        PAGE_MAPPING,
        PAGE_NEW,
        PAGE_STATUS,
        PAGE_SUPPLEMENT,
    )

    with st.sidebar:
        st.markdown("**Projekt**")
        page = st.radio(
            "Navigation",
            NAVIGATION_OPTIONS,
            label_visibility="collapsed",
            key="sidebar_nav",
        )

    previous_page = st.session_state.get(LAST_NAV_PAGE_KEY)
    if previous_page != page:
        if previous_page is not None:
            clear_page_widget_state(previous_page)
        st.session_state[LAST_NAV_PAGE_KEY] = page
        if not is_shutting_down():
            st.rerun()

    if page == PAGE_NEW:
        render_new_project()
    elif page == PAGE_LIST:
        render_project_list()
    elif page == PAGE_CLEAN_MEDIA:
        _wrap_page(PAGE_CLEAN_MEDIA, render_clean_media_page, show_jobs_banner=True)()
    elif page == PAGE_ANALYSIS:
        _wrap_page(PAGE_ANALYSIS, render_project_workbench)()
    elif page == PAGE_MAPPING:
        _wrap_page(PAGE_MAPPING, render_voice_folder_mapping, show_jobs_banner=True)()
    elif page == PAGE_SUPPLEMENT:
        _wrap_page(PAGE_SUPPLEMENT, render_supplement_assets_page, show_jobs_banner=True)()
    elif page == PAGE_EDIT_PLAN:
        _wrap_page(
            PAGE_EDIT_PLAN,
            render_edit_plan_page,
            show_jobs_banner=True,
            purge_mapping_on_enter=True,
        )()
    elif page == PAGE_STATUS:
        render_system_status_page()
