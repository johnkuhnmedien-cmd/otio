"""Artefaktpfade für without_voiceover_enhanced unter ``_otio_enhanced``."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, VOICEOVER_GENERATION_SUBDIR
from otio_app.models import Project

SCRIPT_SUBDIR = "script"
AUDIO_SUBDIR = "audio"
CUT_SUBDIR = "cut"
COVERAGE_SUBDIR = "coverage"
STOCK_SUBDIR = "stock"
CONFIG_SUBDIR = "config"
EXPORTS_SUBDIR = "exports"
STOCK_PROVIDERS_CONFIG_FILENAME = "stock_providers.json"
CUT_PLAN_OPTIONS_FILENAME = "cut_plan_options.json"

SCRIPT_LOCKED_FILENAME = "script_locked.json"
SCRIPT_DRAFT_FILENAME = "script_draft.json"
SEGMENT_TIMINGS_FILENAME = "segment_timings.json"
PAUSE_DIRECTIVES_FILENAME = "pause_directives.json"
NARRATION_TIMELINE_FILENAME = "narration_timeline.json"
ROUGH_CUT_PLAN_FILENAME = "rough_cut_plan.json"
COVERAGE_GAPS_FILENAME = "coverage_gaps.json"
STOCK_SEARCH_RESULTS_FILENAME = "search_results.json"
ACCEPTED_SUPPLEMENTS_FILENAME = "accepted_supplements.json"
SUPPLEMENT_RESOLVE_REPORT_FILENAME = "supplement_resolve_report.json"
SUPPLEMENT_FUNNEL_REPORT_FILENAME = "supplement_funnel_report.json"
STOCK_DOWNLOADS_SUBDIR = "downloads"
FINAL_CUT_PLAN_FILENAME = "final_cut_plan.json"
RESOLVED_TIMELINE_FILENAME = "resolved_timeline.json"
REPAIR_LOG_FILENAME = "timeline_repair_log.json"


def assert_enhanced_work_root(project: Project) -> Path:
    """Stellt sicher, dass Artefakte nur unter ``_otio_enhanced`` landen."""
    work = project.work_dir_path
    if work.name != DEFAULT_ENHANCED_WORK_SUBDIR:
        raise ValueError(
            f"Enhanced-Modus erwartet Arbeitsordner "
            f"'{DEFAULT_ENHANCED_WORK_SUBDIR}', gefunden: '{work.name}'"
        )
    # Defense-in-depth: niemals Discovery- oder Classic-Roots anfassen.
    forbidden = {"_otio_v2", "_otio"}
    for part in work.parts:
        if part in forbidden and part != DEFAULT_ENHANCED_WORK_SUBDIR:
            # ``_otio`` as parent of enhanced is fine only if name is enhanced.
            pass
    if "_otio_v2" in work.parts:
        raise ValueError("Enhanced-Modus darf nicht unter _otio_v2 arbeiten.")
    return work


def enhanced_generation_root(project: Project) -> Path:
    """``_otio_enhanced/{LANG}/voiceover_generation``."""
    assert_enhanced_work_root(project)
    return project.language_work_dir_path / VOICEOVER_GENERATION_SUBDIR


def script_dir(project: Project) -> Path:
    return enhanced_generation_root(project) / SCRIPT_SUBDIR


def audio_dir(project: Project) -> Path:
    return enhanced_generation_root(project) / AUDIO_SUBDIR


def cut_dir(project: Project) -> Path:
    return enhanced_generation_root(project) / CUT_SUBDIR


def coverage_dir(project: Project) -> Path:
    return enhanced_generation_root(project) / COVERAGE_SUBDIR


def stock_dir(project: Project) -> Path:
    return enhanced_generation_root(project) / STOCK_SUBDIR


def config_dir(project: Project) -> Path:
    """``_otio_enhanced/config`` — projektweite Enhanced-Konfiguration."""
    assert_enhanced_work_root(project)
    return project.work_dir_path / CONFIG_SUBDIR


def stock_providers_config_path(project: Project) -> Path:
    return config_dir(project) / STOCK_PROVIDERS_CONFIG_FILENAME


def cut_plan_options_path(project: Project) -> Path:
    return config_dir(project) / CUT_PLAN_OPTIONS_FILENAME


def exports_dir(project: Project) -> Path:
    return project.language_work_dir_path / EXPORTS_SUBDIR


def script_locked_path(project: Project) -> Path:
    return script_dir(project) / SCRIPT_LOCKED_FILENAME


def script_draft_path(project: Project) -> Path:
    return script_dir(project) / SCRIPT_DRAFT_FILENAME


def segment_timings_path(project: Project) -> Path:
    return audio_dir(project) / SEGMENT_TIMINGS_FILENAME


def pause_directives_path(project: Project) -> Path:
    return cut_dir(project) / PAUSE_DIRECTIVES_FILENAME


def narration_timeline_path(project: Project) -> Path:
    return cut_dir(project) / NARRATION_TIMELINE_FILENAME


def rough_cut_plan_path(project: Project) -> Path:
    return cut_dir(project) / ROUGH_CUT_PLAN_FILENAME


def coverage_gaps_path(project: Project) -> Path:
    return coverage_dir(project) / COVERAGE_GAPS_FILENAME


def stock_search_results_path(project: Project) -> Path:
    return stock_dir(project) / STOCK_SEARCH_RESULTS_FILENAME


def accepted_supplements_path(project: Project) -> Path:
    return stock_dir(project) / ACCEPTED_SUPPLEMENTS_FILENAME


def supplement_resolve_report_path(project: Project) -> Path:
    return stock_dir(project) / SUPPLEMENT_RESOLVE_REPORT_FILENAME


def supplement_funnel_report_path(project: Project) -> Path:
    return stock_dir(project) / SUPPLEMENT_FUNNEL_REPORT_FILENAME


def stock_downloads_dir(project: Project) -> Path:
    return stock_dir(project) / STOCK_DOWNLOADS_SUBDIR


def stock_candidate_download_dir(
    project: Project,
    *,
    gap_id: str,
    candidate_id: str,
) -> Path:
    from otio_app.project_layout import safe_folder_slug

    return (
        stock_downloads_dir(project)
        / safe_folder_slug(gap_id or "gap")
        / safe_folder_slug(candidate_id or "candidate")
    )


def final_cut_plan_path(project: Project) -> Path:
    return cut_dir(project) / FINAL_CUT_PLAN_FILENAME


def resolved_timeline_path(project: Project) -> Path:
    return cut_dir(project) / RESOLVED_TIMELINE_FILENAME


def repair_log_path(project: Project) -> Path:
    return cut_dir(project) / REPAIR_LOG_FILENAME
