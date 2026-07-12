"""Zentrale Standardwerte — unabhängig von Pfad- und DB-Konfiguration."""

from __future__ import annotations

DEFAULT_WORK_SUBDIR = "_otio"
DEFAULT_VOICE_OVER_SUBDIR = "Voice over"
DEFAULT_FRAMES_PER_SHOT = 3
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_VOICE_BACKEND = "whisper"
DEFAULT_WHISPER_MODEL = "large-v3"
VOICE_BACKEND_WHISPER = "whisper"
VOICE_BACKEND_GEMINI = "gemini"
VOICE_BACKEND_CHOICES = (
    VOICE_BACKEND_WHISPER,
    VOICE_BACKEND_GEMINI,
)
VOICE_BACKEND_LABELS = {
    VOICE_BACKEND_WHISPER: "Whisper (lokal, kostenlos)",
    VOICE_BACKEND_GEMINI: "Gemini (Cloud, kostenpflichtig)",
}
WHISPER_MODEL_CHOICES = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
)
WHISPER_MODEL_LABELS = {
    "tiny": "Whisper tiny — am schnellsten, grober",
    "base": "Whisper base — schnell",
    "small": "Whisper small — schnell, ausgewogen",
    "medium": "Whisper medium — genauer, langsamer",
    "large-v3": "Whisper large-v3 — beste Qualität, am langsamsten (Standard)",
}
GEMINI_MODEL_CHOICES = (
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
)
GEMINI_MODEL_LABELS = {
    "gemini-3-flash-preview": "Gemini 3 Flash Preview — schnell, Pro-Niveau (Preview)",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite — günstig, Preview (Standard)",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview — beste Qualität (Preview, teurer)",
}

OPENAI_PLAN_MODEL_CHOICES = (
    "openai:gpt-5.5",
    "openai:gpt-5.4-mini",
)
ANTHROPIC_PLAN_MODEL_CHOICES = (
    "anthropic:claude-opus-4-8",
    "anthropic:claude-sonnet-5",
)
EDIT_PLAN_MODEL_CHOICES = GEMINI_MODEL_CHOICES + OPENAI_PLAN_MODEL_CHOICES + ANTHROPIC_PLAN_MODEL_CHOICES
EDIT_PLAN_MODEL_LABELS = {
    **GEMINI_MODEL_LABELS,
    "openai:gpt-5.5": "ChatGPT GPT-5.5 — Flagship, beste Qualität",
    "openai:gpt-5.4-mini": "ChatGPT GPT-5.4 mini — günstig, schnell",
    "anthropic:claude-opus-4-8": "Claude Opus 4.8 — Flagship, beste Qualität",
    "anthropic:claude-sonnet-5": "Claude Sonnet 5 — ausgewogen, günstiger",
}
INVENTORY_FILENAME = "inventory.json"
INVENTORY_SUBDIR = "inventory"
MANUAL_FOLDER_COMPLETION_FILENAME = "manual_folder_completion.json"
VOICE_ANALYSIS_FILENAME = "voice_over_analysis.json"
VOICE_FOLDER_MAPPING_FILENAME = "voice_folder_mapping.json"
EDIT_PLAN_FILENAME = "edit_plan.json"
EDIT_PLAN_SUBDIR = "edit_plan"

PROJECT_MODE_WITH_VOICEOVER = "with_voiceover"
PROJECT_MODE_WITHOUT_VOICEOVER = "without_voiceover"
PROJECT_MODE_CHOICES = (PROJECT_MODE_WITH_VOICEOVER, PROJECT_MODE_WITHOUT_VOICEOVER)
PROJECT_MODE_LABELS = {
    PROJECT_MODE_WITH_VOICEOVER: "Projekt mit Voice-Over",
    PROJECT_MODE_WITHOUT_VOICEOVER: "Projekt ohne Voice-Over",
}

VOICEOVER_GENERATION_SUBDIR = "voiceover_generation"
VOICEOVER_GENERATION_LLM_RUNS_SUBDIR = "llm_runs"
VOICEOVER_GENERATION_AUDIO_SUBDIR = "audio"
VOICEOVER_GENERATION_TTS_RUNS_SUBDIR = "tts_runs"
VOICEOVER_GENERATION_INTRO_AUDIO_FOLDER_NAME = "000_intro"
STYLE_REFERENCES_SUBDIR = "style_references"
STYLE_REFERENCES_UPLOADS_SUBDIR = "uploads"
MODEL_SETTINGS_FILENAME = "model_settings.json"

PROJECT_BRIEF_FILENAME = "project_brief.json"
VOICEOVER_STYLE_REFERENCES_FILENAME = "voiceover_style_references.json"
VOICEOVER_STYLE_PROFILE_FILENAME = "voiceover_style_profile.json"
DRAMATURGY_PLAN_DRAFT_FILENAME = "dramaturgy_plan.draft.json"
DRAMATURGY_PLAN_CONFIRMED_FILENAME = "dramaturgy_plan.confirmed.json"
FOLDER_VOICEOVER_SETTINGS_FILENAME = "folder_voiceover_settings.json"
FOLDER_VOICEOVERS_DRAFT_FILENAME = "folder_voiceovers.draft.json"
FOLDER_VOICEOVER_VALIDATION_REPORT_FILENAME = "folder_voiceover_validation_report.json"
FOLDER_VOICEOVERS_CONFIRMED_FILENAME = "folder_voiceovers.confirmed.json"
INTRO_HOOK_CANDIDATES_FILENAME = "intro_hook_candidates.json"
INTRO_HOOK_CONFIRMED_FILENAME = "intro_hook.confirmed.json"
ELEVENLABS_SETTINGS_FILENAME = "elevenlabs_settings.json"
VOICEOVER_AUDIO_MANIFEST_FILENAME = "voiceover_audio_manifest.json"
VOICEOVER_AUDIO_QA_REPORT_FILENAME = "voiceover_audio_qa_report.json"
CONFIRMED_VOICEOVER_PROJECT_PLAN_FILENAME = "confirmed_voiceover_project_plan.json"
VOICEOVER_PROJECT_PLAN_JSON_FILENAME = "voiceover_project_plan.json"
VOICEOVER_PROJECT_PLAN_MD_FILENAME = "voiceover_project_plan.md"
VOICEOVER_PROJECT_PLAN_CSV_FILENAME = "voiceover_project_plan.csv"

# --- LLM-Provider/Modell-Presets für die Voice-over-Generierungs-Pipeline ---
# Eigenständig von EDIT_PLAN_MODEL_CHOICES: hier werden provider und model als
# getrennte Felder gespeichert (siehe VoiceoverGenerationModelSettings), daher
# ohne "anthropic:"/"openai:"-Präfix.
VOICEOVER_GEN_PROVIDERS = ("anthropic", "openai", "gemini")
VOICEOVER_GEN_MODEL_PRESETS: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"),
    "openai": ("gpt-5.5", "gpt-5.4-mini"),
    "gemini": ("gemini-3.1-pro-preview", "gemini-3.1-flash-lite"),
}
VOICEOVER_GEN_ROLE_STYLE_PROFILE = "style_profile"
VOICEOVER_GEN_ROLE_DRAMATURGY = "dramaturgy"
VOICEOVER_GEN_ROLE_VOICEOVER_AUTHOR = "voiceover_author"
VOICEOVER_GEN_ROLE_VOICEOVER_REVIEW = "voiceover_review"
VOICEOVER_GEN_ROLE_INTRO = "intro"
# Phase 11.1: separate Rolle für die Cut-Plan-Supplement-Query-Generierung
# (Pexels-Suchqueries) — bewusst NICHT dieselbe Rolle wie voiceover_author/
# -review, da hier standardmäßig ein anderes Modell (Gemini, schnell/günstig)
# sinnvoll ist und die Nutzung ausschließlich beim Klick auf „Supplement-
# Kandidaten suchen“ im Cut-Plan-Tab erfolgt.
VOICEOVER_GEN_ROLE_CUT_PLAN_SUPPLEMENT_QUERY = "cut_plan_supplement_query"
VOICEOVER_GEN_ROLES = (
    VOICEOVER_GEN_ROLE_STYLE_PROFILE,
    VOICEOVER_GEN_ROLE_DRAMATURGY,
    VOICEOVER_GEN_ROLE_VOICEOVER_AUTHOR,
    VOICEOVER_GEN_ROLE_VOICEOVER_REVIEW,
    VOICEOVER_GEN_ROLE_INTRO,
    VOICEOVER_GEN_ROLE_CUT_PLAN_SUPPLEMENT_QUERY,
)
VOICEOVER_GEN_ROLE_LABELS = {
    VOICEOVER_GEN_ROLE_STYLE_PROFILE: "Style Profile",
    VOICEOVER_GEN_ROLE_DRAMATURGY: "Dramaturgie",
    VOICEOVER_GEN_ROLE_VOICEOVER_AUTHOR: "Voice-over Autor",
    VOICEOVER_GEN_ROLE_VOICEOVER_REVIEW: "Voice-over Review",
    VOICEOVER_GEN_ROLE_INTRO: "Intro",
    VOICEOVER_GEN_ROLE_CUT_PLAN_SUPPLEMENT_QUERY: "Cut Plan Suchqueries",
}
VOICEOVER_GEN_DEFAULT_PROVIDER = "anthropic"
VOICEOVER_GEN_DEFAULT_MODEL = "claude-sonnet-5"
# Phase 11.1: Standard für die neue Rolle — bewusst Gemini 3.1 Flash Lite
# (schnell, günstig) statt des allgemeinen Anthropic-Standards oben, da diese
# Rolle nur kurze Suchqueries generiert, keine langen redaktionellen Texte.
VOICEOVER_GEN_CUT_PLAN_SUPPLEMENT_QUERY_DEFAULT_PROVIDER = "gemini"
VOICEOVER_GEN_CUT_PLAN_SUPPLEMENT_QUERY_DEFAULT_MODEL = "gemini-3.1-flash-lite"

# --- Vereinfachte Modellauswahl: EIN Dropdown je Rolle (kein Freitext, keine
# separate Provider-Spalte). Die IDs folgen exakt der Konvention von
# resolve_llm_model_id()/split_llm_model_id() ("openai:"/"anthropic:"-Präfix,
# Gemini ohne Präfix) und werden aus VOICEOVER_GEN_MODEL_PRESETS abgeleitet. ---
VOICEOVER_GEN_MODEL_CHOICES: tuple[str, ...] = (
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "openai:gpt-5.5",
    "openai:gpt-5.4-mini",
    "anthropic:claude-opus-4-8",
    "anthropic:claude-sonnet-5",
    "anthropic:claude-haiku-4-5",
)
VOICEOVER_GEN_MODEL_LABELS: dict[str, str] = {
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview — beste Qualität (Preview, teurer)",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite — günstig, Preview",
    "openai:gpt-5.5": "ChatGPT GPT-5.5 — Flagship, beste Qualität",
    "openai:gpt-5.4-mini": "ChatGPT GPT-5.4 mini — günstig, schnell",
    "anthropic:claude-opus-4-8": "Claude Opus 4.8 — Flagship, beste Qualität",
    "anthropic:claude-sonnet-5": "Claude Sonnet 5 — ausgewogen (Standard)",
    "anthropic:claude-haiku-4-5": "Claude Haiku 4.5 — sehr schnell, günstig",
}

# --- Style Profile Bibliothek: projektübergreifend unter data/ gespeichert
# (siehe otio_app.config.ensure_data_dir), NICHT unter dem Arbeitsordner eines
# einzelnen Projekts. ---
STYLE_PROFILE_LIBRARY_FILENAME = "style_profile_library.json"

