"""Geschwisterprojekt in einer anderen Sprache am gleichen Medienordner."""

from __future__ import annotations

from pathlib import Path
import re

from otio_app.defaults import BRIEF_LANGUAGE_CHOICES
from otio_app.models import Project, ProjectCreate
from otio_app.project_repository import (
    create_project,
    find_project_by_root_and_language,
    find_projects_by_root,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)

__all__ = [
    "LanguageSiblingError",
    "auto_run_pipeline_complete",
    "clone_project_for_language",
    "missing_sibling_languages",
    "open_languages_for_auto_run",
    "resolve_sibling_project",
    "selected_languages_in_order",
    "sibling_project_name",
]


class LanguageSiblingError(ValueError):
    """Geschwisterprojekt kann nicht angelegt werden."""


def sibling_project_name(
    source_name: str,
    source_language: str,
    target_language: str,
) -> str:
    """DE_Test Automatic + PT → PT_Test Automatic."""
    source_key = normalize_brief_language(source_language)
    target_key = normalize_brief_language(target_language)
    name = (source_name or "").strip() or target_key
    if name.upper() == source_key:
        return target_key
    pattern = re.compile(rf"^{re.escape(source_key)}([_\s\-])", re.IGNORECASE)
    replaced, count = pattern.subn(target_key + r"\1", name, count=1)
    if count:
        return replaced
    return f"{target_key}_{name}"


def missing_sibling_languages(
    project: Project,
    siblings: list[Project] | None = None,
) -> list[str]:
    """Sprachen aus BRIEF_LANGUAGE_CHOICES, für die noch kein gleichartiger Eintrag da ist."""
    current = normalize_brief_language(project.language)
    occupied: set[str] = {current}
    rows = siblings
    if rows is None:
        rows = find_projects_by_root(project.project_root)
    for item in rows:
        if item.project_mode != project.project_mode:
            continue
        occupied.add(normalize_brief_language(item.language))
    return [lang for lang in BRIEF_LANGUAGE_CHOICES if lang not in occupied]


def auto_run_pipeline_complete(project: Project) -> bool:
    """True wenn Brief→OTIO für skip-done schon erledigt wären."""
    try:
        from otio_app.services.voiceover_generation.intro_hook_service import (
            load_confirmed_intro_hook,
        )
        from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
            list_chapter_audio_statuses,
        )
        from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
            list_chapters_needing_python_timing,
            list_chapters_needing_unified_cut,
        )
        from otio_app.services.without_voiceover_enhanced.elevenlabs_music_service import (
            list_music_generation_targets,
            music_ui_status_chapter,
            music_ui_status_intro,
        )
        from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
            intro_resolved_timeline_path,
            intro_unified_cut_plan_path,
        )
        from otio_app.services.without_voiceover_enhanced.io_utils import load_model
        from otio_app.services.without_voiceover_enhanced.models import (
            UnifiedCutPlanDocument,
        )
        from otio_app.services.without_voiceover_enhanced.paths import exports_dir
        from otio_app.services.without_voiceover_enhanced.script_lock_service import (
            load_locked_script,
        )
        from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
            list_open_funnel_gap_ids,
        )

        if load_locked_script(project) is None:
            return False
        if load_confirmed_intro_hook(project) is None:
            return False
        intro = load_model(
            intro_unified_cut_plan_path(project), UnifiedCutPlanDocument
        )
        if intro is None or not list(getattr(intro, "slots", None) or []):
            return False
        if list_chapters_needing_unified_cut(project):
            return False
        statuses = list_chapter_audio_statuses(project)
        if not statuses:
            return False
        if any(row.is_open for row in statuses):
            return False
        if list_open_funnel_gap_ids(project):
            return False
        if not intro_resolved_timeline_path(project).is_file():
            return False
        if list_chapters_needing_python_timing(project):
            return False
        for kind, folder in list_music_generation_targets(project):
            if kind == "intro":
                music_status = music_ui_status_intro(project)
            else:
                music_status = music_ui_status_chapter(project, folder)
            if str(music_status.get("status") or "") != "completed":
                return False
        otio_path = exports_dir(project) / f"{project.name}_enhanced.otio"
        return otio_path.is_file()
    except Exception:  # noqa: BLE001 — unfertiges Projekt zählt als offen
        return False


