"""Navigation und Workflow-Schritte für die Streamlit-UI."""

from __future__ import annotations

PAGE_NEW = "Neues Projekt"
PAGE_LIST = "Gespeicherte Projekte"
PAGE_ANALYSIS = "① Analysen"
PAGE_MAPPING = "② Zuordnung"
PAGE_EDIT_PLAN = "③ Schnittplan"
PAGE_STATUS = "Systemstatus"

WORKFLOW_PAGES = (PAGE_ANALYSIS, PAGE_MAPPING, PAGE_EDIT_PLAN)

NAVIGATION_OPTIONS = (
    PAGE_NEW,
    PAGE_LIST,
    PAGE_ANALYSIS,
    PAGE_MAPPING,
    PAGE_EDIT_PLAN,
    PAGE_STATUS,
)

ACTIVE_PROJECT_KEY = "active_project_id"