# --- Project Brief: feste Auswahllisten ---
BRIEF_LANGUAGE_CHOICES = ("DE", "EN", "FR", "ES", "PT", "IT")
BRIEF_TONE_TAG_CHOICES = (
    "cinematic",
    "documentary",
    "discovery",
    "calm",
    "dramatic",
    "premium travel",
    "mysterious",
    "energetic",
    "poetic but factual",
    "high-end YouTube documentary",
)
BRIEF_NEGATIVE_RULE_NO_UNVERIFIED_HISTORICAL_CLAIMS = "no_unverified_historical_claims"
# Nutzerfeedback (Juli 2026): die bisherigen Negativregeln waren für den
# konkreten Anwendungsfall unvollständig/missverständlich — vier neue,
# vom Nutzer explizit gewünschte Standardregeln ergänzt. Die übrigen,
# ungenutzten Standardregeln (no_invented_facts, no_exaggerated_superlatives,
# no_clickbait_phrases, no_repetition, no_direct_viewer_address,
# no_in_this_video_phrasing, not_just_asset_descriptions,
# documentary_prose_required) wurden auf ausdrücklichen Wunsch aus der UI
# entfernt, da sie im laufenden Projekt nie aktiviert waren.
BRIEF_NEGATIVE_RULE_BIBLICAL_CHRONOLOGY_REQUIRED = "biblical_chronology_required"
BRIEF_NEGATIVE_RULE_NO_PARTY_SCENES = "no_party_scenes"
BRIEF_NEGATIVE_RULE_VOICE_NOT_AI_SOUNDING = "voice_not_ai_sounding"
BRIEF_NEGATIVE_RULE_NO_CLICHES = "no_cliches"
BRIEF_NEGATIVE_RULE_FLAGS = (
    BRIEF_NEGATIVE_RULE_NO_UNVERIFIED_HISTORICAL_CLAIMS,
    BRIEF_NEGATIVE_RULE_BIBLICAL_CHRONOLOGY_REQUIRED,
    BRIEF_NEGATIVE_RULE_NO_PARTY_SCENES,
    BRIEF_NEGATIVE_RULE_VOICE_NOT_AI_SOUNDING,
    BRIEF_NEGATIVE_RULE_NO_CLICHES,
)
BRIEF_NEGATIVE_RULE_LABELS = {
    BRIEF_NEGATIVE_RULE_NO_UNVERIFIED_HISTORICAL_CLAIMS: "Keine unbelegten historischen Behauptungen",
    BRIEF_NEGATIVE_RULE_BIBLICAL_CHRONOLOGY_REQUIRED: (
        "Zeitangaben müssen mit der biblischen Zeitrechnung übereinstimmen"
    ),
    BRIEF_NEGATIVE_RULE_NO_PARTY_SCENES: "Keine Partyszenen",
    BRIEF_NEGATIVE_RULE_VOICE_NOT_AI_SOUNDING: "Voice-over darf nicht nach KI klingen",
    BRIEF_NEGATIVE_RULE_NO_CLICHES: "Keine Floskeln / abgenutzte Redewendungen",
}
# Ausführliche, an das LLM gerichtete Formulierung je Regel (Englisch, da die
# Prompts selbst auf Englisch sind) — macht die kompakten Regel-Keys im
# Prompt selbsterklärend, statt dass das LLM (oder der Nutzer beim Lesen
# eines Prompt-Exports) allein aus dem Key auf die Bedeutung schließen muss.
BRIEF_NEGATIVE_RULE_INSTRUCTIONS: dict[str, str] = {
    BRIEF_NEGATIVE_RULE_NO_UNVERIFIED_HISTORICAL_CLAIMS: (
        "Do not state historical claims that are not verifiable or widely accepted as fact."
    ),
    BRIEF_NEGATIVE_RULE_BIBLICAL_CHRONOLOGY_REQUIRED: (
        "Any time references, ages, or historical/geological dates must be consistent with "
        "biblical chronology. Do not use secular/mainstream scientific dating (e.g. millions "
        "or billions of years) that contradicts it."
    ),
    BRIEF_NEGATIVE_RULE_NO_PARTY_SCENES: (
        "Do not describe or reference party scenes, nightlife, clubbing, or a celebratory "
        "party atmosphere."
    ),
    BRIEF_NEGATIVE_RULE_VOICE_NOT_AI_SOUNDING: (
        "The narration must read like natural, human documentary narration — avoid robotic, "
        "generic, or obviously AI-generated phrasing patterns."
    ),
    BRIEF_NEGATIVE_RULE_NO_CLICHES: (
        'Avoid clichés, stock phrases, and overused documentary filler expressions (e.g. "a '
        'place like no other", "steeped in history", "a hidden gem").'
    ),
}
DEFAULT_NEGATIVE_RULE_FLAGS: dict[str, bool] = {flag: True for flag in BRIEF_NEGATIVE_RULE_FLAGS}

# --- Dramaturgie: Wortanzahl-Heuristik pro Ordner (Phase 3) ---
# Ausgangsheuristik — soll später in der UI pro Projekt überschreibbar sein.
VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT = 10
VOICEOVER_GEN_MIN_FOLDER_WORDS = 50
VOICEOVER_GEN_MAX_FOLDER_WORDS = 180

# --- Asset-bewusste Cut-Plan-Vorbereitung, Phase 1 (Juli 2026 Nutzerwunsch):
# neuer Standard-Zielwortanzahl-Bereich für Folder Voice-overs. Kürzere
# Segmente lassen dem späteren Cut Plan mehr Spielraum bei Asset-Auswahl/
# Split, ohne die Emotion/Atmosphäre des Textes zu verlieren. Gilt NUR für
# NEU erzeugte FolderVoiceoverSetting-Werte (Modell-Default, Fallback bei
# fehlender Dramaturgie-Empfehlung) sowie für den expliziten Button
# "Zielwortanzahl 135 auf alle aktiven Folder anwenden" — überschreibt
# NIEMALS automatisch bereits gespeicherte folder_voiceover_settings.json.
VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS = 135
VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS = 120
VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS = 150

# --- Asset-bewusste Cut-Plan-Vorbereitung, Phase 2: reine Diagnose-Heuristik
# für die Folder-Voice-over-Asset-Readiness (siehe
# folder_asset_readiness.py). Bewusst ENTKOPPELT von den echten
# CUT_PLAN_DEFAULT_SHOT_*_SEC-Werten — Cut-Plan-Settings sind pro Projekt
# änderbar und gehören zu einer anderen Domäne; diese Heuristik ist nur ein
# früher, ungefährer Hinweis (VOR jeder Audio-Synthese, also ohne echte
# Alignment-Dauer), kein verbindlicher technischer Wert.
FOLDER_ASSET_READINESS_SHOT_MAX_SEC_HEURISTIC = 8.0
FOLDER_ASSET_READINESS_WORDS_PER_SECOND_HEURISTIC = 2.5

# Nutzervorgabe (Juli 2026): globale Asset-Allokationsregeln PRO ORDNER
# (Intro zählt NICHT mit, siehe folder_asset_readiness.py) — zählt jedes
# Vorkommen eines Assets über primary/backup/second_backup/planned_segments
# UND den Closing Shot (siehe ClosingVisualPlan in models.py). Dieselben
# Zahlen werden im Autor-Prompt kommuniziert (siehe prompts.py, "Asset
# allocation across this whole location").
FOLDER_ASSET_READINESS_MAX_TOTAL_OCCURRENCES_PER_ASSET = 3
FOLDER_ASSET_READINESS_MIN_REUSE_DISTANCE_SHOTS = 4
# Schwelle für die Bulk-Aktion „strict_inventory_only + neu generieren“:
# Ordner mit mindestens so vielen Asset-Readiness-Issues werden umgestellt.
# Pro Projekt überschreibbar (siehe asset_readiness_pipeline_settings.json).
FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD = 4
ASSET_READINESS_PIPELINE_SETTINGS_FILENAME = "asset_readiness_pipeline_settings.json"

# Nutzervorgabe (Juli 2026): maximale Anzahl automatischer Korrektur-
# Versuche für die Asset-Allokations-Correction (siehe
# folder_asset_allocation_correction_service.py) — analog zu
# MAX_VOICEOVER_REVIEW_ATTEMPTS, aber ein eigener, kleinerer Wert, da diese
# Correction ausschließlich Asset-Zuordnung repariert (kein Text-Review).
MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS = 2

FOLDER_INVENTORY_SUMMARIES_FILENAME = "folder_inventory_summaries.json"

DRAMATURGY_ROLE_OPENER = "opener"
DRAMATURGY_ROLE_SETUP = "setup"
DRAMATURGY_ROLE_CONTRAST = "contrast"
DRAMATURGY_ROLE_ESCALATION = "escalation"
DRAMATURGY_ROLE_CLIMAX = "climax"
DRAMATURGY_ROLE_RESOLUTION = "resolution"
DRAMATURGY_ROLES = (
    DRAMATURGY_ROLE_OPENER,
    DRAMATURGY_ROLE_SETUP,
    DRAMATURGY_ROLE_CONTRAST,
    DRAMATURGY_ROLE_ESCALATION,
    DRAMATURGY_ROLE_CLIMAX,
    DRAMATURGY_ROLE_RESOLUTION,
)
DRAMATURGY_ROLE_LABELS = {
    DRAMATURGY_ROLE_OPENER: "Opener",
    DRAMATURGY_ROLE_SETUP: "Setup",
    DRAMATURGY_ROLE_CONTRAST: "Kontrast",
    DRAMATURGY_ROLE_ESCALATION: "Steigerung",
    DRAMATURGY_ROLE_CLIMAX: "Höhepunkt",
    DRAMATURGY_ROLE_RESOLUTION: "Ausklang",
}
DRAMATURGY_STATUS_DRAFT = "DRAFT"
DRAMATURGY_STATUS_CONFIRMED = "CONFIRMED"

# --- Folder Voice-overs (Phase 4) ---
MAX_VOICEOVER_REVIEW_ATTEMPTS = 3
WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD = 0.4

FACTUALITY_MODE_STRICT_INVENTORY_ONLY = "strict_inventory_only"
FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE = "normal_safe_general_knowledge"
FACTUALITY_MODE_ATMOSPHERIC_NO_HARD_FACTS = "atmospheric_no_hard_facts"
FACTUALITY_MODE_CHOICES = (
    FACTUALITY_MODE_STRICT_INVENTORY_ONLY,
    FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE,
    FACTUALITY_MODE_ATMOSPHERIC_NO_HARD_FACTS,
)
FACTUALITY_MODE_LABELS = {
    FACTUALITY_MODE_STRICT_INVENTORY_ONLY: "Nur Inventory (streng)",
    FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE: "Normal (sicheres Allgemeinwissen)",
    FACTUALITY_MODE_ATMOSPHERIC_NO_HARD_FACTS: "Atmosphärisch (keine harten Fakten)",
}

ENERGY_CALM = "calm"
ENERGY_MEDIUM = "medium"
ENERGY_HIGH = "high"
ENERGY_CHOICES = (ENERGY_CALM, ENERGY_MEDIUM, ENERGY_HIGH)
ENERGY_LABELS = {
    ENERGY_CALM: "Ruhig",
    ENERGY_MEDIUM: "Mittel",
    ENERGY_HIGH: "Energiegeladen",
}

