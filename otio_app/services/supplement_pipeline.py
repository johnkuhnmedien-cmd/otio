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
    SupplementManifest,
    SupplementManifestEntry,
    SupplementRequest,
)
from otio_app.defaults import (
    ASSET_ORIGIN_GOOGLE,
    ASSET_ORIGIN_NANO_BANANA,
    ASSET_ORIGIN_PEXELS,
    RIGHTS_STATUS_APPROVED,
    RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
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
    get_provider_supplemental_dir,
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
from otio_app.services.supplement_sources import get_supplement_adapter
from otio_app.services.supplement_sources.base import SupplementAsset


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _sidecar_path(local_path: Path) -> Path:
    return local_path.with_suffix(local_path.suffix + ".asset.json")


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


def search_supplement_candidates(
    project: Project,
    request: SupplementRequest,
) -> list[SupplementCandidate]:
    source = request.selected_source
    if not source:
        raise ValueError("Keine Supplement-Quelle gewählt.")
    adapter = get_supplement_adapter(source)
    candidates = adapter.search(request)
    add_candidates(project, candidates)
    update_request(project, request.supplement_request_id, status="CANDIDATES_FOUND")
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
            "Google Search ist nur Discovery. Bitte Treffer öffnen, Rechte prüfen "
            "und die Datei anschließend manuell als Supplement-Asset übernehmen."
        )
    elif candidate.provider == SUPPLEMENT_SOURCE_MANUAL:
        raise ValueError("Manueller Modus: bitte lokales Asset im Schnittplan akzeptieren.")
    else:
        asset = adapter.acquire(candidate, destination)

    sidecar = asset.sidecar.model_copy(
        update={"file_hash": _file_hash(asset.local_path)}
    )
    save_sidecar(sidecar)
    update_request(
        project,
        request.supplement_request_id,
        status="ACQUIRED",
        selected_source=candidate.provider,
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
    """Lädt einen Google-Discovery-Treffer nach expliziter Privatnutzungs-Bestätigung."""
    if candidate.provider != SUPPLEMENT_SOURCE_GOOGLE:
        raise ValueError(f"Kein Google-Kandidat: {candidate.provider}")
    if not candidate.download_url:
        raise ValueError("Google-Kandidat hat keine download_url.")

    destination = _destination_folder(project, request.folder_name, SUPPLEMENT_SOURCE_GOOGLE)
    destination.mkdir(parents=True, exist_ok=True)
    extension = _extension_from_candidate(candidate)
    local_path = destination / (
        f"{request.supplement_request_id}_google_{candidate.provider_asset_id}{extension}"
    )
    try:
        with urllib.request.urlopen(candidate.download_url, timeout=60) as response:
            local_path.write_bytes(response.read())
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Google-Medien-Download fehlgeschlagen: {exc}") from exc

    sidecar = SupplementAssetSidecar(
        asset_id=f"asset_google_{candidate.provider_asset_id}",
        supplement_request_id=request.supplement_request_id,
        provider=SUPPLEMENT_SOURCE_GOOGLE,
        provider_asset_id=candidate.provider_asset_id,
        source_url=candidate.source_page_url,
        download_url=candidate.download_url,
        license=candidate.license or "Google Discovery — private Nutzung bestätigt",
        license_url=candidate.license_url,
        creator=candidate.creator,
        acquisition_method="google_private_download",
        downloaded_at=datetime.now(timezone.utc),
        original_filename=Path(urllib.parse.urlparse(candidate.download_url).path).name,
        local_path=str(local_path),
        file_hash=_file_hash(local_path),
        rights_status=RIGHTS_STATUS_APPROVED,
        approval_status="PRIVATE_USE_ACKNOWLEDGED",
    )
    save_sidecar(sidecar)
    update_request(
        project,
        request.supplement_request_id,
        status="ACQUIRED",
        selected_source=SUPPLEMENT_SOURCE_GOOGLE,
    )
    return SupplementAsset(local_path=local_path, sidecar=sidecar)


def import_manual_supplement_asset(
    project: Project,
    *,
    request: SupplementRequest,
    source_path: Path,
    source_url: str = "",
    rights_status: str = RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
) -> SupplementAsset:
    """Übernimmt eine manuell beschaffte Datei ins passende Supplement-Verzeichnis."""
    if not source_path.is_file():
        raise FileNotFoundError(f"Manuelles Supplement-Asset nicht gefunden: {source_path}")

    destination = _destination_folder(project, request.folder_name, SUPPLEMENT_SOURCE_MANUAL)
    destination.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in source_path.stem
    ).strip("_") or "manual_asset"
    filename = f"{request.supplement_request_id}_manual_{safe_stem}{source_path.suffix.lower()}"
    local_path = destination / filename
    shutil.copy2(source_path, local_path)

    sidecar = SupplementAssetSidecar(
        asset_id=f"asset_manual_{request.supplement_request_id}",
        supplement_request_id=request.supplement_request_id,
        provider=SUPPLEMENT_SOURCE_MANUAL,
        provider_asset_id=source_path.stem,
        source_url=source_url,
        acquisition_method="manual_import",
        downloaded_at=datetime.now(timezone.utc),
        original_filename=source_path.name,
        local_path=str(local_path),
        file_hash=_file_hash(local_path),
        rights_status=rights_status,
        approval_status="MANUAL_IMPORTED",
    )
    save_sidecar(sidecar)
    update_request(
        project,
        request.supplement_request_id,
        status="ACQUIRED",
        selected_source=SUPPLEMENT_SOURCE_MANUAL,
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
        generated_prompt=sidecar.prompt,
        search_query=sidecar.search_query,
        analysis_status="complete" if description else "partial",
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
    return asset


def extend_folder_inventory(
    project: Project,
    *,
    folder_name: str,
    asset: AssetMediaAnalysis,
) -> AssetFolderAnalysis:
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
