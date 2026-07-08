"""Navigation und Workflow-Schritte für die Streamlit-UI."""

from __future__ import annotations

PAGE_NEW = "Neues Projekt"
PAGE_LIST = "Gespeicherte Projekte"
PAGE_CLEAN_MEDIA = "⓪ Clean Media"
PAGE_ANALYSIS = "① Analysen"
PAGE_MAPPING = "② Zuordnung"
PAGE_SUPPLEMENT = "②½ Supplement Assets"
PAGE_EDIT_PLAN = "③ Schnittplan"
PAGE_API_KEYS = "🔑 API-Schlüssel"
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
    PAGE_API_KEYS,
    PAGE_STATUS,
)

# --- "Projekt ohne Voice-Over": eigene Seitenliste, ersetzt Zuordnung/Supplement/Schnittplan ---
PAGE_PROJECT_BRIEF = "① Project Brief"
PAGE_STYLE_REFERENCES = "② Style References"
PAGE_DRAMATURGY = "③ Dramaturgie"
PAGE_FOLDER_VOICEOVERS = "④ Folder Voice-overs"
PAGE_INTRO = "⑤ Intro"
PAGE_AUDIO = "⑥ Audio / ElevenLabs"
PAGE_FINAL_OUTPUT = "⑦ Final Output"

VOICEOVER_GEN_WORKFLOW_PAGES = (
    PAGE_CLEAN_MEDIA,
    PAGE_ANALYSIS,
    PAGE_PROJECT_BRIEF,
    PAGE_STYLE_REFERENCES,
    PAGE_DRAMATURGY,
    PAGE_FOLDER_VOICEOVERS,
    PAGE_INTRO,
    PAGE_AUDIO,
    PAGE_FINAL_OUTPUT,
)

VOICEOVER_GEN_NAVIGATION_OPTIONS = (
    (PAGE_NEW, PAGE_LIST)
    + VOICEOVER_GEN_WORKFLOW_PAGES
    + (PAGE_API_KEYS, PAGE_STATUS)
)

ACTIVE_PROJECT_KEY = "active_project_id"
LAST_NAV_PAGE_KEY = "_last_nav_page"