# --- Asset-bewusste Cut-Plan-Vorbereitung, Phase 7.1: Segment-Planungsmodus
# (Nutzerwunsch, Juli 2026) — steuert, WIE das Autor-LLM mit Shot-Aufteilung
# innerhalb eines Satzes umgeht (siehe prompts.py:
# _segment_asset_planning_block). Default ist bewusst PER_SENTENCE (heutiges
# Verhalten) — bestehende folder_voiceover_settings.json ohne dieses Feld
# ändern dadurch NICHT ihr Verhalten.
SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE = "per_sentence"
SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT = "per_segment"
SEGMENT_ASSET_PLANNING_MODE_LLM_DISCRETION = "llm_discretion"
SEGMENT_ASSET_PLANNING_MODE_CHOICES = (
    SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE,
    SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT,
    SEGMENT_ASSET_PLANNING_MODE_LLM_DISCRETION,
)
SEGMENT_ASSET_PLANNING_MODE_LABELS = {
    SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE: "Wie bisher: 1 Asset pro Satz",
    SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT: "Aktiv pro Segment aufteilen",
    SEGMENT_ASSET_PLANNING_MODE_LLM_DISCRETION: "LLM entscheidet (abwechslungsreich, aber ruhig)",
}
SEGMENT_ASSET_PLANNING_MODE_DEFAULT = SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE

VOICEOVER_STATUS_DRAFT = "DRAFT"
VOICEOVER_STATUS_NEEDS_VALIDATION = "NEEDS_VALIDATION"
VOICEOVER_STATUS_PASS = "PASS"
VOICEOVER_STATUS_NEEDS_USER_REVIEW = "NEEDS_USER_REVIEW"
VOICEOVER_STATUS_CONFIRMED = "CONFIRMED"
VOICEOVER_STATUS_PARTIAL = "PARTIAL"
VOICEOVER_STATUS_UNKNOWN = "UNKNOWN"
VOICEOVER_SETTING_STATUS_PENDING = "PENDING"

# Fehlertypen: deterministisch (Python) vs. LLM-Review (weiche Kriterien) — Phase 4 §8.
VO_ERROR_WORD_COUNT_OUT_OF_RANGE = "WORD_COUNT_OUT_OF_RANGE"
VO_ERROR_LANGUAGE_MISMATCH = "LANGUAGE_MISMATCH"
VO_ERROR_FORBIDDEN_TERM_USED = "FORBIDDEN_TERM_USED"
VO_ERROR_GLOBAL_NEGATIVE_RULE_VIOLATED = "GLOBAL_NEGATIVE_RULE_VIOLATED"
VO_ERROR_FOLDER_NEGATIVE_RULE_VIOLATED = "FOLDER_NEGATIVE_RULE_VIOLATED"
VO_ERROR_HALLUCINATED_FACT = "HALLUCINATED_FACT"
VO_ERROR_TOO_GENERIC = "TOO_GENERIC"
VO_ERROR_TOO_ASSET_DESCRIPTIVE = "TOO_ASSET_DESCRIPTIVE"
VO_ERROR_MISSING_TRANSITION = "MISSING_TRANSITION"
VO_ERROR_MISSING_TRANSITION_TO_NEXT = "MISSING_TRANSITION_TO_NEXT"
VO_ERROR_MISSING_CONTRAST_OR_COMMONALITY = "MISSING_CONTRAST_OR_COMMONALITY"
VO_ERROR_DOES_NOT_MATCH_ASSETS = "DOES_NOT_MATCH_ASSETS"
VO_ERROR_MISSING_ASSET_MAPPING = "MISSING_ASSET_MAPPING"
VO_ERROR_INVALID_ASSET_ID = "INVALID_ASSET_ID"
VO_ERROR_WEAK_ASSET_MATCH = "WEAK_ASSET_MATCH"
VO_ERROR_MISSING_SUPPLEMENT_REASON = "MISSING_SUPPLEMENT_REASON"
VO_ERROR_REPETITIVE_PHRASING = "REPETITIVE_PHRASING"
VO_ERROR_STYLE_PROFILE_MISMATCH = "STYLE_PROFILE_MISMATCH"
VO_ERROR_UNKNOWN_LLM_REVIEW_ERROR = "UNKNOWN_LLM_REVIEW_ERROR"

# Phase 5: zusätzliche Risiko-Typen für die Intro-Hook-Asset-Validierung
# (INVALID_ASSET_ID, MISSING_ASSET_MAPPING, MISSING_SUPPLEMENT_REASON,
# WEAK_ASSET_MATCH, WORD_COUNT_OUT_OF_RANGE, FORBIDDEN_TERM_USED werden von
# den Phase-4-VO_ERROR_*-Konstanten wiederverwendet).
VO_ERROR_INVALID_FOLDER_REFERENCE = "INVALID_FOLDER_REFERENCE"
VO_ERROR_INVALID_SENTENCE_REFERENCE = "INVALID_SENTENCE_REFERENCE"

VO_ERROR_TYPES_DETERMINISTIC = (
    VO_ERROR_WORD_COUNT_OUT_OF_RANGE,
    VO_ERROR_FORBIDDEN_TERM_USED,
    VO_ERROR_INVALID_ASSET_ID,
    VO_ERROR_MISSING_ASSET_MAPPING,
    VO_ERROR_MISSING_SUPPLEMENT_REASON,
    VO_ERROR_WEAK_ASSET_MATCH,
    VO_ERROR_MISSING_TRANSITION,
    VO_ERROR_MISSING_TRANSITION_TO_NEXT,
    VO_ERROR_MISSING_CONTRAST_OR_COMMONALITY,
)
VO_ERROR_TYPES_LLM_REVIEW = (
    VO_ERROR_LANGUAGE_MISMATCH,
    VO_ERROR_GLOBAL_NEGATIVE_RULE_VIOLATED,
    VO_ERROR_FOLDER_NEGATIVE_RULE_VIOLATED,
    VO_ERROR_HALLUCINATED_FACT,
    VO_ERROR_TOO_GENERIC,
    VO_ERROR_TOO_ASSET_DESCRIPTIVE,
    VO_ERROR_DOES_NOT_MATCH_ASSETS,
    VO_ERROR_REPETITIVE_PHRASING,
    VO_ERROR_STYLE_PROFILE_MISMATCH,
)
VO_ERROR_TYPES_ALL = VO_ERROR_TYPES_DETERMINISTIC + VO_ERROR_TYPES_LLM_REVIEW

# --- Intro Hook (Phase 5) ---
INTRO_HOOK_SETTINGS_FILENAME = "intro_hook_settings.json"
INTRO_HOOK_DEFAULT_TARGET_WORDS = 70
INTRO_HOOK_DEFAULT_MIN_WORDS = 60
INTRO_HOOK_DEFAULT_MAX_WORDS = 80
INTRO_HOOK_CANDIDATE_COUNT = 5
INTRO_HOOK_TYPE_MYSTERY = "mystery"
INTRO_HOOK_TYPE_CONTRAST = "contrast"
INTRO_HOOK_TYPE_SURPRISE = "surprise"
INTRO_HOOK_TYPE_CINEMATIC_PROMISE = "cinematic_promise"
INTRO_HOOK_TYPE_QUESTION = "question"
INTRO_HOOK_TYPE_EMOTIONAL = "emotional"
INTRO_HOOK_TYPES = (
    INTRO_HOOK_TYPE_MYSTERY,
    INTRO_HOOK_TYPE_CONTRAST,
    INTRO_HOOK_TYPE_SURPRISE,
    INTRO_HOOK_TYPE_CINEMATIC_PROMISE,
    INTRO_HOOK_TYPE_QUESTION,
    INTRO_HOOK_TYPE_EMOTIONAL,
)
INTRO_HOOK_STATUS_DRAFT = "DRAFT"
INTRO_HOOK_STATUS_READY = "READY"
INTRO_HOOK_STATUS_PARSE_FAILED = "PARSE_FAILED"
INTRO_HOOK_STATUS_FAIL = "FAIL"
INTRO_HOOK_STATUS_CONFIRMED = "CONFIRMED"

# --- ElevenLabs Audio/TTS (Phase 6) ---
ELEVENLABS_MODEL_PRESETS = (
    "eleven_multilingual_v2",
    "eleven_turbo_v2_5",
    "eleven_flash_v2_5",
    "eleven_v3",
)
ELEVENLABS_DEFAULT_MODEL_ID = "eleven_multilingual_v2"
ELEVENLABS_DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io/v1"
ELEVENLABS_MODEL_ID_V3 = "eleven_v3"

# --- Pausen innerhalb eines Folder-Voice-overs (Nutzerfeedback Juli 2026) ---
# Qualitativ statt exakter Sekundenzahl: eleven_v3 unterstützt KEINE
# SSML-<break time="Xs"/>-Tags mit exakter Dauer, sondern nur die drei
# nicht-numerischen Tags unten. Für alle anderen ElevenLabs-Modelle werden
# diese Tags NICHT eingefügt (dort würden sie als Text vorgelesen).
PAUSE_AFTER_NONE = ""
PAUSE_AFTER_SHORT = "short"
PAUSE_AFTER_MEDIUM = "medium"
PAUSE_AFTER_LONG = "long"
PAUSE_AFTER_CHOICES = (PAUSE_AFTER_NONE, PAUSE_AFTER_SHORT, PAUSE_AFTER_MEDIUM, PAUSE_AFTER_LONG)
ELEVENLABS_V3_PAUSE_TAGS = {
    PAUSE_AFTER_SHORT: "[short pause]",
    PAUSE_AFTER_MEDIUM: "[pause]",
    PAUSE_AFTER_LONG: "[long pause]",
}

AUDIO_ALIGNMENT_FILENAME = "voiceover_alignment.json"
TTS_RUN_MANIFEST_FILENAME = "tts_run_manifest.json"
ELEVENLABS_TTS_REQUEST_METADATA_FILENAME = "elevenlabs_tts_request_metadata.json"
ELEVENLABS_TTS_RESPONSE_METADATA_FILENAME = "elevenlabs_tts_response_metadata.json"
ELEVENLABS_TIMESTAMPS_FILENAME = "elevenlabs_timestamps.json"
TTS_ERRORS_FILENAME = "tts_errors.json"
AUDIO_TEST_SUBDIR = "_test"

AUDIO_SCOPE_INTRO = "intro"
AUDIO_SCOPE_FOLDER = "folder"

AUDIO_STATUS_READY = "AUDIO_READY"
AUDIO_STATUS_READY_WITH_WARNINGS = "AUDIO_READY_WITH_WARNINGS"
AUDIO_STATUS_STALE = "STALE"
AUDIO_STATUS_FAILED = "FAILED"
AUDIO_STATUS_MISSING = "MISSING"

TTS_RUN_STATUS_PASS = "PASS"
TTS_RUN_STATUS_FAIL = "FAIL"

ALIGNMENT_SOURCE_ELEVENLABS_TIMESTAMPS = "elevenlabs_timestamps"

ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND = "TEXT_SEGMENT_NOT_FOUND"
ALIGNMENT_WARNING_USED_PROPORTIONAL_FALLBACK = "USED_PROPORTIONAL_FALLBACK"
ALIGNMENT_WARNING_MISSING_CHARACTER_TIMESTAMPS = "MISSING_CHARACTER_TIMESTAMPS"
ALIGNMENT_WARNING_NON_MONOTONIC_TIMESTAMPS = "NON_MONOTONIC_TIMESTAMPS"
ALIGNMENT_WARNING_AUDIO_DURATION_MISMATCH = "AUDIO_DURATION_MISMATCH"
ALIGNMENT_WARNING_EMPTY_SEGMENT_TEXT = "EMPTY_SEGMENT_TEXT"
ALIGNMENT_WARNING_AUDIO_DURATION_UNKNOWN = "AUDIO_DURATION_UNKNOWN"

# --- Final Output / confirmed_voiceover_project_plan (Phase 7) ---
PLAN_STATUS_TEXT_READY = "TEXT_READY"
PLAN_STATUS_AUDIO_PENDING = "AUDIO_PENDING"
PLAN_STATUS_AUDIO_READY = "AUDIO_READY"
PLAN_STATUS_READY_FOR_CUT = "READY_FOR_CUT"
PLAN_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"

