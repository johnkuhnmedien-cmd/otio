"""Manuelle Zuordnung lokaler Dateien zu offenen Coverage Gaps.

Kopiert die Datei nach ``stock/downloads/<gap>/<candidate>/``, setzt
``export_ready`` in Accepted Supplements und inventarisiert mit Gap-Beschreibung.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.media_utils import is_image_media
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    list_export_ready_supplements,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    stock_candidate_download_dir,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
    _folder_for_gap,
    _import_into_inventory,
)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}


class ManualGapAssignError(RuntimeError):
    pass


def gap_search_queries(gap: CoverageGap) -> list[str]:
    """Search-Queries für UI-Kopie (concepts + queries, dedupliziert)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(gap.search_concepts or []) + list(gap.search_queries or []):
        query = str(raw).strip()
        if not query or query in seen:
            continue
        seen.add(query)
        out.append(query)
    if out:
        return out
    fallback = (
        (gap.needed_visual or "").strip()
        or (gap.subject or "").strip()
        or (gap.editorial_purpose or "").strip()
    )
    return [fallback] if fallback else []


def list_open_gaps_for_manual_assign(project: Project) -> list[CoverageGap]:
    """Gaps ohne export_ready Accepted-Supplement (Coverage-Reihenfolge)."""
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return []
    ready_ids = {
        (supplement.gap_id or "").strip()
        for supplement in list_export_ready_supplements(project)
        if (supplement.gap_id or "").strip()
    }
    return [gap for gap in coverage.gaps if gap.gap_id not in ready_ids]


def _detect_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    if suffix in _IMAGE_SUFFIXES:
        return "photo"
    try:
        return "photo" if is_image_media(path) else "video"
    except Exception:  # noqa: BLE001
        return "photo"


def _candidate_id_for_file(gap_id: str, source: Path) -> str:
    digest = hashlib.sha1(
        f"{gap_id}:{source.resolve()}".encode("utf-8")
    ).hexdigest()[:10]
    stem = safe_folder_slug(source.stem)[:40] or "file"
    return f"manual_{stem}_{digest}"


def _current_cut_plan_run_id(project: Project) -> str:
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        compute_cut_plan_run_id_from_path,
    )
    from otio_app.services.without_voiceover_enhanced.paths import unified_cut_plan_path

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if run_id:
        return run_id
    return compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))


def _upsert_accepted(project: Project, candidate: StockCandidate) -> None:
    locked = require_locked_script(project)
    run_id = _current_cut_plan_run_id(project)
    if run_id:
        candidate.cut_plan_run_id = run_id
    existing = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    supplements = list(existing.supplements) if existing else []
    # Pro Gap nur ein Accepted-Eintrag (manuelle Neuzuordnung ersetzt).
    gap_id = (candidate.gap_id or "").strip()
    supplements = [
        supplement
        for supplement in supplements
        if supplement.candidate_id != candidate.candidate_id
        and (not gap_id or (supplement.gap_id or "") != gap_id)
    ]
    supplements.append(candidate)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version=locked.script_version,
            supplements=supplements,
        ),
    )


def _mark_gap_filled_in_funnel_report(
    project: Project, *, gap_id: str, candidate_id: str
) -> None:
    report = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    if report is None:
        return
    found = False
    for index, gap_rep in enumerate(report.gaps):
        if gap_rep.gap_id != gap_id:
            continue
        gap_rep.filled = True
        gap_rep.export_ready_candidate_id = candidate_id
        gap_rep.message = f"export_ready: {candidate_id} (manuell zugeordnet)"
        report.gaps[index] = gap_rep
        found = True
        break
    if not found:
        report.gaps.append(
            SupplementFunnelGapReport(
                gap_id=gap_id,
                filled=True,
                export_ready_candidate_id=candidate_id,
                message=f"export_ready: {candidate_id} (manuell zugeordnet)",
            )
        )
    if gap_id not in report.filled_gap_ids:
        report.filled_gap_ids.append(gap_id)
    report.open_gap_ids = [gid for gid in report.open_gap_ids if gid != gap_id]
    write_json(supplement_funnel_report_path(project), report)


