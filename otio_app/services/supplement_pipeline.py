"""Supplement-Pipeline: Suche, Download, Analyse, Inventory-Erweiterung."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    InventoryDeltaDocument,
    InventoryDeltaEntry,
    SupplementAssetSidecar,
    SupplementCandidate,
    SupplementErrorDocument,
    SupplementErrorEntry,
    SupplementManifest,
    SupplementManifestEntry,
    SupplementRequest,
)
from otio_app.defaults import (
    CANDIDATE_STATUS_DOWNLOAD_FAILED,
    PROVIDER_STATUS_MOCK,
    ASSET_ORIGIN_GOOGLE,
    ASSET_ORIGIN_NANO_BANANA,
    ASSET_ORIGIN_PEXELS,
    RIGHTS_STATUS_APPROVED,
    RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
    REQUEST_STATUS_ACQUIRE_FAILED,
    REQUEST_STATUS_ANALYSIS_PENDING,
    REQUEST_STATUS_CANDIDATES_FOUND,
    REQUEST_STATUS_INVENTORY_UPDATED,
    REQUEST_STATUS_READY_FOR_REPLAN,
    SUPPLEMENT_SOURCE_ADOBE,
    SUPPLEMENT_SOURCE_GOOGLE,
    SUPPLEMENT_SOURCE_MANUAL,
    SUPPLEMENT_SOURCE_NANO_BANANA,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_folder_inventory_delta_path,
    get_folder_inventory_path,
    get_pexels_debug_report_path,
    get_provider_supplemental_dir,
    get_supplement_errors_path,
    get_supplement_manifest_path,
)
from otio_app.services.edit_plan_builder import load_edit_plan, save_edit_plan
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import describe_media_from_frames, is_gemini_configured
from otio_app.services.inventory_hash import compute_folder_inventory_hash
from otio_app.services.inventory_loader import load_folder_inventory, save_folder_inventory
from otio_app.services.media_inventory_cache import save_cached_media
from otio_app.services.media_utils import is_image_media
from otio_app.services.supplement_coverage import evaluate_folder_coverage
from otio_app.services.supplement_requests import (
    add_candidates,
    load_supplement_requests,
    save_supplement_requests,
    update_request,
    upsert_requests,
)
from otio_app.services.supplement_sources import get_provider_readiness, get_supplement_adapter
from otio_app.services.supplement_sources.base import SupplementAsset


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _sidecar_path(local_path: Path) -> Path:
    return local_path.with_suffix(local_path.suffix + ".asset.json")


def record_supplement_error(
    project: Project,
    *,
    request_id: str,
    provider: str,
    error_type: str,
    error_message: str,
    candidate_id: str = "",
    url: str = "",
    query_used: str = "",
    action_required: str = "",
    provider_status_at_failure: str = "",
    http_status: int = 0,
    content_type: str = "",
) -> None:
    path = get_supplement_errors_path(project.work_dir_path)
    document = SupplementErrorDocument(project_id=project.id)
    if path.is_file():
        try:
            document = SupplementErrorDocument.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass
    document.errors.append(
        SupplementErrorEntry(
            request_id=request_id,
            candidate_id=candidate_id,
            provider=provider,
            url=url,
            query_used=query_used,
            error_type=error_type,
            error_message=error_message,
            http_status=http_status,
            content_type=content_type,
            action_required=action_required,
            provider_status_at_failure=provider_status_at_failure,
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def save_sidecar(sidecar: SupplementAssetSidecar) -> Path:
    path = _sidecar_path(Path(sidecar.local_path))
    path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_sidecar(local_path: Path) -> SupplementAssetSidecar | None:
    path = _sidecar_path(local_path)
    if not path.is_file():
        return None
    try:
        return SupplementAssetSidecar.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def run_coverage_for_folder(
    project: Project,
    *,
    folder_name: str,
    voice_file: str,
    segments,
    assets: list[AssetMediaAnalysis],
) -> tuple[list, list[SupplementRequest]]:
    coverages, requests = evaluate_folder_coverage(
        project,
        folder_name=folder_name,
        voice_file=voice_file,
        segments=segments,
        assets=assets,
    )
    if requests:
        upsert_requests(project, requests)
    return coverages, requests


def _write_pexels_debug_report(project: Project, report: dict) -> None:
    path = get_pexels_debug_report_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def search_supplement_candidates(
    project: Project,
    request: SupplementRequest,
) -> list[SupplementCandidate]:
    source = request.selected_source
    if not source:
        raise ValueError("Keine Supplement-Quelle gewählt.")
    adapter = get_supplement_adapter(source)
    readiness = adapter.readiness()
    if readiness.status == "CONFIG_MISSING":
        record_supplement_error(
            project,
            request_id=request.supplement_request_id,
            provider=source,
            error_type="CONFIG_MISSING",
            error_message=readiness.message,
            query_used=request.query_used or "",
            action_required="API-Key konfigurieren oder andere Quelle wählen.",
            provider_status_at_failure=readiness.status,
        )
        update_request(
            project,
            request.supplement_request_id,
            status=REQUEST_STATUS_ACQUIRE_FAILED,
            last_error=readiness.message,
            last_error_at=datetime.now(timezone.utc),
            provider_status_at_failure=readiness.status,
        )
        return []
    try:
        candidates = adapter.search(request)
    except Exception as exc:
        if source == SUPPLEMENT_SOURCE_PEXELS and getattr(adapter, "last_debug_report", None):
            _write_pexels_debug_report(project, adapter.last_debug_report)
        record_supplement_error(
            project,
            request_id=request.supplement_request_id,
            provider=source,
            error_type=type(exc).__name__,
            error_message=str(exc),
            query_used=request.query_used or "",
            action_required="Provider-Konfiguration/Netzwerk prüfen oder andere Quelle wählen.",
            provider_status_at_failure=readiness.status,
        )
        update_request(
            project,
            request.supplement_request_id,
            status=REQUEST_STATUS_ACQUIRE_FAILED,
            last_error=str(exc),
            last_error_at=datetime.now(timezone.utc),
            provider_status_at_failure=readiness.status,
        )
        return []
    if source == SUPPLEMENT_SOURCE_PEXELS and getattr(adapter, "last_debug_report", None):
        _write_pexels_debug_report(project, adapter.last_debug_report)
        api_errors = adapter.last_debug_report.get("errors") or []
        if api_errors and not candidates:
            first_error = api_errors[0]
            error_message = (
                f"Pexels-API-Fehler (HTTP {first_error.get('status')}): "
                f"{first_error.get('message')}"
            )
            record_supplement_error(
                project,
                request_id=request.supplement_request_id,
                provider=source,
                error_type="PEXELS_API_ERROR",
                error_message=error_message,
                query_used=first_error.get("query", ""),
                url=first_error.get("endpoint", ""),
                action_required="API-Key/Query prüfen oder erneut versuchen.",
                provider_status_at_failure=readiness.status,
                http_status=int(first_error.get("status") or 0),
            )
            update_request(
                project,
                request.supplement_request_id,
                status=REQUEST_STATUS_ACQUIRE_FAILED,
                last_error=error_message,
                last_error_at=datetime.now(timezone.utc),
                provider_status_at_failure=readiness.status,
            )
            return []
    add_candidates(project, candidates)
    attempted = []
    if source == SUPPLEMENT_SOURCE_PEXELS and getattr(adapter, "last_debug_report", None):
        attempted = list(adapter.last_debug_report.get("queries_attempted", []))
    elif candidates:
        attempted = sorted({candidate.query_used for candidate in candidates if candidate.query_used})
    elif request.query_used:
        attempted = [request.query_used]
    if not candidates:
        record_supplement_error(
            project,
            request_id=request.supplement_request_id,
            provider=source,
            error_type="NO_CANDIDATES",
            error_message="Keine echten Kandidaten gefunden.",
            query_used=request.query_used or "",
            action_required="Query vereinfachen, andere Quelle wählen oder Manual Import nutzen.",
            provider_status_at_failure=readiness.status,
        )
    update_request(
        project,
        request.supplement_request_id,
        status=REQUEST_STATUS_CANDIDATES_FOUND,
        search_queries_attempted=attempted or request.search_queries_attempted,
        best_query=attempted[0] if attempted else request.best_query,
        query_used=attempted[0] if attempted else request.query_used,
    )
    return candidates


def _destination_folder(project: Project, folder_name: str, provider: str) -> Path:
    return get_provider_supplemental_dir(project.project_root_path, folder_name, provider)


def acquire_supplement_candidate(
    project: Project,
    candidate: SupplementCandidate,
    request: SupplementRequest,
) -> SupplementAsset:
    adapter = get_supplement_adapter(candidate.provider)
    destination = _destination_folder(project, request.folder_name, candidate.provider)
    readiness = adapter.readiness()
    if candidate.is_mock or not candidate.download_enabled or readiness.is_mock:
        message = "Mock-/Demo-Kandidaten dürfen nicht als echte Assets übernommen werden."
        record_supplement_error(
            project,
            request_id=request.supplement_request_id,
            candidate_id=candidate.candidate_id,
            provider=candidate.provider,
            url=candidate.download_url,
            query_used=candidate.query_used,
            error_type="MOCK_CANDIDATE_BLOCKED",
            error_message=message,
            action_required="Echte Quelle konfigurieren oder manuellen Import nutzen.",
            provider_status_at_failure=readiness.status,
        )
        update_request(
            project,
            request.supplement_request_id,
            status=REQUEST_STATUS_ACQUIRE_FAILED,
            last_error=message,
            last_error_at=datetime.now(timezone.utc),
            failed_url=candidate.download_url,
            provider_status_at_failure=readiness.status,
        )
        raise PermissionError(message)

    try:
        if candidate.provider == SUPPLEMENT_SOURCE_ADOBE:
            if candidate.status != "ADOBE_LICENSE_APPROVED":
                raise PermissionError(
                    "Adobe Asset darf nur nach Klick auf «Adobe Asset lizenzieren und herunterladen» "
                    "heruntergeladen werden."
                )
            asset = adapter.acquire(candidate, destination)
        elif candidate.provider == SUPPLEMENT_SOURCE_NANO_BANANA:
            asset = adapter.generate(request, destination)
        elif candidate.provider == SUPPLEMENT_SOURCE_GOOGLE:
            raise PermissionError(
                "Google Search ist Discovery. Bitte Treffer öffnen und Datei manuell importieren."
            )
        elif candidate.provider == SUPPLEMENT_SOURCE_MANUAL:
            raise ValueError("Manueller Modus: bitte lokalen Dateipfad importieren.")
        else:
            asset = adapter.acquire(candidate, destination)
    except Exception as exc:
        record_supplement_error(
            project,
            request_id=request.supplement_request_id,
            candidate_id=candidate.candidate_id,
            provider=candidate.provider,
            url=candidate.download_url,
            query_used=candidate.query_used,
            error_type=type(exc).__name__,
            error_message=str(exc),
            action_required="Kandidat prüfen oder andere Quelle wählen.",
            provider_status_at_failure=readiness.status,
        )
        update_request(
            project,
            request.supplement_request_id,
            status=REQUEST_STATUS_ACQUIRE_FAILED,
            last_error=str(exc),
            last_error_at=datetime.now(timezone.utc),
            failed_url=candidate.download_url,
            provider_status_at_failure=readiness.status,
        )
        raise

    sidecar = asset.sidecar.model_copy(
        update={"file_hash": _file_hash(asset.local_path)}
    )
    save_sidecar(sidecar)
    update_request(
        project,
        request.supplement_request_id,
        status=REQUEST_STATUS_ANALYSIS_PENDING,
        selected_source=candidate.provider,
        query_used=candidate.query_used,
    )
    return SupplementAsset(local_path=asset.local_path, sidecar=sidecar)


def _extension_from_candidate(candidate: SupplementCandidate) -> str:
    parsed = urllib.parse.urlparse(candidate.download_url or candidate.preview_url)
    suffix = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
    if suffix:
        return suffix
    if candidate.media_type == "image":
        return ".jpg"
    return ".mp4"


def acquire_google_candidate_for_private_use(
    project: Project,
    candidate: SupplementCandidate,
    request: SupplementRequest,
) -> SupplementAsset:
    """Deprecated: Google ist nur Discovery, kein automatischer Download."""
    raise PermissionError(
        "Google Search ist aktuell nur Discovery. Bitte Datei lokal herunterladen "
        "und per manuellem Import übernehmen."
    )


def import_manual_supplement_asset(
    project: Project,
    *,
    request: SupplementRequest,
    source_path: Path,
    source_url: str = "",
    rights_status: str = RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
    source_provider: str = SUPPLEMENT_SOURCE_MANUAL,
    acquisition_method: str = "manual_import",
) -> SupplementAsset:
    """Übernimmt eine manuell beschaffte Datei ins passende Supplement-Verzeichnis."""
    if not source_path.is_file():
        raise FileNotFoundError(f"Manuelles Supplement-Asset nicht gefunden: {source_path}")

    destination = _destination_folder(project, request.folder_name, source_provider)
    destination.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in source_path.stem
    ).strip("_") or "manual_asset"
    filename = f"{request.supplement_request_id}_manual_{safe_stem}{source_path.suffix.lower()}"
    local_path = destination / filename
    shutil.copy2(source_path, local_path)

    sidecar = SupplementAssetSidecar(
        asset_id=f"asset_{source_provider}_{request.supplement_request_id}",
        supplement_request_id=request.supplement_request_id,
        provider=source_provider,
        provider_asset_id=source_path.stem,
        source_url=source_url,
        acquisition_method=acquisition_method,
        downloaded_at=datetime.now(timezone.utc),
        original_filename=source_path.name,
        original_local_path=str(source_path),
        local_path=str(local_path),
        file_hash=_file_hash(local_path),
        rights_status=rights_status,
        approval_status="MANUAL_IMPORTED",
    )
    save_sidecar(sidecar)
    update_request(
        project,
        request.supplement_request_id,
        status=REQUEST_STATUS_ANALYSIS_PENDING,
        selected_source=source_provider,
    )
    return SupplementAsset(local_path=local_path, sidecar=sidecar)


def analyze_supplement_asset(
    project: Project,
    *,
    folder_name: str,
    local_path: Path,
    sidecar: SupplementAssetSidecar,
    language: str = "de",
) -> AssetMediaAnalysis:
    if not local_path.is_file() or local_path.stat().st_size <= 0:
        raise ValueError(f"Supplement-Datei fehlt oder ist leer: {local_path}")
    frames_dir = (
        project.work_dir_path
        / "frames"
        / folder_name.replace(" ", "_")
        / local_path.stem
    )
    frame_count = 1 if is_image_media(local_path) else max(1, project.frames_per_shot)
    frames = extract_frames(local_path, frames_dir, frame_count)
    description = ""
    if frames and is_gemini_configured():
        description = describe_media_from_frames(
            local_path.name,
            folder_name,
            frames,
            language,
            model=project.gemini_model,
        )
    elif frames:
        description = f"Supplement-Asset {local_path.name}"

    origin_map = {
        SUPPLEMENT_SOURCE_PEXELS: ASSET_ORIGIN_PEXELS,
        SUPPLEMENT_SOURCE_GOOGLE: ASSET_ORIGIN_GOOGLE,
        SUPPLEMENT_SOURCE_NANO_BANANA: ASSET_ORIGIN_NANO_BANANA,
        SUPPLEMENT_SOURCE_ADOBE: "adobe_stock",
    }
    asset = AssetMediaAnalysis(
        path=str(local_path),
        description=description,
        frames_used=[str(frame) for frame in frames],
        asset_id=sidecar.asset_id,
        asset_origin=origin_map.get(sidecar.provider, sidecar.provider),
        supplement_request_id=sidecar.supplement_request_id,
        rights_status=sidecar.rights_status,
        source_url=sidecar.source_url,
        provider=sidecar.provider,
        media_type=sidecar.media_type,
        aspect_ratio=sidecar.aspect_ratio,
        aspect_ratio_policy=sidecar.aspect_ratio_policy,
        is_16_9=sidecar.is_16_9,
        supplement_validation_status=sidecar.supplement_validation_status or (
            "PASS" if sidecar.approved_for_cut_plan else "NEEDS_USER_REVIEW"
        ),
        supplement_validation_score=sidecar.supplement_validation_score,
        approved_for_cut_plan=sidecar.approved_for_cut_plan,
        generated_prompt=sidecar.prompt,
        search_query=sidecar.search_query,
        analysis_status="complete" if description and frames else "failed",
        description_model=project.gemini_model if is_gemini_configured() else "",
        description_prompt_version="supplement_v1",
        description_generated_at=datetime.now(timezone.utc),
    )
    cache_file = (
        project.work_dir_path
        / "cache"
        / "inventory"
        / folder_name.replace(" ", "_")
        / f"{local_path.name}.json"
    )
    save_cached_media(cache_file, asset)
    update_request(
        project,
        sidecar.supplement_request_id,
        status=REQUEST_STATUS_ANALYSIS_PENDING if asset.analysis_status != "complete" else "ANALYSIS_COMPLETE",
    )
    return asset


def extend_folder_inventory(
    project: Project,
    *,
    folder_name: str,
    asset: AssetMediaAnalysis,
) -> AssetFolderAnalysis:
    asset_path = Path(asset.path)
    if not asset_path.is_file() or asset_path.stat().st_size <= 0:
        raise ValueError(f"Asset-Datei fehlt oder ist leer: {asset.path}")
    if asset.analysis_status != "complete" or not asset.frames_used or not asset.description:
        raise ValueError(
            f"Asset wurde nicht erfolgreich analysiert und darf nicht ins Inventory: {asset.path}"
        )
    if not asset.approved_for_cut_plan and asset.supplement_validation_status != "PASS":
        raise ValueError(
            f"Supplement-Asset ist nicht für den Schnittplan freigegeben: {asset.path}"
        )
    if not load_sidecar(asset_path):
        raise ValueError(f"Sidecar-Metadaten fehlen: {asset.path}")
    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    existing = load_folder_inventory(project, folder_name)
    if existing is None:
        item = AssetFolderAnalysis(folder=folder_name, media_files=[asset.path], assets=[asset])
    else:
        assets = [entry for entry in existing.assets if entry.path != asset.path]
        assets.append(asset)
        media_files = list(existing.media_files)
        if asset.path not in media_files:
            media_files.append(asset.path)
        item = existing.model_copy(
            update={
                "assets": assets,
                "media_files": media_files,
                "description": existing.description,
            }
        )
    save_folder_inventory(path, item)

    delta_path = get_folder_inventory_delta_path(project.work_dir_path, folder_name)
    delta = InventoryDeltaDocument(project_id=project.id, folder_name=folder_name)
    if delta_path.is_file():
        try:
            delta = InventoryDeltaDocument.model_validate(
                json.loads(delta_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass
    delta.entries.append(
        InventoryDeltaEntry(
            asset_id=asset.asset_id,
            path=asset.path,
            asset_origin=asset.asset_origin,
            supplement_request_id=asset.supplement_request_id,
        )
    )
    delta_path.parent.mkdir(parents=True, exist_ok=True)
    delta_path.write_text(delta.model_dump_json(indent=2), encoding="utf-8")

    manifest_path = get_supplement_manifest_path(project.work_dir_path)
    manifest = SupplementManifest(project_id=project.id)
    if manifest_path.is_file():
        try:
            manifest = SupplementManifest.model_validate(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass
    manifest.entries.append(
        SupplementManifestEntry(
            supplement_request_id=asset.supplement_request_id,
            asset_id=asset.asset_id,
            local_path=asset.path,
            provider=asset.provider,
            rights_status=asset.rights_status,
        )
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    update_request(
        project,
        asset.supplement_request_id,
        status=REQUEST_STATUS_READY_FOR_REPLAN,
    )
    return item


def mark_edit_plans_stale_for_folder(project: Project, folder_name: str) -> None:
    plan = load_edit_plan(project, folder_name)
    if plan is None:
        return
    current_hash = compute_folder_inventory_hash(load_folder_inventory(project, folder_name))
    if plan.inventory_hash_at_plan_time and plan.inventory_hash_at_plan_time != current_hash:
        plan = plan.model_copy(update={"confirmed": False})
        save_edit_plan(project, plan)


def approve_adobe_candidate(candidate: SupplementCandidate) -> SupplementCandidate:
    return candidate.model_copy(update={"status": "ADOBE_LICENSE_APPROVED"})


def build_supplement_filename(
    folder_name: str,
    request_id: str,
    slug: str,
    provider: str,
    provider_asset_id: str,
    extension: str,
) -> str:
    folder_slug = folder_name.replace(" ", "_")
    return f"{folder_slug}_{request_id}_{slug}_{provider}_{provider_asset_id}{extension}"