ITEM_READINESS_READY = "READY"
ITEM_READINESS_MISSING_AUDIO = "MISSING_AUDIO"
ITEM_READINESS_MISSING_ALIGNMENT = "MISSING_ALIGNMENT"
ITEM_READINESS_STALE_AUDIO = "STALE_AUDIO"
ITEM_READINESS_WARNING = "WARNING"
ITEM_READINESS_BLOCKED = "BLOCKED"

ASSET_MAPPING_STATUS_PASS = "PASS"
ASSET_MAPPING_STATUS_WARNINGS = "WARNINGS"
ASSET_MAPPING_STATUS_BLOCKED = "BLOCKED"

READINESS_ERROR_MISSING_CONFIRMED_DRAMATURGY = "MISSING_CONFIRMED_DRAMATURGY"
READINESS_ERROR_MISSING_CONFIRMED_INTRO = "MISSING_CONFIRMED_INTRO"
READINESS_ERROR_MISSING_FOLDER_VOICEOVER = "MISSING_FOLDER_VOICEOVER"
READINESS_ERROR_EMPTY_TEXT = "EMPTY_TEXT"
READINESS_ERROR_EMPTY_SENTENCE_ITEMS = "EMPTY_SENTENCE_ITEMS"
READINESS_ERROR_EMPTY_VISUAL_BEATS = "EMPTY_VISUAL_BEATS"
READINESS_ERROR_MISSING_AUDIO = "MISSING_AUDIO"
READINESS_ERROR_MISSING_ALIGNMENT = "MISSING_ALIGNMENT"
READINESS_ERROR_AUDIO_FAILED = "AUDIO_FAILED"
READINESS_ERROR_AUDIO_STALE = "AUDIO_STALE"
READINESS_ERROR_AUDIO_DURATION_MISSING = "AUDIO_DURATION_MISSING"
READINESS_ERROR_NEEDS_USER_REVIEW = "NEEDS_USER_REVIEW"
READINESS_ERROR_PLAN_STALE = "PLAN_STALE"

READINESS_SEVERITY_WARNING = "WARNING"
READINESS_SEVERITY_BLOCKER = "BLOCKER"

# --- Cut Plan (Phase 8): confirmed_voiceover_project_plan.json -> technischer
# Schnittplan-Entwurf. Eigener Namensraum, KEINE Wiederverwendung von
# edit_plan_rules.json/EDIT_PLAN_* — schützt die bestehende Pipeline (§3). ---
CUT_PLAN_SUBDIR = "cut_plan"
CUT_PLAN_SETTINGS_FILENAME = "cut_plan_settings.json"
CUT_PLAN_DRAFT_FILENAME = "cut_plan.draft.json"
CUT_PLAN_VALIDATION_REPORT_FILENAME = "cut_plan.validation_report.json"
CUT_PLAN_CONFIRMED_FILENAME = "cut_plan.confirmed.json"
CUT_PLAN_TRACE_FILENAME = "cut_plan.trace.json"
CUT_PLAN_SUPPLEMENT_REQUESTS_FILENAME = "supplement_requests.from_cut_plan.json"
# Phase 8.6: isolierte Supplement Bridge — ausschließlich unter
# _otio/voiceover_generation/cut_plan/, NIEMALS unter _otio/supplement/.
CUT_PLAN_SUPPLEMENT_CANDIDATES_FILENAME = "supplement_candidates.json"
CUT_PLAN_SUPPLEMENT_ASSETS_SUBDIR = "supplement_assets"
CUT_PLAN_SUPPLEMENT_RUNS_SUBDIR = "supplement_runs"
CUT_PLAN_SUPPLEMENT_MANIFEST_FILENAME = "supplement_manifest.json"

# Validation Repair (Nutzervorgabe, Juli 2026): eigenständiger, dem
# regulären Supplement-Bereich NACHGESCHALTETER Reparatur-Schritt für
# Rest-Blocker, die erst NACH der vollständigen Validierung sichtbar
# werden (BLACK_GAP_DURING_VOICEOVER, ASSET_REUSE_DISTANCE_TOO_SHORT) —
# bewusst eigene Datei/eigenes Dokument, NICHT mit supplement_requests.
# from_cut_plan.json vermischt, da Reparatur-Requests andere Semantik
# haben (Zeitfenster-Reparatur statt Item-Ersatz, siehe
# cut_plan_validation_repair.py).
CUT_PLAN_VALIDATION_REPAIR_REQUESTS_FILENAME = "validation_repair_requests.json"

# Residual Gap Requests (Nutzervorgabe, Juli 2026: "Item hat Asset, aber
# Abdeckung ist unvollständig"): dritter, eigenständiger Reparaturpfad
# zwischen Supplement (Item hat noch KEIN Asset) und Validation Repair
# (kleine Lücke, per Nachbar-Kürzung reparierbar). Bewusst eigene Datei —
# siehe cut_plan_residual_gap_requests.py.
CUT_PLAN_RESIDUAL_GAP_REQUESTS_FILENAME = "residual_gap_requests.json"

CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY = "PATCH_GAP_ONLY"
CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL = "REPLACE_ITEM_VISUAL"

CUT_PLAN_RESIDUAL_GAP_STATUS_OPEN = "OPEN"
CUT_PLAN_RESIDUAL_GAP_STATUS_CANDIDATES_FOUND = "CANDIDATES_FOUND"
CUT_PLAN_RESIDUAL_GAP_STATUS_ACCEPTED = "ACCEPTED"
CUT_PLAN_RESIDUAL_GAP_STATUS_NO_MATCH = "NO_MATCH"
CUT_PLAN_RESIDUAL_GAP_STATUS_FAILED = "FAILED"

CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP = "BLACK_GAP"
CUT_PLAN_VALIDATION_REPAIR_TYPE_ASSET_REUSE_DISTANCE = "ASSET_REUSE_DISTANCE"

CUT_PLAN_VALIDATION_REPAIR_STATUS_PENDING = "PENDING"
CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED = "ACCEPTED"
CUT_PLAN_VALIDATION_REPAIR_STATUS_FAILED = "FAILED"
CUT_PLAN_VALIDATION_REPAIR_STATUS_NO_MATCH = "NO_MATCH"
# Nutzervorgabe (Juli 2026): eine BLACK_GAP-Lücke, deren Nachbar-Segmente
# zusammen nicht genug 'Kürzungs-Spielraum' haben, um das Reparatur-
# Fenster sicher auf mindestens shot_min_sec zu erweitern (siehe
# compute_black_gap_repair_plan) — bewusst ANDERER Status als NO_MATCH
# (kein Kandidat gefunden), da hier das Problem strukturell ist, nicht
# an fehlenden Kandidaten liegt. UI-Hinweis: normaler Supplement-Request
# für das gesamte Item nötig.
CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR = "UNSAFE_TO_REPAIR"

# Phase 6 (Nutzervorgabe, Juli 2026): für BLACK_GAP-Reparaturen werden
# erfahrungsgemäß bessere/passendere Ergebnisse mit Fotos statt Videos
# erzielt (kein Risiko einer zu kurzen Videoquelle, beliebig haltbar) —
# deshalb Foto VOR Video, umgekehrt zur sonst üblichen redaktionellen
# Video-Präferenz. Für ASSET_REUSE_DISTANCE (ein komplettes Ersatz-Asset
# für ein ganzes Segment) bleibt die bestehende Video-vor-Foto-Präferenz
# bestehen.
CUT_PLAN_VALIDATION_REPAIR_ASSET_TYPE_ORDER_BLACK_GAP = ("image", "video")
CUT_PLAN_VALIDATION_REPAIR_ASSET_TYPE_ORDER_ASSET_REUSE_DISTANCE = ("video", "image")

# Phase 9.1: isolierte EditPlan-Bridge — ausschließlich unter
# _otio/voiceover_generation/cut_plan/edit_plan_bridge/, NIEMALS unter
# _otio/edit_plan/ oder _otio/exports/.
CUT_PLAN_EDIT_PLAN_BRIDGE_SUBDIR = "edit_plan_bridge"
CUT_PLAN_EDIT_PLAN_BRIDGE_DRAFT_FILENAME = "edit_plan_from_cut_plan.draft.json"
CUT_PLAN_EDIT_PLAN_BRIDGE_TRACE_FILENAME = "edit_plan_bridge_trace.json"
CUT_PLAN_EDIT_PLAN_BRIDGE_VALIDATION_REPORT_FILENAME = "edit_plan_bridge_validation_report.json"
CUT_PLAN_EDIT_PLAN_BRIDGE_AUDIO_PLAN_FILENAME = "bridge_audio_plan.json"
# Phase 9.3: Confirm/Freeze der EditPlan Bridge — weiterhin isolierter
# Snapshot, KEIN Produktions-EditPlan, KEIN locked Plan, KEIN OTIO-Export.
CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_DRAFT_FILENAME = "edit_plan_from_cut_plan.confirmed.json"
CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_AUDIO_PLAN_FILENAME = "bridge_audio_plan.confirmed.json"
CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_TRACE_FILENAME = "edit_plan_bridge_trace.confirmed.json"
CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRM_MANIFEST_FILENAME = "edit_plan_bridge_confirm_manifest.json"

EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED = "CONFIRMED"

EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS = "PASS"
EDIT_PLAN_BRIDGE_VALIDATION_STATUS_WARNING = "WARNING"
EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED = "BLOCKED"

# TimelineItem.type für aus CutPlanAudioItem gebaute Bridge-Items — bewusst
# KEIN bestehender Produktions-Typ (siehe timeline_plan_builder.VISUAL_VIDEO_TYPES/
# NARRATION_VISUAL_TYPES), damit ein versehentlich in die Produktionspipeline
# geratener Bridge-Draft dort nicht fälschlich als Video-/Bild-Visual erkannt wird.
EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO = "voiceover_audio"
# EditPlanDocument.candidate_status-Marker für Bridge-Drafts (§11: „deutlich
# als bridge draft markieren“) — kollidiert nicht mit Produktions-Werten wie
# "BLOCKED".
EDIT_PLAN_BRIDGE_CANDIDATE_STATUS_MARKER = "EDIT_PLAN_BRIDGE_DRAFT"

# EditPlanBridgeValidationError.type
EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_MISSING = "CONFIRMED_CUT_PLAN_MISSING"
EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_STALE = "CONFIRMED_CUT_PLAN_STALE"
EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_NOT_CONFIRMED = "CONFIRMED_CUT_PLAN_NOT_CONFIRMED"
EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_HAS_BLOCKERS = "CONFIRMED_CUT_PLAN_HAS_BLOCKERS"
EDIT_PLAN_BRIDGE_ERROR_AUDIO_ITEM_MISSING = "AUDIO_ITEM_MISSING"
EDIT_PLAN_BRIDGE_ERROR_VISUAL_ITEM_MISSING = "VISUAL_ITEM_MISSING"
EDIT_PLAN_BRIDGE_ERROR_ZERO_OR_NEGATIVE_DURATION = "ZERO_OR_NEGATIVE_DURATION"
EDIT_PLAN_BRIDGE_ERROR_AUDIO_SOURCE_NOT_ZERO = "AUDIO_SOURCE_NOT_ZERO"
EDIT_PLAN_BRIDGE_ERROR_AUDIO_FILE_MISSING = "AUDIO_FILE_MISSING"
EDIT_PLAN_BRIDGE_ERROR_ASSET_FILE_MISSING = "ASSET_FILE_MISSING"
EDIT_PLAN_BRIDGE_ERROR_TIMELINE_OVERLAP = "TIMELINE_OVERLAP"
EDIT_PLAN_BRIDGE_ERROR_BLACK_GAP_DURING_AUDIO = "BLACK_GAP_DURING_AUDIO"
EDIT_PLAN_BRIDGE_ERROR_NON_MONOTONIC_TIMELINE = "NON_MONOTONIC_TIMELINE"
EDIT_PLAN_BRIDGE_ERROR_SECRET_LEAK_DETECTED = "SECRET_LEAK_DETECTED"
# Phase 9.2: Bridge-Hardening — Boundary-Chaining + Audio-Kompatibilität.
EDIT_PLAN_BRIDGE_ERROR_VISUAL_TIMELINE_GAP = "VISUAL_TIMELINE_GAP"
EDIT_PLAN_BRIDGE_ERROR_SOURCE_RANGE_INVALID = "SOURCE_RANGE_INVALID"
EDIT_PLAN_BRIDGE_ERROR_VIDEO_SOURCE_EXCEEDS_DURATION = "VIDEO_SOURCE_EXCEEDS_DURATION"
EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISSING = "AUDIO_PLAN_MISSING"
EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISMATCH = "AUDIO_PLAN_MISMATCH"

