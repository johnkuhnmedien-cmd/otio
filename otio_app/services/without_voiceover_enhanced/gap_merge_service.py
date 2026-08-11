"""Phase 5: Deterministischer Gap-Merge (ersetzt LLM Final Cut).

Timing bleibt unverändert — nur Asset-/Medienfelder werden getauscht.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from otio_app.models import Project
from otio_app.services.media_utils import is_image_media, probe_duration_seconds
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
from otio_app.services.without_voiceover_enhanced.media_hold import (
    MediaHoldError,
    ensure_still_hold_video,
    ensure_video_padded_hold,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGapsDocument,
    CutSlot,
    FunnelCandidateRecord,
    GapMergeReport,
    GapMergeSlotResult,
    ResolvedShot,
    ResolvedTimelineDocument,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
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


def is_bridge_shot(
    shot: ResolvedShot,
    *,
    unified: UnifiedCutPlanDocument | None = None,
) -> bool:
    """Kapitel-Bridge (nie Funnel)."""
    if str(shot.shot_id).startswith("bridge_"):
        return True
    if str(shot.editorial_function or "").strip().lower() == "chapter_transition":
        return True
    if unified is not None:
        for slot in unified.slots:
            if slot.slot_id != shot.shot_id:
                continue
            return (
                str(slot.narrative_function or "").strip().lower()
                == "chapter_transition"
            )
    return False


def _slot_for_shot(
    shot: ResolvedShot,
    unified: UnifiedCutPlanDocument | None,
) -> CutSlot | None:
    if unified is None:
        return None
    for slot in unified.slots:
        if slot.slot_id == shot.shot_id:
            return slot
    return None


def _usable_remaining_seconds(
    shot: ResolvedShot,
    entry: dict,
    *,
    head_trim: float,
) -> float | None:
    """Nutzbare Restdauer nach aktuellem source_end; None = Still/unbegrenzt."""
    path = Path(str(entry.get("path") or shot.resolved_media_path or ""))
    media_kind = str(entry.get("media_kind") or entry.get("media_type") or "").lower()
    if media_kind in {"image", "photo"} or (path.suffix and is_image_media(path)):
        return None
    media_dur = entry.get("duration_seconds")
    if media_dur is None:
        media_dur = shot.resolved_media_duration_seconds
    if media_dur is None or float(media_dur) <= 0:
        return 0.0
    available_start = float(
        entry.get("available_start_seconds")
        if entry.get("available_start_seconds") is not None
        else (shot.resolved_available_start_seconds or 0.0)
    )
    usable_end = available_start + float(media_dur)
    return max(0.0, usable_end - float(shot.source_end_seconds))


def _try_extend_previous_over_bridge(
    project: Project,
    prev: ResolvedShot,
    bridge: ResolvedShot,
    *,
    catalog,
    head_trim: float,
    short_tolerance: float,
    fps: float,
    repairs: list[str],
) -> ResolvedShot | None:
    """Closing-Shot über Bridge verlängern, wenn nutzbare Restdauer reicht."""
    bid = bridge.shot_id

    def _skip(reason: str) -> None:
        repairs.append(f"{bid}: Extend nicht möglich — {reason}")

    if prev.is_placeholder or prev.open_gap or not prev.asset_id:
        _skip("Vorgänger ist Placeholder/open_gap oder ohne asset_id")
        return None
    if not prev.resolved_media_path:
        _skip("Vorgänger ohne resolved_media_path")
        return None
    entry, _err = lookup_catalog_entry(catalog, prev.asset_id)
    if entry is None:
        entry = {
            "path": prev.resolved_media_path,
            "duration_seconds": prev.resolved_media_duration_seconds,
            "available_start_seconds": prev.resolved_available_start_seconds,
            "media_kind": prev.resolved_media_kind or "video",
            "usable_in_s": None,
            "canonical_id": prev.asset_id,
        }
    need = max(
        0.0,
        float(bridge.timeline_end_seconds) - float(bridge.timeline_start_seconds),
    )
    if need <= 1e-9:
        _skip("Bridge-Dauer ≈ 0")
        return None

    remaining = _usable_remaining_seconds(prev, entry, head_trim=head_trim)
    extended = prev.model_copy(deep=True)
    new_end = float(bridge.timeline_end_seconds)
    new_span = max(0.0, new_end - float(extended.timeline_start_seconds))

    # Still: immer per Hold über die Bridge tragbar.
    path = Path(str(entry.get("path") or extended.resolved_media_path))
    if remaining is None or is_image_media(path) or extended.hold_mode == "freeze_video":
        try:
            if is_image_media(path) and extended.hold_mode != "freeze_video":
                hold = ensure_still_hold_video(
                    project, path, duration_seconds=new_span, fps=fps
                )
            else:
                hold = ensure_video_padded_hold(
                    project,
                    Path(extended.resolved_media_path),
                    target_duration_seconds=new_span,
                    fps=fps,
                )
        except MediaHoldError as exc:
            _skip(f"Still/Hold fehlgeschlagen ({exc})")
            return None
        extended.timeline_end_seconds = round(new_end, 6)
        extended.resolved_media_path = str(hold)
        extended.resolved_media_kind = "video"
        extended.resolved_available_start_seconds = 0.0
        extended.resolved_media_duration_seconds = round(new_span, 6)
        extended.source_start_seconds = 0.0
        extended.source_end_seconds = round(new_span, 6)
        extended.hold_mode = "freeze_video"
        extended.open_gap = False
        extended.is_placeholder = False
        repairs.append(
            f"{bridge.shot_id}: Bridge von {extended.shot_id} getragen "
            f"(Still/Hold, +{need:.2f}s)."
        )
        return extended

    if remaining + short_tolerance + 1e-9 < need:
        _skip(
            f"Rest {remaining:.2f}s + Toleranz {short_tolerance:.1f}s "
            f"< Bridge {need:.2f}s"
        )
        return None

    # Motion: Source erweitern; knappe Toleranz → optional kurzes tpad nur für Bridge.
    extended.timeline_end_seconds = round(new_end, 6)
    new_source_end = float(extended.source_end_seconds) + need
    available_start = float(entry.get("available_start_seconds") or 0.0)
    media_dur = float(entry.get("duration_seconds") or 0.0)
    usable_end = available_start + media_dur
    if new_source_end <= usable_end + 1e-6:
        extended.source_end_seconds = round(new_source_end, 6)
        extended.open_gap = False
        extended.is_placeholder = False
        repairs.append(
            f"{bridge.shot_id}: Bridge von {extended.shot_id} getragen "
            f"(Source +{need:.2f}s, Rest {remaining:.2f}s)."
        )
        return extended

    # Innerhalb Toleranz: Hold-Pad für den Überstand (nur Bridge-Ausnahme).
    shortfall = new_source_end - usable_end
    if shortfall > short_tolerance + 1e-9:
        _skip(
            f"Source-Überstand {shortfall:.2f}s > Toleranz {short_tolerance:.1f}s"
        )
        return None
    try:
        hold = ensure_video_padded_hold(
            project,
            Path(extended.resolved_media_path),
            target_duration_seconds=new_span,
            fps=fps,
        )
    except MediaHoldError as exc:
        _skip(f"Video-Hold fehlgeschlagen ({exc})")
        return None
    extended.resolved_media_path = str(hold)
    extended.resolved_media_kind = "video"
    extended.resolved_available_start_seconds = 0.0
    extended.resolved_media_duration_seconds = round(new_span, 6)
    extended.source_start_seconds = 0.0
    extended.source_end_seconds = round(new_span, 6)
    extended.hold_mode = "bridge_hold"
    extended.open_gap = False
    extended.is_placeholder = False
    repairs.append(
        f"{bridge.shot_id}: Bridge von {extended.shot_id} getragen "
        f"(Hold +{shortfall:.2f}s innerhalb Toleranz)."
    )
    return extended


def _entry_is_photo(entry: dict) -> bool:
    kind = str(entry.get("media_kind") or entry.get("media_type") or "").lower()
    if kind in {"image", "photo"}:
        return True
    path = Path(str(entry.get("path") or ""))
    return bool(path.suffix) and is_image_media(path)


def _entry_aspect_ratio(entry: dict) -> float | None:
    w = entry.get("width")
    h = entry.get("height")
    try:
        if w is not None and h is not None and float(h) > 0:
            return float(w) / float(h)
    except (TypeError, ValueError):
        pass
    path = Path(str(entry.get("path") or ""))
    if not path.is_file() or not is_image_media(path):
        return None
    try:
        from PIL import Image

        with Image.open(path) as image:
            iw, ih = image.size
        if ih > 0:
            return float(iw) / float(ih)
    except Exception:  # noqa: BLE001
        return None
    return None


def _pick_bridge_asset_id(
    bridge: ResolvedShot,
    *,
    unified: UnifiedCutPlanDocument | None,
    catalog,
    used_asset_ids: set[str],
    folder_hint: str,
    min_duration: float,
) -> str | None:
    """Kandidaten aus Plan, sonst bestes ungenutztes Kapitel-Asset.

    Ranking: Videos vor Fotos; Hochkant-Fotos (aspect < 1) zuletzt.
    """
    slot = _slot_for_shot(bridge, unified)
    ordered_ids: list[str] = []
    if slot is not None:
        ordered_ids.extend(
            str(a).strip() for a in (slot.bridge_candidate_asset_ids or []) if str(a).strip()
        )

    def _usable(entry: dict) -> float:
        # Stills sind dauer-unbegrenzt (Hold); vor duration-None-Check.
        if _entry_is_photo(entry):
            return 1e9
        dur = entry.get("duration_seconds")
        if dur is None:
            return 0.0
        trim = 0.0
        if entry.get("usable_in_s") is not None:
            trim = max(0.0, float(entry["usable_in_s"]))
        return max(0.0, float(dur) - trim)

    def _rank_key(asset_id: str, entry: dict) -> tuple:
        is_photo = 1 if _entry_is_photo(entry) else 0
        aspect = _entry_aspect_ratio(entry)
        portrait = 1 if (is_photo and aspect is not None and aspect < 1.0) else 0
        usable = _usable(entry)
        return (is_photo, portrait, -usable, str(asset_id))

    # Plan-Kandidaten: unter den gültigen Videos vor Fotos / Hochkant zuletzt.
    plan_ranked: list[tuple] = []
    for asset_id in ordered_ids:
        entry, _err = lookup_catalog_entry(catalog, asset_id)
        if entry is None:
            continue
        if _usable(entry) + 1e-9 < min_duration:
            continue
        plan_ranked.append((_rank_key(asset_id, entry), str(entry.get("canonical_id") or asset_id)))
    if plan_ranked:
        plan_ranked.sort()
        return plan_ranked[0][1]

    # Bestes ungenutztes Asset des endenden Kapitels.
    folder = (folder_hint or "").strip().lower()
    ranked: list[tuple] = []
    for asset_id, entry in (catalog.by_id or {}).items():
        if asset_id in used_asset_ids:
            continue
        entry_folder = str(entry.get("folder") or "").strip().lower()
        if folder and entry_folder and entry_folder != folder:
            continue
        usable = _usable(entry)
        if usable + 1e-9 < min_duration:
            continue
        ranked.append((_rank_key(str(asset_id), entry), str(asset_id)))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][1]


def _resolve_bridge_with_asset(
    project: Project,
    bridge: ResolvedShot,
    asset_id: str,
    *,
    catalog,
    fps: float,
    head_trim: float,
    short_tolerance: float,
    repairs: list[str],
) -> ResolvedShot | None:
    entry, lookup_error = lookup_catalog_entry(catalog, asset_id)
    if entry is None:
        repairs.append(
            f"{bridge.shot_id}: Bridge-Asset {asset_id} nicht im Katalog "
            f"({lookup_error})."
        )
        return None
    try:
        resolved = _resolve_shot_media(
            project,
            shot_id=bridge.shot_id,
            asset_id=str(entry.get("canonical_id") or asset_id),
            entry=entry,
            timeline_start=float(bridge.timeline_start_seconds),
            timeline_end=float(bridge.timeline_end_seconds),
            fps=fps,
            head_trim=head_trim,
            short_tolerance=short_tolerance,
            editorial_function="chapter_transition",
            may_overlap_pause=True,
            repairs=repairs,
        )
    except TimelineResolveError as exc:
        repairs.append(f"{bridge.shot_id}: Bridge-Asset fehlgeschlagen — {exc}")
        return None
    resolved.timeline_start_seconds = float(bridge.timeline_start_seconds)
    resolved.timeline_end_seconds = float(bridge.timeline_end_seconds)
    resolved.chapter_id = bridge.chapter_id
    resolved.folder_name = bridge.folder_name or str(entry.get("folder") or "")
    resolved.asset_fit = "acceptable"
    resolved.asset_fit_reason = "bridge_fill"
    resolved.cut_alignment = bridge.cut_alignment
    resolved.coverage_gap_id = None
    resolved.open_gap = False
    resolved.is_placeholder = False
    resolved.editorial_function = "chapter_transition"
    return resolved


def fill_chapter_bridges(
    project: Project,
    timeline: ResolvedTimelineDocument,
    *,
    unified: UnifiedCutPlanDocument | None,
    catalog,
    options,
    repairs: list[str],
    report: GapMergeReport,
) -> list[ResolvedShot]:
    """E2E-4: Bridge-Fill entfernt — Legacy-``bridge_*``-Slots werden verworfen."""
    del project, catalog, options, repairs, report
    ordered = sorted(
        list(timeline.shots),
        key=lambda s: (s.timeline_start_seconds, s.shot_id),
    )
    return [shot for shot in ordered if not is_bridge_shot(shot, unified=unified)]


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


def _is_manual_accepted(item: StockCandidate) -> bool:
    return (
        str(item.provider or "").strip().lower() == "manual"
        or str(getattr(item, "assign_status", "") or "").strip().lower() == "manual"
    )


def _export_ready_accepted_for_gap(
    project: Project, gap_id: str
) -> list[StockCandidate]:
    """export_ready Accepted-Supplements für eine Gap (Manual oder Funnel)."""
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    if accepted is None:
        return []
    out: list[StockCandidate] = []
    for item in accepted.supplements or []:
        if str(item.gap_id or "").strip() != gap_id:
            continue
        if str(item.media_validation_status or "").strip() != "export_ready":
            continue
        out.append(item)
    return out


def _rebuild_gap_merge_aggregates(report: GapMergeReport) -> GapMergeReport:
    """Leitet Listenfelder aus ``slots`` neu ab."""
    merged_shot_ids: list[str] = []
    kept_local_shot_ids: list[str] = []
    open_none_gap_ids: list[str] = []
    review_shot_ids: list[str] = []
    errors: list[str] = []
    for slot in report.slots or []:
        status = str(slot.status or "").strip().lower()
        sid = str(slot.shot_id or "").strip()
        gid = str(slot.coverage_gap_id or "").strip()
        if status == "merged" and sid:
            merged_shot_ids.append(sid)
        elif status == "kept_local_weak" and sid:
            kept_local_shot_ids.append(sid)
        elif status == "open_none" and gid:
            open_none_gap_ids.append(gid)
        if slot.review_flag and sid:
            review_shot_ids.append(sid)
        if status in {"failed", "open_none"} and slot.message:
            errors.append(f"{sid or gid}: {slot.message}")
    open_count = len(set(open_none_gap_ids))
    report.merged_shot_ids = list(dict.fromkeys(merged_shot_ids))
    report.kept_local_shot_ids = list(dict.fromkeys(kept_local_shot_ids))
    report.open_none_gap_ids = list(dict.fromkeys(open_none_gap_ids))
    report.review_shot_ids = list(dict.fromkeys(review_shot_ids))
    # Bewahre explizite Report-Errors, ergänze Slot-Errors.
    report.errors = list(dict.fromkeys(list(report.errors or []) + errors))
    report.message = (
        f"Merge: {len(report.merged_shot_ids)} ersetzt, "
        f"{len(report.kept_local_shot_ids)} weak behalten, "
        f"{open_count} none offen, "
        f"{len(report.review_shot_ids)} Review-Flags."
    )
    return report


def merge_gap_merge_reports(
    existing: GapMergeReport | None,
    incoming: GapMergeReport,
) -> GapMergeReport:
    """Kapitel-Merge in bestehenden globalen Report einpflegen.

    Gleiche ``cut_plan_run_id``: Slots anderer Gaps/Shots bleiben erhalten.
    Andere/fehlende Run-ID: Incoming ersetzt den Report.
    """
    incoming_run = str(incoming.cut_plan_run_id or "").strip()
    if existing is None:
        return _rebuild_gap_merge_aggregates(incoming.model_copy(deep=True))
    existing_run = str(existing.cut_plan_run_id or "").strip()
    if incoming_run and existing_run and incoming_run != existing_run:
        return _rebuild_gap_merge_aggregates(incoming.model_copy(deep=True))
    if incoming_run and not existing_run:
        return _rebuild_gap_merge_aggregates(incoming.model_copy(deep=True))

    touched_gaps = {
        str(slot.coverage_gap_id or "").strip()
        for slot in incoming.slots or []
        if str(slot.coverage_gap_id or "").strip()
    }
    touched_shots = {
        str(slot.shot_id or "").strip()
        for slot in incoming.slots or []
        if str(slot.shot_id or "").strip()
    }
    kept = [
        slot
        for slot in existing.slots or []
        if str(slot.coverage_gap_id or "").strip() not in touched_gaps
        and str(slot.shot_id or "").strip() not in touched_shots
    ]
    merged = GapMergeReport(
        schema_version=incoming.schema_version or existing.schema_version,
        script_version=incoming.script_version or existing.script_version,
        cut_plan_run_id=incoming_run or existing_run,
        slots=kept + list(incoming.slots or []),
        repairs=list(
            dict.fromkeys(list(existing.repairs or []) + list(incoming.repairs or []))
        ),
        errors=list(incoming.errors or []),
    )
    return _rebuild_gap_merge_aggregates(merged)


def _persist_gap_merge_report(project: Project, report: GapMergeReport) -> None:
    """Schreibt Gap-Merge-Report — bei Kapitel-Läufen mit bestehendem Report mergen."""
    existing = load_model(gap_merge_report_path(project), GapMergeReport)
    merged = merge_gap_merge_reports(existing, report)
    write_json(gap_merge_report_path(project), merged)


def _write_merge_rejection_to_funnel(
    project: Project,
    *,
    gap_id: str,
    rejected_candidate_ids: list[str],
    cut_plan_run_id: str,
    message: str,
) -> None:
    """E2E-4: Merge lehnt Gap ab → Funnel ggf. neu öffnen für Re-Ranking.

    Solange ein ``export_ready`` Accepted-Fill für die Gap existiert (Manual
    oder Funnel-Download), bleibt der Fill erhalten — Python Timing darf
    Zuordnungen nicht auf „offen“ zurücksetzen.
    """
    accepted_ready = _export_ready_accepted_for_gap(project, gap_id)
    has_accepted_fill = bool(accepted_ready)
    keep_ids = {
        str(item.candidate_id or "").strip()
        for item in accepted_ready
        if item.candidate_id
    }

    report = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    if report is None:
        return
    funnel_run = str(getattr(report, "cut_plan_run_id", "") or "").strip()
    if cut_plan_run_id and funnel_run and funnel_run != cut_plan_run_id:
        return
    rejected = [str(x).strip() for x in rejected_candidate_ids if str(x).strip()]
    # Aktuelle export_ready Accepted nie verwerfen.
    rejected = [cid for cid in rejected if cid and cid not in keep_ids]

    found = False
    for index, gap_rep in enumerate(report.gaps):
        if gap_rep.gap_id != gap_id:
            continue
        existing = {
            str(x).strip()
            for x in (gap_rep.rejected_candidate_ids or [])
            if str(x).strip()
        }
        existing.update(rejected)
        if has_accepted_fill:
            gap_rep.rejected_candidate_ids = sorted(existing)
            gap_rep.filled = True
            if not gap_rep.export_ready_candidate_id and accepted_ready:
                gap_rep.export_ready_candidate_id = str(
                    accepted_ready[0].candidate_id or ""
                ) or None
            note = "Accepted-Fill behalten (Timing/Merge)."
            prev = str(gap_rep.message or "").strip()
            gap_rep.message = f"{prev} — {note}".strip(" —") if prev else note
        else:
            gap_rep.filled = False
            gap_rep.export_ready_candidate_id = None
            gap_rep.review_ready_candidate_id = None
            gap_rep.rejected_candidate_ids = sorted(existing)
            gap_rep.message = message or "Merge: kein geeigneter Kandidat."
        report.gaps[index] = gap_rep
        found = True
        break
    if not found and not has_accepted_fill:
        report.gaps.append(
            SupplementFunnelGapReport(
                gap_id=gap_id,
                filled=False,
                rejected_candidate_ids=sorted(set(rejected)),
                message=message or "Merge: kein geeigneter Kandidat.",
            )
        )
    if has_accepted_fill:
        if gap_id not in (report.filled_gap_ids or []):
            report.filled_gap_ids = list(report.filled_gap_ids or []) + [gap_id]
        report.open_gap_ids = [g for g in (report.open_gap_ids or []) if g != gap_id]
    else:
        report.filled_gap_ids = [g for g in report.filled_gap_ids if g != gap_id]
        if gap_id not in report.open_gap_ids:
            report.open_gap_ids.append(gap_id)
    write_json(supplement_funnel_report_path(project), report)

    # Nur nicht-export_ready / nicht-keep Rejects aus Accepted entfernen.
    # export_ready Fills (Manual + Funnel) bleiben für Status/UI erhalten.
    if rejected:
        accepted = load_model(
            accepted_supplements_path(project), AcceptedSupplementsDocument
        )
        if accepted is not None:
            reject_set = set(rejected)
            kept = [
                s
                for s in accepted.supplements
                if _is_manual_accepted(s)
                or str(s.candidate_id or "").strip() in keep_ids
                or s.candidate_id not in reject_set
                or (s.gap_id or "").strip() != gap_id
            ]
            if len(kept) != len(accepted.supplements):
                write_json(
                    accepted_supplements_path(project),
                    AcceptedSupplementsDocument(
                        schema_version=accepted.schema_version,
                        script_version=accepted.script_version,
                        supplements=kept,
                    ),
                )


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
        # Redaktion hat schwaches Supplement bewusst freigegeben.
        if (
            bucket == "weak"
            and str(getattr(candidate, "assign_status", "") or "").strip().lower()
            == "confirmed_weak"
        ):
            bucket = "manual"
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


def _candidate_media_entry(candidate: StockCandidate) -> dict | None:
    """Katalog-Eintrag für ein Accepted-/Funnel-Supplement mit lokaler Datei."""
    media_path = Path(str(candidate.local_media_path or "")).expanduser()
    if not media_path.is_file():
        return None
    media_type = str(candidate.media_type or "").strip().lower()
    if not media_type:
        media_type = "photo" if is_image_media(media_path) else "video"
    kind = "image" if media_type in {"photo", "image"} else "video"
    duration = candidate.duration_seconds
    if duration is None and kind == "video":
        try:
            duration = probe_duration_seconds(media_path)
        except Exception:  # noqa: BLE001
            duration = None
    return {
        "path": str(media_path),
        "canonical_id": str(candidate.candidate_id or ""),
        "duration_seconds": float(duration) if duration is not None else None,
        "media_kind": kind,
        "media_type": media_type or kind,
        "folder": "",
        "available_start_seconds": 0.0,
        "usable_in_s": None,
    }


def _best_placeable_gap_fill(
    candidates: list[StockCandidate],
    accepted_ready: list[StockCandidate],
) -> StockCandidate | None:
    """Längstes platzierbares Fill (Datei vorhanden), unabhängig von min_duration."""
    pool: list[StockCandidate] = []
    seen: set[str] = set()
    for candidate in list(candidates) + list(accepted_ready):
        cid = str(candidate.candidate_id or "").strip()
        if not cid or cid in seen:
            continue
        if _candidate_media_entry(candidate) is None:
            continue
        seen.add(cid)
        pool.append(candidate)
    if not pool:
        return None

    def _dur(item: StockCandidate) -> float:
        entry = _candidate_media_entry(item) or {}
        raw = entry.get("duration_seconds")
        if raw is None:
            # Stills / unbekannte Dauer: hinter Videos, aber platzierbar.
            return 0.0 if str(entry.get("media_kind") or "") == "video" else 1e9
        return float(raw)

    return max(pool, key=_dur)


def _gap_fill_reuse_violation(
    asset_id: str,
    *,
    provisional_shots: list[ResolvedShot],
    gap_shot_id: str,
    max_asset_usage: int,
    min_asset_reuse_distance_shots: int,
    reuse_key_index: Mapping[str, str] | None = None,
) -> str | None:
    """Cut-Plan-Reuse beim Gap-Fill: Nachbar, Abstand, max_usage.

    ``provisional_shots`` ist die redaktionelle Sequenz inkl. des Gap-Shots
    (noch offen/leer). Intro-Ordner sind ausgenommen — wie im Timeline-Resolver.
    """
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        reuse_identity_key,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _is_intro_folder,
    )

    aid = str(asset_id or "").strip()
    if not aid:
        return None
    key = reuse_identity_key(aid, index=reuse_key_index)
    if not key:
        return None

    min_gap = max(1, int(min_asset_reuse_distance_shots or 0))
    max_usage = max(1, int(max_asset_usage or 1))

    gap_index = next(
        (
            index
            for index, shot in enumerate(provisional_shots)
            if str(shot.shot_id) == gap_shot_id
        ),
        None,
    )
    if gap_index is None:
        return None

    usage = 0
    last_before: int | None = None
    next_after: int | None = None
    for index, shot in enumerate(provisional_shots):
        if index == gap_index:
            continue
        other_id = str(shot.asset_id or "").strip()
        if shot.open_gap or not other_id:
            continue
        folder = str(shot.folder_name or "")
        if _is_intro_folder(folder):
            continue
        other_key = reuse_identity_key(other_id, index=reuse_key_index)
        if other_key != key:
            continue
        usage += 1
        if index < gap_index:
            last_before = index
        elif next_after is None:
            next_after = index

    if usage + 1 > max_usage:
        return (
            f"Asset {aid} überschreitet max_asset_usage={max_usage} "
            "(Gap-Merge übersprungen)."
        )

    if last_before is not None:
        gap_shots = gap_index - int(last_before) - 1
        if gap_shots < min_gap:
            if gap_shots == 0:
                return (
                    f"Benachbartes Asset {aid} bereits in "
                    f"{provisional_shots[last_before].shot_id} "
                    "(Gap-Merge übersprungen)."
                )
            return (
                f"Asset {aid} erneut nach {gap_shots} Shots "
                f"(min Abstand {min_gap}) — Gap-Merge übersprungen."
            )

    if next_after is not None:
        gap_shots = int(next_after) - gap_index - 1
        if gap_shots < min_gap:
            if gap_shots == 0:
                return (
                    f"Benachbartes Asset {aid} bereits in "
                    f"{provisional_shots[next_after].shot_id} "
                    "(Gap-Merge übersprungen)."
                )
            return (
                f"Asset {aid} vor Wiederverwendung in "
                f"{provisional_shots[next_after].shot_id} nur {gap_shots} Shots "
                f"Abstand (min {min_gap}) — Gap-Merge übersprungen."
            )
    return None


def _filter_candidates_by_cut_plan_reuse(
    candidates: list[StockCandidate],
    *,
    provisional_shots: list[ResolvedShot],
    gap_shot_id: str,
    max_asset_usage: int,
    min_asset_reuse_distance_shots: int,
    reuse_key_index: Mapping[str, str] | None = None,
) -> tuple[list[StockCandidate], list[str]]:
    """Entfernt Kandidaten, die Nachbar-/Reuse-Regeln verletzen."""
    kept: list[StockCandidate] = []
    rejected: list[str] = []
    for candidate in candidates:
        reason = _gap_fill_reuse_violation(
            str(candidate.candidate_id or ""),
            provisional_shots=provisional_shots,
            gap_shot_id=gap_shot_id,
            max_asset_usage=max_asset_usage,
            min_asset_reuse_distance_shots=min_asset_reuse_distance_shots,
            reuse_key_index=reuse_key_index,
        )
        if reason:
            rejected.append(reason)
            continue
        kept.append(candidate)
    return kept, rejected


def _place_short_gap_fill_with_shortfall(
    project: Project,
    shot: ResolvedShot,
    candidate: StockCandidate,
    *,
    gap_id: str,
    fps: float,
    head_trim: float,
    short_tolerance: float,
    repairs: list[str],
) -> list[ResolvedShot] | None:
    """Zu kurzes Accepted-/Funnel-Fill: Asset + roter Shortfall-Tail."""
    entry = _candidate_media_entry(candidate)
    if entry is None:
        return None
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        TimedSlot,
        _short_asset_with_red_placeholder_tail,
    )

    timed = TimedSlot(
        slot_id=str(shot.shot_id),
        start_seconds=float(shot.timeline_start_seconds),
        end_seconds=float(shot.timeline_end_seconds),
        start_boundary_id="",
        end_boundary_id="",
        cut_alignment=str(shot.cut_alignment or ""),
        asset_id=str(candidate.candidate_id or ""),
        asset_fit="acceptable",
        asset_fit_reason=(
            f"gap_merge short fill ← {candidate.candidate_id} "
            "(Rest als roter Shortfall)"
        ),
        coverage_gap_id=gap_id,
        narrative_function=str(shot.editorial_function or ""),
        source_range_intent="",
        visual_intent="",
        needed_visual="",
    )
    try:
        parts = _short_asset_with_red_placeholder_tail(
            project,
            timed,
            entry=entry,
            asset_id=str(candidate.candidate_id or ""),
            fps=fps,
            head_trim=head_trim,
            short_tolerance=short_tolerance,
            repairs=repairs,
        )
    except Exception:  # noqa: BLE001 — Merge soft
        return None
    if not parts:
        return None
    for part in parts:
        part.chapter_id = shot.chapter_id
        part.folder_name = shot.folder_name or part.folder_name
        part.cut_alignment = shot.cut_alignment
        if str(part.shot_id).endswith("__shortfall"):
            part.open_gap = True
            part.asset_fit = "weak"
        else:
            part.open_gap = False
            if not str(part.asset_fit or "").strip():
                part.asset_fit = "acceptable"
    return parts


def merge_export_ready_gaps_into_timeline(
    project: Project,
    *,
    timeline: ResolvedTimelineDocument | None = None,
    require_closed_none: bool = False,
    persist: bool = True,
    unified: UnifiedCutPlanDocument | None = None,
    persist_report: bool | None = None,
) -> tuple[ResolvedTimelineDocument, GapMergeReport]:
    """Ersetzt Gap-Assets deterministisch; Timeline-Zeiten bleiben fix.

    ``require_closed_none=False``: Preview/Merge-Zwischenstand erlaubt offene none.
    ``True``: offene none → ``GapMergeError`` (Produktions-Gate).
    ``unified``: optional Kapitel-/Intro-Plan (sonst globaler Unified-Plan).
    ``persist_report``: Default = ``persist``; bei Kapitel-Timing Report schreiben,
    Timeline aber nur in chapter_resolved speichern.
    """
    locked = require_locked_script(project)
    if timeline is None:
        timeline = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if timeline is None:
        raise GapMergeError("Resolved Timeline fehlt.")

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if unified is None:
        unified = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if persist_report is None:
        persist_report = persist
    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    options = load_cut_plan_options(project)
    ready = list_export_ready_supplements(project)
    records_by_gap = _funnel_records_by_gap(funnel)
    catalog = build_asset_catalog(project, fps=float(timeline.fps or project.fps))
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        build_asset_reuse_key_index,
    )

    reuse_key_index = build_asset_reuse_key_index(project)
    max_asset_usage = int(options.max_asset_usage)
    min_asset_reuse_distance_shots = int(options.min_asset_reuse_distance_shots)

    cut_plan_run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if not cut_plan_run_id and unified is not None:
        from otio_app.services.without_voiceover_enhanced.gap_status_service import (
            compute_cut_plan_run_id,
        )

        cut_plan_run_id = compute_cut_plan_run_id(unified)
    report = GapMergeReport(
        script_version=locked.script_version,
        cut_plan_run_id=cut_plan_run_id,
    )
    fps = float(timeline.fps or project.fps)
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))
    repairs = list(timeline.repairs or [])

    # E2E-4: keine Bridge-Slots mehr — Kapitelwechsel = Nachlauf→Vorlauf.
    working = timeline.model_copy(deep=True)
    working.shots = [
        s for s in timeline.shots if not is_bridge_shot(s, unified=unified)
    ]

    updated_shots: list[ResolvedShot] = []
    for shot_pos, shot in enumerate(working.shots):
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

        provisional_shots = updated_shots + list(working.shots[shot_pos:])

        local_fit = _local_fit_for_shot(shot, unified=unified, coverage=coverage)
        target = _target_duration_for_shot(shot, coverage=coverage)
        min_duration = required_candidate_duration_seconds(
            target,
            head_trim=head_trim,
            short_tolerance=short_tolerance,
        )
        candidates = _candidates_for_gap(ready, gap_id)
        candidates, reuse_rejects = _filter_candidates_by_cut_plan_reuse(
            candidates,
            provisional_shots=provisional_shots,
            gap_shot_id=shot.shot_id,
            max_asset_usage=max_asset_usage,
            min_asset_reuse_distance_shots=min_asset_reuse_distance_shots,
            reuse_key_index=reuse_key_index,
        )
        chosen, bucket, pick_msg, review = _pick_supplement(
            candidates,
            records=records_by_gap.get(gap_id, []),
            local_fit=local_fit,
            min_duration=min_duration,
        )
        if chosen is None and reuse_rejects:
            pick_msg = (
                f"{pick_msg} Reuse-Filter: {reuse_rejects[0]}"
                if pick_msg
                else f"Reuse-Filter: {reuse_rejects[0]}"
            )

        if chosen is None:
            gap_confirmed_weak = False
            if coverage is not None:
                for cov_gap in coverage.gaps or []:
                    if (cov_gap.gap_id or "").strip() != gap_id:
                        continue
                    gap_confirmed_weak = bool(
                        getattr(cov_gap, "user_confirmed_weak", False)
                    )
                    break
            if local_fit == "weak" and shot.asset_id and (
                not shot.open_gap or gap_confirmed_weak
            ):
                result = GapMergeSlotResult(
                    shot_id=shot.shot_id,
                    coverage_gap_id=gap_id,
                    status="kept_local_weak",
                    previous_asset_id=shot.asset_id,
                    new_asset_id=shot.asset_id,
                    local_fit=local_fit,
                    message=(
                        "Weak-Asset redaktionell bestätigt — behalten."
                        if gap_confirmed_weak
                        else f"Upgrade übersprungen — {pick_msg}"
                    ),
                )
                report.slots.append(result)
                report.kept_local_shot_ids.append(shot.shot_id)
                updated_shots.append(shot)
                continue
            # Roter Dauer-Shortfall ohne Supplement: erwarteter Zwischenstand,
            # kein harter Timeline-Fehler (Repair reicht).
            if str(shot.shot_id).endswith("__shortfall"):
                repairs.append(
                    f"{shot.shot_id}: Shortfall offen — {pick_msg}"
                )
                updated_shots.append(shot)
                continue
            accepted_ready = (
                _export_ready_accepted_for_gap(project, gap_id)
                if local_fit == "none"
                else []
            )
            if local_fit == "none":
                accepted_ready, accepted_reuse_rejects = (
                    _filter_candidates_by_cut_plan_reuse(
                        accepted_ready,
                        provisional_shots=provisional_shots,
                        gap_shot_id=shot.shot_id,
                        max_asset_usage=max_asset_usage,
                        min_asset_reuse_distance_shots=min_asset_reuse_distance_shots,
                        reuse_key_index=reuse_key_index,
                    )
                )
                reuse_rejects = list(reuse_rejects) + list(accepted_reuse_rejects)
                # Zu kurze Accepted/Funnel-Fills trotzdem platzieren:
                # nutzbares Asset + roter Shortfall-Placeholder für den Rest.
                short_fill = _best_placeable_gap_fill(candidates, accepted_ready)
                if short_fill is not None:
                    short_parts = _place_short_gap_fill_with_shortfall(
                        project,
                        shot,
                        short_fill,
                        gap_id=gap_id,
                        fps=fps,
                        head_trim=head_trim,
                        short_tolerance=short_tolerance,
                        repairs=repairs,
                    )
                    if short_parts:
                        result = GapMergeSlotResult(
                            shot_id=shot.shot_id,
                            coverage_gap_id=gap_id,
                            status="merged",
                            previous_asset_id=shot.asset_id or "",
                            new_asset_id=str(short_fill.candidate_id or ""),
                            local_fit=local_fit,
                            supplement_fit_bucket="manual",
                            message=(
                                f"{pick_msg} — zu kurz platziert: "
                                f"{short_fill.candidate_id} + roter Shortfall"
                            ),
                        )
                        report.slots.append(result)
                        report.merged_shot_ids.append(shot.shot_id)
                        updated_shots.extend(short_parts)
                        continue

            if reuse_rejects and local_fit == "none":
                pick_msg = (
                    f"{pick_msg} Reuse-Filter: {reuse_rejects[0]}"
                    if pick_msg and "Reuse-Filter:" not in pick_msg
                    else (pick_msg or f"Reuse-Filter: {reuse_rejects[0]}")
                )
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
                # E2E-4: Funnel neu öffnen — sonst Deadlock (Funnel skip / Merge leer).
                _write_merge_rejection_to_funnel(
                    project,
                    gap_id=gap_id,
                    rejected_candidate_ids=[
                        c.candidate_id for c in candidates if c.candidate_id
                    ],
                    cut_plan_run_id=cut_plan_run_id,
                    message=pick_msg,
                )
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
            # Dauer knapp unter Bedarf → Asset behalten + roter Shortfall.
            short_parts = _place_short_gap_fill_with_shortfall(
                project,
                shot,
                chosen,
                gap_id=gap_id,
                fps=fps,
                head_trim=head_trim,
                short_tolerance=short_tolerance,
                repairs=repairs,
            )
            if short_parts:
                result = GapMergeSlotResult(
                    shot_id=shot.shot_id,
                    coverage_gap_id=gap_id,
                    status="merged",
                    previous_asset_id=shot.asset_id or "",
                    new_asset_id=str(chosen.candidate_id or ""),
                    local_fit=local_fit,
                    supplement_fit_bucket=bucket,
                    review_flag=review,
                    message=(
                        f"{pick_msg} — Resolve kurz: "
                        f"{chosen.candidate_id} + roter Shortfall ({exc})"
                    ),
                )
                report.slots.append(result)
                report.merged_shot_ids.append(shot.shot_id)
                updated_shots.extend(short_parts)
                continue
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
    if persist_report:
        _persist_gap_merge_report(project, report)

    if require_closed_none and report.open_none_gap_ids:
        raise GapMergeError(
            "Offene none-Gaps nach Merge: "
            + ", ".join(sorted(set(report.open_none_gap_ids)))
        )
    return merged_timeline, report