def assign_local_file_to_open_gap(
    project: Project,
    *,
    gap_id: str,
    source_path: str,
) -> StockCandidate:
    """Kopiert lokale Datei, validiert, Accepted + Inventar für offene Gap."""
    gap_id = (gap_id or "").strip()
    if not gap_id:
        raise ManualGapAssignError("Gap-ID fehlt.")

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None:
        raise ManualGapAssignError("Keine Coverage Gaps vorhanden.")
    gap = next((item for item in coverage.gaps if item.gap_id == gap_id), None)
    if gap is None:
        raise ManualGapAssignError(f"Unbekannte Gap-ID: {gap_id}")

    ready_ids = {
        (supplement.gap_id or "").strip()
        for supplement in list_export_ready_supplements(project)
        if (supplement.gap_id or "").strip()
    }
    if gap_id in ready_ids:
        raise ManualGapAssignError(
            f"Gap `{gap_id}` ist bereits export_ready — zuerst nicht nötig."
        )

    raw = (source_path or "").strip().strip('"').strip("'")
    if not raw:
        raise ManualGapAssignError("Kein Dateipfad angegeben.")
    if raw.startswith(("http://", "https://")):
        raise ManualGapAssignError("Nur lokale Dateipfade — keine http(s)-URL.")
    source = Path(raw).expanduser()
    if not source.is_file():
        raise ManualGapAssignError(f"Datei nicht gefunden: {source}")

    media_type = _detect_media_type(source)
    status, error = validate_local_media_path(str(source), media_type)
    if status != STATUS_EXPORT_READY:
        raise ManualGapAssignError(
            error or f"Datei technisch ungültig ({status})."
        )

    locked = require_locked_script(project)
    candidate_id = _candidate_id_for_file(gap_id, source)
    target_dir = stock_candidate_download_dir(
        project, gap_id=gap_id, candidate_id=candidate_id
    )
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_folder_slug(candidate_id)}{source.suffix.lower()}"
    shutil.copy2(source, target)

    status2, error2 = validate_local_media_path(str(target), media_type)
    if status2 != STATUS_EXPORT_READY:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise ManualGapAssignError(
            error2 or f"Kopie technisch ungültig ({status2})."
        )

    description = (
        (gap.needed_visual or "").strip()
        or (gap.subject or "").strip()
        or (gap.editorial_purpose or "").strip()
        or gap_id
    )
    candidate = StockCandidate(
        candidate_id=candidate_id,
        provider="manual",
        provider_asset_id=source.name,
        title=description[:120],
        media_type=media_type,
        creator="manual",
        source_page=str(source),
        download_url="",
        preview_url="",
        license="manual_local",
        attribution="Manuell zugeordnet",
        selected=True,
        gap_id=gap_id,
        local_media_path=str(target),
        media_validation_status=STATUS_EXPORT_READY,
        media_validation_error=None,
        funnel_managed=True,
        license_metadata_status="missing",
        cut_plan_run_id=_current_cut_plan_run_id(project),
    )
    folder = _folder_for_gap(project, gap, locked)
    from otio_app.services.without_voiceover_enhanced.supplement_clean_media import (
        ensure_new_supplement_clean_media,
    )

    cleaned = ensure_new_supplement_clean_media(
        project, folder_name=folder, media_path=target
    )
    candidate.local_media_path = str(cleaned)
    _upsert_accepted(project, candidate)
    _import_into_inventory(
        project,
        folder_name=folder,
        candidate=candidate,
        media_path=cleaned,
        frames=[],
        description=description,
        validation_status="PASS",
        validation_score=1.0,
    )
    _mark_gap_filled_in_funnel_report(
        project, gap_id=gap_id, candidate_id=candidate_id
    )
    return candidate