# --- Phase 10.1: Production EditPlan Staging — isoliertes Staging-Paket aus
# dem bestätigten EditPlan-Bridge-Snapshot, ausschließlich unter
# _otio/voiceover_generation/cut_plan/production_edit_plan_staging/. NIEMALS
# unter _otio/edit_plan/ (das erfolgt erst in einer späteren, separaten
# Promote-Phase). ---
PRODUCTION_EDIT_PLAN_STAGING_SUBDIR = "production_edit_plan_staging"
PRODUCTION_EDIT_PLAN_PACKAGE_FILENAME = "production_edit_plan_package.json"
PRODUCTION_EDIT_PLAN_STAGED_EDIT_PLANS_SUBDIR = "staged_edit_plans"
PRODUCTION_EDIT_PLAN_STAGED_EDIT_PLAN_FILENAME = "edit_plan.json"
PRODUCTION_EDIT_PLAN_MAPPING_TRACE_FILENAME = "production_edit_plan_mapping_trace.json"
PRODUCTION_EDIT_PLAN_VALIDATION_REPORT_FILENAME = "production_edit_plan_validation_report.json"

# ProductionEditPlanPackage.status / ProductionEditPlanValidationReport.status
PRODUCTION_EDIT_PLAN_STATUS_STAGED = "STAGED"
PRODUCTION_EDIT_PLAN_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
PRODUCTION_EDIT_PLAN_STATUS_BLOCKED = "BLOCKED"

# EditPlanDocument.candidate_status-Marker für gestagte Produktions-Pläne —
# deutlich von EDIT_PLAN_BRIDGE_CANDIDATE_STATUS_MARKER unterschieden, damit
# man am Wert allein erkennt, aus welcher Phase ein Draft stammt.
PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT = "PRODUCTION_EDIT_PLAN_STAGING_DRAFT"

# ProductionEditPlanValidationError.type — Grundset für Phase 10.1.
PRODUCTION_EDIT_PLAN_ERROR_BRIDGE_SNAPSHOT_MISSING = "BRIDGE_SNAPSHOT_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_BRIDGE_SNAPSHOT_STALE = "BRIDGE_SNAPSHOT_STALE"
PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED = "VOICEOVER_AUDIO_ITEM_LEAKED"
PRODUCTION_EDIT_PLAN_ERROR_MISSING_VOICEOVER_PLAN = "MISSING_VOICEOVER_PLAN"
PRODUCTION_EDIT_PLAN_ERROR_LOCAL_TIMELINE_MAPPING_ERROR = "LOCAL_TIMELINE_MAPPING_ERROR"
PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED = "PRODUCTION_TIMELINE_VALIDATION_FAILED"
# Phase 10.2: Section-Reconciliation + Mapping-Sanity-Checks (Grundset,
# volle Timeline-/Voiceover-Validierung folgt erst in Phase 10.3).
PRODUCTION_EDIT_PLAN_ERROR_VISUAL_WITHOUT_AUDIO_SECTION = "VISUAL_WITHOUT_AUDIO_SECTION"
PRODUCTION_EDIT_PLAN_ERROR_NO_VISUAL_ITEMS_FOR_SECTION = "NO_VISUAL_ITEMS_FOR_SECTION"
PRODUCTION_EDIT_PLAN_ERROR_SHOT_SYNTHESIS_FAILED = "SHOT_SYNTHESIS_FAILED"
PRODUCTION_EDIT_PLAN_ERROR_ZERO_OR_NEGATIVE_DURATION = "ZERO_OR_NEGATIVE_DURATION"

# ProductionEditPlanValidationReport.status — Phase 10.3 volle Revalidierung.
PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS = "PASS"
PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING = "WARNING"
PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED = "BLOCKED"

# Phase 10.3: ProductionEditPlanValidationError.type — vollständige
# Revalidierung des Staging-Pakets (Package/Trace-Ebene). Wo bereits ein
# passender Phase-10.1/10.2-Fehlertyp existiert (z. B. MISSING_VOICEOVER_PLAN,
# VOICEOVER_AUDIO_ITEM_LEAKED, PRODUCTION_TIMELINE_VALIDATION_FAILED), wird
# dieser wiederverwendet statt dupliziert.
PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING = "PRODUCTION_STAGING_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE = "PRODUCTION_STAGING_STALE"
PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_TRACE_MISSING = "PRODUCTION_STAGING_TRACE_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING = "STAGED_EDIT_PLAN_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH = "STAGED_EDIT_PLAN_HASH_MISMATCH"
PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_CONFIRMED_TRUE = "STAGED_EDIT_PLAN_CONFIRMED_TRUE"
PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_STATUS_INVALID = "STAGED_EDIT_PLAN_STATUS_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE = "STAGED_EDIT_PLAN_EMPTY_TIMELINE"
PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS = "STAGED_EDIT_PLAN_EMPTY_SHOTS"
PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING = "VOICEOVER_PLAN_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_PATH_MISSING = "VOICEOVER_AUDIO_PATH_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_INVALID = "VOICEOVER_DURATION_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID = "VOICEOVER_TIMING_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_SOURCE_INVALID = "VOICEOVER_DURATION_SOURCE_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TRIM_POLICY_INVALID = "VOICEOVER_TRIM_POLICY_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_DURATION_INVALID = "TIMELINE_ITEM_DURATION_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_SOURCE_RANGE_INVALID = "TIMELINE_ITEM_SOURCE_RANGE_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_ASSET_MISSING = "TIMELINE_ITEM_ASSET_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_TYPE_INVALID = "TIMELINE_ITEM_TYPE_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_SHOT_COUNT_MISMATCH = "SHOT_COUNT_MISMATCH"
PRODUCTION_EDIT_PLAN_ERROR_SHOT_DURATION_INVALID = "SHOT_DURATION_INVALID"
PRODUCTION_EDIT_PLAN_ERROR_SHOT_TIMING_OUTSIDE_VOICEOVER = "SHOT_TIMING_OUTSIDE_VOICEOVER"
PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_MISSING = "MAPPING_TRACE_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING = "MAPPING_TRACE_ITEM_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ROUNDTRIP_FAILED = "MAPPING_TRACE_ROUNDTRIP_FAILED"
PRODUCTION_EDIT_PLAN_ERROR_SECRET_LEAK_DETECTED = "SECRET_LEAK_DETECTED"
# Wiederverwendete Phase-10.1/10.2-Typen (Aliase für Phase-10.3-Aufrufer, damit
# der 10.3-Fehlerkatalog vollständig unter den PRODUCTION_EDIT_PLAN_ERROR_*-
# Namen auffindbar ist, ohne bestehende Konstanten zu duplizieren):
# - VOICEOVER_AUDIO_ITEM_LEAKED -> PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED
# - TIMELINE_VALIDATION_FAILED -> PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED

# --- Phase 10.5: Production EditPlan Promote Readiness / Dry Run — prüft rein
# lesend, was ein SPÄTERER Promote nach `_otio/edit_plan/` tun würde. Schreibt
# ausschließlich unter production_edit_plan_staging/, NIEMALS nach
# `_otio/edit_plan/`, kein tatsächliches Kopieren, kein Lock, kein OTIO. ---
PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_FILENAME = "production_edit_plan_promote_readiness.json"
PRODUCTION_EDIT_PLAN_PROMOTE_DRY_RUN_TRACE_FILENAME = "production_edit_plan_promote_dry_run_trace.json"

# ProductionEditPlanPromoteReadinessDocument.status
PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY = "READY"
PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED = "BLOCKED"

# ProductionEditPlanPromoteSectionReadiness.promote_action /
# ProductionEditPlanPromoteDryRunTraceEntry.promote_action
PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE = "WOULD_CREATE"
PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE = "WOULD_OVERWRITE"
PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_SKIP_INTRO = "WOULD_SKIP_INTRO"
PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED = "BLOCKED"

# Zusätzliche Fehlertypen, die es in Phase 10.3 noch nicht gab (Rest wird
# additiv aus dem Phase-10.3-Katalog wiederverwendet, siehe Docstring von
# production_edit_plan_promote_readiness.py):
PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_MISSING = "VALIDATION_REPORT_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_NOT_PASS = "VALIDATION_REPORT_NOT_PASS"
PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_STALE = "VALIDATION_REPORT_STALE"
PRODUCTION_EDIT_PLAN_ERROR_SECTION_HAS_BLOCKERS = "SECTION_HAS_BLOCKERS"
PRODUCTION_EDIT_PLAN_ERROR_EXISTING_PRODUCTION_PLAN_UNREADABLE = "EXISTING_PRODUCTION_PLAN_UNREADABLE"

# --- Phase 10.6: Actual Production EditPlan Promote (Backup + Manifest +
# Kollisionsschutz). Ab hier ist Schreiben nach `_otio/edit_plan/` erlaubt —
# AUSSCHLIESSLICH innerhalb von production_edit_plan_promote_execute.py über
# promote_production_edit_plans(). Kein OTIO-Export, kein Render, kein Lock,
# keine LLM-Planung, kein Aufruf der Save- oder Build-Funktionen der
# bestehenden Produktions-EditPlan-Pipeline. ---
PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_FILENAME = "production_edit_plan_promote_manifest.json"
PRODUCTION_EDIT_PLAN_VOICE_FOLDER_MAPPING_PATCH_FILENAME = "production_edit_plan_voice_folder_mapping_patch.json"
PRODUCTION_EDIT_PLAN_PROMOTE_BACKUPS_SUBDIR = "promote_backups"

# ProductionEditPlanPromoteManifest.status
PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED = "PROMOTED"
PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_BLOCKED = "BLOCKED"

# ProductionEditPlanPromoteSectionResult.promote_action — bewusst eigene
# Konstanten, getrennt von PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_* (Phase 10.5
# Dry-Run: "was WÜRDE passieren") — Phase 10.6 dokumentiert "was IST passiert".
PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED = "CREATED"
PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_OVERWRITTEN = "OVERWRITTEN"
PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_SKIPPED_INTRO = "SKIPPED_INTRO"
PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_BLOCKED = "BLOCKED"

# ProductionEditPlanVoiceFolderMappingPatchEntry.action
PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD = "WOULD_ADD"
PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT = "ALREADY_PRESENT"
PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_NEEDS_REVIEW = "NEEDS_REVIEW"