def open_languages_for_auto_run(
    project: Project,
    siblings: list[Project] | None = None,
) -> list[str]:
    """Andere Sprachen ohne fertigen Auto-Lauf (fehlend oder unfertig)."""
    current = normalize_brief_language(project.language)
    rows = siblings
    if rows is None:
        rows = find_projects_by_root(project.project_root)
    by_lang: dict[str, Project] = {}
    for item in rows:
        if item.project_mode != project.project_mode:
            continue
        key = normalize_brief_language(item.language)
        by_lang[key] = item
    open_langs: list[str] = []
    for lang in BRIEF_LANGUAGE_CHOICES:
        if lang == current:
            continue
        existing = by_lang.get(lang)
        if existing is None or not auto_run_pipeline_complete(existing):
            open_langs.append(lang)
    return open_langs


def selected_languages_in_order(
    open_langs: list[str],
    selected: list[str] | None,
) -> list[str]:
    """Gewählte Sprachen in der offenen Reihenfolge — nichts extra, nichts umsortiert."""
    wanted = {
        normalize_brief_language(item)
        for item in (selected or [])
        if str(item).strip()
    }
    return [lang for lang in open_langs if lang in wanted]


def resolve_sibling_project(
    source: Project,
    language: str,
    *,
    db_path: Path | None = None,
) -> Project:
    """Vorhandenes Geschwisterprojekt oder neu anlegen — ohne Auto-Lauf."""
    target = normalize_brief_language(language)
    existing = find_project_by_root_and_language(
        source.project_root,
        target,
        db_path=db_path,
        project_mode=source.project_mode,
    )
    if existing is not None:
        return existing
    return clone_project_for_language(
        source, target, db_path=db_path, start_auto_run=False
    )


def clone_project_for_language(
    source: Project,
    language: str,
    *,
    db_path: Path | None = None,
    start_auto_run: bool = False,
) -> Project:
    """Neues DB-Projekt: gleicher Ordner/Modus/Assets, andere Sprache.

    Clean Media und Analysen bleiben unter dem gemeinsamen ``work_dir``.
    Editorial (Brief, Skripte, Cuts) liegt neu unter ``work_dir/{LANG}/``.
    """
    target = normalize_brief_language(language)
    current = normalize_brief_language(source.language)
    if target == current:
        raise LanguageSiblingError(
            f"Projekt ist bereits Sprache {current}."
        )
    if start_auto_run:
        if not source.is_without_voiceover_enhanced:
            raise LanguageSiblingError(
                "Auto-Lauf gibt es nur für Enhanced-MVP-Projekte."
            )
        if not str(source.video_place or "").strip():
            raise LanguageSiblingError(
                "Kein Land/Region am Projekt — zuerst unter Gespeicherte Projekte eintragen."
            )
    existing = find_project_by_root_and_language(
        source.project_root,
        target,
        db_path=db_path,
        project_mode=source.project_mode,
    )
    if existing is not None:
        raise LanguageSiblingError(
            f"{target} gibt es am gleichen Ordner schon: {existing.name}."
        )
    data = ProjectCreate(
        name=sibling_project_name(source.name, source.language, target),
        project_root=source.project_root,
        work_dir=source.work_dir,
        project_mode=source.project_mode,
        voice_over_subdir=source.voice_over_subdir,
        language=target.lower(),
        video_place=source.video_place,
        frames_per_shot=source.frames_per_shot,
        fps=source.fps,
        width=source.width,
        height=source.height,
        aspect_ratio=source.aspect_ratio,
        target_platform=source.target_platform,
        notes=source.notes,
    )
    cloned = create_project(
        data,
        db_path=db_path,
        asset_subdir_names=list(source.asset_subdir_names),
        selected_asset_subdirs=list(source.selected_asset_subdirs),
    )
    if start_auto_run:
        _start_auto_run(cloned)
    return cloned


def _start_auto_run(project: Project) -> None:
    from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
        get_enhanced_auto_run_job_manager,
    )

    get_enhanced_auto_run_job_manager().start(project)
