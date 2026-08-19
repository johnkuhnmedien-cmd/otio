"""Streamlit-Navigation — echte Seitenisolierung gegen Ghost-Widgets."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from otio_app.services.job_registry import begin_ui_script_run, reconcile_all_jobs
from otio_app.build_info import expected_feature_markers, format_build_label
from otio_app.models import ProjectMode
from otio_app.project_repository import get_project_by_id
from otio_app.ui.activity import record_script_run, render_activity_panel
from otio_app.ui.analysis_jobs_ui import render_analysis_jobs_banner
from otio_app.ui.clean_media import render_clean_media_page
from otio_app.ui.edit_plan import render_edit_plan_page
from otio_app.ui.adobe_research_import_page import render_adobe_research_import_page
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY, PAGE_ANALYSIS, PAGE_API_KEYS, PAGE_MAPPING, PAGE_SUPPLEMENT
from otio_app.ui.active_project_session import restore_active_project_into_session
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
from otio_app.ui.without_voiceover_enhanced.audio_tab import render_enhanced_audio_page
from otio_app.ui.without_voiceover_enhanced.cut_plan_tab import render_enhanced_cut_plan_page
from otio_app.ui.without_voiceover_enhanced.final_output_tab import (
    render_enhanced_final_output_page,
)
from otio_app.ui.without_voiceover_enhanced.auto_run_ui import (
    render_enhanced_auto_run_page,
    render_enhanced_auto_run_page_panel,
    render_enhanced_auto_run_sidebar,
)
from otio_app.ui.without_voiceover_enhanced.folder_voiceovers_tab import (
    render_enhanced_folder_voiceovers_page,
)
from otio_app.ui.without_voiceover_enhanced.maps_tab import render_enhanced_maps_page


_CURRENT_PAGE_KEY = "_otio_current_page"
# app.py „Projekt bearbeiten“ → nach st.navigation zum Page-Objekt springen
PENDING_SWITCH_URL_PATH_KEY = "_otio_pending_switch_url_path"


def _active_project_mode() -> ProjectMode:
    """Ermittelt den Projektmodus des aktiven Projekts (Default: mit Voice-Over).

    Wird ohne Datenbankzugriff auf 'with_voiceover' aufgelöst, solange noch kein
    Projekt aktiv ist (z. B. beim allerersten App-Start) — die bestehende
    Navigation bleibt dadurch für alle bisherigen Projekte unverändert.
    """
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
    show_jobs_banner: bool = True,
    jobs_banner_skip: tuple[str, ...] = (),
    purge_mapping_on_enter: bool = False,
    show_auto_run_panel: bool = True,
) -> Callable[[], None]:
    def wrapped() -> None:
        previous_page = st.session_state.get(_CURRENT_PAGE_KEY)
        if previous_page != page_id:
            if purge_mapping_on_enter:
                clear_page_widget_state(PAGE_MAPPING)
            st.session_state[_CURRENT_PAGE_KEY] = page_id

        begin_ui_script_run()
        reconcile_all_jobs()
        record_script_run(page_id)
        project_id = st.session_state.get(ACTIVE_PROJECT_KEY)
        if show_jobs_banner and project_id:
            render_analysis_jobs_banner(project_id, skip_kinds=jobs_banner_skip)
        if show_auto_run_panel and project_id:
            project = get_project_by_id(str(project_id))
            if (
                project is not None
                and project.project_mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED
            ):
                render_enhanced_auto_run_page_panel(str(project_id))
        render_fn()

    wrapped.__name__ = f"page_{page_id.replace(' ', '_')}"
    return wrapped


def _build_with_voiceover_pages(
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> list:
    from otio_app.ui.navigation import (
        PAGE_ADOBE_IMPORT,
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
        st.Page(
            _wrap_page(
                PAGE_ADOBE_IMPORT,
                render_adobe_research_import_page,
                show_auto_run_panel=False,
            ),
            title=PAGE_ADOBE_IMPORT,
            url_path="adobe-stock-import",
        ),
        st.Page(
            _wrap_page(PAGE_NEW, render_new_project, show_auto_run_panel=False),
            title=PAGE_NEW,
            url_path="neues-projekt",
            default=True,
        ),
        st.Page(
            _wrap_page(PAGE_LIST, render_project_list, show_auto_run_panel=False),
            title=PAGE_LIST,
            url_path="projekte",
        ),
        st.Page(
            _wrap_page(PAGE_CLEAN_MEDIA, render_clean_media_page, jobs_banner_skip=("clean",)),
            title=PAGE_CLEAN_MEDIA,
            url_path="clean-media",
        ),
        st.Page(
            _wrap_page(
                PAGE_ANALYSIS,
                render_project_workbench,
                jobs_banner_skip=("voice", "assets", "recovery"),
            ),
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
        st.Page(
            _wrap_page(PAGE_API_KEYS, render_api_keys_page, show_auto_run_panel=False),
            title=PAGE_API_KEYS,
            url_path="api-schluessel",
        ),
        st.Page(
            _wrap_page(PAGE_STATUS, render_system_status_page, show_auto_run_panel=False),
            title=PAGE_STATUS,
            url_path="systemstatus",
        ),
    ]


def _build_without_voiceover_pages(
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> list:
    from otio_app.ui.navigation import (
        PAGE_ADOBE_IMPORT,
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
        st.Page(
            _wrap_page(
                PAGE_ADOBE_IMPORT,
                render_adobe_research_import_page,
                show_auto_run_panel=False,
            ),
            title=PAGE_ADOBE_IMPORT,
            url_path="adobe-stock-import",
        ),
        st.Page(
            _wrap_page(PAGE_NEW, render_new_project, show_auto_run_panel=False),
            title=PAGE_NEW,
            url_path="neues-projekt",
            default=True,
        ),
        st.Page(
            _wrap_page(PAGE_LIST, render_project_list, show_auto_run_panel=False),
            title=PAGE_LIST,
            url_path="projekte",
        ),
        st.Page(
            _wrap_page(PAGE_CLEAN_MEDIA, render_clean_media_page, jobs_banner_skip=("clean",)),
            title=PAGE_CLEAN_MEDIA,
            url_path="clean-media",
        ),
        st.Page(
            _wrap_page(
                PAGE_ANALYSIS,
                render_project_workbench,
                jobs_banner_skip=("voice", "assets", "recovery"),
            ),
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
        st.Page(
            _wrap_page(PAGE_API_KEYS, render_api_keys_page, show_auto_run_panel=False),
            title=PAGE_API_KEYS,
            url_path="api-schluessel",
        ),
        st.Page(
            _wrap_page(PAGE_STATUS, render_system_status_page, show_auto_run_panel=False),
            title=PAGE_STATUS,
            url_path="systemstatus",
        ),
    ]


def _build_without_voiceover_enhanced_pages(
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> list:
    """Enhanced MVP: gleiche frühen Schritte, aber Audio → Cut Plan → Final Output."""
    from otio_app.ui.navigation import (
        PAGE_ADOBE_IMPORT,
        PAGE_API_KEYS,
        PAGE_AUDIO,
        PAGE_AUTO_RUN,
        PAGE_CLEAN_MEDIA,
        PAGE_CUT_PLAN_ENHANCED,
        PAGE_DRAMATURGY,
        PAGE_FINAL_OUTPUT_ENHANCED,
        PAGE_FOLDER_VOICEOVERS,
        PAGE_INTRO,
        PAGE_LIST,
        PAGE_MAPS,
        PAGE_NEW,
        PAGE_PROJECT_BRIEF,
        PAGE_STATUS,
        PAGE_STYLE_REFERENCES,
    )

    return [
        st.Page(
            _wrap_page(
                PAGE_ADOBE_IMPORT,
                render_adobe_research_import_page,
                show_auto_run_panel=False,
            ),
            title=PAGE_ADOBE_IMPORT,
            url_path="adobe-stock-import",
        ),
        st.Page(
            _wrap_page(PAGE_NEW, render_new_project, show_auto_run_panel=False),
            title=PAGE_NEW,
            url_path="neues-projekt",
            default=True,
        ),
        st.Page(
            _wrap_page(PAGE_LIST, render_project_list, show_auto_run_panel=False),
            title=PAGE_LIST,
            url_path="projekte",
        ),
        st.Page(
            _wrap_page(PAGE_CLEAN_MEDIA, render_clean_media_page, jobs_banner_skip=("clean",)),
            title=PAGE_CLEAN_MEDIA,
            url_path="clean-media",
        ),
        st.Page(
            _wrap_page(
                PAGE_ANALYSIS,
                render_project_workbench,
                jobs_banner_skip=("voice", "assets", "recovery"),
            ),
            title=PAGE_ANALYSIS,
            url_path="analysen",
        ),
        st.Page(
            _wrap_page(
                PAGE_AUTO_RUN,
                render_enhanced_auto_run_page,
                show_auto_run_panel=False,
            ),
            title=PAGE_AUTO_RUN,
            url_path="auto-lauf",
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
            _wrap_page(PAGE_MAPS, render_enhanced_maps_page),
            title=PAGE_MAPS,
            url_path="karten",
        ),
        st.Page(
            _wrap_page(PAGE_FOLDER_VOICEOVERS, render_enhanced_folder_voiceovers_page),
            title=PAGE_FOLDER_VOICEOVERS,
            url_path="folder-voiceovers",
        ),
        st.Page(
            _wrap_page(PAGE_INTRO, render_intro_page),
            title=PAGE_INTRO,
            url_path="intro",
        ),
        st.Page(
            _wrap_page(PAGE_AUDIO, render_enhanced_audio_page),
            title=PAGE_AUDIO,
            url_path="audio",
        ),
        st.Page(
            _wrap_page(PAGE_CUT_PLAN_ENHANCED, render_enhanced_cut_plan_page),
            title=PAGE_CUT_PLAN_ENHANCED,
            url_path="cut-plan",
        ),
        st.Page(
            _wrap_page(PAGE_FINAL_OUTPUT_ENHANCED, render_enhanced_final_output_page),
            title=PAGE_FINAL_OUTPUT_ENHANCED,
            url_path="final-output",
        ),
        st.Page(
            _wrap_page(PAGE_API_KEYS, render_api_keys_page, show_auto_run_panel=False),
            title=PAGE_API_KEYS,
            url_path="api-schluessel",
        ),
        st.Page(
            _wrap_page(PAGE_STATUS, render_system_status_page, show_auto_run_panel=False),
            title=PAGE_STATUS,
            url_path="systemstatus",
        ),
    ]


def _make_page(render_fn: Callable[[], None], *, title: str, url_path: str, default: bool = False, visibility: str = "visible"):
    """st.Page mit visibility, falls die installierte Streamlit-Version das kann."""
    kwargs: dict = {"title": title, "url_path": url_path, "default": default}
    try:
        return st.Page(render_fn, visibility=visibility, **kwargs)
    except TypeError:
        return st.Page(render_fn, **kwargs)


def _ensure_hidden_auto_lauf_route(pages: list) -> list:
    """``/auto-lauf`` muss immer auflösbar sein, auch ohne Enhanced-Session.

    Sonst bleibt die Fläche nach einem Neustart schwarz (Streamlit wartet auf
    eine Seite, die in der klassischen Navigation nicht existiert).
    """
    existing = {
        str(getattr(page, "url_path", "") or "").strip("/")
        for page in pages
    }
    if "auto-lauf" in existing:
        return pages
    from otio_app.ui.navigation import PAGE_AUTO_RUN

    hidden = _make_page(
        render_enhanced_auto_run_page,
        title=PAGE_AUTO_RUN,
        url_path="auto-lauf",
        visibility="hidden",
    )
    return list(pages) + [hidden]


def run_app_navigation(
    *,
    render_new_project: Callable[[], None],
    render_project_list: Callable[[], None],
) -> None:
    """Startet st.navigation — nur die aktive Seite wird gerendert."""
    restore_active_project_into_session()
    if not hasattr(st, "navigation"):
        _run_legacy_pages(
            render_new_project=render_new_project,
            render_project_list=render_project_list,
        )
        return

    mode = _active_project_mode()
    if mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
        pages = _build_without_voiceover_enhanced_pages(
            render_new_project, render_project_list
        )
        workflow_caption = (
            "Workflow (Enhanced MVP): ⓪ → ① → Brief → ② → ③ → ③½ Karten → ④ → ⑤ → "
            "⑥ Audio → ⑦ Cut Plan → ⑧ Final Output"
        )
    elif mode == ProjectMode.WITHOUT_VOICEOVER:
        pages = _build_without_voiceover_pages(render_new_project, render_project_list)
        workflow_caption = "Workflow (ohne Voice-Over): ⓪ → ① → ① Brief → ② → ③ → ④ → ⑤ → ⑥ → ⑦ → ⑧"
    else:
        pages = _build_with_voiceover_pages(render_new_project, render_project_list)
        workflow_caption = "Workflow: ⓪ → ① → ② → ②½ → ③ · API-Keys & Diagnose in der Sidebar"

    pages = _ensure_hidden_auto_lauf_route(pages)

    with st.sidebar:
        st.caption(f"Build: **{format_build_label()}**")
        st.caption(workflow_caption)
        if mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
            render_enhanced_auto_run_sidebar()
        render_activity_panel()

    navigation = st.navigation(pages, position="sidebar")
    pending_url = st.session_state.pop(PENDING_SWITCH_URL_PATH_KEY, None)
    if pending_url:
        target = str(pending_url).strip().strip("/")
        for page in pages:
            page_url = str(getattr(page, "url_path", "") or "").strip().strip("/")
            if page_url == target:
                # st.navigation: switch_page braucht das Page-Objekt, keinen String.
                st.switch_page(page)
                return
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
        PAGE_ADOBE_IMPORT,
        PAGE_AUDIO,
        PAGE_AUTO_RUN,
        PAGE_CLEAN_MEDIA,
        PAGE_CUT_PLAN,
        PAGE_CUT_PLAN_ENHANCED,
        PAGE_DRAMATURGY,
        PAGE_EDIT_PLAN,
        PAGE_FINAL_OUTPUT,
        PAGE_FINAL_OUTPUT_ENHANCED,
        PAGE_FOLDER_VOICEOVERS,
        PAGE_INTRO,
        PAGE_LIST,
        PAGE_MAPS,
        PAGE_MAPPING,
        PAGE_NEW,
        PAGE_PROJECT_BRIEF,
        PAGE_STATUS,
        PAGE_STYLE_REFERENCES,
        PAGE_SUPPLEMENT,
        VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS,
        VOICEOVER_GEN_NAVIGATION_OPTIONS,
    )

    mode = _active_project_mode()
    if mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
        options = VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS
    elif mode == ProjectMode.WITHOUT_VOICEOVER:
        options = VOICEOVER_GEN_NAVIGATION_OPTIONS
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
        if mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
            render_enhanced_auto_run_sidebar()

    previous_page = st.session_state.get(LAST_NAV_PAGE_KEY)
    if previous_page != page:
        if previous_page is not None:
            clear_page_widget_state(previous_page)
        st.session_state[LAST_NAV_PAGE_KEY] = page
        if not is_shutting_down():
            st.rerun()

    if page == PAGE_ADOBE_IMPORT:
        _wrap_page(
            PAGE_ADOBE_IMPORT,
            render_adobe_research_import_page,
            show_auto_run_panel=False,
        )()
    elif page == PAGE_NEW:
        _wrap_page(PAGE_NEW, render_new_project, show_auto_run_panel=False)()
    elif page == PAGE_LIST:
        _wrap_page(PAGE_LIST, render_project_list, show_auto_run_panel=False)()
    elif page == PAGE_CLEAN_MEDIA:
        _wrap_page(PAGE_CLEAN_MEDIA, render_clean_media_page, jobs_banner_skip=("clean",))()
    elif page == PAGE_ANALYSIS:
        _wrap_page(
            PAGE_ANALYSIS,
            render_project_workbench,
            jobs_banner_skip=("voice", "assets", "recovery"),
        )()
    elif page == PAGE_AUTO_RUN:
        _wrap_page(
            PAGE_AUTO_RUN,
            render_enhanced_auto_run_page,
            show_auto_run_panel=False,
        )()
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
    elif page == PAGE_MAPS:
        _wrap_page(PAGE_MAPS, render_enhanced_maps_page)()
    elif page == PAGE_FOLDER_VOICEOVERS:
        if mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
            _wrap_page(PAGE_FOLDER_VOICEOVERS, render_enhanced_folder_voiceovers_page)()
        else:
            _wrap_page(PAGE_FOLDER_VOICEOVERS, render_folder_voiceovers_page)()
    elif page == PAGE_INTRO:
        _wrap_page(PAGE_INTRO, render_intro_page)()
    elif page == PAGE_AUDIO:
        if mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED:
            _wrap_page(PAGE_AUDIO, render_enhanced_audio_page)()
        else:
            _wrap_page(PAGE_AUDIO, render_audio_page)()
    elif page == PAGE_FINAL_OUTPUT:
        _wrap_page(PAGE_FINAL_OUTPUT, render_final_output_page)()
    elif page == PAGE_CUT_PLAN:
        _wrap_page(PAGE_CUT_PLAN, render_cut_plan_page)()
    elif page == PAGE_CUT_PLAN_ENHANCED:
        _wrap_page(PAGE_CUT_PLAN_ENHANCED, render_enhanced_cut_plan_page)()
    elif page == PAGE_FINAL_OUTPUT_ENHANCED:
        _wrap_page(PAGE_FINAL_OUTPUT_ENHANCED, render_enhanced_final_output_page)()
    elif page == PAGE_API_KEYS:
        _wrap_page(PAGE_API_KEYS, render_api_keys_page, show_auto_run_panel=False)()
    elif page == PAGE_STATUS:
        _wrap_page(PAGE_STATUS, render_system_status_page, show_auto_run_panel=False)()
