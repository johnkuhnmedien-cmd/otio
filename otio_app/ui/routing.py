"""Streamlit-Navigation — echte Seitenisolierung gegen Ghost-Widgets."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from otio_app.services.job_registry import reconcile_all_jobs
from otio_app.build_info import expected_feature_markers, format_build_label
from otio_app.models import ProjectMode
from otio_app.project_repository import get_project_by_id
from otio_app.ui.activity import record_script_run, render_activity_panel
from otio_app.ui.analysis_jobs_ui import render_analysis_jobs_banner
from otio_app.ui.clean_media import render_clean_media_page
from otio_app.ui.edit_plan import render_edit_plan_page
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY, PAGE_ANALYSIS, PAGE_API_KEYS, PAGE_MAPPING, PAGE_SUPPLEMENT
from otio_app.ui.page_state import clear_page_widget_state
from otio_app.ui.project_workbench import render_project_workbench
from otio_app.ui.supplement_assets import render_supplement_assets_page
from otio_app.ui.api_keys_page import render_api_keys_page
from otio_app.ui.system_status import render_system_status_page
from otio_app.ui.voice_folder_mapping import render_voice_folder_mapping
from otio_app.ui.voiceover_generation.audio_tab import render_audio_page
from otio_app.ui.voiceover_generation.cut_plan_tab import render_cut_plan_page
from otio_app.ui.voiceover_generation.dramaturgy_tab import render_dramaturgy_page
from otio_app.ui.voiceover_generation.final_output_tab import render_final_output_page
from otio_app.ui.voiceover_generation.folder_voiceovers_tab import render_folder_voiceovers_page
from otio_app.ui.voiceover_generation.intro_tab import render_intro_page
from otio_app.ui.voiceover_generation.project_brief_tab import render_project_brief_page
from otio_app.ui.voiceover_generation.style_references_tab import render_style_references_page
from otio_app.discovery_v2.ui import (
    render_discovery_asset_analysis_page,
    render_discovery_editorial_page,
    render_discovery_inventory_page,
    render_discovery_media_intake_page,
    render_discovery_narration_page,
    render_discovery_overview_page,
    render_discovery_review_export_page,
    render_discovery_settings_page,
    render_discovery_technical_validation_page,
    render_discovery_visual_edit_page,
)
from otio_app.discovery_v2.ui.route_context import (
    discovery_shell_requested,
    restore_discovery_route_context,
    sync_discovery_page_route,
)


_CURRENT_PAGE_KEY = "_otio_current_page"


def _active_project_mode() -> ProjectMode:
    """Ermittelt den Projektmodus des aktiven Projekts (Default: mit Voice-Over).

    Discovery-Reload/Deep-Link: Wenn die Route einen Discovery-Kontext verlangt
    (query/path), bleibt die Discovery-Navigation erhalten — kein stiller
    Classic-Fallback bei fehlender/ungültiger Project-ID.

    Ohne Projekt und ohne Discovery-Route-Hinweis bleibt der Default
    ``with_voiceover`` (erster App-Start / Classic unverändert).
    """
    if discovery_shell_requested():
        project_id = st.session_state.get(ACTIVE_PROJECT_KEY)
        if project_id:
            project = get_project_by_id(project_id)
            if project is not None and project.project_mode == ProjectMode.DISCOVERY_V2:
                return ProjectMode.DISCOVERY_V2
        return ProjectMode.DISCOVERY_V2

    project_id = st.session_state.get(ACTIVE_PROJECT_KEY)
    if not project_id:
        return ProjectMode.WITH_VOICEOVER
    project = get_project_by_id(project_id)
    if project is None:
        return ProjectMode.WITH_VOICEOVER
    return project.project_mode


def _wrap_page(
    page_id: str,
    render_fn: Callable[[], None],
    *,
    show_jobs_banner: bool = False,
    purge_mapping_on_enter: bool = False,
    discovery_page_slug: str | None = None,
) -> Callable[[], None]:
    def wrapped() -> None:
        previous_page = st.session_state.get(_CURRENT_PAGE_KEY)
        if previous_page != page_id:
            if purge_mapping_on_enter:
                clear_page_widget_state(PAGE_MAPPING)
            st.session_state[_CURRENT_PAGE_KEY] = page_id

        if discovery_page_slug:
            sync_discovery_page_route(discovery_page_slug)

        reconcile_all_jobs()
        record_script_run(page_id)
        if show_jobs_banner:
            project_id = st.session_state.get(ACTIVE_PROJECT_KEY)
            if project_id:
                render_analysis_jobs_banner(project_id)
        render_fn()

    wrapped.__name__ = f"page_{page_id.replace(' ', '_')}"
    return wrapped


def _build_with_voiceover_pages(
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> list:
    from otio_app.ui.navigation import (
        PAGE_API_KEYS,
        PAGE_CLEAN_MEDIA,
        PAGE_EDIT_PLAN,
        PAGE_LIST,
        PAGE_MAPPING,
        PAGE_NEW,
        PAGE_STATUS,
        PAGE_SUPPLEMENT,
    )

    return [
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
        st.Page(render_api_keys_page, title=PAGE_API_KEYS, url_path="api-schluessel"),
        st.Page(render_system_status_page, title=PAGE_STATUS, url_path="systemstatus"),
    ]


def _build_without_voiceover_pages(
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> list:
    from otio_app.ui.navigation import (
        PAGE_API_KEYS,
        PAGE_AUDIO,
        PAGE_CLEAN_MEDIA,
        PAGE_CUT_PLAN,
        PAGE_DRAMATURGY,
        PAGE_FINAL_OUTPUT,
        PAGE_FOLDER_VOICEOVERS,
        PAGE_INTRO,
        PAGE_LIST,
        PAGE_NEW,
        PAGE_PROJECT_BRIEF,
        PAGE_STATUS,
        PAGE_STYLE_REFERENCES,
    )

    return [
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
            _wrap_page(PAGE_PROJECT_BRIEF, render_project_brief_page),
            title=PAGE_PROJECT_BRIEF,
            url_path="project-brief",
        ),
        st.Page(
            _wrap_page(PAGE_STYLE_REFERENCES, render_style_references_page),
            title=PAGE_STYLE_REFERENCES,
            url_path="style-references",
        ),
        st.Page(
            _wrap_page(PAGE_DRAMATURGY, render_dramaturgy_page),
            title=PAGE_DRAMATURGY,
            url_path="dramaturgie",
        ),
        st.Page(
            _wrap_page(PAGE_FOLDER_VOICEOVERS, render_folder_voiceovers_page),
            title=PAGE_FOLDER_VOICEOVERS,
            url_path="folder-voiceovers",
        ),
        st.Page(
            _wrap_page(PAGE_INTRO, render_intro_page),
            title=PAGE_INTRO,
            url_path="intro",
        ),
        st.Page(
            _wrap_page(PAGE_AUDIO, render_audio_page),
            title=PAGE_AUDIO,
            url_path="audio",
        ),
        st.Page(
            _wrap_page(PAGE_FINAL_OUTPUT, render_final_output_page),
            title=PAGE_FINAL_OUTPUT,
            url_path="final-output",
        ),
        st.Page(
            _wrap_page(PAGE_CUT_PLAN, render_cut_plan_page),
            title=PAGE_CUT_PLAN,
            url_path="cut-plan",
        ),
        st.Page(render_api_keys_page, title=PAGE_API_KEYS, url_path="api-schluessel"),
        st.Page(render_system_status_page, title=PAGE_STATUS, url_path="systemstatus"),
    ]


def _build_discovery_v2_pages(
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> list:
    from otio_app.ui.navigation import (
        PAGE_API_KEYS,
        PAGE_DISCOVERY_ASSET_ANALYSIS,
        PAGE_DISCOVERY_EDITORIAL,
        PAGE_DISCOVERY_INVENTORY,
        PAGE_DISCOVERY_MEDIA_INTAKE,
        PAGE_DISCOVERY_NARRATION,
        PAGE_DISCOVERY_OVERVIEW,
        PAGE_DISCOVERY_REVIEW_EXPORT,
        PAGE_DISCOVERY_SETTINGS,
        PAGE_DISCOVERY_TECHNICAL_VALIDATION,
        PAGE_DISCOVERY_VISUAL_EDIT,
        PAGE_LIST,
        PAGE_NEW,
        PAGE_STATUS,
    )

    return [
        st.Page(render_new_project, title=PAGE_NEW, url_path="neues-projekt", default=True),
        st.Page(render_project_list, title=PAGE_LIST, url_path="projekte"),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_OVERVIEW,
                render_discovery_overview_page,
                discovery_page_slug="overview",
            ),
            title=PAGE_DISCOVERY_OVERVIEW,
            url_path="discovery-v2",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_INVENTORY,
                render_discovery_inventory_page,
                discovery_page_slug="inventory",
            ),
            title=PAGE_DISCOVERY_INVENTORY,
            url_path="discovery-medienbestand",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_TECHNICAL_VALIDATION,
                render_discovery_technical_validation_page,
                discovery_page_slug="technical_validation",
            ),
            title=PAGE_DISCOVERY_TECHNICAL_VALIDATION,
            url_path="discovery-technische-pruefung",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_MEDIA_INTAKE,
                render_discovery_media_intake_page,
                discovery_page_slug="media_intake",
            ),
            title=PAGE_DISCOVERY_MEDIA_INTAKE,
            url_path="discovery-media-intake",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_ASSET_ANALYSIS,
                render_discovery_asset_analysis_page,
                discovery_page_slug="asset_analysis",
            ),
            title=PAGE_DISCOVERY_ASSET_ANALYSIS,
            url_path="discovery-assetanalyse",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_EDITORIAL,
                render_discovery_editorial_page,
                discovery_page_slug="editorial",
            ),
            title=PAGE_DISCOVERY_EDITORIAL,
            url_path="discovery-editorial",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_NARRATION,
                render_discovery_narration_page,
                discovery_page_slug="narration",
            ),
            title=PAGE_DISCOVERY_NARRATION,
            url_path="discovery-narration",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_VISUAL_EDIT,
                render_discovery_visual_edit_page,
                discovery_page_slug="visual_edit",
            ),
            title=PAGE_DISCOVERY_VISUAL_EDIT,
            url_path="discovery-visual-edit",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_REVIEW_EXPORT,
                render_discovery_review_export_page,
                discovery_page_slug="review_export",
            ),
            title=PAGE_DISCOVERY_REVIEW_EXPORT,
            url_path="discovery-review-export",
        ),
        st.Page(
            _wrap_page(
                PAGE_DISCOVERY_SETTINGS,
                render_discovery_settings_page,
                discovery_page_slug="settings",
            ),
            title=PAGE_DISCOVERY_SETTINGS,
            url_path="discovery-settings",
        ),
        st.Page(render_api_keys_page, title=PAGE_API_KEYS, url_path="api-schluessel"),
        st.Page(render_system_status_page, title=PAGE_STATUS, url_path="systemstatus"),
    ]


def run_app_navigation(
    *,
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> None:
    """Startet st.navigation — nur die aktive Seite wird gerendert."""
    # Reload/Deep-Link: Project-ID aus der Route vor Mode-/Nav-Aufbau laden.
    restore_discovery_route_context()

    if not hasattr(st, "navigation"):
        _run_legacy_pages(
            render_new_project=render_new_project,
            render_project_list=render_project_list,
        )
        return

    mode = _active_project_mode()
    if mode == ProjectMode.WITHOUT_VOICEOVER:
        pages = _build_without_voiceover_pages(render_new_project, render_project_list)
        workflow_caption = "Workflow (ohne Voice-Over): ⓪ → ① → ① Brief → ② → ③ → ④ → ⑤ → ⑥ → ⑦ → ⑧"
    elif mode == ProjectMode.DISCOVERY_V2:
        pages = _build_discovery_v2_pages(render_new_project, render_project_list)
        workflow_caption = (
            "Workflow (Discovery V2): Übersicht · Medienbestand · "
            "Technische Prüfung · Media Intake · Assetanalyse · Editorial · Narration · Visual Edit · Review & Export · Projekteinstellungen"
        )
    else:
        pages = _build_with_voiceover_pages(render_new_project, render_project_list)
        workflow_caption = "Workflow: ⓪ → ① → ② → ②½ → ③ · API-Keys & Diagnose in der Sidebar"

    with st.sidebar:
        st.caption(f"Build: **{format_build_label()}**")
        st.caption(workflow_caption)
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
        DISCOVERY_V2_NAVIGATION_OPTIONS,
        LAST_NAV_PAGE_KEY,
        NAVIGATION_OPTIONS,
        PAGE_AUDIO,
        PAGE_CLEAN_MEDIA,
        PAGE_CUT_PLAN,
        PAGE_DISCOVERY_ASSET_ANALYSIS,
        PAGE_DISCOVERY_EDITORIAL,
        PAGE_DISCOVERY_INVENTORY,
        PAGE_DISCOVERY_MEDIA_INTAKE,
        PAGE_DISCOVERY_NARRATION,
        PAGE_DISCOVERY_OVERVIEW,
        PAGE_DISCOVERY_REVIEW_EXPORT,
        PAGE_DISCOVERY_SETTINGS,
        PAGE_DISCOVERY_TECHNICAL_VALIDATION,
        PAGE_DISCOVERY_VISUAL_EDIT,
        PAGE_DRAMATURGY,
        PAGE_EDIT_PLAN,
        PAGE_FINAL_OUTPUT,
        PAGE_FOLDER_VOICEOVERS,
        PAGE_INTRO,
        PAGE_LIST,
        PAGE_MAPPING,
        PAGE_NEW,
        PAGE_PROJECT_BRIEF,
        PAGE_STATUS,
        PAGE_STYLE_REFERENCES,
        PAGE_SUPPLEMENT,
        VOICEOVER_GEN_NAVIGATION_OPTIONS,
    )

    mode = _active_project_mode()
    if mode == ProjectMode.WITHOUT_VOICEOVER:
        options = VOICEOVER_GEN_NAVIGATION_OPTIONS
    elif mode == ProjectMode.DISCOVERY_V2:
        options = DISCOVERY_V2_NAVIGATION_OPTIONS
    else:
        options = NAVIGATION_OPTIONS

    with st.sidebar:
        st.markdown("**Projekt**")
        page = st.radio(
            "Navigation",
            options,
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
    elif page == PAGE_PROJECT_BRIEF:
        _wrap_page(PAGE_PROJECT_BRIEF, render_project_brief_page)()
    elif page == PAGE_STYLE_REFERENCES:
        _wrap_page(PAGE_STYLE_REFERENCES, render_style_references_page)()
    elif page == PAGE_DRAMATURGY:
        _wrap_page(PAGE_DRAMATURGY, render_dramaturgy_page)()
    elif page == PAGE_FOLDER_VOICEOVERS:
        _wrap_page(PAGE_FOLDER_VOICEOVERS, render_folder_voiceovers_page)()
    elif page == PAGE_INTRO:
        _wrap_page(PAGE_INTRO, render_intro_page)()
    elif page == PAGE_AUDIO:
        _wrap_page(PAGE_AUDIO, render_audio_page)()
    elif page == PAGE_FINAL_OUTPUT:
        _wrap_page(PAGE_FINAL_OUTPUT, render_final_output_page)()
    elif page == PAGE_CUT_PLAN:
        _wrap_page(PAGE_CUT_PLAN, render_cut_plan_page)()
    elif page == PAGE_DISCOVERY_OVERVIEW:
        _wrap_page(PAGE_DISCOVERY_OVERVIEW, render_discovery_overview_page)()
    elif page == PAGE_DISCOVERY_INVENTORY:
        _wrap_page(PAGE_DISCOVERY_INVENTORY, render_discovery_inventory_page)()
    elif page == PAGE_DISCOVERY_TECHNICAL_VALIDATION:
        _wrap_page(
            PAGE_DISCOVERY_TECHNICAL_VALIDATION,
            render_discovery_technical_validation_page,
        )()
    elif page == PAGE_DISCOVERY_MEDIA_INTAKE:
        _wrap_page(
            PAGE_DISCOVERY_MEDIA_INTAKE,
            render_discovery_media_intake_page,
        )()
    elif page == PAGE_DISCOVERY_ASSET_ANALYSIS:
        _wrap_page(
            PAGE_DISCOVERY_ASSET_ANALYSIS,
            render_discovery_asset_analysis_page,
        )()
    elif page == PAGE_DISCOVERY_EDITORIAL:
        _wrap_page(
            PAGE_DISCOVERY_EDITORIAL,
            render_discovery_editorial_page,
        )()
    elif page == PAGE_DISCOVERY_NARRATION:
        _wrap_page(
            PAGE_DISCOVERY_NARRATION,
            render_discovery_narration_page,
        )()
    elif page == PAGE_DISCOVERY_VISUAL_EDIT:
        _wrap_page(
            PAGE_DISCOVERY_VISUAL_EDIT,
            render_discovery_visual_edit_page,
        )()
    elif page == PAGE_DISCOVERY_REVIEW_EXPORT:
        _wrap_page(
            PAGE_DISCOVERY_REVIEW_EXPORT,
            render_discovery_review_export_page,
        )()
    elif page == PAGE_DISCOVERY_SETTINGS:
        _wrap_page(PAGE_DISCOVERY_SETTINGS, render_discovery_settings_page)()
    elif page == PAGE_API_KEYS:
        render_api_keys_page()
    elif page == PAGE_STATUS:
        render_system_status_page()
