"""Phase 5: Deterministischer Gap-Merge (ersetzt LLM Final Cut).

Timing bleibt unverändert — nur Asset-/Medienfelder werden getauscht.
"""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.fit_bridge import (
    fit_bucket_from_final_score,
    passes_duration_prefilter,
    required_candidate_duration_seconds,
    supplement_beats_local,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    list_export_ready_supplements,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGapsDocument,
    FunnelCandidateRecord,
    GapMergeReport,
    GapMergeSlotResult,
    ResolvedShot,
    ResolvedTimelineDocument,
    StockCandidate,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    gap_merge_report_path,
    resolved_timeline_path,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    _resolve_shot_media,
    build_asset_catalog,
    lookup_catalog_entry,
)


class GapMergeError(RuntimeError):
    """Fehler beim Gap-Merge."""


def _funnel_records_by_gap(
    funnel: SupplementFunnelReport | None,
) -> dict[str, list[FunnelCandidateRecord]]:
    if funnel is None:
        return {}
    out: dict[str, list[FunnelCandidateRecord]] = {}
    for gap_report in funnel.gaps:
        out[gap_report.gap_id] = list(gap_report.candidates)
    return out


def _score_for_candidate(
    records: list[FunnelCandidateRecord],
    candidate_id: str,
) -> tuple[int | None, str]:
    for record in records:
        if record.candidate_id == candidate_id:
            bucket = (record.fit_bucket or "").strip().lower()
            if not bucket and record.final_score is not None:
                bucket = fit_bucket_from_final_score(record.final_score)
            return record.final_score, bucket or "reject"
    # Manual-Assign / ohne Funnel-Score → acceptable (PLAN Ausnahme).
    return None, "manual"


def _local_fit_for_shot(
    shot: ResolvedShot,
    *,
    unified: UnifiedCutPlanDocument | None,
    coverage: CoverageGapsDocument | None,
) -> str:
    fit = str(shot.asset_fit or "").strip().lower()
    if fit in {"strong", "acceptable", "weak", "none"}:
        return fit
    if unified is not None:
        for slot in unified.slots:
            if slot.slot_id == shot.shot_id or (
                shot.coverage_gap_id
                and slot.coverage_gap_id == shot.coverage_gap_id
            ):
                return str(slot.asset_fit or "none")
    if coverage is not None and shot.coverage_gap_id:
        for gap in coverage.gaps:
            if gap.gap_id == shot.coverage_gap_id:
                # weak gaps sind medium priority; none = high
                return "none" if gap.priority == "high" else "weak"
    return "none" if shot.open_gap or not shot.asset_id else "weak"


def _target_duration_for_shot(
    shot: ResolvedShot,
    *,
    coverage: CoverageGapsDocument | None,
) -> float | None:
    if coverage is not None and shot.coverage_gap_id:
        for gap in coverage.gaps:
            if gap.gap_id == shot.coverage_gap_id and gap.target_duration_seconds is not None:
                return float(gap.target_duration_seconds)
    # Fallback: Timeline-Span (Timing ist final).
    return max(0.0, float(shot.timeline_end_seconds) - float(shot.timeline_start_seconds))


def _candidates_for_gap(
    ready: list[StockCandidate],
    gap_id: str,
) -> list[StockCandidate]:
    gid = (gap_id or "").strip()
    return [c for c in ready if (c.gap_id or "").strip() == gid]


def _pick_supplement(
    candidates: list[StockCandidate],
    *,
    records: list[FunnelCandidateRecord],
    local_fit: str,
    min_duration: float | None,
) -> tuple[StockCandidate | None, str, str, bool]:
    """Returns (candidate|None, bucket, message, review_flag)."""
    ranked: list[tuple[int, str, StockCandidate, str, bool]] = []
    for candidate in candidates:
        ok, reason = passes_duration_prefilter(candidate, min_duration=min_duration)
        if not ok:
            # Defensiv: echte Datei nachmessen falls API-Metadaten knauserig waren.
            path = Path(str(candidate.local_media_path or ""))
            if path.is_file() and min_duration is not None:
                try:
                    probed = probe_duration_seconds(path)
                except Exception:  # noqa: BLE001
                    probed = None
                if probed is not None and float(probed) + 1e-9 >= float(min_duration):
                    ok = True
                    reason = ""
            if not ok:
                continue
        score, bucket = _score_for_candidate(records, candidate.candidate_id)
        if bucket == "reject":
            continue
        if not supplement_beats_local(supplement_bucket=bucket, local_fit=local_fit):
            continue
        review = local_fit == "none" and bucket == "weak"
        # Höherer Score zuerst; manual (None) als 60.
        sort_score = int(score) if score is not None else 60
        ranked.append((sort_score, bucket, candidate, reason, review))

    if not ranked:
        return None, "", "Kein geeigneter export_ready-Kandidat.", False
    ranked.sort(
        key=lambda row: (
            -row[0],
            (row[2].candidate_id or ""),
        )
    )
    score, bucket, chosen, _reason, review = ranked[0]
    return chosen, bucket, f"gewählt score={score} bucket={bucket}", review


