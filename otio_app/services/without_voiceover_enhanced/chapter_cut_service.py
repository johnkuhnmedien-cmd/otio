"""Per-Kapitel Unified Cut: Plan/Timing/OTIO unter ``cut/chapters/{slug}/``."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from otio_app.defaults import (
    ENHANCED_CHAPTER_LLM_MAX_WORKERS,
    ENHANCED_CHAPTER_TIMING_MAX_WORKERS,
)
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
    open_gap_ids: list[str] = field(default_factory=list)

    @property
    def open_gap_count(self) -> int:
        return len(self.open_gap_ids)

    @property
    def timing_ready(self) -> bool:
        """Python Timing nur mit Plan und ohne offene Coverage Gaps."""
        return self.has_plan and self.open_gap_count == 0


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


def chapter_open_gap_ids(
    project: Project,
    folder_name: str,
    *,
    open_gap_id_set: set[str] | None = None,
) -> list[str]:
    """Offene Coverage-Gap-IDs eines Kapitel-Plans (Reihenfolge der Slots)."""
    plan = load_chapter_unified_plan(project, folder_name)
    if plan is None or not plan.slots:
        return []
    if open_gap_id_set is None:
        from otio_app.services.without_voiceover_enhanced.gap_status_service import (
            summarize_gap_status,
        )

        open_gap_id_set = set(summarize_gap_status(project).open_gap_ids)
    seen: set[str] = set()
    ordered: list[str] = []
    for slot in plan.slots:
        gid = str(getattr(slot, "coverage_gap_id", "") or "").strip()
        if not gid or gid not in open_gap_id_set or gid in seen:
            continue
        seen.add(gid)
        ordered.append(gid)
    return ordered


def get_chapter_cut_status(
    project: Project,
    folder_name: str,
    *,
    open_gap_id_set: set[str] | None = None,
) -> ChapterCutStatus:
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
    open_ids = chapter_open_gap_ids(
        project, folder_name, open_gap_id_set=open_gap_id_set
    )
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
        open_gap_ids=open_ids,
    )


def list_chapter_cut_statuses(project: Project) -> list[ChapterCutStatus]:
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        summarize_gap_status,
    )

    open_set = set(summarize_gap_status(project).open_gap_ids)
    return [
        get_chapter_cut_status(project, name, open_gap_id_set=open_set)
        for name in list_body_chapter_names(project)
    ]


def list_chapters_needing_unified_cut(project: Project) -> list[str]:
    """Körper-Kapitel ohne Unified-Plan (offene LLM Cuts)."""
    return [
        status.folder_name
        for status in list_chapter_cut_statuses(project)
        if not status.has_plan
    ]


def list_chapters_needing_python_timing(project: Project) -> list[str]:
    """Körper-Kapitel mit Plan, geschlossenen Gaps, ohne passendes Resolved."""
    return [
        status.folder_name
        for status in list_chapter_cut_statuses(project)
        if status.timing_ready and not status.matches
    ]


def list_chapters_ready_for_python_timing(project: Project) -> list[str]:
    """Körper-Kapitel mit Plan und ohne offene Gaps (auch bereits getimte)."""
    return [
        status.folder_name
        for status in list_chapter_cut_statuses(project)
        if status.timing_ready
    ]


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
    prior_plans: list[UnifiedCutPlanDocument] | None = None,
) -> ChapterCutGenerateResult:
    """Ein Körper-Kapitel: Unified-LLM → ``cut/chapters/{slug}/unified_cut_plan.json``."""
    assert_enhanced_work_root(project)
    if not folder_name or is_intro_folder_name(folder_name):
        raise ChapterCutError(
            "Intro läuft über die Intro-Buttons — kein Kapitel-Cut für Intro."
        )
    prior = (
        list(prior_plans)
        if prior_plans is not None
        else load_prior_chapter_plans(project, folder_name)
    )
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


def _prior_plans_from_snapshot(
    folder_name: str,
    *,
    body_order: list[str],
    snapshot: dict[str, UnifiedCutPlanDocument],
) -> list[UnifiedCutPlanDocument]:
    """Prior-Pläne aus einem Disk-Snapshot (stabil für parallele Batches)."""
    prior: list[UnifiedCutPlanDocument] = []
    for name in body_order:
        if name == folder_name:
            break
        plan = snapshot.get(name)
        if plan is not None and plan.slots:
            prior.append(plan)
    return prior


def generate_all_chapter_unified_cuts(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    chapter_names: list[str] | None = None,
    only_open: bool = False,
    max_workers: int | None = None,
) -> list[ChapterCutGenerateResult]:
    """Körper-Kapitel parallel; schreibt pro-Kapitel JSON + globalen Merge.

    ``only_open=True``: nur Kapitel ohne bestehenden Unified-Plan.
    ``chapter_names``: explizite Teilmenge (Dramaturgie-Filter bleibt außen).
    Prior-„used assets“-Ledger kommt aus einem Snapshot vor dem Batch
    (nicht aus frisch geschriebenen Parallel-Ergebnissen).
    """
    if chapter_names is not None:
        names = [str(n).strip() for n in chapter_names if str(n).strip()]
    elif only_open:
        names = list_chapters_needing_unified_cut(project)
    else:
        names = list_body_chapter_names(project)
    if not names:
        raise ChapterCutError(
            "Keine offenen Körper-Kapitel für den Unified Cut."
            if only_open
            else "Keine Körper-Kapitel für den Unified Cut."
        )

    body_order = list_body_chapter_names(project)
    snapshot: dict[str, UnifiedCutPlanDocument] = {}
    for name in body_order:
        plan = load_chapter_unified_plan(project, name)
        if plan is not None and plan.slots:
            snapshot[name] = plan

    workers = max(1, int(max_workers or ENHANCED_CHAPTER_LLM_MAX_WORKERS))
    workers = min(workers, len(names))
    total = len(names)
    results_by_name: dict[str, ChapterCutGenerateResult] = {}
    errors: list[str] = []

    def _one(name: str) -> ChapterCutGenerateResult:
        prior = _prior_plans_from_snapshot(
            name, body_order=body_order, snapshot=snapshot
        )
        return generate_chapter_unified_cut(
            project,
            name,
            provider=provider,
            model=model,
            llm_callable=llm_callable,
            refresh_merged=False,
            prior_plans=prior,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, name): name for name in names}
        done = 0
        for future in as_completed(futures):
            name = futures[future]
            done += 1
            if progress_callback is not None:
                progress_callback(name, done, total)
            try:
                results_by_name[name] = future.result()
            except Exception as exc:  # noqa: BLE001 — Batch sammelt Fehler
                errors.append(f"{name}: {exc}")

    out = [results_by_name[name] for name in names if name in results_by_name]
    if out:
        refresh_merged_unified_cut_plan(project)
    if errors:
        raise ChapterCutError(
            f"{len(errors)}/{total} Kapitel-LLM-Cut(s) fehlgeschlagen "
            f"({len(out)} ok):\n" + "\n".join(f"- {err}" for err in errors)
        )
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

    open_gaps = chapter_open_gap_ids(project, folder_name)
    if open_gaps:
        preview = ", ".join(open_gaps[:5])
        more = f" (+{len(open_gaps) - 5})" if len(open_gaps) > 5 else ""
        raise ChapterCutError(
            f"Python Timing für „{folder_name}“ blockiert: "
            f"{len(open_gaps)} offene Coverage Gap(s) — zuerst Funnel/Manual "
            f"schließen ({preview}{more})."
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

    # Accepted/Funnel/Manual → Placeholder-Shots ersetzen (Timing-Zeiten bleiben).
    # Ohne Merge landen manuelle Pfad-Zuweisungen nie im Kapitel-OTIO.
    from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
        merge_export_ready_gaps_into_timeline,
    )

    try:
        # Kein globaler gap_merge_report — parallel-sicher (Kapitel-Dateien reichen).
        resolved, _merge_report = merge_export_ready_gaps_into_timeline(
            project,
            timeline=resolved,
            unified=plan,
            require_closed_none=False,
            persist=False,
            persist_report=False,
        )
    except Exception:  # noqa: BLE001 — Merge soft; Timing-Ergebnis behalten
        pass

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
    chapter_names: list[str] | None = None,
    only_open: bool = False,
    max_workers: int | None = None,
) -> list[tuple[str, ResolvedTimelineDocument]]:
    """Python-Timing für Körper-Kapitel (parallel).

    ``only_open=True``: nur Kapitel mit Plan + geschlossenen Gaps, ohne Resolved.
    Sonst: alle timing-bereiten Kapitel (Plan + Gaps zu). Kapitel mit offenen
    Gaps werden übersprungen / nicht angeboten.
    """
    if chapter_names is not None:
        names = [str(n).strip() for n in chapter_names if str(n).strip()]
    elif only_open:
        names = list_chapters_needing_python_timing(project)
    else:
        names = list_chapters_ready_for_python_timing(project)
    if not names:
        raise ChapterCutError(
            "Keine Kapitel für Python Timing "
            "(offene Gaps schließen oder Plan fehlt / Timing schon fertig)."
            if only_open
            else "Keine Kapitel für Python Timing "
            "(Plan fehlt oder Coverage Gaps noch offen)."
        )

    workers = max(1, int(max_workers or ENHANCED_CHAPTER_TIMING_MAX_WORKERS))
    workers = min(workers, len(names))
    total = len(names)
    results_by_name: dict[str, ResolvedTimelineDocument] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(resolve_chapter_timeline, project, name): name for name in names
        }
        done = 0
        for future in as_completed(futures):
            name = futures[future]
            done += 1
            if progress_callback is not None:
                progress_callback(name, done, total)
            try:
                results_by_name[name] = future.result()
            except Exception as exc:  # noqa: BLE001 — Batch sammelt Fehler
                errors.append(f"{name}: {exc}")

    if errors:
        raise ChapterCutError(
            f"{len(errors)}/{total} Python-Timing(s) fehlgeschlagen:\n"
            + "\n".join(f"- {err}" for err in errors)
        )

    return [(name, results_by_name[name]) for name in names if name in results_by_name]


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