# Zusätzliche Fehlertypen für Phase 10.6 (Rest additiv aus Phase 10.3/10.5
# wiederverwendet):
PRODUCTION_EDIT_PLAN_ERROR_OVERWRITE_NOT_ALLOWED = "OVERWRITE_NOT_ALLOWED"
PRODUCTION_EDIT_PLAN_ERROR_PROMOTE_BACKUP_FAILED = "PROMOTE_BACKUP_FAILED"
PRODUCTION_EDIT_PLAN_ERROR_PROMOTE_READINESS_MISSING = "PROMOTE_READINESS_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_PROMOTE_DRY_RUN_TRACE_MISSING = "PROMOTE_DRY_RUN_TRACE_MISSING"
PRODUCTION_EDIT_PLAN_ERROR_PROMOTE_READINESS_STALE = "PROMOTE_READINESS_STALE"
PRODUCTION_EDIT_PLAN_ERROR_PROMOTE_READINESS_NOT_ELIGIBLE = "PROMOTE_READINESS_NOT_ELIGIBLE"

# --- Phase 10.7: Voice Folder Mapping Merge — explizit bestätigte, selektive
# Übernahme von production_edit_plan_voice_folder_mapping_patch.json (Phase
# 10.6) in die echte `voice_folder_mapping.json`. Backup vor jedem Write,
# NEEDS_REVIEW-Konflikte MÜSSEN explizit vom Nutzer aufgelöst werden. Kein
# OTIO-Export, kein Render, kein Lock, keine automatische Neuplanung. ---
VOICE_FOLDER_MAPPING_MERGE_MANIFEST_FILENAME = "voice_folder_mapping_merge_manifest.json"
VOICE_FOLDER_MAPPING_MERGE_BACKUPS_SUBDIR = "voice_folder_mapping_merge_backups"

# VoiceFolderMappingMergeManifest.status
VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_MERGED = "MERGED"
VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_BLOCKED = "BLOCKED"

# VoiceFolderMappingMergeEntryResult.action
VOICE_FOLDER_MAPPING_MERGE_ACTION_ADDED = "ADDED"
VOICE_FOLDER_MAPPING_MERGE_ACTION_UPDATED = "UPDATED"
VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_ALREADY_PRESENT = "SKIPPED_ALREADY_PRESENT"
VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_BY_USER = "SKIPPED_BY_USER"
VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_CONFLICT_UNRESOLVED = "SKIPPED_CONFLICT_UNRESOLVED"

# Nutzer-Resolution je NEEDS_REVIEW-Konflikt (folder_resolutions-Werte)
VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY = "APPLY"
VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP = "SKIP"

# --- Phase 10.8: OTIO Export Readiness Check für bereits promotete/gemappte
# Folder. Bewusste, eng begrenzte Ausnahme von der sonst strikten Isolation:
# nutzt otio_exporter.merge_confirmed_edit_plans() (rein lesende Vorschau-
# Funktion der bestehenden Produktionspipeline, KEIN Export, KEIN ffprobe,
# KEIN Schreiben) — NUR für bereits explizit promotete (Phase 10.6) UND
# gemappte (Phase 10.7) Folder, die zu diesem Zeitpunkt bereits vollwertige
# Produktionsdaten sind. build_otio_timeline()/export_otio_timeline() bleiben
# weiterhin tabu (siehe production_edit_plan_otio_export_readiness.py
# Modul-Docstring). ---
PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_REPORT_FILENAME = "production_edit_plan_otio_export_readiness_report.json"

# OtioExportReadinessReport.status
PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY = "READY"
PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY = "NOT_READY"
PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED = "BLOCKED"

# OtioExportReadinessFolderResult.status
PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_READY = "READY"
PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY = "NOT_READY"
PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_SKIPPED = "SKIPPED"

# --- Phase 10.9: Gesamt-Übersichts-Dashboard — rein lesende Aggregation des
# Status aller vorherigen Phasen (Staging/Validation/Promote-Readiness/
# Promote/Voice-Folder-Mapping-Merge/OTIO-Export-Readiness). Kein neuer
# Seiteneffekt, kein neues Artefakt außer dem Overview selbst (wird nicht
# persistiert, da es immer live aus den bestehenden Artefakten berechnet
# wird). ---
PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_STATUS_NOT_STARTED = "NOT_STARTED"

# ProductionEditPlanPipelineOverview.overall_status
PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED = "NOT_STARTED"
PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_IN_PROGRESS = "IN_PROGRESS"
PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_BLOCKED = "BLOCKED"
PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_COMPLETE = "COMPLETE"

# PipelineStageOverview.stage_id
PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_STAGING = "staging"
PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VALIDATION = "validation"
PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE_READINESS = "promote_readiness"
PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_PROMOTE = "promote"
PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_VOICE_FOLDER_MAPPING_MERGE = "voice_folder_mapping_merge"
PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_ID_OTIO_EXPORT_READINESS = "otio_export_readiness"

# Cut Plan Settings — Defaults (verbindliche Werte laut Nutzerentscheidung §1/§3/§5)
CUT_PLAN_DEFAULT_INITIAL_AUDIO_OFFSET_SEC = 1.0
CUT_PLAN_DEFAULT_PAUSE_BETWEEN_SECTIONS_SEC = 0.25
CUT_PLAN_DEFAULT_SECTION_VISUAL_PREROLL_SEC = 0.0
CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC = 1.0  # gilt nur für Video, nie für Bild/Audio
CUT_PLAN_DEFAULT_SHOT_MIN_SEC = 3.0
CUT_PLAN_DEFAULT_SHOT_MAX_SEC = 8.0

# Nutzervorgabe (Juli 2026): Mindestdauer für den zusätzlichen Closing-Shot-
# CutPlanItem (siehe cut_plan_timeline_service._closing_item_skeleton) —
# deckt den Fall ab, dass der letzte Satz bereits bis exakt ans Audio-Ende
# reicht (kein natürlicher "Audio-Tail"). Bewusst < CUT_PLAN_DEFAULT_SHOT_
# MIN_SEC, damit ein knapper Audio-Tail höchstens die weiche SHOT_TOO_SHORT-
# Warnung (>= 1.0s), NIE den harten Blocker (< 1.0s) auslöst — der
# anschließende Visual-Coverage-Fix (extend_section_end_visuals_over_pauses)
# streckt diesen Shot im Regelfall ohnehin über die Sektionspause hinweg.
CUT_PLAN_DEFAULT_CLOSING_SHOT_MIN_DURATION_SEC = 1.0

# Nutzervorgabe (Juli 2026): vorher als Konstante _MAX_AUTO_FILLED_GAP_SEC in
# cut_plan_visual_coverage.py fest auf 1.0s codiert — jetzt pro Projekt
# einstellbar (siehe CutPlanSettings.black_gap_auto_hold_max_sec), damit
# mehr BLACK_GAP_DURING_VOICEOVER-Fälle bereits vor der Supplement-Suche
# durch einfaches Halten des vorherigen Bildes/Videos geschlossen werden
# können, ohne den Cut-Plan-Code selbst zu ändern.
CUT_PLAN_DEFAULT_BLACK_GAP_AUTO_HOLD_MAX_SEC = 1.0
# Nutzervorgabe (Juli 2026): kleine Restlücke bei Sektionspausen-Hold
# (z. B. Video hat 4.0s Reserve, Pause braucht 5.0s) gilt bis zu diesem
# Wert noch als akzeptabel — kein BLACK_GAP_DURING_VOICEOVER. Pro Projekt
# in CutPlanSettings.section_pause_hold_tolerance_sec einstellbar.
CUT_PLAN_DEFAULT_SECTION_PAUSE_HOLD_TOLERANCE_SEC = 1.5

# Nutzervorgabe (Juli 2026): Assets sollen generell bis zum Start des
# NÄCHSTEN Satzes weiterlaufen, statt exakt am eigenen Satzende zu enden —
# eliminiert einen großen Teil der BLACK_GAP_DURING_VOICEOVER-Fälle bereits
# beim Bau der VisualSegments (siehe cut_plan_asset_selector.
# compute_visual_window_end_sec), statt sie nachträglich per Repair-Pipeline
# zu schließen. Deaktiviert per Default (bewusst additiv/opt-in, siehe
# Phase-1-Kommentar in cut_plan_asset_selector.py) — Phase 2 verdrahtet
# dies erst in choose_asset_for_cut_item/die Split-Logik.
CUT_PLAN_DEFAULT_EXTEND_VISUAL_WINDOW_TO_NEXT_SENTENCE = False
# Obergrenze, wie viel von einer Satzpause in das visuelle Fenster des
# VORHERIGEN Satzes hineingezogen werden darf — verhindert, dass eine sehr
# lange, redaktionell bedeutsame Pause (z. B. Kapitelwechsel) blind
# mitgestreckt wird; solche Fälle bleiben weiterhin als
# BLACK_GAP_DURING_VOICEOVER sichtbar bzw. laufen durch die bestehende
# Validation-Repair-Pipeline.
CUT_PLAN_DEFAULT_MAX_SENTENCE_PAUSE_EXTENSION_SEC = 3.0
CUT_PLAN_DEFAULT_MAX_ASSET_USAGE = 2
CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS = 0
CUT_PLAN_DEFAULT_TIMELINE_FPS = 25
CUT_PLAN_DEFAULT_TIMELINE_WIDTH = 3840
CUT_PLAN_DEFAULT_TIMELINE_HEIGHT = 2160

# CutPlanDocument.status
CUT_PLAN_STATUS_DRAFT = "DRAFT"
CUT_PLAN_STATUS_VALIDATED = "VALIDATED"
CUT_PLAN_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
CUT_PLAN_STATUS_CONFIRMED = "CONFIRMED"
CUT_PLAN_STATUS_BLOCKED = "BLOCKED"
CUT_PLAN_STATUS_CHOICES = (
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_STATUS_VALIDATED,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
    CUT_PLAN_STATUS_CONFIRMED,
    CUT_PLAN_STATUS_BLOCKED,
)

# CutPlanItem.asset_selection_status
CUT_PLAN_ASSET_SELECTION_UNRESOLVED = "UNRESOLVED"
CUT_PLAN_ASSET_SELECTION_PRIMARY_USED = "PRIMARY_USED"
CUT_PLAN_ASSET_SELECTION_BACKUP_USED = "BACKUP_USED"
CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED = "SUPPLEMENT_REQUIRED"
CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED = "SUPPLEMENT_USED"
# Phase 11.4: kein Stock-Kandidat hat die Auto-Resolver-Prüfung bestanden ->
# stattdessen ein bereits vorhandenes, neutrales Asset aus demselben Ordner
# verwendet (siehe cut_plan_generic_fallback_service.py).
CUT_PLAN_ASSET_SELECTION_GENERIC_FALLBACK_USED = "GENERIC_FALLBACK_USED"
# Phase 11.6: Nutzer hat bewusst ein bestimmtes, bereits vorhandenes Asset
# aus dem Ordner-Inventory manuell zugewiesen (kein Stock, kein Auto-
# Resolver-Fallback).
CUT_PLAN_ASSET_SELECTION_MANUAL_USED = "MANUAL_ASSET_USED"
CUT_PLAN_ASSET_SELECTION_BLOCKED = "BLOCKED"
CUT_PLAN_ASSET_SELECTION_CHOICES = (
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
    CUT_PLAN_ASSET_SELECTION_GENERIC_FALLBACK_USED,
    CUT_PLAN_ASSET_SELECTION_MANUAL_USED,
    CUT_PLAN_ASSET_SELECTION_BLOCKED,
)

# Phase 8.6: CutPlanSupplementRequest.status
CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_OPEN = "OPEN"
CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_CANDIDATES_FOUND = "CANDIDATES_FOUND"
CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED = "ACCEPTED"
CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_REJECTED = "REJECTED"
CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_FAILED = "FAILED"
CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_CHOICES = (
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_OPEN,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_CANDIDATES_FOUND,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_REJECTED,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_FAILED,
)

# Phase 8.6: CutPlanSupplementCandidatesDocument.status
CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY = "READY"
CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_NO_RESULTS = "NO_RESULTS"
CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_FAILED = "FAILED"