def merge_export_ready_gaps_into_timeline(
    project: Project,
    *,
    timeline: ResolvedTimelineDocument | None = None,
    require_closed_none: bool = False,
    persist: bool = True,
) -> tuple[ResolvedTimelineDocument, GapMergeReport]:
    """Ersetzt Gap-Assets deterministisch; Timeline-Zeiten bleiben fix.

    ``require_closed_none=False``: Preview/Merge-Zwischenstand erlaubt offene none.
    ``True``: offene none → ``GapMergeError`` (Produktions-Gate).
    """
    locked = require_locked_script(project)
    if timeline is None:
        timeline = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if timeline is None:
        raise GapMergeError("Resolved Timeline fehlt.")

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    unified = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    options = load_cut_plan_options(project)
    ready = list_export_ready_supplements(project)
    records_by_gap = _funnel_records_by_gap(funnel)
    catalog = build_asset_catalog(project, fps=float(timeline.fps or project.fps))

    report = GapMergeReport(script_version=locked.script_version)
    fps = float(timeline.fps or project.fps)
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))
    repairs = list(timeline.repairs or [])

    updated_shots: list[ResolvedShot] = []
    for shot in timeline.shots:
        gap_id = (shot.coverage_gap_id or "").strip()
        if not gap_id and not shot.open_gap:
            updated_shots.append(shot)
            continue
        if not gap_id:
            # open_gap ohne gap_id — nicht mergebar
            result = GapMergeSlotResult(
                shot_id=shot.shot_id,
                coverage_gap_id="",
                status="failed",
                previous_asset_id=shot.asset_id or "",
                message="open_gap ohne coverage_gap_id.",
            )
            report.slots.append(result)
            report.errors.append(f"{shot.shot_id}: {result.message}")
            updated_shots.append(shot)
            continue

        local_fit = _local_fit_for_shot(shot, unified=unified, coverage=coverage)
        target = _target_duration_for_shot(shot, coverage=coverage)
        min_duration = required_candidate_duration_seconds(
            target,
            head_trim=head_trim,
            short_tolerance=short_tolerance,
        )
        candidates = _candidates_for_gap(ready, gap_id)
        chosen, bucket, pick_msg, review = _pick_supplement(
            candidates,
            records=records_by_gap.get(gap_id, []),
            local_fit=local_fit,
            min_duration=min_duration,
        )

        if chosen is None:
            if local_fit == "weak" and shot.asset_id and not shot.open_gap:
                result = GapMergeSlotResult(
                    shot_id=shot.shot_id,
                    coverage_gap_id=gap_id,
                    status="kept_local_weak",
                    previous_asset_id=shot.asset_id,
                    new_asset_id=shot.asset_id,
                    local_fit=local_fit,
                    message=f"Upgrade übersprungen — {pick_msg}",
                )
                report.slots.append(result)
                report.kept_local_shot_ids.append(shot.shot_id)
                updated_shots.append(shot)
                continue
            result = GapMergeSlotResult(
                shot_id=shot.shot_id,
                coverage_gap_id=gap_id,
                status="open_none" if local_fit == "none" else "failed",
                previous_asset_id=shot.asset_id or "",
                local_fit=local_fit,
                message=pick_msg,
            )
            report.slots.append(result)
            if local_fit == "none":
                report.open_none_gap_ids.append(gap_id)
            else:
                report.errors.append(f"{shot.shot_id}: {pick_msg}")
            updated_shots.append(shot)
            continue

        entry, lookup_error = lookup_catalog_entry(catalog, chosen.candidate_id)
        if entry is None:
            # Fallback: direkt aus Supplement-Pfad katalogisieren.
            media_path = Path(str(chosen.local_media_path or ""))
            if not media_path.is_file():
                result = GapMergeSlotResult(
                    shot_id=shot.shot_id,
                    coverage_gap_id=gap_id,
                    status="failed",
                    previous_asset_id=shot.asset_id or "",
                    local_fit=local_fit,
                    supplement_fit_bucket=bucket,
                    message=lookup_error or "Supplement-Datei fehlt.",
                )
                report.slots.append(result)
                report.errors.append(f"{shot.shot_id}: {result.message}")
                updated_shots.append(shot)
                continue
            entry = {
                "path": str(media_path),
                "canonical_id": chosen.candidate_id,
                "duration_seconds": chosen.duration_seconds,
                "media_kind": (
                    "image"
                    if (chosen.media_type or "").lower() in {"photo", "image"}
                    else "video"
                ),
                "folder": "",
                "available_start_seconds": 0.0,
                "usable_in_s": None,
            }

        timeline_start = float(shot.timeline_start_seconds)
        timeline_end = float(shot.timeline_end_seconds)
        try:
            resolved = _resolve_shot_media(
                project,
                shot_id=shot.shot_id,
                asset_id=str(entry.get("canonical_id") or chosen.candidate_id),
                entry=entry,
                timeline_start=timeline_start,
                timeline_end=timeline_end,
                fps=fps,
                head_trim=head_trim,
                short_tolerance=short_tolerance,
                editorial_function=shot.editorial_function,
                may_overlap_pause=shot.may_overlap_pause,
                repairs=repairs,
            )
        except TimelineResolveError as exc:
            result = GapMergeSlotResult(
                shot_id=shot.shot_id,
                coverage_gap_id=gap_id,
                status="failed",
                previous_asset_id=shot.asset_id or "",
                local_fit=local_fit,
                supplement_fit_bucket=bucket,
                message=str(exc),
            )
            report.slots.append(result)
            report.errors.append(f"{shot.shot_id}: {exc}")
            updated_shots.append(shot)
            continue

        # Timing-Immutability: Start/Ende aus dem Input-Shot erzwingen.
        resolved.timeline_start_seconds = timeline_start
        resolved.timeline_end_seconds = timeline_end
        resolved.chapter_id = shot.chapter_id
        resolved.folder_name = shot.folder_name or resolved.folder_name
        resolved.asset_fit = bucket if bucket != "manual" else "acceptable"
        resolved.asset_fit_reason = (
            f"gap_merge ← {chosen.candidate_id} ({pick_msg})"
        )
        resolved.cut_alignment = shot.cut_alignment
        resolved.coverage_gap_id = gap_id
        resolved.open_gap = False

        result = GapMergeSlotResult(
            shot_id=shot.shot_id,
            coverage_gap_id=gap_id,
            status="merged",
            previous_asset_id=shot.asset_id or "",
            new_asset_id=resolved.asset_id,
            local_fit=local_fit,
            supplement_fit_bucket=bucket,
            review_flag=review,
            message=pick_msg,
        )
        report.slots.append(result)
        report.merged_shot_ids.append(shot.shot_id)
        if review:
            report.review_shot_ids.append(shot.shot_id)
        updated_shots.append(resolved)

    merged_timeline = timeline.model_copy(deep=True)
    merged_timeline.shots = updated_shots
    merged_timeline.repairs = repairs
    # Merge-Fehler zusätzlich im Timeline-Dokument spiegeln (soft).
    merge_errors = list(report.errors)
    if require_closed_none and report.open_none_gap_ids:
        merge_errors.append(
            "Offene none-Gaps: " + ", ".join(sorted(set(report.open_none_gap_ids)))
        )
    merged_timeline.errors = list(dict.fromkeys(list(timeline.errors or []) + merge_errors))

    report.repairs = [
        r for r in repairs if r not in (timeline.repairs or [])
    ]
    report.errors = merge_errors
    open_count = len(set(report.open_none_gap_ids))
    report.message = (
        f"Merge: {len(report.merged_shot_ids)} ersetzt, "
        f"{len(report.kept_local_shot_ids)} weak behalten, "
        f"{open_count} none offen, "
        f"{len(report.review_shot_ids)} Review-Flags."
    )

    if persist:
        write_json(resolved_timeline_path(project), merged_timeline)
        write_json(gap_merge_report_path(project), report)

    if require_closed_none and report.open_none_gap_ids:
        raise GapMergeError(
            "Offene none-Gaps nach Merge: "
            + ", ".join(sorted(set(report.open_none_gap_ids)))
        )
    return merged_timeline, report
