"""Application-Service: Discovery-V2-Routenauflösung (reload-fähig).

Kein Streamlit. Mode kommt aus dem persistierten Projekt, nicht aus der URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from otio_app.models import Project, ProjectMode
from otio_app.project_repository import get_project_by_id


QUERY_PROJECT_ID = "project_id"
QUERY_PAGE = "page"

DISCOVERY_SAFE_START_SLUG = "overview"

# slug -> Streamlit url_path
DISCOVERY_PAGE_URL_PATHS: Mapping[str, str] = {
    "overview": "discovery-v2",
    "inventory": "discovery-medienbestand",
    "technical_validation": "discovery-technische-pruefung",
    "media_intake": "discovery-media-intake",
    "asset_analysis": "discovery-assetanalyse",
    "editorial": "discovery-editorial",
    "narration": "discovery-narration",
    "visual_edit": "discovery-visual-edit",
    "review_export": "discovery-review-export",
    "settings": "discovery-settings",
}

DISCOVERY_PAGE_ALLOWLIST = frozenset(DISCOVERY_PAGE_URL_PATHS.keys())

_URL_PATH_TO_SLUG: Mapping[str, str] = {
    path: slug for slug, path in DISCOVERY_PAGE_URL_PATHS.items()
}


class DiscoveryRouteStatus(str, Enum):
    OK = "ok"
    PROJECT_CONTEXT_MISSING = "project_context_missing"
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_MODE_MISMATCH = "project_mode_mismatch"
    INVALID_DISCOVERY_ROUTE = "invalid_discovery_route"


@dataclass(frozen=True)
class DiscoveryRouteResolution:
    status: DiscoveryRouteStatus
    project_id: str | None
    project: Project | None
    page_slug: str | None
    url_path: str | None
    fallback_page_slug: str | None
    message: str | None
    keep_discovery_shell: bool

    @property
    def ok(self) -> bool:
        return self.status == DiscoveryRouteStatus.OK and self.project is not None


def slug_for_url_path(url_path: str | None) -> str | None:
    if not url_path:
        return None
    cleaned = str(url_path).strip().strip("/")
    if cleaned in _URL_PATH_TO_SLUG:
        return _URL_PATH_TO_SLUG[cleaned]
    if cleaned in DISCOVERY_PAGE_ALLOWLIST:
        return cleaned
    return None


def url_path_for_slug(page_slug: str | None) -> str | None:
    if not page_slug:
        return None
    return DISCOVERY_PAGE_URL_PATHS.get(str(page_slug).strip())


def normalize_discovery_page_slug(raw: str | None) -> tuple[str | None, bool]:
    """Normalize page token.

    Returns ``(slug_or_none, provided_but_unknown)``.
    Empty/None → ``(None, False)``.
    Known slug or url_path → ``(slug, False)``.
    Unknown non-empty → ``(None, True)``.
    """
    if raw is None:
        return None, False
    token = str(raw).strip().strip("/")
    if not token:
        return None, False
    if token in DISCOVERY_PAGE_ALLOWLIST:
        return token, False
    slug = _URL_PATH_TO_SLUG.get(token)
    if slug is not None:
        return slug, False
    return None, True


def is_discovery_url_path(url_path: str | None) -> bool:
    return slug_for_url_path(url_path) is not None


def resolve_discovery_route(
    *,
    project_id: str | None,
    page_token: str | None = None,
    url_path: str | None = None,
    db_path=None,
) -> DiscoveryRouteResolution:
    """Resolve project + page for Discovery reload / deep link."""
    path_slug = slug_for_url_path(url_path)
    page_slug, page_unknown = normalize_discovery_page_slug(page_token)
    effective_slug = path_slug or page_slug
    discovery_context = bool(effective_slug or page_unknown or (url_path and "discovery" in str(url_path)))

    if page_unknown and not path_slug:
        # Unknown page with optional project — keep project, fall back to overview.
        project = None
        pid = (project_id or "").strip() or None
        if pid:
            project = get_project_by_id(pid, db_path=db_path)
            if project is None:
                return DiscoveryRouteResolution(
                    status=DiscoveryRouteStatus.PROJECT_NOT_FOUND,
                    project_id=pid,
                    project=None,
                    page_slug=DISCOVERY_SAFE_START_SLUG,
                    url_path=url_path_for_slug(DISCOVERY_SAFE_START_SLUG),
                    fallback_page_slug=DISCOVERY_SAFE_START_SLUG,
                    message=(
                        f"Projekt `{pid}` wurde nicht gefunden. "
                        "Bitte ein Discovery-V2-Projekt auswählen."
                    ),
                    keep_discovery_shell=True,
                )
            if project.project_mode != ProjectMode.DISCOVERY_V2:
                return DiscoveryRouteResolution(
                    status=DiscoveryRouteStatus.PROJECT_MODE_MISMATCH,
                    project_id=pid,
                    project=project,
                    page_slug=DISCOVERY_SAFE_START_SLUG,
                    url_path=url_path_for_slug(DISCOVERY_SAFE_START_SLUG),
                    fallback_page_slug=DISCOVERY_SAFE_START_SLUG,
                    message=(
                        "Dieses Projekt ist kein Discovery-V2-Projekt und kann "
                        "hier nicht geöffnet werden. Bitte ein Discovery-V2-Projekt wählen."
                    ),
                    keep_discovery_shell=True,
                )
        return DiscoveryRouteResolution(
            status=DiscoveryRouteStatus.INVALID_DISCOVERY_ROUTE,
            project_id=pid,
            project=project,
            page_slug=DISCOVERY_SAFE_START_SLUG,
            url_path=url_path_for_slug(DISCOVERY_SAFE_START_SLUG),
            fallback_page_slug=DISCOVERY_SAFE_START_SLUG,
            message=(
                "Unbekannte Discovery-Seite. Wechsel zur Übersicht "
                f"(`{DISCOVERY_SAFE_START_SLUG}`)."
            ),
            keep_discovery_shell=True,
        )

    pid = (project_id or "").strip() or None
    if not pid:
        return DiscoveryRouteResolution(
            status=DiscoveryRouteStatus.PROJECT_CONTEXT_MISSING,
            project_id=None,
            project=None,
            page_slug=effective_slug or DISCOVERY_SAFE_START_SLUG,
            url_path=url_path_for_slug(effective_slug or DISCOVERY_SAFE_START_SLUG),
            fallback_page_slug=None,
            message="Kein Projekt in der Route. Bitte ein Discovery-V2-Projekt auswählen.",
            keep_discovery_shell=discovery_context,
        )

    project = get_project_by_id(pid, db_path=db_path)
    if project is None:
        return DiscoveryRouteResolution(
            status=DiscoveryRouteStatus.PROJECT_NOT_FOUND,
            project_id=pid,
            project=None,
            page_slug=effective_slug or DISCOVERY_SAFE_START_SLUG,
            url_path=url_path_for_slug(effective_slug or DISCOVERY_SAFE_START_SLUG),
            fallback_page_slug=None,
            message=(
                f"Projekt `{pid}` wurde nicht gefunden. "
                "Bitte ein Discovery-V2-Projekt auswählen."
            ),
            keep_discovery_shell=True,
        )

    if project.project_mode != ProjectMode.DISCOVERY_V2:
        return DiscoveryRouteResolution(
            status=DiscoveryRouteStatus.PROJECT_MODE_MISMATCH,
            project_id=pid,
            project=project,
            page_slug=effective_slug,
            url_path=url_path_for_slug(effective_slug) if effective_slug else None,
            fallback_page_slug=None,
            message=(
                "Projektmodus stimmt nicht mit Discovery V2 überein. "
                "Das Projekt wird nicht als Discovery V2 geöffnet."
            ),
            keep_discovery_shell=discovery_context,
        )

    slug = effective_slug or DISCOVERY_SAFE_START_SLUG
    return DiscoveryRouteResolution(
        status=DiscoveryRouteStatus.OK,
        project_id=pid,
        project=project,
        page_slug=slug,
        url_path=url_path_for_slug(slug),
        fallback_page_slug=None,
        message=None,
        keep_discovery_shell=True,
    )


__all__ = [
    "QUERY_PAGE",
    "QUERY_PROJECT_ID",
    "DISCOVERY_SAFE_START_SLUG",
    "DISCOVERY_PAGE_ALLOWLIST",
    "DISCOVERY_PAGE_URL_PATHS",
    "DiscoveryRouteResolution",
    "DiscoveryRouteStatus",
    "is_discovery_url_path",
    "normalize_discovery_page_slug",
    "resolve_discovery_route",
    "slug_for_url_path",
    "url_path_for_slug",
]
