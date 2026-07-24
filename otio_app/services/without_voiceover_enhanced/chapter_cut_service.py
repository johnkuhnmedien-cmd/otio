"""Per-Kapitel Unified Cut: Plan/Timing/OTIO unter ``cut/chapters/{slug}/``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    FolderUnifiedCutResult,
    _used_in_ledger_text,
    generate_unified_cut_for_folder,
    list_cut_plan_chapter_names,
    merge_and_persist_unified_cuts,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    intro_resolved_timeline_path,
    intro_unified_cut_plan_path,
    resolve_intro_timeline,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    is_intro_folder_name,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    assert_enhanced_work_root,
    chapter_resolved_timeline_path,
    chapter_unified_cut_plan_path,
    resolved_timeline_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)


class ChapterCutError(RuntimeError):
    pass


@dataclass
class ChapterCutStatus:
    folder_name: str
    folder_slug: str
    has_plan: bool = False
    plan_slots: int = 0
    has_resolved: bool = False
    resolved_shots: int = 0
    matches: bool = False
    plan_path: Path | None = None
    resolved_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)


@dataclass
class ChapterCutGenerateResult:
    folder_name: str
    plan: UnifiedCutPlanDocument
    slot_count: int
    gap_count: int


def list_body_chapter_names(project: Project) -> list[str]:
    """Dramaturgie-Reihenfolge der Körper-Kapitel (ohne Intro)."""
    return [
        name
        for name in list_cut_plan_chapter_names(project)
        if name and not is_intro_folder_name(name)
    ]


def chapter_folder_slug(folder_name: str) -> str:
    return safe_folder_slug((folder_name or "").strip() or "chapter")


def load_chapter_unified_plan(
    project: Project, folder_name: str
) -> UnifiedCutPlanDocument | None:
    return load_model(
        chapter_unified_cut_plan_path(project, folder_name),
        UnifiedCutPlanDocument,
    )


def load_chapter_resolved(
    project: Project, folder_name: str
) -> ResolvedTimelineDocument | None:
    return load_model(
        chapter_resolved_timeline_path(project, folder_name),
        ResolvedTimelineDocument,
    )


def chapter_resolved_matches_plan(
    plan: UnifiedCutPlanDocument | None,
    resolved: ResolvedTimelineDocument | None,
) -> bool:
    if plan is None or resolved is None:
        return False
    return len(resolved.shots) == len(plan.slots)


def invalidate_chapter_resolved_timeline(project: Project, folder_name: str) -> bool:
    path = chapter_resolved_timeline_path(project, folder_name)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def get_chapter_cut_status(project: Project, folder_name: str) -> ChapterCutStatus:
    slug = chapter_folder_slug(folder_name)
    plan_path = chapter_unified_cut_plan_path(project, folder_name)
    resolved_path = chapter_resolved_timeline_path(project, folder_name)
    plan = load_chapter_unified_plan(project, folder_name)
    resolved = load_chapter_resolved(project, folder_name)
    errors: list[str] = []
    repairs: list[str] = []
    if resolved is not None:
        errors = list(resolved.errors or [])
        repairs = list(resolved.repairs or [])
    return ChapterCutStatus(
        folder_name=folder_name,
        folder_slug=slug,
        has_plan=plan is not None and bool(plan.slots),
        plan_slots=len(plan.slots) if plan is not None else 0,
        has_resolved=resolved is not None
        and bool(resolved.shots or resolved.audio_segments),
        resolved_shots=len(resolved.shots) if resolved is not None else 0,
        matches=chapter_resolved_matches_plan(plan, resolved),
        plan_path=plan_path,
        resolved_path=resolved_path,
        errors=errors,
        repairs=repairs,
    )


def list_chapter_cut_statuses(project: Project) -> list[ChapterCutStatus]:
    return [get_chapter_cut_status(project, name) for name in list_body_chapter_names(project)]


def load_prior_chapter_plans(
    project: Project, folder_name: str
) -> list[UnifiedCutPlanDocument]:
    """Pläne aller Körper-Kapitel vor ``folder_name`` (Dramaturgie-Reihenfolge)."""
    prior: list[UnifiedCutPlanDocument] = []
    for name in list_body_chapter_names(project):
        if name == folder_name:
            break
        plan = load_chapter_unified_plan(project, name)
        if plan is not None and plan.slots:
            prior.append(plan)
    return prior


def persist_chapter_unified_plan(
    project: Project,
    folder_name: str,
    plan: UnifiedCutPlanDocument,
    *,
    refresh_merged: bool = True,
) -> UnifiedCutPlanDocument:
    """Schreibt Kapitel-Plan und optional den globalen Merge neu."""
    assert_enhanced_work_root(project)
    write_json(chapter_unified_cut_plan_path(project, folder_name), plan)
    invalidate_chapter_resolved_timeline(project, folder_name)
    if refresh_merged:
        refresh_merged_unified_cut_plan(project)
    return plan


def refresh_merged_unified_cut_plan(project: Project) -> UnifiedCutPlanDocument | None:
    """Merged Intro + alle Kapitel-Pläne → ``unified_cut_plan.json``."""
    results: list[FolderUnifiedCutResult] = []
    for name in list_body_chapter_names(project):
        plan = load_chapter_unified_plan(project, name)
        if plan is None or not plan.slots:
            continue
        results.append(
            FolderUnifiedCutResult(
                folder_name=name,
                status="PASS",
                plan=plan,
                slot_count=len(plan.slots),
                pause_count=len(plan.pause_directives),
                gap_count=sum(
                    1 for s in plan.slots if str(s.asset_fit) in {"weak", "none"}
                ),
            )
        )
    if not results:
        return load_model(intro_unified_cut_plan_path(project), UnifiedCutPlanDocument)

    # merge_and_persist hängt Intro vorne an; Kapitel-Dateien nicht erneut
    # schreiben/invalidieren (Resolved der anderen Kapitel bleibt).
    return merge_and_persist_unified_cuts(
        project, results, write_chapter_artifacts=False
    )


def generate_chapter_unified_cut(
    project: Project,
    folder_name: str,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    refresh_merged: bool = True,
) -> ChapterCutGenerateResult:
    """Ein Körper-Kapitel: Unified-LLM → ``cut/chapters/{slug}/unified_cut_plan.json``."""
    assert_enhanced_work_root(project)
    if not folder_name or is_intro_folder_name(folder_name):
        raise ChapterCutError(
            "Intro läuft über die Intro-Buttons — kein Kapitel-Cut für Intro."
        )
    prior = load_prior_chapter_plans(project, folder_name)
    ledger = _used_in_ledger_text(prior)
    result = generate_unified_cut_for_folder(
        project,
        folder_name,
        provider=provider,
        model=model,
        llm_callable=llm_callable,
        used_in_ledger_text=ledger,
    )
    if result.status != "PASS" or result.plan is None:
        raise ChapterCutError(
            result.error or f"Unified Cut fehlgeschlagen für „{folder_name}“."
        )
    plan = persist_chapter_unified_plan(
        project,
        folder_name,
        result.plan,
        refresh_merged=refresh_merged,
    )
    gap_count = sum(1 for s in plan.slots if str(s.asset_fit) in {"weak", "none"})
    return ChapterCutGenerateResult(
        folder_name=folder_name,
        plan=plan,
        slot_count=len(plan.slots),
        gap_count=gap_count,
    )


def generate_all_chapter_unified_cuts(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[ChapterCutGenerateResult]:
    """Alle Körper-Kapitel sequenziell; schreibt pro-Kapitel JSON + globalen Merge."""
    names = list_body_chapter_names(project)
    if not names:
        raise ChapterCutError("Keine Körper-Kapitel für den Unified Cut.")
    out: list[ChapterCutGenerateResult] = []
    total = len(names)
    for index, name in enumerate(names, start=1):
        if progress_callback is not None:
            progress_callback(name, index, total)
        # Merge erst am Ende — sonst N× volle Merge-Kosten.
        out.append(
            generate_chapter_unified_cut(
                project,
                name,
                provider=provider,
                model=model,
                llm_callable=llm_callable,
                refresh_merged=False,
            )
        )
    refresh_merged_unified_cut_plan(project)
    return out


def resolve_chapter_timeline(
    project: Project,
    folder_name: str,
) -> ResolvedTimelineDocument:
    """Python-Timing nur für ein Körper-Kapitel → chapter resolved JSON."""
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        UnifiedTimelineError,
        resolve_unified_timeline,
    )

    assert_enhanced_work_root(project)
    if not folder_name or is_intro_folder_name(folder_name):
        raise ChapterCutError("Intro-Timing über „Intro: Python Timing“.")
    plan = load_chapter_unified_plan(project, folder_name)
    if plan is None or not plan.slots:
        raise ChapterCutError(
            f"Kapitel-Plan fehlt für „{folder_name}“ — zuerst LLM Cut."
        )

    target = (folder_name or "").strip()

    def _include(name: str) -> bool:
        text = (name or "").strip()
        return text == target or text.lower() == target.lower()

    try:
        resolved = resolve_unified_timeline(
            project,
            plan=plan,
            allow_open_gaps=True,
            persist=False,
            include_chapter=_include,
        )
    except UnifiedTimelineError as exc:
        raise ChapterCutError(str(exc)) from exc

    # Auf Kapitel-Ursprung 0 normalisieren (falls Resolver mit Offsets startet).
    origin = 0.0
    if resolved.chapters:
        origin = min(ch.chapter_video_start for ch in resolved.chapters)
    elif resolved.shots:
        origin = min(s.timeline_start_seconds for s in resolved.shots)
    elif resolved.audio_segments:
        origin = min(a.timeline_start_seconds for a in resolved.audio_segments)

    if abs(origin) > 1e-6:
        resolved = _shift_timeline(resolved, origin)

    write_json(chapter_resolved_timeline_path(project, folder_name), resolved)
    return resolved


def resolve_all_chapter_timelines(
    project: Project,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[tuple[str, ResolvedTimelineDocument]]:
    names = list_body_chapter_names(project)
    if not names:
        raise ChapterCutError("Keine Körper-Kapitel für Python Timing.")
    out: list[tuple[str, ResolvedTimelineDocument]] = []
    total = len(names)
    for index, name in enumerate(names, start=1):
        if progress_callback is not None:
            progress_callback(name, index, total)
        out.append((name, resolve_chapter_timeline(project, name)))
    return out


def _shift_timeline(
    resolved: ResolvedTimelineDocument, origin: float
) -> ResolvedTimelineDocument:
    shifted_shots = [
        s.model_copy(
            update={
                "timeline_start_seconds": round(s.timeline_start_seconds - origin, 6),
                "timeline_end_seconds": round(s.timeline_end_seconds - origin, 6),
            }
        )
        for s in resolved.shots
    ]
    shifted_audio = [
        a.model_copy(
            update={
                "timeline_start_seconds": round(a.timeline_start_seconds - origin, 6),
                "timeline_end_seconds": round(a.timeline_end_seconds - origin, 6),
            }
        )
        for a in resolved.audio_segments
    ]
    shifted_chapters = [
        ch.model_copy(
            update={
                "chapter_video_start": round(ch.chapter_video_start - origin, 6),
                "chapter_audio_start": round(ch.chapter_audio_start - origin, 6),
                "chapter_audio_end": round(ch.chapter_audio_end - origin, 6),
                "chapter_video_end": round(ch.chapter_video_end - origin, 6),
            }
        )
        for ch in resolved.chapters
    ]
    total = 0.0
    if shifted_chapters:
        total = max(total, shifted_chapters[-1].chapter_video_end)
    if shifted_shots:
        total = max(total, max(s.timeline_end_seconds for s in shifted_shots))
    if shifted_audio:
        total = max(
            total,
            max(
                a.timeline_end_seconds + a.pause_after_seconds for a in shifted_audio
            ),
        )
    return resolved.model_copy(
        update={
            "shots": shifted_shots,
            "audio_segments": shifted_audio,
            "chapters": shifted_chapters,
            "total_duration_seconds": round(total, 6),
        }
    )


def _offset_timeline(
    resolved: ResolvedTimelineDocument, offset: float
) -> ResolvedTimelineDocument:
    if abs(offset) < 1e-9:
        return resolved
    shots = [
        s.model_copy(
            update={
                "timeline_start_seconds": round(s.timeline_start_seconds + offset, 6),
                "timeline_end_seconds": round(s.timeline_end_seconds + offset, 6),
            }
        )
        for s in resolved.shots
    ]
    audios = [
        a.model_copy(
            update={
                "timeline_start_seconds": round(a.timeline_start_seconds + offset, 6),
                "timeline_end_seconds": round(a.timeline_end_seconds + offset, 6),
            }
        )
        for a in resolved.audio_segments
    ]
    chapters = [
        ch.model_copy(
            update={
                "chapter_video_start": round(ch.chapter_video_start + offset, 6),
                "chapter_audio_start": round(ch.chapter_audio_start + offset, 6),
                "chapter_audio_end": round(ch.chapter_audio_end + offset, 6),
                "chapter_video_end": round(ch.chapter_video_end + offset, 6),
            }
        )
        for ch in resolved.chapters
    ]
    return resolved.model_copy(
        update={
            "shots": shots,
            "audio_segments": audios,
            "chapters": chapters,
            "total_duration_seconds": round(
                float(resolved.total_duration_seconds) + offset, 6
            ),
        }
    )


def concatenate_resolved_timelines(
    parts: list[ResolvedTimelineDocument],
    *,
    script_version: str,
    fps: float,
) -> ResolvedTimelineDocument:
    """Reiht Intro/Kapitel-Timelines hintereinander (je lokal bei 0 beginnend)."""
    if not parts:
        return ResolvedTimelineDocument(
            script_version=script_version,
            fps=fps,
            total_duration_seconds=0.0,
        )
    shots: list[ResolvedShot] = []
    audios: list[ResolvedAudioSegment] = []
    chapters: list[ResolvedChapterEnvelope] = []
    repairs: list[str] = []
    errors: list[str] = []
    cursor = 0.0
    preroll = parts[0].voiceover_preroll_sec
    postroll = parts[-1].voiceover_postroll_sec
    for part in parts:
        shifted = _offset_timeline(part, cursor)
        shots.extend(shifted.shots)
        audios.extend(shifted.audio_segments)
        chapters.extend(shifted.chapters)
        repairs.extend(part.repairs or [])
        errors.extend(part.errors or [])
        cursor = max(cursor, float(shifted.total_duration_seconds))
        # Falls total_duration unter dem letzten Clip liegt.
        if shifted.shots:
            cursor = max(
                cursor, max(s.timeline_end_seconds for s in shifted.shots)
            )
        if shifted.audio_segments:
            cursor = max(
                cursor,
                max(
                    a.timeline_end_seconds + a.pause_after_seconds
                    for a in shifted.audio_segments
                ),
            )
        if shifted.chapters:
            cursor = max(
                cursor, max(ch.chapter_video_end for ch in shifted.chapters)
            )
    return ResolvedTimelineDocument(
        script_version=script_version,
        fps=fps,
        total_duration_seconds=round(cursor, 6),
        audio_segments=audios,
        shots=shots,
        chapters=chapters,
        voiceover_preroll_sec=preroll,
        voiceover_postroll_sec=postroll,
        repairs=repairs,
        errors=errors,
    )


def _ensure_intro_resolved(project: Project) -> ResolvedTimelineDocument | None:
    intro_plan = load_model(
        intro_unified_cut_plan_path(project), UnifiedCutPlanDocument
    )
    if intro_plan is None or not intro_plan.slots:
        return None
    intro_resolved = load_model(
        intro_resolved_timeline_path(project), ResolvedTimelineDocument
    )
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_resolved_matches_plan,
    )

    if intro_resolved is None or not intro_resolved_matches_plan(
        intro_plan, intro_resolved
    ):
        intro_resolved = resolve_intro_timeline(project)
    return intro_resolved


def build_merged_resolved_timeline(
    project: Project,
    *,
    include_intro: bool = True,
    persist_global: bool = True,
) -> ResolvedTimelineDocument:
    """Intro + Kapitel in Dramaturgie-Reihenfolge → eine Timeline."""
    assert_enhanced_work_root(project)
    locked = require_locked_script(project)
    parts: list[ResolvedTimelineDocument] = []
    if include_intro:
        intro = _ensure_intro_resolved(project)
        if intro is not None and (intro.shots or intro.audio_segments):
            parts.append(intro)

    missing: list[str] = []
    for name in list_body_chapter_names(project):
        plan = load_chapter_unified_plan(project, name)
        resolved = load_chapter_resolved(project, name)
        if plan is None or not plan.slots:
            missing.append(f"{name} (kein Plan)")
            continue
        if resolved is None or not chapter_resolved_matches_plan(plan, resolved):
            try:
                resolved = resolve_chapter_timeline(project, name)
            except ChapterCutError as exc:
                missing.append(f"{name} ({exc})")
                continue
        if resolved.shots or resolved.audio_segments:
            parts.append(resolved)

    if not parts:
        detail = "; ".join(missing) if missing else "keine Timelines"
        raise ChapterCutError(
            f"Keine Kapitel-Timelines zum Mergen — {detail}."
        )

    fps = float(parts[0].fps or project.fps or 25.0)
    merged = concatenate_resolved_timelines(
        parts,
        script_version=locked.script_version,
        fps=fps,
    )
    if missing:
        merged = merged.model_copy(
            update={
                "errors": [
                    *list(merged.errors or []),
                    f"Fehlende Kapitel beim Merge: {', '.join(missing)}",
                ]
            }
        )
    if persist_global:
        write_json(resolved_timeline_path(project), merged)
    return merged


def export_chapter_otio(
    project: Project,
    folder_name: str,
    *,
    basename: str | None = None,
    allow_errors: bool = True,
) -> Path:
    """OTIO nur für ein Körper-Kapitel."""
    assert_enhanced_work_root(project)
    if not folder_name or is_intro_folder_name(folder_name):
        raise ChapterCutError("Intro-OTIO über „Intro: OTIO exportieren“.")
    plan = load_chapter_unified_plan(project, folder_name)
    resolved = load_chapter_resolved(project, folder_name)
    if plan is None or not plan.slots:
        raise EnhancedOtioExportError(
            f"Kapitel-Plan fehlt für „{folder_name}“ — zuerst LLM Cut."
        )
    if resolved is None or not chapter_resolved_matches_plan(plan, resolved):
        try:
            resolved = resolve_chapter_timeline(project, folder_name)
        except ChapterCutError as exc:
            raise EnhancedOtioExportError(str(exc)) from exc
    if not resolved.shots and not resolved.audio_segments:
        raise EnhancedOtioExportError(
            f"Kapitel „{folder_name}“: keine Shots/Audio für OTIO."
        )
    slug = chapter_folder_slug(folder_name)
    name = (basename or "").strip() or f"{project.name}_{slug}"
    return export_otio_from_resolved_timeline(
        project,
        basename=name,
        allow_errors=allow_errors,
        resolved=resolved,
        timeline_name=f"{project.name} {folder_name}",
    )


def export_all_chapters_otio(
    project: Project,
    *,
    basename: str | None = None,
    allow_errors: bool = True,
    include_intro: bool = True,
) -> Path:
    """Merged Intro + alle Kapitel → eine OTIO (+ globale resolved_timeline.json)."""
    merged = build_merged_resolved_timeline(
        project,
        include_intro=include_intro,
        persist_global=True,
    )
    name = (basename or "").strip() or f"{project.name}_enhanced"
    return export_otio_from_resolved_timeline(
        project,
        basename=name,
        allow_errors=allow_errors,
        resolved=merged,
        timeline_name=f"{project.name} enhanced",
    )
