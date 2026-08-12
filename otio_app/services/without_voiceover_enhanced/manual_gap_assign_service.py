"""Manuelle Zuordnung lokaler Dateien oder Medien-URLs zu Coverage Gaps.

Kopiert/lädt die Datei nach ``stock/downloads/<gap>/<candidate>/``, setzt
``export_ready`` in Accepted Supplements und inventarisiert mit Gap-Beschreibung.

E2E-4 Nachtrag: Manual-Assign ist ein Override — ersetzt vorhandene Accepted-
Kandidaten desselben Gaps (kein Fehler bei bereits export_ready).
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

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
from otio_app.services.without_voiceover_enhanced.stock.safe_fetch import (
    ALLOWED_IMAGE_CONTENT_TYPES,
    MANUAL_URL_PROVIDER,
    SafeFetchError,
    decode_preview_image,
    fetch_full_media_bytes,
)
from otio_app.services.supplement_inventory import INTAKE_SOURCE_MANUAL
from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
    _folder_for_gap,
    _import_into_inventory,
)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


class ManualGapAssignError(RuntimeError):
    pass


@dataclass
class ManualGapAssignResult:
    """Ergebnis einer manuellen Gap-Zuordnung (ggf. mit Supersede-Hinweis)."""

    candidate: StockCandidate
    superseded_candidate_id: str | None = None

    @property
    def hint(self) -> str | None:
        if not self.superseded_candidate_id:
            return None
        return f"Ersetzt vorhandenen Kandidaten {self.superseded_candidate_id}."


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


def _candidate_id_for_url(gap_id: str, url: str) -> str:
    digest = hashlib.sha1(f"{gap_id}:{url.strip()}".encode("utf-8")).hexdigest()[:10]
    path = unquote(urlparse(url).path or "")
    stem = safe_folder_slug(Path(path).stem)[:40] or "url"
    return f"manual_{stem}_{digest}"


def _extension_for_download(url: str, content_type: str) -> str:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype in _CONTENT_TYPE_EXTENSIONS:
        return _CONTENT_TYPE_EXTENSIONS[ctype]
    path = unquote(urlparse(url).path or "")
    suffix = Path(path).suffix.lower()
    if suffix in _IMAGE_SUFFIXES or suffix in _VIDEO_SUFFIXES:
        return suffix
    if ctype.startswith("image/"):
        return ".jpg"
    if ctype.startswith("video/"):
        return ".mp4"
    return ".bin"


def _download_url_to_temp_file(url: str) -> tuple[Path, str]:
    """Lädt HTTPS-Medien-URL sicher herunter → (temp_path, final_url)."""
    try:
        fetched = fetch_full_media_bytes(url, provider=MANUAL_URL_PROVIDER)
    except SafeFetchError as exc:
        raise ManualGapAssignError(f"Download fehlgeschlagen: {exc}") from exc

    content_type = (fetched.content_type or "").split(";", 1)[0].strip().lower()
    if content_type in ALLOWED_IMAGE_CONTENT_TYPES or content_type.startswith("image/"):
        try:
            decode_preview_image(fetched.content)
        except SafeFetchError as exc:
            raise ManualGapAssignError(f"URL ist kein gültiges Bild: {exc}") from exc

    suffix = _extension_for_download(fetched.final_url or url, content_type)
    tmp = tempfile.NamedTemporaryFile(prefix="manual_gap_", suffix=suffix, delete=False)
    try:
        tmp.write(fetched.content)
        tmp.flush()
    finally:
        tmp.close()
    return Path(tmp.name), fetched.final_url or url


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


def _existing_accepted_for_gap(
    project: Project, gap_id: str
) -> list[StockCandidate]:
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is None:
        return []
    gid = (gap_id or "").strip()
    return [s for s in accepted.supplements if (s.gap_id or "").strip() == gid]


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
    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        refresh_coverage_gaps_external_export,
    )

    refresh_coverage_gaps_external_export(project)


def _mark_gap_filled_in_funnel_report(
    project: Project,
    *,
    gap_id: str,
    candidate_id: str,
    rejected_candidate_ids: list[str] | None = None,
) -> None:
    report = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    if report is None:
        return
    rejected = {
        str(x).strip()
        for x in (rejected_candidate_ids or [])
        if str(x).strip() and str(x).strip() != candidate_id
    }
    found = False
    for index, gap_rep in enumerate(report.gaps):
        if gap_rep.gap_id != gap_id:
            continue
        existing_rejected = {
            str(x).strip()
            for x in (gap_rep.rejected_candidate_ids or [])
            if str(x).strip()
        }
        existing_rejected.update(rejected)
        gap_rep.filled = True
        gap_rep.export_ready_candidate_id = candidate_id
        gap_rep.rejected_candidate_ids = sorted(existing_rejected)
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
                rejected_candidate_ids=sorted(rejected),
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
    intake_source: str = INTAKE_SOURCE_MANUAL,
) -> ManualGapAssignResult:
    """Kopiert lokale Datei oder lädt https-URL, validiert, Accepted + Inventar.

    E2E-4 Nachtrag: bewusste Redaktionsentscheidung — ersetzt vorhandene
    Accepted-Kandidaten desselben Gaps (kein Fehler bei export_ready).

    ``intake_source`` unterscheidet die Herkunft im Inventar (manuelle Zuweisung
    in der App vs. externe Recherche über die Inbox).
    """
    gap_id = (gap_id or "").strip()
    if not gap_id:
        raise ManualGapAssignError("Gap-ID fehlt.")

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None:
        raise ManualGapAssignError("Keine Coverage Gaps vorhanden.")
    gap = next((item for item in coverage.gaps if item.gap_id == gap_id), None)
    if gap is None:
        raise ManualGapAssignError(f"Unbekannte Gap-ID: {gap_id}")

    previous = _existing_accepted_for_gap(project, gap_id)
    superseded_ids = [
        str(s.candidate_id).strip()
        for s in previous
        if str(s.candidate_id or "").strip()
    ]
    # Primärer Hinweis: zuletzt vorhandener / export_ready Kandidat.
    superseded_hint_id = next(
        (
            str(s.candidate_id).strip()
            for s in previous
            if (s.media_validation_status or "") == STATUS_EXPORT_READY
            and str(s.candidate_id or "").strip()
        ),
        superseded_ids[0] if superseded_ids else None,
    )

    raw = (source_path or "").strip().strip('"').strip("'")
    if not raw:
        raise ManualGapAssignError("Kein Dateipfad oder Medien-URL angegeben.")

    downloaded_temp: Path | None = None
    download_url = ""
    source_label = raw
    try:
        if raw.startswith(("http://", "https://")):
            if raw.startswith("http://"):
                raise ManualGapAssignError("Nur HTTPS-URLs erlaubt (kein http://).")
            downloaded_temp, final_url = _download_url_to_temp_file(raw)
            source = downloaded_temp
            download_url = final_url
            source_label = final_url
            candidate_id = _candidate_id_for_url(gap_id, raw)
            provider_asset_id = Path(urlparse(final_url).path or "").name or "url_media"
            license_value = "manual_url"
            attribution = "Manuell per URL zugeordnet"
        else:
            source = Path(raw).expanduser()
            if not source.is_file():
                raise ManualGapAssignError(f"Datei nicht gefunden: {source}")
            candidate_id = _candidate_id_for_file(gap_id, source)
            provider_asset_id = source.name
            license_value = "manual_local"
            attribution = "Manuell zugeordnet"

        media_type = _detect_media_type(source)
        status, error = validate_local_media_path(str(source), media_type)
        if status != STATUS_EXPORT_READY:
            raise ManualGapAssignError(
                error or f"Datei technisch ungültig ({status})."
            )

        locked = require_locked_script(project)
        # Gleicher Datei-/URL-Hash → gleiche ID; nicht als „ersetzt“ werten.
        if candidate_id in superseded_ids:
            superseded_ids = [cid for cid in superseded_ids if cid != candidate_id]
            if superseded_hint_id == candidate_id:
                superseded_hint_id = superseded_ids[0] if superseded_ids else None

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
        duration_seconds: float | None = None
        if media_type == "video":
            try:
                from otio_app.services.media_utils import probe_duration_seconds

                probed = probe_duration_seconds(target)
                if probed is not None and float(probed) > 0:
                    duration_seconds = float(probed)
            except Exception:  # noqa: BLE001
                duration_seconds = None

        candidate = StockCandidate(
            candidate_id=candidate_id,
            provider="manual",
            provider_asset_id=provider_asset_id,
            title=description[:120],
            media_type=media_type,
            creator="manual",
            source_page=source_label,
            download_url=download_url,
            preview_url=download_url if media_type == "photo" else "",
            license=license_value,
            attribution=attribution,
            selected=True,
            gap_id=gap_id,
            local_media_path=str(target),
            duration_seconds=duration_seconds,
            media_validation_status=STATUS_EXPORT_READY,
            media_validation_error=None,
            funnel_managed=True,
            license_metadata_status="missing",
            cut_plan_run_id=_current_cut_plan_run_id(project),
            assign_status="manual",
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
            intake_source=intake_source,
        )
        _mark_gap_filled_in_funnel_report(
            project,
            gap_id=gap_id,
            candidate_id=candidate_id,
            rejected_candidate_ids=superseded_ids,
        )
        return ManualGapAssignResult(
            candidate=candidate,
            superseded_candidate_id=superseded_hint_id,
        )
    finally:
        if downloaded_temp is not None:
            try:
                downloaded_temp.unlink(missing_ok=True)
            except OSError:
                pass
