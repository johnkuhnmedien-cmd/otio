"""Streamlit glue: Discovery route sync, restore, and project binding.

Canonical truth for reload: query params + Application Service.
session_state caches UI state but must not override an invalid/missing route.
"""

from __future__ import annotations

from urllib.parse import urlparse

import streamlit as st

from otio_app.discovery_v2.application.project_route_service import (
    DISCOVERY_SAFE_START_SLUG,
    QUERY_PAGE,
    QUERY_PROJECT_ID,
    DiscoveryRouteResolution,
    DiscoveryRouteStatus,
    is_discovery_url_path,
    resolve_discovery_route,
    slug_for_url_path,
    url_path_for_slug,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_repository import get_project_by_id, list_projects
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY

ROUTE_HINT_KEY = "_discovery_v2_route_hint"
ROUTE_ERROR_KEY = "_discovery_v2_route_error"
ROUTE_PAGE_SLUG_KEY = "_discovery_v2_route_page_slug"
_LAST_BOUND_PROJECT_KEY = "_discovery_v2_last_bound_project_id"

_DISCOVERY_SESSION_PREFIXES = (
    "discovery_v2_",
    "_discovery_v2_",
)


def _query_get(name: str) -> str | None:
    try:
        raw = st.query_params.get(name)
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    text = str(raw).strip() if raw is not None else ""
    return text or None


def _query_set(**values: str | None) -> None:
    """Update query params; omit None/empty values."""
    try:
        current = dict(st.query_params.to_dict())
    except Exception:
        current = {}
    for key, value in values.items():
        if value is None or str(value).strip() == "":
            current.pop(key, None)
        else:
            current[key] = str(value).strip()
    try:
        # Preserve unrelated params; replace managed keys.
        st.query_params.from_dict(current)
    except Exception:
        try:
            for key, value in values.items():
                if value is None or str(value).strip() == "":
                    if key in st.query_params:
                        del st.query_params[key]
                else:
                    st.query_params[key] = str(value).strip()
        except Exception:
            return


def current_streamlit_url_path() -> str:
    """Best-effort path segment from st.context.url (empty outside Streamlit)."""
    try:
        url = getattr(st.context, "url", None)
    except Exception:
        url = None
    if not url:
        return ""
    path = urlparse(str(url)).path.strip("/")
    if not path:
        return ""
    parts = [part for part in path.split("/") if part]
    for part in reversed(parts):
        if is_discovery_url_path(part) or part.startswith("discovery"):
            return part
    return parts[-1] if parts else ""


def clear_discovery_project_ui_state() -> None:
    """Drop Discovery UI cache when the active project changes."""
    doomed = [
        key
        for key in list(st.session_state.keys())
        if isinstance(key, str)
        and key.startswith(_DISCOVERY_SESSION_PREFIXES)
        and key
        not in {
            ROUTE_HINT_KEY,
            ROUTE_ERROR_KEY,
            ROUTE_PAGE_SLUG_KEY,
            "discovery_v2_ui_flash",
        }
    ]
    for key in doomed:
        st.session_state.pop(key, None)


def write_discovery_route(
    *,
    project_id: str | None,
    page_slug: str | None,
) -> None:
    from otio_app.discovery_v2.application.project_route_service import (
        normalize_discovery_page_slug,
    )

    slug, _unknown = normalize_discovery_page_slug(page_slug)
    if slug is None:
        slug = DISCOVERY_SAFE_START_SLUG
    st.session_state[ROUTE_PAGE_SLUG_KEY] = slug
    _query_set(**{QUERY_PROJECT_ID: project_id, QUERY_PAGE: slug})


def bind_active_discovery_project(project_id: str, *, page_slug: str | None = None) -> None:
    previous = st.session_state.get(ACTIVE_PROJECT_KEY)
    if previous and previous != project_id:
        clear_discovery_project_ui_state()
    st.session_state[ACTIVE_PROJECT_KEY] = project_id
    st.session_state[_LAST_BOUND_PROJECT_KEY] = project_id
    slug = page_slug or st.session_state.get(ROUTE_PAGE_SLUG_KEY) or DISCOVERY_SAFE_START_SLUG
    write_discovery_route(project_id=project_id, page_slug=slug)


def restore_discovery_route_context() -> DiscoveryRouteResolution | None:
    """URL → Application Service → session. Call before mode/nav build.

    Returns a resolution when Discovery route context is present; otherwise None
    (Classic / Without-VO first-start paths unchanged).
    """
    url_project_id = _query_get(QUERY_PROJECT_ID)
    url_page = _query_get(QUERY_PAGE)
    url_path = current_streamlit_url_path()
    path_is_discovery = is_discovery_url_path(url_path) or (
        bool(url_path) and str(url_path).startswith("discovery")
    )

    if not url_project_id and not url_page and not path_is_discovery:
        # No Discovery route signal — leave Classic/Without defaults alone.
        st.session_state.pop(ROUTE_HINT_KEY, None)
        # Still sync outbound if a Discovery project is already active in session
        # and user navigates onto a Discovery page later (handled in page wrap).
        return None

    resolution = resolve_discovery_route(
        project_id=url_project_id,
        page_token=url_page,
        url_path=url_path or None,
    )

    if resolution.status == DiscoveryRouteStatus.OK and resolution.project is not None:
        bind_active_discovery_project(
            resolution.project.id,
            page_slug=resolution.page_slug,
        )
        st.session_state.pop(ROUTE_ERROR_KEY, None)
        st.session_state[ROUTE_HINT_KEY] = True
        return resolution

    # Error / missing project — keep Discovery shell, never silent Classic fallback.
    st.session_state[ROUTE_HINT_KEY] = True
    st.session_state[ROUTE_ERROR_KEY] = {
        "code": resolution.status.value,
        "message": resolution.message or resolution.status.value,
    }
    if resolution.project is not None and resolution.status == DiscoveryRouteStatus.PROJECT_MODE_MISMATCH:
        # Do not bind non-Discovery project as active Discovery context.
        st.session_state.pop(ACTIVE_PROJECT_KEY, None)
    elif resolution.status == DiscoveryRouteStatus.PROJECT_NOT_FOUND:
        st.session_state.pop(ACTIVE_PROJECT_KEY, None)
    elif resolution.status == DiscoveryRouteStatus.PROJECT_CONTEXT_MISSING:
        # Deep link page without id: do not invent a project from stale session
        # if URL explicitly omitted project_id on a discovery path.
        if url_project_id is None and (path_is_discovery or url_page):
            session_id = st.session_state.get(ACTIVE_PROJECT_KEY)
            if session_id:
                project = get_project_by_id(session_id)
                if project is not None and project.project_mode == ProjectMode.DISCOVERY_V2:
                    # Promote session project into the canonical URL.
                    bind_active_discovery_project(
                        project.id,
                        page_slug=resolution.page_slug or DISCOVERY_SAFE_START_SLUG,
                    )
                    st.session_state.pop(ROUTE_ERROR_KEY, None)
                    return resolve_discovery_route(
                        project_id=project.id,
                        page_token=resolution.page_slug,
                        url_path=url_path or None,
                    )
            st.session_state.pop(ACTIVE_PROJECT_KEY, None)
    elif resolution.status == DiscoveryRouteStatus.INVALID_DISCOVERY_ROUTE:
        if resolution.project is not None:
            bind_active_discovery_project(
                resolution.project.id,
                page_slug=resolution.fallback_page_slug or DISCOVERY_SAFE_START_SLUG,
            )
        else:
            write_discovery_route(
                project_id=resolution.project_id,
                page_slug=resolution.fallback_page_slug or DISCOVERY_SAFE_START_SLUG,
            )
        # Attempt controlled navigation to safe start page.
        _maybe_switch_to_safe_start(resolution.fallback_page_slug or DISCOVERY_SAFE_START_SLUG)

    st.session_state[ROUTE_PAGE_SLUG_KEY] = (
        resolution.fallback_page_slug
        or resolution.page_slug
        or DISCOVERY_SAFE_START_SLUG
    )
    return resolution


def _maybe_switch_to_safe_start(page_slug: str) -> None:
    path = url_path_for_slug(page_slug)
    if not path:
        return
    switch = getattr(st, "switch_page", None)
    if not callable(switch):
        return
    try:
        switch(path)
    except Exception:
        return


def discovery_shell_requested() -> bool:
    return bool(st.session_state.get(ROUTE_HINT_KEY))


def render_discovery_route_banner() -> None:
    payload = st.session_state.get(ROUTE_ERROR_KEY)
    if not payload:
        return
    message = payload.get("message") if isinstance(payload, dict) else str(payload)
    code = payload.get("code") if isinstance(payload, dict) else None
    if code:
        st.warning(f"{code}: {message}")
    else:
        st.warning(str(message))


def sync_discovery_page_route(page_slug: str) -> None:
    """Keep query page in sync when a Discovery page renders."""
    project_id = st.session_state.get(ACTIVE_PROJECT_KEY)
    write_discovery_route(project_id=project_id, page_slug=page_slug)


def render_discovery_project_selector(label: str = "Projekt") -> Project | None:
    """Project picker for Discovery pages; updates canonical route on change."""
    from otio_app.discovery_v2.ui.flash import consume_discovery_flash

    render_discovery_route_banner()
    consume_discovery_flash()

    projects = [
        project
        for project in list_projects()
        if project.project_mode == ProjectMode.DISCOVERY_V2
    ]
    if not projects:
        st.info(
            "Noch kein Discovery-V2-Projekt vorhanden. "
            "Lege zuerst unter „Neues Projekt“ eines an."
        )
        return None

    labels = {project.id: project.name for project in projects}
    default_id = st.session_state.get(ACTIVE_PROJECT_KEY)
    if default_id not in labels:
        default_id = None

    options = list(labels.keys())
    if default_id is None:
        # Force explicit choice when route had no valid project.
        selected_id = st.selectbox(
            label,
            options=options,
            format_func=lambda pid: labels[pid],
            index=None,
            placeholder="Discovery-V2-Projekt wählen…",
            key="discovery_v2_project_selector",
        )
    else:
        selected_id = st.selectbox(
            label,
            options=options,
            format_func=lambda pid: labels[pid],
            index=options.index(default_id),
            key="discovery_v2_project_selector",
        )

    if not selected_id:
        return None

    page_slug = st.session_state.get(ROUTE_PAGE_SLUG_KEY) or DISCOVERY_SAFE_START_SLUG
    previous = st.session_state.get(ACTIVE_PROJECT_KEY)
    if previous != selected_id:
        # Project switch: clear old UI state, reset to safe start page.
        clear_discovery_project_ui_state()
        bind_active_discovery_project(selected_id, page_slug=DISCOVERY_SAFE_START_SLUG)
        if previous is not None:
            _maybe_switch_to_safe_start(DISCOVERY_SAFE_START_SLUG)
            st.rerun()
    else:
        bind_active_discovery_project(selected_id, page_slug=page_slug)

    project = get_project_by_id(selected_id)
    if project is None:
        st.warning("project_not_found: Projekt konnte nicht geladen werden.")
        return None
    if project.project_mode != ProjectMode.DISCOVERY_V2:
        st.warning(
            "project_mode_mismatch: Dieses Projekt ist kein Discovery-V2-Projekt."
        )
        st.session_state.pop(ACTIVE_PROJECT_KEY, None)
        return None
    return project


__all__ = [
    "ROUTE_ERROR_KEY",
    "ROUTE_HINT_KEY",
    "ROUTE_PAGE_SLUG_KEY",
    "bind_active_discovery_project",
    "clear_discovery_project_ui_state",
    "current_streamlit_url_path",
    "discovery_shell_requested",
    "render_discovery_project_selector",
    "render_discovery_route_banner",
    "restore_discovery_route_context",
    "sync_discovery_page_route",
    "write_discovery_route",
]
