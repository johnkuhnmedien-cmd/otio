"""Shortfalls nachvollziehbar machen: welches Video, welche Länge, welche Datei."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from otio_app.models import Project
from otio_app.services.media_utils import is_image_media, is_video_media
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    _canonical_plan_shot_id,
)


@dataclass(frozen=True)
class ShortfallInspectRow:
    folder_name: str
    slot_id: str
    shortfall_shot_id: str
    asset_id: str
    media_path: str
    filename: str
    media_exists: bool
    is_image: bool
    is_video: bool
    usable_seconds: float | None
    need_seconds: float | None
    shortfall_seconds: float | None
    gap_id: str
    reason: str
    source: str  # "timing" | "plan"


def format_shortfall_inspect_label(row: ShortfallInspectRow) -> str:
    """Eine Zeile für Fehler/UI: Slot, Asset, Datei, nutzbar vs. nötig."""
    asset = (row.asset_id or "").strip() or "kein Asset"
    file_bit = f" · {row.filename}" if row.filename else ""
    if row.usable_seconds is not None and row.need_seconds is not None:
        dur_bit = (
            f" — nutzbar {row.usable_seconds:.1f}s, "
            f"Slot braucht {row.need_seconds:.1f}s"
        )
    elif row.shortfall_seconds is not None:
        dur_bit = f" — es fehlen {row.shortfall_seconds:.1f}s"
    else:
        dur_bit = ""
    return f"{row.slot_id}: {asset}{file_bit}{dur_bit}"


def _shot_span_seconds(shot: ResolvedShot) -> float:
    return max(
        0.0,
        float(shot.timeline_end_seconds) - float(shot.timeline_start_seconds),
    )


def _existing_media_path(raw: str, project: Project | None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        return path
    if project is None or path.is_absolute():
        return None
    for root in (project.project_root_path, project.work_dir_path):
        candidate = Path(root) / path
        if candidate.is_file():
            return candidate
    return None


def _path_from_catalog_entry(entry: Mapping[str, Any] | None) -> str:
    if not entry:
        return ""
    return str(entry.get("path") or entry.get("file") or "").strip()


def _lookup_catalog_path(
    project: Project | None,
    asset_id: str,
    *,
    folder_name: str = "",
    catalog: Any | None = None,
) -> tuple[str, Any]:
    """``(path, catalog)`` — Katalog wird bei Bedarf einmal gebaut."""
    aid = str(asset_id or "").strip()
    if not aid or project is None:
        return "", catalog
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        build_asset_catalog,
        lookup_catalog_entry,
    )

    if catalog is None:
        folders = [folder_name] if folder_name else None
        catalog = build_asset_catalog(
            project,
            fps=float(project.fps or 25.0),
            folder_names=folders,
        )
    entry, _err = lookup_catalog_entry(catalog, aid)
    return _path_from_catalog_entry(entry), catalog


def _blocking_placeholder(shot: ResolvedShot) -> bool:
    return bool(getattr(shot, "is_placeholder", False)) or bool(
        getattr(shot, "open_gap", False)
    )


def collect_shortfall_rows_from_resolved(
    resolved: ResolvedTimelineDocument | None,
    *,
    folder_name: str = "",
    project: Project | None = None,
) -> list[ShortfallInspectRow]:
    """Placeholder/Shortfall-Shots inkl. Parent-Video und gemessener Dauer."""
    if resolved is None:
        return []
    by_id = {str(shot.shot_id): shot for shot in resolved.shots or []}
    catalog = None
    rows: list[ShortfallInspectRow] = []
    for shot in resolved.shots or []:
        if not _blocking_placeholder(shot):
            continue
        shot_id = str(shot.shot_id or "")
        parent_id = _canonical_plan_shot_id(shot_id)
        parent = by_id.get(parent_id)
        is_tail = shot_id.endswith("__shortfall")
        slot_id = parent_id or shot_id
        asset_id = str(
            (parent.asset_id if parent is not None else "") or shot.asset_id or ""
        ).strip()
        media_raw = ""
        if parent is not None and not _blocking_placeholder(parent):
            media_raw = str(parent.resolved_media_path or "").strip()
        if not media_raw and not _blocking_placeholder(shot):
            media_raw = str(shot.resolved_media_path or "").strip()
        if not media_raw and asset_id:
            media_raw, catalog = _lookup_catalog_path(
                project, asset_id, folder_name=folder_name, catalog=catalog
            )
        existing = _existing_media_path(media_raw, project)
        path_text = str(existing) if existing is not None else media_raw
        usable: float | None = None
        need: float | None = None
        missing: float | None = None
        if is_tail and parent is not None:
            usable = _shot_span_seconds(parent)
            missing = _shot_span_seconds(shot)
            need = usable + missing
        else:
            missing = _shot_span_seconds(shot)
            need = missing
            if parent is not None and parent is not shot and not _blocking_placeholder(
                parent
            ):
                usable = _shot_span_seconds(parent)
                need = usable + missing
        reason = str(getattr(shot, "asset_fit_reason", "") or "").strip()
        if not reason and parent is not None:
            reason = str(getattr(parent, "asset_fit_reason", "") or "").strip()
        if not reason:
            reason = "Placeholder / offener Gap"
        filename = Path(path_text).name if path_text else ""
        rows.append(
            ShortfallInspectRow(
                folder_name=folder_name
                or str(shot.folder_name or shot.chapter_id or "").strip(),
                slot_id=slot_id,
                shortfall_shot_id=shot_id,
                asset_id=asset_id,
                media_path=path_text,
                filename=filename,
                media_exists=existing is not None,
                is_image=bool(existing and is_image_media(existing)),
                is_video=bool(existing and is_video_media(existing)),
                usable_seconds=usable,
                need_seconds=need,
                shortfall_seconds=missing,
                gap_id=str(shot.coverage_gap_id or "").strip(),
                reason=reason,
                source="timing",
            )
        )
    return rows


def collect_shortfall_rows_from_plan(
    project: Project,
    folder_name: str,
    plan: UnifiedCutPlanDocument | None,
) -> list[ShortfallInspectRow]:
    """Falls noch kein Timing: zu kurze Motion-Zuweisungen aus dem LLM-Plan."""
    if plan is None or not plan.slots:
        return []
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        load_cut_plan_options,
    )
    from otio_app.services.without_voiceover_enhanced.cut_slot_duration_guard import (
        catalog_from_prompt_assets,
        collect_too_short_for_chapter_cut,
        chapter_segment_offsets,
    )
    from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
        load_segment_timings,
    )
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        is_intro_folder_name,
    )
    from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
        load_segment_alignments,
    )
    from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
        sentence_index_by_id,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        build_asset_catalog,
        lookup_catalog_entry,
    )

    options = load_cut_plan_options(project)
    sentence_index = sentence_index_by_id(load_segment_alignments(project))
    if not sentence_index:
        return []
    timings = load_segment_timings(project)
    segments = list(timings.segments) if timings is not None else []
    catalog = build_asset_catalog(
        project,
        fps=float(project.fps or 25.0),
        folder_names=[folder_name] if folder_name else None,
    )
    prompt_rows: list[dict[str, Any]] = []
    for asset_id, entry in (catalog.by_id or {}).items():
        row = dict(entry)
        row.setdefault("local_asset_id", asset_id)
        row.setdefault("asset_id", asset_id)
        prompt_rows.append(row)
    hits = collect_too_short_for_chapter_cut(
        plan,
        catalog_from_prompt_assets(prompt_rows),
        options=options,
        sentence_index=sentence_index,
        segment_offsets=chapter_segment_offsets(segments),
        is_intro=is_intro_folder_name(folder_name),
    )
    rows: list[ShortfallInspectRow] = []
    for hit in hits:
        if hit.slot_id in {"intro_opener_asset_id", "intro_closing_asset_id"}:
            continue
        entry, _err = lookup_catalog_entry(catalog, hit.asset_id)
        media_raw = _path_from_catalog_entry(entry)
        existing = _existing_media_path(media_raw, project)
        path_text = str(existing) if existing is not None else media_raw
        missing = max(0.0, float(hit.need_seconds) - float(hit.planning_usable))
        rows.append(
            ShortfallInspectRow(
                folder_name=folder_name,
                slot_id=hit.slot_id,
                shortfall_shot_id=hit.slot_id,
                asset_id=hit.asset_id,
                media_path=path_text,
                filename=Path(path_text).name if path_text else "",
                media_exists=existing is not None,
                is_image=bool(existing and is_image_media(existing)),
                is_video=bool(existing and is_video_media(existing)),
                usable_seconds=float(hit.planning_usable),
                need_seconds=float(hit.need_seconds),
                shortfall_seconds=missing,
                gap_id="",
                reason=hit.reason,
                source="plan",
            )
        )
    return rows


def collect_shortfall_inspect_rows(
    project: Project,
    folder_name: str,
    *,
    resolved: ResolvedTimelineDocument | None = None,
    plan: UnifiedCutPlanDocument | None = None,
) -> list[ShortfallInspectRow]:
    """Timing-Shortfalls bevorzugen; sonst Schätzung aus dem LLM-Plan."""
    rows = collect_shortfall_rows_from_resolved(
        resolved, folder_name=folder_name, project=project
    )
    if rows:
        return rows
    return collect_shortfall_rows_from_plan(project, folder_name, plan)


def production_blocking_placeholder_labels(
    resolved: ResolvedTimelineDocument | None,
    *,
    folder_name: str = "",
    project: Project | None = None,
) -> list[str]:
    """Shots, die Produktions-OTIO sperren — mit Asset und Dauer, nicht nur Slot-ID."""
    rows = collect_shortfall_rows_from_resolved(
        resolved, folder_name=folder_name, project=project
    )
    if rows:
        return [format_shortfall_inspect_label(row) for row in rows]
    if resolved is None:
        return []
    labels: list[str] = []
    for shot in resolved.shots or []:
        if not _blocking_placeholder(shot):
            continue
        gap = str(getattr(shot, "coverage_gap_id", "") or "").strip() or "—"
        asset = str(shot.asset_id or "").strip()
        extra = f" · {asset}" if asset else ""
        labels.append(f"{shot.shot_id}{extra} ({gap})")
    return labels