# Phase 8.6: CutPlanSupplementAsset.status
CUT_PLAN_SUPPLEMENT_ASSET_STATUS_ACQUIRED = "ACQUIRED"
CUT_PLAN_SUPPLEMENT_ASSET_STATUS_FAILED = "FAILED"

# Phase 11.2: Cut-Plan-Supplement-Suche schlägt standardmäßig 5 Kandidaten
# statt der allgemeinen Pexels-Adapter-Obergrenze (3, siehe
# MAX_CANDIDATES_PER_REQUEST in supplement_sources/pexels.py) vor — nur für
# diese Pipeline, siehe SupplementRequest.max_candidates.
CUT_PLAN_SUPPLEMENT_MAX_CANDIDATES = 5

# CutPlanItem.duration_strategy
CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT = "SINGLE_SHOT"
CUT_PLAN_DURATION_STRATEGY_SPLIT = "SPLIT"
CUT_PLAN_DURATION_STRATEGY_MERGED = "MERGED"
CUT_PLAN_DURATION_STRATEGY_CHOICES = (
    CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
    CUT_PLAN_DURATION_STRATEGY_SPLIT,
    CUT_PLAN_DURATION_STRATEGY_MERGED,
)

# CutPlanValidationReport.status
CUT_PLAN_VALIDATION_STATUS_PASS = "PASS"
CUT_PLAN_VALIDATION_STATUS_WARNING = "WARNING"
CUT_PLAN_VALIDATION_STATUS_BLOCKED = "BLOCKED"

# CutPlanValidationError.must_be_fixed_by
CUT_PLAN_FIX_BY_PYTHON = "python"
CUT_PLAN_FIX_BY_LLM = "llm"
CUT_PLAN_FIX_BY_USER = "user"

# CutPlanValidationError.type — Fehlertypen vorbereitet für Phase 8.4
# (Validierung selbst ist in Phase 8.1 noch NICHT implementiert).
CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY = "SOURCE_PLAN_NOT_READY"
CUT_PLAN_ERROR_MISSING_AUDIO = "MISSING_AUDIO"
CUT_PLAN_ERROR_MISSING_ALIGNMENT = "MISSING_ALIGNMENT"
CUT_PLAN_ERROR_INVALID_AUDIO_PATH = "INVALID_AUDIO_PATH"
CUT_PLAN_ERROR_INVALID_ASSET_ID = "INVALID_ASSET_ID"
CUT_PLAN_ERROR_MISSING_ASSET_MAPPING = "MISSING_ASSET_MAPPING"
CUT_PLAN_ERROR_ASSET_FILE_MISSING = "ASSET_FILE_MISSING"
CUT_PLAN_ERROR_ASSET_TOO_SHORT = "ASSET_TOO_SHORT"
CUT_PLAN_ERROR_SOURCE_RANGE_INVALID = "SOURCE_RANGE_INVALID"
CUT_PLAN_ERROR_SHOT_TOO_SHORT = "SHOT_TOO_SHORT"
CUT_PLAN_ERROR_SHOT_TOO_LONG = "SHOT_TOO_LONG"
CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED = "MAX_ASSET_USAGE_EXCEEDED"
CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT = "ASSET_REUSE_DISTANCE_TOO_SHORT"
CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED = "SUPPLEMENT_REQUIRED"
CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING = "SUPPLEMENT_REASON_MISSING"
CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER = "BLACK_GAP_DURING_VOICEOVER"
CUT_PLAN_ERROR_TIMELINE_OVERLAP = "TIMELINE_OVERLAP"
CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED = "AUDIO_GAP_UNEXPECTED"
CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR = "FRAME_ROUNDING_ERROR"
CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID = "AMBIGUOUS_ASSET_ID"
CUT_PLAN_ERROR_TYPES = (
    CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
    CUT_PLAN_ERROR_MISSING_AUDIO,
    CUT_PLAN_ERROR_MISSING_ALIGNMENT,
    CUT_PLAN_ERROR_INVALID_AUDIO_PATH,
    CUT_PLAN_ERROR_INVALID_ASSET_ID,
    CUT_PLAN_ERROR_MISSING_ASSET_MAPPING,
    CUT_PLAN_ERROR_ASSET_FILE_MISSING,
    CUT_PLAN_ERROR_ASSET_TOO_SHORT,
    CUT_PLAN_ERROR_SOURCE_RANGE_INVALID,
    CUT_PLAN_ERROR_SHOT_TOO_SHORT,
    CUT_PLAN_ERROR_SHOT_TOO_LONG,
    CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED,
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
    CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_ERROR_TIMELINE_OVERLAP,
    CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED,
    CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR,
    CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID,
)

# Bugfix (Nutzervorgabe Juli 2026, "wieso tauchen Black Gaps trotz neuem
# Ansatz wieder auf?"): die EINZIGEN Blocker-Typen, die VOR der Asset-
# Auswahl (Phase 8.2, cut_plan_timeline_service.py) auf einem CutPlanItem
# gesetzt werden können — also ein echtes, durch Asset-Auswahl NICHT
# lösbares Timing-/Struktur-Problem darstellen (keine verlässliche
# Timeline-Zeit vorhanden). Vorher nur lokal in cut_plan_supplement_bridge.
# py als `_TIMING_BLOCKER_TYPES` definiert und ausschließlich für die
# Supplement-Request-Erzeugung genutzt — jetzt hier zentral, damit
# cut_plan_asset_selector.choose_asset_for_cut_item dieselbe Unterscheidung
# treffen kann: ALLE ANDEREN Blocker-Typen (z. B. BLACK_GAP_DURING_
# VOICEOVER, ASSET_TOO_SHORT, SHOT_TOO_LONG) können bereits aus einem
# VORHERIGEN vollständigen Validierungslauf auf dem Item kleben (siehe
# attach_validation_to_cut_plan) und dürfen eine ERNEUTE Asset-Auswahl
# NICHT als „Zeit-Mapping blockiert“ verhindern — sonst bleibt ein Item,
# das einmal einen dieser Blocker bekommen hat, bei jedem weiteren
# „Asset-Auswahl anwenden“ dauerhaft ohne VisualSegment, selbst wenn eine
# geänderte Einstellung (z. B. das Visual-Window-Feature) die eigentliche
# Ursache inzwischen beheben könnte.
CUT_PLAN_TIMING_BLOCKER_TYPES = frozenset(
    {
        CUT_PLAN_ERROR_MISSING_ALIGNMENT,
        CUT_PLAN_ERROR_MISSING_AUDIO,
        CUT_PLAN_ERROR_INVALID_AUDIO_PATH,
        CUT_PLAN_ERROR_SOURCE_RANGE_INVALID,
        CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
    }
)

# Blocker-Typen, die typischerweise erst bei einem vollständigen
# Validierungslauf (attach_validation_to_cut_plan) auf item.blockers/
# cut_plan.blockers landen und nach einer inhaltlichen Korrektur
# (Supplement-Übernahme, erneute Asset-Auswahl) als VERALTET gelten —
# im Gegensatz zu CUT_PLAN_TIMING_BLOCKER_TYPES, die echte Struktur-/
# Alignment-Probleme beschreiben und bestehen bleiben dürfen.
CUT_PLAN_STALE_VALIDATION_BLOCKER_TYPES = frozenset(
    {
        CUT_PLAN_ERROR_INVALID_ASSET_ID,
        CUT_PLAN_ERROR_MISSING_ASSET_MAPPING,
        CUT_PLAN_ERROR_ASSET_FILE_MISSING,
        CUT_PLAN_ERROR_ASSET_TOO_SHORT,
        CUT_PLAN_ERROR_SHOT_TOO_SHORT,
        CUT_PLAN_ERROR_SHOT_TOO_LONG,
        CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED,
        CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
        CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
        CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING,
        CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
        CUT_PLAN_ERROR_TIMELINE_OVERLAP,
        CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED,
        CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR,
        CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID,
    }
)

# Phase D (Nutzervorgabe): Cut-Plan-Drafts mit vielen offenen Items können
# hunderte bis tausende Einzelmeldungen erzeugen (siehe Cut-Plan-Diagnose,
# Juli 2026) — die UI gruppiert deshalb nach Fehlertyp UND einer groben
# Root-Cause-Kategorie, statt jede Meldung einzeln als eigene Zeile
# anzuzeigen (siehe group_cut_plan_errors_by_type in cut_plan_validator.py).
CUT_PLAN_ERROR_CATEGORY_AUDIO_ALIGNMENT = "Audio/Alignment"
CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY = "Asset-Beschaffung"
CUT_PLAN_ERROR_CATEGORY_TIMELINE_STRUCTURE = "Timeline/Struktur"
CUT_PLAN_ERROR_CATEGORY_VISUAL_COVERAGE = "Visuelle Abdeckung"
CUT_PLAN_ERROR_CATEGORY_OTHER = "Sonstiges"
CUT_PLAN_ERROR_CATEGORY_LABELS: dict[str, str] = {
    CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY: CUT_PLAN_ERROR_CATEGORY_AUDIO_ALIGNMENT,
    CUT_PLAN_ERROR_MISSING_AUDIO: CUT_PLAN_ERROR_CATEGORY_AUDIO_ALIGNMENT,
    CUT_PLAN_ERROR_MISSING_ALIGNMENT: CUT_PLAN_ERROR_CATEGORY_AUDIO_ALIGNMENT,
    CUT_PLAN_ERROR_INVALID_AUDIO_PATH: CUT_PLAN_ERROR_CATEGORY_AUDIO_ALIGNMENT,
    CUT_PLAN_ERROR_INVALID_ASSET_ID: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_MISSING_ASSET_MAPPING: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_ASSET_FILE_MISSING: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_ASSET_TOO_SHORT: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID: CUT_PLAN_ERROR_CATEGORY_ASSET_SUPPLY,
    CUT_PLAN_ERROR_SOURCE_RANGE_INVALID: CUT_PLAN_ERROR_CATEGORY_TIMELINE_STRUCTURE,
    CUT_PLAN_ERROR_SHOT_TOO_SHORT: CUT_PLAN_ERROR_CATEGORY_TIMELINE_STRUCTURE,
    CUT_PLAN_ERROR_SHOT_TOO_LONG: CUT_PLAN_ERROR_CATEGORY_TIMELINE_STRUCTURE,
    CUT_PLAN_ERROR_TIMELINE_OVERLAP: CUT_PLAN_ERROR_CATEGORY_TIMELINE_STRUCTURE,
    CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR: CUT_PLAN_ERROR_CATEGORY_TIMELINE_STRUCTURE,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER: CUT_PLAN_ERROR_CATEGORY_VISUAL_COVERAGE,
    CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED: CUT_PLAN_ERROR_CATEGORY_VISUAL_COVERAGE,
}

