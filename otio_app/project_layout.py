"""Projektordner-Struktur und Asset-Erkennung."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from otio_app.defaults import (
    DEFAULT_WORK_SUBDIR,
    EDIT_PLAN_FILENAME,
    EDIT_PLAN_SUBDIR,
    INVENTORY_FILENAME,
    INVENTORY_SUBDIR,
    VOICE_ANALYSIS_FILENAME,
    VOICE_FOLDER_MAPPING_FILENAME,
)

LANGUAGE_FOLDER_NAMES: dict[str, str] = {
    "de": "DE",
    "en": "EN",
}

VOICE_OVER_NAME_HINTS: tuple[str, ...] = (
    "voice over",
    "voiceover",
    "voice-over",
    "voice_over",
)


def language_folder_name(language: str) -> str:
    """Ordnername für Voice-over-Sprachen (z. B. de -> DE)."""
    normalized = language.strip().lower()
    return LANGUAGE_FOLDER_NAMES.get(normalized, language.strip().upper())


def get_language_work_dir(work_dir: Path, language: str) -> Path:
    """Sprachspezifischer Editorial-Arbeitsordner: `_otio/{DE|EN}/`.

    SHARED-Artefakte (clean, inventory, frames, …) bleiben unter `work_dir`.
    """
    return work_dir / language_folder_name(language)


def default_work_dir(project_root: Path) -> Path:
    """Standard-Arbeitsordner innerhalb des Projektroots."""
    return project_root / DEFAULT_WORK_SUBDIR


def get_voice_over_dir(
    project_root: Path,
    voice_over_subdir: str,
    language: str,
) -> Path:
    """Pfad zum sprachspezifischen Voice-over-Unterordner."""
    return project_root / voice_over_subdir / language_folder_name(language)


def safe_folder_slug(value: str) -> str:
    """Dateiname-sicherer Slug für Ordner- und Medien-Cache."""
    return value.replace(" ", "_").replace("/", "_")


def get_inventory_dir(work_dir: Path) -> Path:
    """Verzeichnis für pro-Ordner-Inventar-JSONs unter dem Arbeitsordner."""
    return work_dir / INVENTORY_SUBDIR


def get_folder_inventory_path(work_dir: Path, folder_name: str) -> Path:
    """Pfad zur Inventar-JSON eines Asset-Ordners (z. B. _otio/inventory/Florida_Keys.json)."""
    return get_inventory_dir(work_dir) / f"{safe_folder_slug(folder_name)}.json"


def get_inventory_path(project_root: Path) -> Path:
    """Legacy-Pfad zur zentralen inventory.json im Projektroot (Migration)."""
    return project_root / INVENTORY_FILENAME


def get_voice_analysis_path(project_root: Path) -> Path:
    return project_root / VOICE_ANALYSIS_FILENAME


def get_voice_folder_mapping_path(base_dir: Path) -> Path:
    """Pfad zur Voice↔Folder-Zuordnung.

    `base_dir` ist der Language-Scope (`_otio/{LANG}/`). Legacy-Aufrufe mit
    `project_root` werden weiterhin akzeptiert, bevorzugter Speicherort ist
    der Language-Work-Dir.
    """
    return base_dir / VOICE_FOLDER_MAPPING_FILENAME


def get_edit_plan_path(project_root: Path) -> Path:
    """Legacy-Pfad zur zentralen edit_plan.json im Projektroot (Migration)."""
    return project_root / EDIT_PLAN_FILENAME


def get_edit_plan_dir(work_dir: Path) -> Path:
    """Verzeichnis für pro-Ort-Schnittpläne unter dem Arbeitsordner."""
    return work_dir / EDIT_PLAN_SUBDIR


def get_model_comparison_runs_dir(work_dir: Path) -> Path:
    from otio_app.defaults import MODEL_COMPARISON_SUBDIR

    return work_dir / MODEL_COMPARISON_SUBDIR


def get_model_comparison_batch_dir(work_dir: Path, comparison_id: str) -> Path:
    return get_model_comparison_runs_dir(work_dir) / comparison_id


def get_model_comparison_run_dir(work_dir: Path, comparison_id: str, run_id: str) -> Path:
    return get_model_comparison_batch_dir(work_dir, comparison_id) / run_id


def get_model_comparison_summary_path(work_dir: Path, comparison_id: str) -> Path:
    from otio_app.defaults import MODEL_COMPARISON_SUMMARY_FILENAME

    return get_model_comparison_batch_dir(work_dir, comparison_id) / MODEL_COMPARISON_SUMMARY_FILENAME


def get_folder_edit_plan_path(work_dir: Path, folder_name: str) -> Path:
    """Pfad zur Schnittplan-JSON eines Asset-Ordners (z. B. _otio/edit_plan/Florida_Keys.json)."""
    return get_edit_plan_dir(work_dir) / f"{safe_folder_slug(folder_name)}.json"


# --- "Projekt ohne Voice-Over": Dramaturgie- & Voice-over-Generierungs-Pipeline ---
# Eigener Artefaktbaum, komplett getrennt von edit_plan/exports. Siehe
# otio_app.defaults für die Datei-/Unterordnernamen.


def get_voiceover_generation_dir(work_dir: Path) -> Path:
    """Wurzel aller Artefakte der Dramaturgie-/Voice-over-Generierungs-Pipeline."""
    from otio_app.defaults import VOICEOVER_GENERATION_SUBDIR

    return work_dir / VOICEOVER_GENERATION_SUBDIR


def get_project_brief_path(work_dir: Path) -> Path:
    from otio_app.defaults import PROJECT_BRIEF_FILENAME

    return get_voiceover_generation_dir(work_dir) / PROJECT_BRIEF_FILENAME


def get_voiceover_style_references_path(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_STYLE_REFERENCES_FILENAME

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_STYLE_REFERENCES_FILENAME


def get_style_references_dir(work_dir: Path) -> Path:
    """Verzeichnis für hochgeladene Style-Referenz-Rohtexte (Audit, nicht die Quelle der Wahrheit)."""
    from otio_app.defaults import STYLE_REFERENCES_SUBDIR

    return get_voiceover_generation_dir(work_dir) / STYLE_REFERENCES_SUBDIR


def get_style_references_uploads_dir(work_dir: Path) -> Path:
    from otio_app.defaults import STYLE_REFERENCES_UPLOADS_SUBDIR

    return get_style_references_dir(work_dir) / STYLE_REFERENCES_UPLOADS_SUBDIR


def get_model_settings_path(work_dir: Path) -> Path:
    """Provider-/Modell-Einstellungen pro Rolle (style_profile, dramaturgy, …)."""
    from otio_app.defaults import MODEL_SETTINGS_FILENAME

    return get_voiceover_generation_dir(work_dir) / MODEL_SETTINGS_FILENAME


def get_folder_inventory_summaries_path(work_dir: Path) -> Path:
    """Debug-Artefakt: alle pro-Ordner-Zusammenfassungen, die an das Dramaturgie-LLM gingen."""
    from otio_app.defaults import FOLDER_INVENTORY_SUMMARIES_FILENAME

    return get_voiceover_generation_dir(work_dir) / FOLDER_INVENTORY_SUMMARIES_FILENAME


def get_voiceover_style_profile_path(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_STYLE_PROFILE_FILENAME

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_STYLE_PROFILE_FILENAME


def get_dramaturgy_plan_draft_path(work_dir: Path) -> Path:
    from otio_app.defaults import DRAMATURGY_PLAN_DRAFT_FILENAME

    return get_voiceover_generation_dir(work_dir) / DRAMATURGY_PLAN_DRAFT_FILENAME


def get_dramaturgy_plan_confirmed_path(work_dir: Path) -> Path:
    from otio_app.defaults import DRAMATURGY_PLAN_CONFIRMED_FILENAME

    return get_voiceover_generation_dir(work_dir) / DRAMATURGY_PLAN_CONFIRMED_FILENAME


def get_folder_voiceover_settings_path(work_dir: Path) -> Path:
    from otio_app.defaults import FOLDER_VOICEOVER_SETTINGS_FILENAME

    return get_voiceover_generation_dir(work_dir) / FOLDER_VOICEOVER_SETTINGS_FILENAME


def get_folder_voiceovers_draft_path(work_dir: Path) -> Path:
    from otio_app.defaults import FOLDER_VOICEOVERS_DRAFT_FILENAME

    return get_voiceover_generation_dir(work_dir) / FOLDER_VOICEOVERS_DRAFT_FILENAME


def get_folder_voiceover_validation_report_path(work_dir: Path) -> Path:
    from otio_app.defaults import FOLDER_VOICEOVER_VALIDATION_REPORT_FILENAME

    return get_voiceover_generation_dir(work_dir) / FOLDER_VOICEOVER_VALIDATION_REPORT_FILENAME


def get_folder_voiceovers_confirmed_path(work_dir: Path) -> Path:
    from otio_app.defaults import FOLDER_VOICEOVERS_CONFIRMED_FILENAME

    return get_voiceover_generation_dir(work_dir) / FOLDER_VOICEOVERS_CONFIRMED_FILENAME


def get_intro_hook_candidates_path(work_dir: Path) -> Path:
    from otio_app.defaults import INTRO_HOOK_CANDIDATES_FILENAME

    return get_voiceover_generation_dir(work_dir) / INTRO_HOOK_CANDIDATES_FILENAME


def get_intro_hook_confirmed_path(work_dir: Path) -> Path:
    from otio_app.defaults import INTRO_HOOK_CONFIRMED_FILENAME

    return get_voiceover_generation_dir(work_dir) / INTRO_HOOK_CONFIRMED_FILENAME


def get_intro_hook_settings_path(work_dir: Path) -> Path:
    from otio_app.defaults import INTRO_HOOK_SETTINGS_FILENAME

    return get_voiceover_generation_dir(work_dir) / INTRO_HOOK_SETTINGS_FILENAME


def get_asset_readiness_pipeline_settings_path(work_dir: Path) -> Path:
    from otio_app.defaults import ASSET_READINESS_PIPELINE_SETTINGS_FILENAME

    return get_voiceover_generation_dir(work_dir) / ASSET_READINESS_PIPELINE_SETTINGS_FILENAME


def get_elevenlabs_settings_path(work_dir: Path) -> Path:
    """Persistente ElevenLabs-Einstellungen (Voice/Modell/Stimm-Parameter) — niemals den API-Key."""
    from otio_app.defaults import ELEVENLABS_SETTINGS_FILENAME

    return get_voiceover_generation_dir(work_dir) / ELEVENLABS_SETTINGS_FILENAME


def get_voiceover_audio_manifest_path(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_AUDIO_MANIFEST_FILENAME

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_AUDIO_MANIFEST_FILENAME


def get_voiceover_audio_qa_report_path(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_AUDIO_QA_REPORT_FILENAME

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_AUDIO_QA_REPORT_FILENAME


def get_confirmed_voiceover_project_plan_path(work_dir: Path) -> Path:
    """Redaktionelle Quelle der Wahrheit für die spätere Schnittplan-Pipeline."""
    from otio_app.defaults import CONFIRMED_VOICEOVER_PROJECT_PLAN_FILENAME

    return get_voiceover_generation_dir(work_dir) / CONFIRMED_VOICEOVER_PROJECT_PLAN_FILENAME


def get_voiceover_project_plan_json_path(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_PROJECT_PLAN_JSON_FILENAME

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_PROJECT_PLAN_JSON_FILENAME


def get_voiceover_project_plan_md_path(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_PROJECT_PLAN_MD_FILENAME

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_PROJECT_PLAN_MD_FILENAME


def get_voiceover_project_plan_csv_path(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_PROJECT_PLAN_CSV_FILENAME

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_PROJECT_PLAN_CSV_FILENAME


def get_voiceover_generation_audio_dir(work_dir: Path) -> Path:
    """Wurzel aller erzeugten Audiodateien (TTS) dieser Pipeline."""
    from otio_app.defaults import VOICEOVER_GENERATION_AUDIO_SUBDIR

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_GENERATION_AUDIO_SUBDIR


def get_intro_audio_dir(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_GENERATION_INTRO_AUDIO_FOLDER_NAME

    return get_voiceover_generation_audio_dir(work_dir) / VOICEOVER_GENERATION_INTRO_AUDIO_FOLDER_NAME


def get_folder_voiceover_audio_dir(work_dir: Path, order_index: int, folder_name: str) -> Path:
    """Audio-Verzeichnis eines Ortes: audio/{order_index}_{safe_folder_name}/."""
    return get_voiceover_generation_audio_dir(work_dir) / f"{order_index}_{safe_folder_slug(folder_name)}"


def get_folder_tts_runs_dir(work_dir: Path, order_index: int, folder_name: str) -> Path:
    from otio_app.defaults import VOICEOVER_GENERATION_TTS_RUNS_SUBDIR

    return get_folder_voiceover_audio_dir(work_dir, order_index, folder_name) / VOICEOVER_GENERATION_TTS_RUNS_SUBDIR


def get_tts_run_dir(work_dir: Path, order_index: int, folder_name: str, tts_run_id: str) -> Path:
    """Ein nachvollziehbarer TTS-Lauf: Request/Response-Metadaten, Timestamps, Fehler — nie den API-Key."""
    return get_folder_tts_runs_dir(work_dir, order_index, folder_name) / tts_run_id


def get_llm_runs_dir(work_dir: Path) -> Path:
    """Wurzel aller nachvollziehbaren LLM-Läufe dieser Pipeline (Raw/Parsed/Review/Correction)."""
    from otio_app.defaults import VOICEOVER_GENERATION_LLM_RUNS_SUBDIR

    return get_voiceover_generation_dir(work_dir) / VOICEOVER_GENERATION_LLM_RUNS_SUBDIR


def get_llm_run_dir(work_dir: Path, run_id: str) -> Path:
    return get_llm_runs_dir(work_dir) / run_id


def get_intro_tts_runs_dir(work_dir: Path) -> Path:
    from otio_app.defaults import VOICEOVER_GENERATION_TTS_RUNS_SUBDIR

    return get_intro_audio_dir(work_dir) / VOICEOVER_GENERATION_TTS_RUNS_SUBDIR


def get_intro_tts_run_dir(work_dir: Path, tts_run_id: str) -> Path:
    return get_intro_tts_runs_dir(work_dir) / tts_run_id


def get_folder_alignment_path(work_dir: Path, order_index: int, folder_name: str) -> Path:
    from otio_app.defaults import AUDIO_ALIGNMENT_FILENAME

    return get_folder_voiceover_audio_dir(work_dir, order_index, folder_name) / AUDIO_ALIGNMENT_FILENAME


def get_intro_alignment_path(work_dir: Path) -> Path:
    from otio_app.defaults import AUDIO_ALIGNMENT_FILENAME

    return get_intro_audio_dir(work_dir) / AUDIO_ALIGNMENT_FILENAME


def get_audio_test_dir(work_dir: Path) -> Path:
    """Für 'Test Voice' — kein Manifest-Eintrag, nur zum Anhören der Einstellungen."""
    from otio_app.defaults import AUDIO_TEST_SUBDIR

    return get_voiceover_generation_audio_dir(work_dir) / AUDIO_TEST_SUBDIR


# --- Cut Plan (Phase 8): eigener Unterordner, getrennt von _otio/edit_plan/ ---


def get_cut_plan_dir(work_dir: Path) -> Path:
    """Wurzel aller Cut-Plan-Artefakte (Phase 8) — getrennt von _otio/edit_plan/."""
    from otio_app.defaults import CUT_PLAN_SUBDIR

    return get_voiceover_generation_dir(work_dir) / CUT_PLAN_SUBDIR


def get_cut_plan_settings_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_SETTINGS_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_SETTINGS_FILENAME


def get_cut_plan_draft_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_DRAFT_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_DRAFT_FILENAME


def get_cut_plan_validation_report_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_VALIDATION_REPORT_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_VALIDATION_REPORT_FILENAME


def get_cut_plan_confirmed_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_CONFIRMED_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_CONFIRMED_FILENAME


def get_cut_plan_trace_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_TRACE_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_TRACE_FILENAME


def get_cut_plan_supplement_requests_path(work_dir: Path) -> Path:
    """Isolierte Supplement-Requests aus dem Cut-Plan-Workflow — NIEMALS
    identisch mit _otio/supplement/supplement_requests.json (Produktion)."""
    from otio_app.defaults import CUT_PLAN_SUPPLEMENT_REQUESTS_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_SUPPLEMENT_REQUESTS_FILENAME


def get_cut_plan_supplement_candidates_path(work_dir: Path) -> Path:
    """Isolierter Kandidaten-Speicher (Phase 8.6) — NIEMALS identisch mit
    _otio/supplement/supplement_requests.json (Produktion, dort sind
    Kandidaten Teil desselben Dokuments)."""
    from otio_app.defaults import CUT_PLAN_SUPPLEMENT_CANDIDATES_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_SUPPLEMENT_CANDIDATES_FILENAME


def get_cut_plan_supplement_assets_dir(work_dir: Path) -> Path:
    """Wurzel aller heruntergeladenen Cut-Plan-Supplement-Assets — getrennt
    von {folder}/_supplemental/ (Produktions-Konvention)."""
    from otio_app.defaults import CUT_PLAN_SUPPLEMENT_ASSETS_SUBDIR

    return get_cut_plan_dir(work_dir) / CUT_PLAN_SUPPLEMENT_ASSETS_SUBDIR


def get_cut_plan_supplement_asset_request_dir(work_dir: Path, request_id: str) -> Path:
    """Unterordner je Supplement Request unter supplement_assets/."""
    safe_request_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in request_id) or "request"
    return get_cut_plan_supplement_assets_dir(work_dir) / safe_request_id


def get_cut_plan_supplement_runs_dir(work_dir: Path) -> Path:
    """Wurzel für optionale Such-/Beschaffungslauf-Protokolle (Phase 8.6)."""
    from otio_app.defaults import CUT_PLAN_SUPPLEMENT_RUNS_SUBDIR

    return get_cut_plan_dir(work_dir) / CUT_PLAN_SUPPLEMENT_RUNS_SUBDIR


def get_cut_plan_supplement_manifest_path(work_dir: Path) -> Path:
    """Optionales Gesamt-Manifest aller akzeptierten Cut-Plan-Supplement-Assets."""
    from otio_app.defaults import CUT_PLAN_SUPPLEMENT_MANIFEST_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_SUPPLEMENT_MANIFEST_FILENAME


def get_cut_plan_validation_repair_requests_path(work_dir: Path) -> Path:
    """Validation Repair Requests (eigenständig, siehe
    cut_plan_validation_repair.py) — getrennt von
    supplement_requests.from_cut_plan.json, da Reparatur-Requests eine
    andere Semantik haben (Zeitfenster-Reparatur statt Item-Ersatz)."""
    from otio_app.defaults import CUT_PLAN_VALIDATION_REPAIR_REQUESTS_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_VALIDATION_REPAIR_REQUESTS_FILENAME


def get_cut_plan_residual_gap_requests_path(work_dir: Path) -> Path:
    """Residual Gap Requests (eigenständig, siehe
    cut_plan_residual_gap_requests.py) — getrennt von sowohl
    supplement_requests.from_cut_plan.json (Item hat noch KEIN Asset) als
    auch validation_repair_requests.json (kleine, per Nachbar-Kürzung
    reparierbare Lücke)."""
    from otio_app.defaults import CUT_PLAN_RESIDUAL_GAP_REQUESTS_FILENAME

    return get_cut_plan_dir(work_dir) / CUT_PLAN_RESIDUAL_GAP_REQUESTS_FILENAME


# --- EditPlan Bridge (Phase 9.1): isolierte Brücke von cut_plan.confirmed.json
# zu einem EditPlanDocument-kompatiblen Draft — getrennt von _otio/edit_plan/
# und _otio/exports/. ---


def get_cut_plan_edit_plan_bridge_dir(work_dir: Path) -> Path:
    """Wurzel aller EditPlan-Bridge-Artefakte (Phase 9.1)."""
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_SUBDIR

    return get_cut_plan_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_SUBDIR


def get_cut_plan_edit_plan_bridge_draft_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_DRAFT_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_DRAFT_FILENAME


def get_cut_plan_edit_plan_bridge_trace_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_TRACE_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_TRACE_FILENAME


def get_cut_plan_edit_plan_bridge_validation_report_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_VALIDATION_REPORT_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_VALIDATION_REPORT_FILENAME


def get_cut_plan_edit_plan_bridge_audio_plan_path(work_dir: Path) -> Path:
    """Phase 9.2: strukturierte Audio-Plan-Datei — vermeidet, dass eine
    spätere Phase aus dem TimelineItem-Sondertyp 'voiceover_audio' raten muss."""
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_AUDIO_PLAN_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_AUDIO_PLAN_FILENAME


# --- EditPlan Bridge Confirm/Freeze (Phase 9.3): isolierte Snapshot-Dateien,
# NIEMALS ein Produktions-EditPlan unter _otio/edit_plan/. ---


def get_cut_plan_edit_plan_bridge_confirmed_draft_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_DRAFT_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_DRAFT_FILENAME


def get_cut_plan_edit_plan_bridge_confirmed_audio_plan_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_AUDIO_PLAN_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_AUDIO_PLAN_FILENAME


def get_cut_plan_edit_plan_bridge_confirmed_trace_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_TRACE_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRMED_TRACE_FILENAME


def get_cut_plan_edit_plan_bridge_confirm_manifest_path(work_dir: Path) -> Path:
    from otio_app.defaults import CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRM_MANIFEST_FILENAME

    return get_cut_plan_edit_plan_bridge_dir(work_dir) / CUT_PLAN_EDIT_PLAN_BRIDGE_CONFIRM_MANIFEST_FILENAME


# --- Production EditPlan Staging (Phase 10.1): isoliertes Staging-Paket aus
# dem bestätigten EditPlan-Bridge-Snapshot — Geschwister von edit_plan_bridge/
# unter demselben cut_plan/-Wurzelverzeichnis. NIEMALS unter _otio/edit_plan/
# (das erfolgt erst in einer separaten, späteren Promote-Phase). ---


def get_production_edit_plan_staging_dir(work_dir: Path) -> Path:
    """Wurzel aller Production-EditPlan-Staging-Artefakte (Phase 10.1)."""
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_STAGING_SUBDIR

    return get_cut_plan_dir(work_dir) / PRODUCTION_EDIT_PLAN_STAGING_SUBDIR


def get_production_edit_plan_package_path(work_dir: Path) -> Path:
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_PACKAGE_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_PACKAGE_FILENAME


def get_staged_edit_plans_dir(work_dir: Path) -> Path:
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_STAGED_EDIT_PLANS_SUBDIR

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_STAGED_EDIT_PLANS_SUBDIR


def get_staged_edit_plan_dir(work_dir: Path, staging_section_id: str) -> Path:
    """Unterordner je Sektion unter staged_edit_plans/ — dateinamensicher."""
    safe_section_id = (
        "".join(char if char.isalnum() or char in "-_" else "_" for char in staging_section_id) or "section"
    )
    return get_staged_edit_plans_dir(work_dir) / safe_section_id


def get_staged_edit_plan_path(work_dir: Path, staging_section_id: str) -> Path:
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_STAGED_EDIT_PLAN_FILENAME

    return get_staged_edit_plan_dir(work_dir, staging_section_id) / PRODUCTION_EDIT_PLAN_STAGED_EDIT_PLAN_FILENAME


def get_production_edit_plan_mapping_trace_path(work_dir: Path) -> Path:
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_MAPPING_TRACE_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_MAPPING_TRACE_FILENAME


def get_production_edit_plan_validation_report_path(work_dir: Path) -> Path:
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_VALIDATION_REPORT_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_VALIDATION_REPORT_FILENAME


def get_production_edit_plan_promote_readiness_path(work_dir: Path) -> Path:
    """Phase 10.5: Promote Readiness / Dry Run — rein prüfend, kein Schreiben
    nach `_otio/edit_plan/`."""
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_FILENAME


def get_production_edit_plan_promote_dry_run_trace_path(work_dir: Path) -> Path:
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_PROMOTE_DRY_RUN_TRACE_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_PROMOTE_DRY_RUN_TRACE_FILENAME


def get_production_edit_plan_promote_manifest_path(work_dir: Path) -> Path:
    """Phase 10.6: Promote-Manifest EINES tatsächlichen Promote-Laufs."""
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_FILENAME


def get_production_edit_plan_voice_folder_mapping_patch_path(work_dir: Path) -> Path:
    """Phase 10.6: reiner Vorbereitungs-Patch — verändert `voice_folder_mapping.json`
    selbst NICHT."""
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_VOICE_FOLDER_MAPPING_PATCH_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_VOICE_FOLDER_MAPPING_PATCH_FILENAME


def get_production_edit_plan_promote_backups_dir(work_dir: Path) -> Path:
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_PROMOTE_BACKUPS_SUBDIR

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_PROMOTE_BACKUPS_SUBDIR


def get_production_edit_plan_promote_backup_run_dir(work_dir: Path, promote_run_id: str) -> Path:
    safe_run_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in promote_run_id) or "run"
    return get_production_edit_plan_promote_backups_dir(work_dir) / safe_run_id


def get_voice_folder_mapping_merge_manifest_path(work_dir: Path) -> Path:
    """Phase 10.7: Merge-Manifest EINES tatsächlichen Voice-Folder-Mapping-
    Merge-Laufs — dokumentiert, was in `voice_folder_mapping.json`
    tatsächlich verändert wurde."""
    from otio_app.defaults import VOICE_FOLDER_MAPPING_MERGE_MANIFEST_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / VOICE_FOLDER_MAPPING_MERGE_MANIFEST_FILENAME


def get_voice_folder_mapping_merge_backups_dir(work_dir: Path) -> Path:
    from otio_app.defaults import VOICE_FOLDER_MAPPING_MERGE_BACKUPS_SUBDIR

    return get_production_edit_plan_staging_dir(work_dir) / VOICE_FOLDER_MAPPING_MERGE_BACKUPS_SUBDIR


def get_voice_folder_mapping_merge_backup_run_dir(work_dir: Path, merge_run_id: str) -> Path:
    safe_run_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in merge_run_id) or "run"
    return get_voice_folder_mapping_merge_backups_dir(work_dir) / safe_run_id


def get_otio_export_readiness_report_path(work_dir: Path) -> Path:
    """Phase 10.8: rein lesende OTIO-Export-Readiness-Prüfung für bereits
    promotete/gemappte Folder — kein Export, keine .otio-Datei."""
    from otio_app.defaults import PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_REPORT_FILENAME

    return get_production_edit_plan_staging_dir(work_dir) / PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_REPORT_FILENAME


def get_supplement_dir(work_dir: Path) -> Path:
    """Verzeichnis für Supplement-Workflow-Dateien."""
    from otio_app.defaults import SUPPLEMENT_SUBDIR

    return work_dir / SUPPLEMENT_SUBDIR


def get_supplement_requests_path(work_dir: Path) -> Path:
    from otio_app.defaults import SUPPLEMENT_REQUESTS_FILENAME

    return get_supplement_dir(work_dir) / SUPPLEMENT_REQUESTS_FILENAME


def get_supplement_manifest_path(work_dir: Path) -> Path:
    from otio_app.defaults import SUPPLEMENT_MANIFEST_FILENAME

    return get_supplement_dir(work_dir) / SUPPLEMENT_MANIFEST_FILENAME


def get_supplement_errors_path(work_dir: Path) -> Path:
    from otio_app.defaults import SUPPLEMENT_ERRORS_FILENAME

    return get_supplement_dir(work_dir) / SUPPLEMENT_ERRORS_FILENAME


def get_pexels_debug_report_path(work_dir: Path) -> Path:
    from otio_app.defaults import PEXELS_DEBUG_REPORT_FILENAME

    return get_supplement_dir(work_dir) / PEXELS_DEBUG_REPORT_FILENAME


def get_folder_inventory_delta_path(work_dir: Path, folder_name: str) -> Path:
    from otio_app.defaults import INVENTORY_DELTA_SUFFIX

    return get_inventory_dir(work_dir) / f"{safe_folder_slug(folder_name)}{INVENTORY_DELTA_SUFFIX}"


def get_folder_supplemental_dir(project_root: Path, folder_name: str) -> Path:
    from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME

    return project_root / folder_name / SUPPLEMENTAL_FOLDER_NAME


def get_provider_supplemental_dir(project_root: Path, folder_name: str, provider: str) -> Path:
    return get_folder_supplemental_dir(project_root, folder_name) / f"_{provider}"


def get_exports_dir(work_dir: Path) -> Path:
    """Verzeichnis für OTIO-Exporte unter dem Arbeitsordner."""
    from otio_app.defaults import EXPORTS_SUBDIR

    return work_dir / EXPORTS_SUBDIR


def get_clean_media_output_dir(work_dir: Path) -> Path:
    """Transcodierte Medien: _otio/clean/<Ordner>/…"""
    from otio_app.defaults import CLEAN_MEDIA_OUTPUT_SUBDIR

    return work_dir / CLEAN_MEDIA_OUTPUT_SUBDIR


def get_clean_media_manifest_dir(work_dir: Path) -> Path:
    """Manifeste original → clean: _otio/clean_media/<Ordner>.json"""
    from otio_app.defaults import CLEAN_MEDIA_MANIFEST_SUBDIR

    return work_dir / CLEAN_MEDIA_MANIFEST_SUBDIR


def get_folder_clean_manifest_path(work_dir: Path, folder_name: str) -> Path:
    return get_clean_media_manifest_dir(work_dir) / f"{safe_folder_slug(folder_name)}.json"


def get_folder_clean_output_dir(work_dir: Path, folder_name: str) -> Path:
    return get_clean_media_output_dir(work_dir) / safe_folder_slug(folder_name)


def clean_output_path_for_media(
    work_dir: Path,
    folder_name: str,
    original_path: Path,
) -> Path:
    """Zielpfad für eine transcodierte MP4-Datei."""
    stem = safe_folder_slug(original_path.stem) or "media"
    return get_folder_clean_output_dir(work_dir, folder_name) / f"{stem}.mp4"


def aspect_filled_output_path_for_media(
    work_dir: Path,
    folder_name: str,
    original_path: Path,
    *,
    width: int,
    height: int,
) -> Path:
    """Eigener Dateiname für Zoom/Crop — Resolve verwechselt ihn nicht mit dem Original."""
    stem = safe_folder_slug(original_path.stem) or "media"
    return get_folder_clean_output_dir(work_dir, folder_name) / f"{stem}_{width}x{height}.mp4"


def export_processed_output_path_for_media(
    work_dir: Path,
    folder_name: str,
    original_path: Path,
    *,
    width: int,
    height: int,
    with_title: bool = False,
) -> Path:
    """Zielpfad für Export-Transcode (Zoom und/oder Titel-Overlay)."""
    stem = safe_folder_slug(original_path.stem) or "media"
    suffix = f"{width}x{height}"
    if with_title:
        suffix = f"{suffix}_title"
    return get_folder_clean_output_dir(work_dir, folder_name) / f"{stem}_{suffix}.mp4"


def get_otio_export_path(work_dir: Path, project_name: str) -> Path:
    """Standard-Pfad für den OTIO-Export eines Projekts."""
    return resolve_otio_export_path(work_dir, basename=project_name)


def default_otio_export_basename(
    *,
    project_name: str,
    folder_names: tuple[str, ...] | list[str],
    language: str | None = None,
) -> str:
    """Standard-Dateiname ohne Endung — ein Ort → Ordnername, sonst Projektname.

    Mit `language` wird der Sprachcode angehängt (z. B. `USA_DE`), damit
    Exporte verschiedener Sprachen sich nicht überschreiben.
    """
    folders = tuple(folder_names)
    if len(folders) == 1:
        base = safe_folder_slug(folders[0]) or safe_folder_slug(project_name) or "timeline"
    else:
        base = safe_folder_slug(project_name) or "timeline"
    if language and str(language).strip():
        lang = language_folder_name(str(language))
        if not base.upper().endswith(f"_{lang}"):
            return f"{base}_{lang}"
    return base


def resolve_otio_export_path(work_dir: Path, *, basename: str) -> Path:
    """Zielpfad für OTIO-Export aus einem vom Nutzer gewählten Basisnamen."""
    cleaned = basename.strip()
    if cleaned.lower().endswith(".otio"):
        cleaned = cleaned[:-5].strip()
    safe_name = safe_folder_slug(cleaned) or "timeline"
    return get_exports_dir(work_dir) / f"{safe_name}.otio"


def safe_path_is_dir(path: Path) -> bool:
    """Prüft ein Verzeichnis ohne Hänger bei nicht verfügbaren Cloud-Dateien."""
    try:
        return path.is_dir()
    except OSError:
        return False


def is_probably_icloud_path(path: Path) -> bool:
    """Erkennt typische iCloud-/CloudDocs-Pfade auf dem Mac."""
    resolved = str(path.expanduser().resolve())
    markers = (
        "Mobile Documents",
        "com~apple~CloudDocs",
        "iCloud Drive",
    )
    return any(marker in resolved for marker in markers)


def _names_match(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def detect_voice_over_folder(subdirectory_names: list[str]) -> str | None:
    """Sucht einen Voice-over-Ordner anhand üblicher Namen."""
    for name in subdirectory_names:
        folded = name.strip().casefold()
        if folded in VOICE_OVER_NAME_HINTS:
            return name
        if "voice" in folded and "over" in folded:
            return name
    return None


def resolve_voice_over_folder_name(
    subdirectory_names: list[str],
    preferred_name: str,
) -> str | None:
    """Findet den Voice-over-Ordner (case-insensitive) oder erkennt ihn automatisch."""
    for name in subdirectory_names:
        if _names_match(name, preferred_name):
            return name
    return detect_voice_over_folder(subdirectory_names)


@dataclass(frozen=True)
class PathDiagnostic:
    input_path: str
    resolved_path: str
    exists: bool
    is_directory: bool
    total_entries: int
    subdirectory_names: list[str]
    file_names: list[str]
    unreadable_entries: list[str]
    read_error: str | None
    icloud_path: bool
    used_icloud_fallback: bool

    @property
    def has_entries(self) -> bool:
        return self.total_entries > 0


def _list_names_subprocess(path: Path) -> tuple[list[str], list[str], str | None]:
    """Fallback für macOS/iCloud: Ordner mit /bin/ls -1p lesen (ohne shell=True)."""
    try:
        result = subprocess.run(
            ["/bin/ls", "-1p", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [], [], str(exc)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return [], [], message or f"ls exit {result.returncode}"

    subdirs: list[str] = []
    all_names: list[str] = []
    for line in result.stdout.splitlines():
        entry = line.strip()
        if not entry:
            continue
        if entry.endswith("/"):
            name = entry.rstrip("/")
            if not name.startswith("."):
                subdirs.append(name)
                all_names.append(name)
        elif not entry.startswith("."):
            all_names.append(entry)
    return subdirs, all_names, None


def diagnose_project_root(project_root: Path) -> PathDiagnostic:
    """Liefert eine ausführliche Diagnose für den Projektordner."""
    resolved = project_root.expanduser().resolve()
    exists = False
    is_directory = False
    try:
        exists = resolved.exists()
        is_directory = resolved.is_dir()
    except OSError as exc:
        return PathDiagnostic(
            input_path=str(project_root),
            resolved_path=str(resolved),
            exists=False,
            is_directory=False,
            total_entries=0,
            subdirectory_names=[],
            file_names=[],
            unreadable_entries=[],
            read_error=str(exc),
            icloud_path=is_probably_icloud_path(resolved),
            used_icloud_fallback=False,
        )

    raw_names: list[str] = []
    read_error: str | None = None
    for lister in (_list_names_iterdir, _list_names_os_listdir, _list_names_glob):
        raw_names, read_error = lister(resolved)
        if raw_names or read_error:
            break

    subdirectory_names: list[str] = []
    file_names: list[str] = []
    unreadable_entries: list[str] = []
    used_icloud_fallback = False

    for name in raw_names:
        if name.startswith("."):
            continue
        child = resolved / name
        try:
            if child.is_dir():
                subdirectory_names.append(name)
            elif child.is_file():
                file_names.append(name)
            else:
                unreadable_entries.append(name)
        except OSError:
            unreadable_entries.append(name)

    if not subdirectory_names and raw_names and is_probably_icloud_path(resolved):
        subdirectory_names = sorted(
            name for name in raw_names if not name.startswith(".")
        )
        used_icloud_fallback = bool(subdirectory_names)

    if not subdirectory_names and exists and is_directory:
        ls_subdirs, ls_all, ls_error = _list_names_subprocess(resolved)
        if ls_subdirs:
            subdirectory_names = sorted(ls_subdirs, key=str.lower)
            if not raw_names:
                raw_names = ls_all
            used_icloud_fallback = True
        elif ls_error and not read_error:
            read_error = ls_error

    return PathDiagnostic(
        input_path=str(project_root),
        resolved_path=str(resolved),
        exists=exists,
        is_directory=is_directory,
        total_entries=len(raw_names),
        subdirectory_names=sorted(subdirectory_names, key=str.lower),
        file_names=sorted(file_names, key=str.lower),
        unreadable_entries=sorted(unreadable_entries, key=str.lower),
        read_error=read_error,
        icloud_path=is_probably_icloud_path(resolved),
        used_icloud_fallback=used_icloud_fallback,
    )


def _list_names_iterdir(path: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in path.iterdir()], None
    except OSError as exc:
        return [], str(exc)


def _list_names_os_listdir(path: Path) -> tuple[list[str], str | None]:
    try:
        return list(os.listdir(path)), None
    except OSError as exc:
        return [], str(exc)


def _list_names_glob(path: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in path.glob("*")], None
    except OSError as exc:
        return [], str(exc)


def list_project_subdirectories(
    project_root: Path,
) -> tuple[list[str], str | None, PathDiagnostic, str | None]:
    """Liest alle Unterordner im Projektroot."""
    diagnostic = diagnose_project_root(project_root)

    if not diagnostic.exists:
        return (
            [],
            f"Projektordner existiert nicht: {diagnostic.resolved_path}",
            diagnostic,
            None,
        )
    if not diagnostic.is_directory:
        return (
            [],
            f"Pfad ist kein Verzeichnis: {diagnostic.resolved_path}",
            diagnostic,
            None,
        )
    if diagnostic.read_error:
        return (
            [],
            (
                f"Ordner konnte nicht gelesen werden ({diagnostic.resolved_path}): "
                f"{diagnostic.read_error}"
            ),
            diagnostic,
            None,
        )

    names = list(diagnostic.subdirectory_names)
    warning: str | None = None
    if diagnostic.used_icloud_fallback:
        warning = (
            "iCloud-Ordner erkannt: Unterordner wurden über Dateinamen erkannt. "
            "Bitte im Finder lokal laden, falls Inhalte fehlen."
        )

    if not names:
        hint = (
            f"Keine Unterordner in `{diagnostic.resolved_path}` gefunden "
            f"({diagnostic.total_entries} Einträge insgesamt)."
        )
        if diagnostic.icloud_path:
            hint += (
                " Dies ist ein iCloud-Pfad — öffne den Ordner im Finder und lade "
                "die Inhalte lokal herunter (Wolke-Symbol verschwindet)."
            )
        elif diagnostic.file_names:
            hint += (
                f" Gefundene Dateien im Root: {', '.join(diagnostic.file_names[:5])}."
            )
        return [], hint, diagnostic, warning

    return names, None, diagnostic, warning


@dataclass(frozen=True)
class ProjectStructureScan:
    project_root: Path
    work_dir: Path
    voice_over_subdir: str
    language: str
    all_subdirectory_names: list[str] = field(default_factory=list)
    asset_subdir_names: list[str] = field(default_factory=list)
    system_folder_names: list[str] = field(default_factory=list)
    voice_over_folder_name: str | None = None
    voice_over_dir: Path | None = None
    voice_over_language_dir: Path | None = None
    voice_over_language_exists: bool = False
    error: str | None = None
    warning: str | None = None
    diagnostic: PathDiagnostic | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def classify_subdirectories(
    subdirectory_names: list[str],
    voice_over_subdir: str,
    work_dir: Path,
    project_root: Path,
    language: str,
    *,
    warning: str | None = None,
    diagnostic: PathDiagnostic | None = None,
) -> ProjectStructureScan:
    """Ordnet Unterordner in Assets, Voice-over und System ein."""
    voice_over_folder_name = resolve_voice_over_folder_name(
        subdirectory_names,
        voice_over_subdir,
    )

    reserved_names: set[str] = {DEFAULT_WORK_SUBDIR.casefold()}
    if work_dir.parent == project_root:
        reserved_names.add(work_dir.name.casefold())

    asset_names: list[str] = []
    system_names: list[str] = []

    for name in subdirectory_names:
        folded = name.casefold()
        if voice_over_folder_name and _names_match(name, voice_over_folder_name):
            continue
        if folded in reserved_names:
            system_names.append(name)
            continue
        asset_names.append(name)

    voice_over_dir = (
        project_root / voice_over_folder_name if voice_over_folder_name else None
    )
    voice_over_language_dir = (
        get_voice_over_dir(project_root, voice_over_folder_name, language)
        if voice_over_folder_name
        else None
    )
    voice_over_language_exists = (
        safe_path_is_dir(voice_over_language_dir)
        if voice_over_language_dir is not None
        else False
    )

    return ProjectStructureScan(
        project_root=project_root,
        work_dir=work_dir,
        voice_over_subdir=voice_over_folder_name or voice_over_subdir.strip(),
        language=language,
        all_subdirectory_names=list(subdirectory_names),
        asset_subdir_names=sorted(asset_names, key=str.lower),
        system_folder_names=sorted(system_names, key=str.lower),
        voice_over_folder_name=voice_over_folder_name,
        voice_over_dir=voice_over_dir,
        voice_over_language_dir=voice_over_language_dir,
        voice_over_language_exists=voice_over_language_exists,
        warning=warning,
        diagnostic=diagnostic,
    )


def scan_project_structure(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
    language: str,
) -> ProjectStructureScan:
    """Scannt den Projektordner und klassifiziert alle Unterordner."""
    subdirectory_names, error, diagnostic, warning = list_project_subdirectories(
        project_root
    )
    if error:
        return ProjectStructureScan(
            project_root=project_root.expanduser().resolve(),
            work_dir=work_dir,
            voice_over_subdir=voice_over_subdir.strip(),
            language=language,
            error=error,
            diagnostic=diagnostic,
        )
    return classify_subdirectories(
        subdirectory_names,
        voice_over_subdir,
        work_dir,
        project_root.expanduser().resolve(),
        language,
        warning=warning,
        diagnostic=diagnostic,
    )


def classify_subdirectories_no_voiceover(
    subdirectory_names: list[str],
    work_dir: Path,
    project_root: Path,
    *,
    warning: str | None = None,
    diagnostic: PathDiagnostic | None = None,
) -> ProjectStructureScan:
    """Wie classify_subdirectories(), aber ohne Voice-over-Erkennung/-Ausschluss.

    Für "Projekt ohne Voice-Over": Es gibt keinen Voice-over-Ordner, der aus der
    Asset-Auswahl ausgenommen werden müsste — alle Unterordner (außer dem
    Arbeitsordner _otio) gelten als Asset-Ordner.
    """
    reserved_names: set[str] = {DEFAULT_WORK_SUBDIR.casefold()}
    if work_dir.parent == project_root:
        reserved_names.add(work_dir.name.casefold())

    asset_names: list[str] = []
    system_names: list[str] = []
    for name in subdirectory_names:
        if name.casefold() in reserved_names:
            system_names.append(name)
        else:
            asset_names.append(name)

    return ProjectStructureScan(
        project_root=project_root,
        work_dir=work_dir,
        voice_over_subdir="",
        language="",
        all_subdirectory_names=list(subdirectory_names),
        asset_subdir_names=sorted(asset_names, key=str.lower),
        system_folder_names=sorted(system_names, key=str.lower),
        voice_over_folder_name=None,
        voice_over_dir=None,
        voice_over_language_dir=None,
        voice_over_language_exists=False,
        warning=warning,
        diagnostic=diagnostic,
    )


def scan_project_structure_no_voiceover(
    project_root: Path,
    work_dir: Path,
) -> ProjectStructureScan:
    """Scannt den Projektordner für "Projekt ohne Voice-Over" (keine Voice-over-Klassifikation)."""
    subdirectory_names, error, diagnostic, warning = list_project_subdirectories(
        project_root
    )
    if error:
        return ProjectStructureScan(
            project_root=project_root.expanduser().resolve(),
            work_dir=work_dir,
            voice_over_subdir="",
            language="",
            error=error,
            diagnostic=diagnostic,
        )
    return classify_subdirectories_no_voiceover(
        subdirectory_names,
        work_dir,
        project_root.expanduser().resolve(),
        warning=warning,
        diagnostic=diagnostic,
    )


def discover_asset_subdir_names(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
    language: str = "de",
) -> list[str]:
    """Listet Asset-Unterordner-Namen."""
    scan = scan_project_structure(project_root, work_dir, voice_over_subdir, language)
    return scan.asset_subdir_names


def discover_asset_subdirs(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
    language: str = "de",
) -> list[Path]:
    """Listet Asset-Unterordner im Projektroot."""
    return [
        project_root / name
        for name in discover_asset_subdir_names(
            project_root,
            work_dir,
            voice_over_subdir,
            language,
        )
    ]
