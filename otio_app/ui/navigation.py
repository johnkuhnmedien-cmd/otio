"""Navigation und Workflow-Schritte für die Streamlit-UI."""

from __future__ import annotations

PAGE_NEW = "Neues Projekt"
PAGE_LIST = "Gespeicherte Projekte"
PAGE_CLEAN_MEDIA = "⓪ Clean Media"
PAGE_ANALYSIS = "① Analysen"
PAGE_MAPPING = "② Zuordnung"
PAGE_SUPPLEMENT = "②½ Supplement Assets"
PAGE_EDIT_PLAN = "③ Schnittplan"
PAGE_STATUS = "Systemstatus"

WORKFLOW_PAGES = (PAGE_CLEAN_MEDIA, PAGE_ANALYSIS, PAGE_MAPPING, PAGE_SUPPLEMENT, PAGE_EDIT_PLAN)

NAVIGATION_OPTIONS = (
    PAGE_NEW,
    PAGE_LIST,
    PAGE_CLEAN_MEDIA,
    PAGE_ANALYSIS,
    PAGE_MAPPING,
    PAGE_SUPPLEMENT,
    PAGE_EDIT_PLAN,
    PAGE_STATUS,
)

ACTIVE_PROJECT_KEY = "active_project_id"
LAST_NAV_PAGE_KEY = "_last_nav_page"