MODEL_COMPARISON_SUBDIR = "model_comparison_runs"
MODEL_COMPARISON_SUMMARY_FILENAME = "model_comparison_summary.json"
MODEL_COMPARISON_PRESETS: dict[str, tuple[str, ...]] = {
    "gemini": ("gemini-3.1-pro-preview", "gemini-3.1-flash-lite"),
    "anthropic": ("anthropic:claude-sonnet-5", "anthropic:claude-opus-4-8"),
    "openai": ("openai:gpt-5.5", "openai:gpt-5.4-mini"),
}
EXPORTS_SUBDIR = "exports"
CLEAN_MEDIA_OUTPUT_SUBDIR = "clean"
CLEAN_MEDIA_MANIFEST_SUBDIR = "clean_media"
DEFAULT_SHOT_MIN_SEC = 3.0
DEFAULT_SHOT_MAX_SEC = 8.0
DEFAULT_AUDIO_OFFSET_SEC = 1.0
DEFAULT_SECTION_OUTRO_SEC = 5.0
MAX_GEMINI_PLAN_ATTEMPTS = 3
OUTRO_BEAT_ID = "outro_001"
DEFAULT_TEXT_SPLITTERS = (", und ", ", ", " und ")
FALLBACK_SOURCE_LOCAL = "local"
FALLBACK_SOURCE_MISSING = "missing"
FALLBACK_SOURCE_ADOBE_STOCK = "adobe_stock"
FALLBACK_SOURCE_PEXELS = "pexels"
FALLBACK_SOURCE_GEMINI_IMAGE = "gemini_image"
FALLBACK_SOURCE_CHOICES = (
    FALLBACK_SOURCE_LOCAL,
    FALLBACK_SOURCE_ADOBE_STOCK,
    FALLBACK_SOURCE_PEXELS,
    FALLBACK_SOURCE_GEMINI_IMAGE,
)
FALLBACK_SOURCE_LABELS = {
    FALLBACK_SOURCE_LOCAL: "Lokale Assets",
    FALLBACK_SOURCE_ADOBE_STOCK: "Adobe Stock",
    FALLBACK_SOURCE_PEXELS: "Pexels & Co.",
    FALLBACK_SOURCE_GEMINI_IMAGE: "KI-Bild (Gemini)",
}
DEFAULT_FALLBACK_ORDER = (
    FALLBACK_SOURCE_LOCAL,
    FALLBACK_SOURCE_ADOBE_STOCK,
    FALLBACK_SOURCE_PEXELS,
    FALLBACK_SOURCE_GEMINI_IMAGE,
)

SUPPLEMENT_SUBDIR = "supplement"
SUPPLEMENT_REQUESTS_FILENAME = "supplement_requests.json"
SUPPLEMENT_MANIFEST_FILENAME = "supplement_manifest.json"
SUPPLEMENT_ERRORS_FILENAME = "supplement_errors.json"
PEXELS_DEBUG_REPORT_FILENAME = "pexels_debug_report.json"
INVENTORY_DELTA_SUFFIX = ".inventory_delta.json"
SUPPLEMENTAL_FOLDER_NAME = "_supplemental"
DEFAULT_COVERAGE_THRESHOLD = 0.55

MATCH_QUALITY_SEHR_GUT = "sehr_gut"
MATCH_QUALITY_GUT = "gut"
MATCH_QUALITY_MITTEL = "mittel"
MATCH_QUALITY_UNPASSEND = "unpassend"

MATCH_QUALITY_CHOICES = (
    MATCH_QUALITY_SEHR_GUT,
    MATCH_QUALITY_GUT,
    MATCH_QUALITY_MITTEL,
    MATCH_QUALITY_UNPASSEND,
)

MATCH_QUALITY_LABELS = {
    MATCH_QUALITY_SEHR_GUT: "Sehr gut",
    MATCH_QUALITY_GUT: "Gut",
    MATCH_QUALITY_MITTEL: "Mittel",
    MATCH_QUALITY_UNPASSEND: "Unpassend",
}

PROVIDER_STATUS_MOCK = "MOCK"
PROVIDER_STATUS_CONFIG_MISSING = "CONFIG_MISSING"
PROVIDER_STATUS_READY = "READY"
PROVIDER_STATUS_ERROR = "ERROR"

CANDIDATE_STATUS_FOUND = "CANDIDATE_FOUND"
CANDIDATE_STATUS_MOCK_ONLY = "CANDIDATE_MOCK_ONLY"
CANDIDATE_STATUS_SELECTED = "SELECTED"
CANDIDATE_STATUS_ACQUIRE_STARTED = "ACQUIRE_STARTED"
CANDIDATE_STATUS_ACQUIRED = "ACQUIRED"
CANDIDATE_STATUS_DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
CANDIDATE_STATUS_REJECTED_ASPECT_RATIO = "REJECTED_ASPECT_RATIO"
CANDIDATE_STATUS_NOT_DOWNLOADABLE_16_9 = "NOT_DOWNLOADABLE_16_9"
CANDIDATE_STATUS_GENERATION_FAILED = "GENERATION_FAILED"
CANDIDATE_STATUS_IMPORTED_MANUALLY = "IMPORTED_MANUALLY"
CANDIDATE_STATUS_ANALYSIS_PENDING = "ANALYSIS_PENDING"
CANDIDATE_STATUS_ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
CANDIDATE_STATUS_INVENTORY_ADDED = "INVENTORY_ADDED"

REQUEST_STATUS_PENDING_SOURCE = "PENDING_SOURCE_SELECTION"
REQUEST_STATUS_SOURCE_SELECTED = "SOURCE_SELECTED"
REQUEST_STATUS_CANDIDATES_FOUND = "CANDIDATES_FOUND"
REQUEST_STATUS_AWAITING_SELECTION = "AWAITING_USER_SELECTION"
REQUEST_STATUS_ACQUIRE_FAILED = "ACQUIRE_FAILED"
REQUEST_STATUS_ASSET_ACQUIRED = "ASSET_ACQUIRED"
REQUEST_STATUS_ANALYSIS_PENDING = "ANALYSIS_PENDING"
REQUEST_STATUS_INVENTORY_UPDATED = "INVENTORY_UPDATED"
REQUEST_STATUS_READY_FOR_REPLAN = "READY_FOR_REPLAN"

SUPPLEMENT_SOURCE_ADOBE = "adobe_stock"
SUPPLEMENT_SOURCE_PEXELS = "pexels"
SUPPLEMENT_SOURCE_GOOGLE = "google_search"
SUPPLEMENT_SOURCE_NANO_BANANA = "nano_banana"
SUPPLEMENT_SOURCE_MANUAL = "manual"

SUPPLEMENT_SOURCE_LABELS = {
    SUPPLEMENT_SOURCE_ADOBE: "Adobe Stock",
    SUPPLEMENT_SOURCE_PEXELS: "Pexels",
    SUPPLEMENT_SOURCE_GOOGLE: "Google Suche",
    SUPPLEMENT_SOURCE_NANO_BANANA: "Nano Banana / KI-Bild",
    SUPPLEMENT_SOURCE_MANUAL: "Manuell später",
}

ASSET_ORIGIN_LOCAL = "local_original"
ASSET_ORIGIN_ADOBE = "adobe_stock"
ASSET_ORIGIN_PEXELS = "pexels"
ASSET_ORIGIN_GOOGLE = "google_search"
ASSET_ORIGIN_NANO_BANANA = "nano_banana"
ASSET_ORIGIN_MANUAL = "manual"

RIGHTS_STATUS_APPROVED = "APPROVED"
RIGHTS_STATUS_NEEDS_LICENSE_REVIEW = "NEEDS_LICENSE_REVIEW"
RIGHTS_STATUS_GENERATED_APPROVED = "GENERATED_APPROVED"
RIGHTS_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"

# --- Cut-Plan-Supplement Phase 12.1/12.2a: Adobe Stock Suche ---
# X-Product-Header — per ADOBE_STOCK_PRODUCT_NAME (Umgebungsvariable/
# user_secrets.env, siehe otio_app.services.api_keys.get_api_key) optional
# überschreibbar; bewusst KEIN eigenes UI-Feld dafür (kein Secret, ein
# sinnvoller Default reicht für praktisch alle Nutzer).
ADOBE_STOCK_DEFAULT_PRODUCT_NAME = "OTIO-App/1.0"
ADOBE_STOCK_SEARCH_ENDPOINT = "https://stock.adobe.io/Rest/Media/1/Search/Files"
# Adobe media_type_id-Codes (siehe Search-API-Referenz) — nur die für die
# Supplement-Suche relevanten Typen (Foto/Video), alles andere (Illustration,
# Vektor, 3D, Templates, Premium, Audio) wird beim Mapping übersprungen.
ADOBE_STOCK_MEDIA_TYPE_ID_PHOTO = 1
ADOBE_STOCK_MEDIA_TYPE_ID_VIDEO = 4
# Nutzervorgabe (Juli 2026): generative-AI-Stockassets werden bei Adobe
# Stock IMMER ausgeschlossen — sowohl über den Suchfilter
# (search_parameters[filters][gentech]=false) als auch als zusätzlicher
# Code-seitiger Sicherheitsnetz-Check auf is_gentech in der Response.
ADOBE_STOCK_REJECTED_REASON_GENTECH = "ADOBE_GENTECH_REJECTED"

# --- Phase 12.3: Adobe Stock Lizenzierung + Download ("sofort lizenzieren,
# dann erst per Gemini prüfen" — Nutzerentscheidung Juli 2026: unlimited
# Adobe-Stock-Plan, ein versehentlich falsch lizenziertes Asset kostet
# nichts extra) ---
ADOBE_STOCK_LICENSE_ENDPOINT = "https://stock.adobe.io/Rest/Libraries/1/Content/License"
# Phase 12.12: Diagnose-Endpunkte vor Content/License — zeigen Quota,
# purchase_options, possible_licenses und den aktuellen Lizenzstatus eines
# Assets (hilfreich bei CC-Pro-/Unlimited-Plänen und manueller API-Freigabe).
ADOBE_STOCK_CONTENT_INFO_ENDPOINT = "https://stock.adobe.io/Rest/Libraries/1/Content/Info"
ADOBE_STOCK_MEMBER_PROFILE_ENDPOINT = "https://stock.adobe.io/Rest/Libraries/1/Member/Profile"
# Gültige license-Parameterwerte für Content/License — Fotos/3D/Templates
# nutzen "Standard", Videos ausschließlich "Video_HD" oder "Video_4K"
# (siehe Adobe-Stock-Lizenzierungs-Referenz).
ADOBE_STOCK_LICENSE_TYPE_STANDARD = "Standard"
ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD = "Video_HD"
ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K = "Video_4K"
# Nutzervorgabe: 4K nur, wenn die Datei <= 600 MB ist — sonst HD. Ist die
# Größe vorab unbekannt, wird 4K versucht und während des Downloads auf HD
# gewechselt, falls die Grenze überschritten wird.
ADOBE_STOCK_VIDEO_4K_MAX_BYTES = 600 * 1024 * 1024
ADOBE_STOCK_MIN_DOWNLOAD_BYTES = 100 * 1024

# Phase 12.5: Reihenfolge, in der der Cut-Plan-Auto-Resolver Stock-Provider
# durchsucht — Nutzervorgabe: erst Adobe Stock (sofortige Lizenzierung,
# unlimited Plan), dann Pexels als kostenlose Ausweichquelle, danach greift
# der generische Ordner-Fallback (siehe cut_plan_generic_fallback_service.py).
CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER = (SUPPLEMENT_SOURCE_ADOBE, SUPPLEMENT_SOURCE_PEXELS)

# Phase E (Nutzervorgabe, Juli 2026): Pseudo-"Provider"-Label für einen
# Auto-Resolve-Kandidaten, der aus einem bereits heruntergeladenen Cut-Plan-
# Supplement-Asset rekonstruiert wurde (siehe supplement_manifest.json,
# find_reusable_local_supplement_candidates in
# cut_plan_supplement_auto_resolve_service.py) — läuft als eigene Stufe VOR
# der externen Adobe-/Pexels-Suche, damit ein bereits lizenziertes Asset
# nicht unnötig ein zweites Mal beschafft wird.
CUT_PLAN_AUTO_RESOLVE_PROVIDER_LOCAL_REUSE = "local_reuse"


def resolve_voice_backend(backend: str | None) -> str:
    if backend and backend.strip() in VOICE_BACKEND_CHOICES:
        return backend.strip()
    return DEFAULT_VOICE_BACKEND


def resolve_whisper_model(model: str | None) -> str:
    if model and model.strip() in WHISPER_MODEL_CHOICES:
        return model.strip()
    return DEFAULT_WHISPER_MODEL
