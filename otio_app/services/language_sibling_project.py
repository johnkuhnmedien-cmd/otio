"""Geschwisterprojekt in einer anderen Sprache am gleichen Medienordner."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Sequence

from otio_app.defaults import BRIEF_LANGUAGE_CHOICES
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import (
    create_project,
    find_project_by_root_and_language,
    find_projects_by_root,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)

__all__ = [
    "LanguageFamilyStatus",
    "LanguageSiblingError",
    "SavedProjectGroup",
    "auto_run_pipeline_complete",
    "clone_project_for_language",
    "family_display_name",
    "family_language_statuses",
    "group_saved_projects",
    "missing_sibling_languages",
    "open_languages_for_auto_run",
    "pick_family_representative",
    "resolve_sibling_project",
    "selected_languages_in_order",
    "sibling_project_name",
]


class LanguageSiblingError(ValueError):
    """Geschwisterprojekt kann nicht angelegt werden."""


@dataclass(frozen=True)
class LanguageFamilyStatus:
    language: str
    exists: bool
    project_id: str | None
    project_name: str | None
    done_count: int
    step_total: int
    last_done_label: str
    next_label: str
    funnel_done: bool
    youtube_done: bool


@dataclass(frozen=True)
class SavedProjectGroup:
    display_name: str
    project_mode: ProjectMode
    projects: tuple[Project, ...]
    representative: Project
    grouped: bool


def family_display_name(name: str, language: str) -> str:
    """IT_Test Automatic + IT → Test Automatic."""
    lang = normalize_brief_language(language)
    text = (name or "").strip() or lang
    if text.upper() == lang:
        return text
    pattern = re.compile(rf"^{re.escape(lang)}([_\s\-])", re.IGNORECASE)
    stripped, count = pattern.subn("", text, count=1)
    if count:
        return stripped.strip() or text
    return text


def pick_family_representative(projects: Sequence[Project]) -> Project:
    """Ältestes Geschwisterprojekt — typisch die zuerst angelegte Sprache."""
    if not projects:
        raise ValueError("Keine Projekte in der Familie.")
    return sorted(
        projects,
        key=lambda item: (_created_sort_key(item), str(item.id)),
    )[0]


def _created_sort_key(project: Project) -> str:
    created = getattr(project, "created_at", None)
    if isinstance(created, datetime):
        return created.isoformat()
    return str(created or "")


def family_title(projects: Sequence[Project]) -> str:
    names = [
        family_display_name(item.name, item.language) for item in projects if item.name
    ]
    if not names:
        return pick_family_representative(projects).name
    return Counter(names).most_common(1)[0][0]


def group_saved_projects(projects: Sequence[Project]) -> list[SavedProjectGroup]:
    """Enhanced-Geschwister eines Ordners → eine Karte; andere Modi bleiben einzeln."""
    enhanced_members: dict[tuple[str, str], list[Project]] = {}
    for item in projects:
        if item.is_without_voiceover_enhanced:
            key = (item.project_root, item.project_mode.value)
            enhanced_members.setdefault(key, []).append(item)

    groups: list[SavedProjectGroup] = []
    seen: set[tuple[str, str]] = set()
    for item in projects:
        if item.is_without_voiceover_enhanced:
            key = (item.project_root, item.project_mode.value)
            if key in seen:
                continue
            seen.add(key)
            members = tuple(enhanced_members[key])
            groups.append(
                SavedProjectGroup(
                    display_name=family_title(members),
                    project_mode=item.project_mode,
                    projects=members,
                    representative=pick_family_representative(members),
                    grouped=True,
                )
            )
            continue
        groups.append(
            SavedProjectGroup(
                display_name=item.name,
                project_mode=item.project_mode,
                projects=(item,),
                representative=item,
                grouped=False,
            )
        )
    return groups


def family_language_statuses(
    projects: Sequence[Project],
) -> list[LanguageFamilyStatus]:
    """Eine Zeile je Katalog-Sprache: angelegt?, Funnel/YouTube, nächster Schritt."""
    by_lang: dict[str, Project] = {}
    for item in projects:
        by_lang[normalize_brief_language(item.language)] = item
    langs = list(BRIEF_LANGUAGE_CHOICES)
    rows_by_lang: dict[str, LanguageFamilyStatus] = {}
    existing = [lang for lang in langs if lang in by_lang]
    for lang in langs:
        if lang not in by_lang:
            rows_by_lang[lang] = language_family_status(lang, None)
    if len(existing) <= 1:
        for lang in existing:
            rows_by_lang[lang] = language_family_status(lang, by_lang[lang])
    elif existing:
        workers = min(8, len(existing))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(language_family_status, lang, by_lang[lang]): lang
                for lang in existing
            }
            for future in as_completed(futures):
                lang = futures[future]
                rows_by_lang[lang] = future.result()
    return [rows_by_lang[lang] for lang in langs]


def language_family_status(
    language: str,
    project: Project | None,
) -> LanguageFamilyStatus:
    lang = normalize_brief_language(language)
    if project is None:
        from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
            AUTO_RUN_STEPS,
        )

        return LanguageFamilyStatus(
            language=lang,
            exists=False,
            project_id=None,
            project_name=None,
            done_count=0,
            step_total=len(AUTO_RUN_STEPS),
            last_done_label="—",
            next_label="anlegen",
            funnel_done=False,
            youtube_done=False,
        )
    from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
        summarize_auto_run_stage,
    )

    summary = summarize_auto_run_stage(project)
    return LanguageFamilyStatus(
        language=lang,
        exists=True,
        project_id=project.id,
        project_name=project.name,
        done_count=summary.done_count,
        step_total=summary.step_total,
        last_done_label=summary.last_done_label,
        next_label=summary.next_label,
        funnel_done=summary.funnel_done,
        youtube_done=summary.youtube_done,
    )


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


def auto_run_pipeline_complete(
    project: Project,
    *,
    stop_after: str = "youtube",
) -> bool:
    """True wenn Brief→Funnel bzw. Brief→YouTube für skip-done schon erledigt wären."""
    try:
        from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
            pipeline_complete_through,
        )

        return bool(pipeline_complete_through(project, stop_after))
    except TypeError:
        return False
    except Exception:  # noqa: BLE001 — unfertiges Projekt zählt als offen
        return False


def _is_pipeline_complete(project: Project, stop_after: str = "youtube") -> bool:
    try:
        return bool(auto_run_pipeline_complete(project, stop_after=stop_after))
    except TypeError:
        return bool(auto_run_pipeline_complete(project))


def open_languages_for_auto_run(
    project: Project,
    siblings: list[Project] | None = None,
    *,
    stop_after: str = "youtube",
    include_current: bool = False,
) -> list[str]:
    """Sprachen ohne fertigen Auto-Lauf (fehlend oder unfertig) für das Ziel."""
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
        if lang == current and not include_current:
            continue
        existing = by_lang.get(lang)
        if existing is None or not _is_pipeline_complete(existing, stop_after):
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
    current = normalize_brief_language(source.language)
    if target == current:
        existing = find_project_by_root_and_language(
            source.project_root,
            target,
            db_path=db_path,
            project_mode=source.project_mode,
        )
        return existing or source
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
