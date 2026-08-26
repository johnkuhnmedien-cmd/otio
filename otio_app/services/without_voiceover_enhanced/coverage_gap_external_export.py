"""Immer aktuelle Coverage-Gaps-JSON für externe Such-/Download-Apps.

Schreibt ``coverage/coverage_gaps_external.json`` mit Gap-ID, Suchbegriffen
und dem Drop-Ordner, in den die externe App Medien legen muss. Dateien im
Inbox werden vor Python Timing automatisch als Gap-Fill übernommen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otio_app.defaults import CLEAN_MEDIA_OUTPUT_SUBDIR
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path, safe_folder_slug
from otio_app.services.supplement_inventory import INTAKE_SOURCE_INBOX
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapExternalEntry,
    CoverageGapExternalFilledAsset,
    CoverageGapExternalSavePaths,
    CoverageGapsDocument,
    CoverageGapsExternalDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gap_inbox_dir,
    coverage_gaps_external_path,
    coverage_gaps_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
)

_MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".mkv",
    ".avi",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".gif",
}

_DROP_NOTE = (
    "Medien-Datei (mp4/mov/jpg/png/…) in drop_dir ablegen. "
    "Beim nächsten Python Timing / Cut-Plan-Refresh wird sie "
    "automatisch für diesen Gap übernommen."
)


@dataclass
class CoverageGapInboxIngestResult:
    gap_id: str
    source_path: str
    candidate_id: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _rel_to_project(project: Project, path: Path) -> str:
    root = Path(project.project_root).expanduser().resolve()
    try:
        return str(path.expanduser().resolve().relative_to(root))
    except Exception:  # noqa: BLE001
        return str(path)


def _folder_for_gap(project: Project, gap: CoverageGap) -> str:
    from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
        _folder_for_gap as resolve_folder,
    )

    locked = load_locked_script(project)
    if locked is None:
        from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
            _folder_for_gap as concepts_folder,
        )

        return concepts_folder(gap, None) or (
            project.selected_asset_subdirs[0]
            if project.selected_asset_subdirs
            else ""
        )
    try:
        return resolve_folder(project, gap, locked)
    except Exception:  # noqa: BLE001
        from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
            _folder_for_gap as concepts_folder,
        )

        return concepts_folder(gap, locked) or (
            project.selected_asset_subdirs[0]
            if project.selected_asset_subdirs
            else ""
        )


def _slot_id_for_gap(gap: CoverageGap) -> str:
    related = [str(x).strip() for x in (gap.related_shot_ids or []) if str(x).strip()]
    if related:
        return related[0]
    gid = str(gap.gap_id or "").strip()
    if gid.startswith("gap_"):
        return gid[4:]
    return ""


def _filled_by_gap(
    project: Project, *, cut_plan_run_id: str
) -> dict[str, CoverageGapExternalFilledAsset]:
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    if accepted is None:
        return {}
    expected = (cut_plan_run_id or "").strip()
    out: dict[str, CoverageGapExternalFilledAsset] = {}
    for cand in accepted.supplements or []:
        gid = str(cand.gap_id or "").strip()
        if not gid:
            continue
        status = str(cand.media_validation_status or "").strip()
        if status != "export_ready":
            continue
        cand_run = str(getattr(cand, "cut_plan_run_id", "") or "").strip()
        # Gleicher Maßstab wie die Cut-Plan-UI (summarize_gap_status):
        # ohne passende Run-ID zählt der Fill nicht — sonst bleibt die
        # External-JSON auf filled, während die App 44 offen anzeigt.
        if expected:
            if not cand_run or cand_run != expected:
                continue
        out[gid] = CoverageGapExternalFilledAsset(
            candidate_id=str(cand.candidate_id or ""),
            local_media_path=str(cand.local_media_path or ""),
            media_type=str(cand.media_type or ""),
            provider=str(cand.provider or ""),
            cut_plan_run_id=cand_run,
        )
    return out


def _save_paths(project: Project, *, gap_id: str, folder_name: str) -> CoverageGapExternalSavePaths:
    drop = coverage_gap_inbox_dir(project, gap_id)
    drop.mkdir(parents=True, exist_ok=True)
    folder = (folder_name or "").strip() or (
        project.selected_asset_subdirs[0] if project.selected_asset_subdirs else "Assets"
    )
    slug = safe_folder_slug(folder)
    work = Path(project.work_dir).expanduser()
    inventory = get_folder_inventory_path(work, folder)
    clean = work / CLEAN_MEDIA_OUTPUT_SUBDIR / slug
    return CoverageGapExternalSavePaths(
        drop_dir=_rel_to_project(project, drop),
        drop_dir_absolute=str(drop.resolve()),
        inventory_path=_rel_to_project(project, inventory),
        inventory_path_absolute=str(inventory.resolve()),
        clean_dir=_rel_to_project(project, clean),
        clean_dir_absolute=str(clean.resolve()),
        note=_DROP_NOTE,
    )


def build_coverage_gaps_external_export(
    project: Project,
    coverage: CoverageGapsDocument | None = None,
) -> CoverageGapsExternalDocument:
    if coverage is None:
        coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    export_path = coverage_gaps_external_path(project)
    if coverage is None:
        return CoverageGapsExternalDocument(
            updated_at=datetime.now(timezone.utc).isoformat(),
            project_root=str(Path(project.project_root).expanduser().resolve()),
            work_dir=str(Path(project.work_dir).expanduser().resolve()),
            export_path=_rel_to_project(project, export_path),
            export_path_absolute=str(export_path.resolve()),
        )

    run_id = str(coverage.cut_plan_run_id or "").strip()
    filled_map = _filled_by_gap(project, cut_plan_run_id=run_id)
    entries: list[CoverageGapExternalEntry] = []
    open_count = 0
    filled_count = 0
    for gap in coverage.gaps or []:
        gid = str(gap.gap_id or "").strip()
        if not gid:
            continue
        folder = _folder_for_gap(project, gap)
        filled = filled_map.get(gid)
        confirmed_weak = bool(getattr(gap, "user_confirmed_weak", False))
        status = "filled" if (filled is not None or confirmed_weak) else "open"
        if status == "open":
            open_count += 1
        else:
            filled_count += 1
        concepts = [str(x).strip() for x in (gap.search_concepts or []) if str(x).strip()]
        queries = [str(x).strip() for x in (gap.search_queries or []) if str(x).strip()]
        if not queries and concepts:
            queries = list(concepts)
        entries.append(
            CoverageGapExternalEntry(
                gap_id=gid,
                status=status,
                folder_name=folder,
                folder_slug=safe_folder_slug(folder) if folder else "",
                slot_id=_slot_id_for_gap(gap),
                related_shot_ids=[
                    str(x).strip()
                    for x in (gap.related_shot_ids or [])
                    if str(x).strip()
                ],
                needed_visual=str(gap.needed_visual or ""),
                editorial_purpose=str(gap.editorial_purpose or ""),
                reason=str(gap.reason or ""),
                search_concepts=concepts,
                search_queries=queries,
                must_include=[
                    str(x).strip() for x in (gap.must_include or []) if str(x).strip()
                ],
                must_avoid=[
                    str(x).strip() for x in (gap.must_avoid or []) if str(x).strip()
                ],
                desired_motion=str(gap.desired_motion or ""),
                desired_framing=str(gap.desired_framing or ""),
                preferred_media_type=str(gap.preferred_media_type or "video"),
                target_duration_seconds=gap.target_duration_seconds,
                priority=str(gap.priority or "high"),
                covered_sentence_ids=[
                    str(x).strip()
                    for x in (gap.covered_sentence_ids or [])
                    if str(x).strip()
                ],
                save=_save_paths(project, gap_id=gid, folder_name=folder),
                filled_asset=filled,
            )
        )

    return CoverageGapsExternalDocument(
        updated_at=datetime.now(timezone.utc).isoformat(),
        script_version=str(coverage.script_version or ""),
        cut_plan_run_id=run_id,
        project_root=str(Path(project.project_root).expanduser().resolve()),
        work_dir=str(Path(project.work_dir).expanduser().resolve()),
        export_path=_rel_to_project(project, export_path),
        export_path_absolute=str(export_path.resolve()),
        open_count=open_count,
        filled_count=filled_count,
        gaps=entries,
    )


def refresh_coverage_gaps_external_export(
    project: Project,
    coverage: CoverageGapsDocument | None = None,
) -> CoverageGapsExternalDocument:
    """Baut die External-JSON neu und legt Inbox-Ordner für offene Gaps an."""
    from otio_app.services.without_voiceover_enhanced.paths import (
        assert_enhanced_work_root,
    )

    assert_enhanced_work_root(project)
    document = build_coverage_gaps_external_export(project, coverage=coverage)
    write_json(coverage_gaps_external_path(project), document)
    return document


def persist_coverage_gaps(
    project: Project,
    coverage: CoverageGapsDocument,
) -> CoverageGapsDocument:
    """Schreibt coverage_gaps.json und hält die External-JSON synchron."""
    from otio_app.services.without_voiceover_enhanced.paths import (
        assert_enhanced_work_root,
    )

    assert_enhanced_work_root(project)
    write_json(coverage_gaps_path(project), coverage)
    refresh_coverage_gaps_external_export(project, coverage=coverage)
    return coverage


def _first_media_in_dir(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    files = sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_file()
            and not child.name.startswith(".")
            and child.suffix.lower() in _MEDIA_SUFFIXES
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def ingest_coverage_gap_inbox(project: Project) -> list[CoverageGapInboxIngestResult]:
    """Übernimmt Dateien aus ``coverage/inbox/{gap_id}/`` als Manual-Fills."""
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return []

    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        summarize_gap_status,
    )
    from otio_app.services.without_voiceover_enhanced.manual_gap_assign_service import (
        ManualGapAssignError,
        assign_local_file_to_open_gap,
    )

    open_ids = set(summarize_gap_status(project).open_gap_ids)
    results: list[CoverageGapInboxIngestResult] = []
    for gap in coverage.gaps:
        gid = str(gap.gap_id or "").strip()
        if not gid or gid not in open_ids:
            continue
        inbox = coverage_gap_inbox_dir(project, gid)
        media = _first_media_in_dir(inbox)
        if media is None:
            continue
        try:
            assigned = assign_local_file_to_open_gap(
                project,
                gap_id=gid,
                source_path=str(media),
                intake_source=INTAKE_SOURCE_INBOX,
            )
            results.append(
                CoverageGapInboxIngestResult(
                    gap_id=gid,
                    source_path=str(media),
                    candidate_id=str(
                        getattr(assigned.candidate, "candidate_id", "") or ""
                    ),
                )
            )
        except ManualGapAssignError as exc:
            results.append(
                CoverageGapInboxIngestResult(
                    gap_id=gid,
                    source_path=str(media),
                    error=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                CoverageGapInboxIngestResult(
                    gap_id=gid,
                    source_path=str(media),
                    error=str(exc),
                )
            )

    if results:
        refresh_coverage_gaps_external_export(project)
    return results
