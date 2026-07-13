"""Übernimmt akzeptierte Cut-Plan-Supplements ins Folder-Inventory (ohne VO).

Absichtlich außerhalb von `voiceover_generation/cut_plan_*`, damit die
Cut-Plan-Module weiterhin keine Produktions-Orchestrierung referenzieren.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import AssetMediaAnalysis, SupplementAssetSidecar
from otio_app.defaults import (
    ASSET_ORIGIN_PEXELS,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
    SUPPLEMENT_SOURCE_ADOBE,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.models import Project
from otio_app.services.frame_extract import extract_frames
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import is_image_media
from otio_app.services.supplement_pipeline import extend_folder_inventory, save_sidecar
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    load_cut_plan_supplement_manifest,
    load_cut_plan_supplement_requests,
)


@dataclass
class CutPlanInventoryImportReport:
    considered: int = 0
    imported: int = 0
    skipped_existing: int = 0
    skipped: list[str] = field(default_factory=list)
    imported_by_folder: dict[str, int] = field(default_factory=dict)


def _origin_for_provider(provider: str) -> str:
    if provider == SUPPLEMENT_SOURCE_PEXELS:
        return ASSET_ORIGIN_PEXELS
    if provider == SUPPLEMENT_SOURCE_ADOBE:
        return "adobe_stock"
    return provider or "supplement"


def _is_cut_plan_supplement_path(path: Path | str) -> bool:
    parts = Path(path).parts
    return "cut_plan" in parts and "supplement_assets" in parts


def _best_validation_for_request(entry, request_id: str):
    if entry is None:
        return None
    for validation in entry.validations:
        if validation.request_id == request_id and (
            validation.accepted or validation.validation_status == "PASS"
        ):
            return validation
    for validation in entry.validations:
        if validation.request_id == request_id:
            return validation
    accepted = [item for item in entry.validations if item.accepted]
    if accepted:
        return accepted[0]
    passes = [item for item in entry.validations if item.validation_status == "PASS"]
    if passes:
        return passes[0]
    return entry.validations[0] if entry.validations else None


def _manifest_entry_for_path(manifest, asset_path: str):
    try:
        target = str(Path(asset_path).expanduser().resolve())
    except OSError:
        target = str(Path(asset_path))
    for entry in manifest.entries:
        try:
            entry_key = str(Path(entry.asset_path).expanduser().resolve())
        except OSError:
            entry_key = str(Path(entry.asset_path))
        if entry_key == target or entry.asset_path == asset_path:
            return entry
        if entry.provider_asset_id and Path(entry.asset_path).name == Path(asset_path).name:
            return entry
    return None


def _ensure_sidecar(
    *,
    local_path: Path,
    request_id: str,
    provider: str,
    provider_asset_id: str,
    asset_id: str,
    source_url: str,
    media_type: str,
    folder_name: str,
    validation_status: str,
    validation_score: float,
    approved: bool,
) -> SupplementAssetSidecar:
    from otio_app.services.supplement_pipeline import load_sidecar

    existing = load_sidecar(local_path)
    if existing is not None:
        return existing
    sidecar = SupplementAssetSidecar(
        asset_id=asset_id,
        supplement_request_id=request_id,
        provider=provider,
        provider_asset_id=provider_asset_id,
        source_url=source_url,
        location_name=folder_name,
        media_type=media_type,
        acquisition_method="cut_plan_import",
        local_path=str(local_path),
        original_filename=local_path.name,
        downloaded_at=datetime.now(timezone.utc),
        supplement_validation_status=validation_status,
        supplement_validation_score=validation_score,
        approved_for_cut_plan=approved,
    )
    save_sidecar(sidecar)
    return sidecar


def _build_asset_from_accepted(
    project: Project,
    *,
    folder_name: str,
    request_id: str,
    accepted_asset_id: str,
    accepted_asset_path: str,
    manifest_entry,
    validation,
) -> AssetMediaAnalysis:
    local_path = Path(accepted_asset_path)
    if not local_path.is_file() or local_path.stat().st_size <= 0:
        raise ValueError(f"Datei fehlt: {accepted_asset_path}")

    provider = (manifest_entry.provider if manifest_entry else "") or SUPPLEMENT_SOURCE_PEXELS
    provider_asset_id = (manifest_entry.provider_asset_id if manifest_entry else "") or ""
    asset_id = accepted_asset_id or (
        f"asset_{provider}_{provider_asset_id}" if provider_asset_id else f"cut_supplement_{request_id}"
    )
    media_type = (manifest_entry.asset_type if manifest_entry else "") or (
        "image" if is_image_media(local_path) else "video"
    )
    source_url = (manifest_entry.source_url if manifest_entry else "") or ""

    description = ""
    validation_status = "PASS"
    validation_score = 1.0
    if validation is not None:
        description = (validation.description or "").strip()
        validation_status = validation.validation_status or "PASS"
        validation_score = float(validation.validation_score or 0.0)
    if not description:
        description = f"Cut-Plan-Supplement {local_path.name}"

    # Akzeptierte Cut-Plan-Assets gelten als freigegeben — auch bei WEAK_PASS.
    approved = True
    if validation_status in {"FAIL"}:
        approved = False

    _ensure_sidecar(
        local_path=local_path,
        request_id=request_id,
        provider=provider,
        provider_asset_id=provider_asset_id,
        asset_id=asset_id,
        source_url=source_url,
        media_type=media_type,
        folder_name=folder_name,
        validation_status=validation_status if validation_status else "PASS",
        validation_score=validation_score,
        approved=approved,
    )

    frames_dir = (
        project.work_dir_path
        / "frames"
        / folder_name.replace(" ", "_")
        / local_path.stem
    )
    # Vorhandene Auto-Resolve-Frames wiederverwenden, falls vorhanden.
    cut_plan_frames = local_path.parent / "frames"
    existing_frames: list[Path] = []
    if cut_plan_frames.is_dir():
        existing_frames = sorted(
            path
            for path in cut_plan_frames.rglob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )[: max(1, project.frames_per_shot)]
    if existing_frames:
        frames = existing_frames
    else:
        frame_count = 1 if is_image_media(local_path) else max(1, project.frames_per_shot)
        frames = extract_frames(local_path, frames_dir, frame_count)
    if not frames:
        raise ValueError(f"Keine Frames extrahierbar: {local_path.name}")

    return AssetMediaAnalysis(
        path=str(local_path),
        description=description,
        frames_used=[str(frame) for frame in frames],
        asset_id=asset_id,
        asset_origin=_origin_for_provider(provider),
        supplement_request_id=request_id,
        source_url=source_url,
        provider=provider,
        media_type=media_type,
        supplement_validation_status=validation_status or "PASS",
        supplement_validation_score=validation_score,
        approved_for_cut_plan=approved,
        analysis_status="complete",
        description_prompt_version="cut_plan_import_v1",
        description_generated_at=datetime.now(timezone.utc),
    )


def list_accepted_cut_plan_supplements_pending_inventory(
    project: Project,
) -> list[dict[str, str]]:
    """Akzeptierte Cut-Plan-Assets, die noch nicht im Folder-Inventory stehen."""
    requests_doc = load_cut_plan_supplement_requests(project)
    if requests_doc is None:
        return []
    pending: list[dict[str, str]] = []
    inventory_paths_by_folder: dict[str, set[str]] = {}
    inventory_ids_by_folder: dict[str, set[str]] = {}

    for request in requests_doc.requests:
        if request.status != CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED:
            continue
        if not request.accepted_asset_path or not request.folder_name:
            continue
        folder = request.folder_name
        if folder not in inventory_paths_by_folder:
            inventory = load_folder_inventory(project, folder)
            paths: set[str] = set()
            ids: set[str] = set()
            if inventory is not None:
                for asset in inventory.assets:
                    paths.add(asset.path)
                    try:
                        paths.add(str(Path(asset.path).resolve()))
                    except OSError:
                        pass
                    if asset.asset_id:
                        ids.add(asset.asset_id)
            inventory_paths_by_folder[folder] = paths
            inventory_ids_by_folder[folder] = ids

        path = request.accepted_asset_path
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = path
        if (
            path in inventory_paths_by_folder[folder]
            or resolved in inventory_paths_by_folder[folder]
            or (request.accepted_asset_id and request.accepted_asset_id in inventory_ids_by_folder[folder])
        ):
            continue
        pending.append(
            {
                "folder_name": folder,
                "request_id": request.request_id,
                "asset_path": path,
                "asset_id": request.accepted_asset_id,
            }
        )
    return pending


def import_accepted_cut_plan_supplements_into_inventory(
    project: Project,
    *,
    folder_names: list[str] | None = None,
) -> CutPlanInventoryImportReport:
    """Importiert akzeptierte Cut-Plan-Supplements ins Folder-Inventory.

    Nutzt vorhandene Gemini-Beschreibungen/Validierungen aus dem Cut-Plan-
    Manifest — kein erneuter LLM-Lauf. Frames werden lokal extrahiert bzw.
    wiederverwendet.
    """
    report = CutPlanInventoryImportReport()
    requests_doc = load_cut_plan_supplement_requests(project)
    if requests_doc is None:
        return report

    manifest = load_cut_plan_supplement_manifest(project)
    allowed = set(folder_names) if folder_names is not None else None

    for request in requests_doc.requests:
        if request.status != CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED:
            continue
        if not request.accepted_asset_path or not request.folder_name:
            continue
        if allowed is not None and request.folder_name not in allowed:
            continue

        report.considered += 1
        folder = request.folder_name
        inventory = load_folder_inventory(project, folder)
        existing_paths = {asset.path for asset in (inventory.assets if inventory else [])}
        existing_ids = {asset.asset_id for asset in (inventory.assets if inventory else []) if asset.asset_id}
        try:
            resolved = str(Path(request.accepted_asset_path).resolve())
        except OSError:
            resolved = request.accepted_asset_path
        if (
            request.accepted_asset_path in existing_paths
            or resolved in existing_paths
            or (request.accepted_asset_id and request.accepted_asset_id in existing_ids)
        ):
            report.skipped_existing += 1
            continue

        entry = _manifest_entry_for_path(manifest, request.accepted_asset_path)
        validation = _best_validation_for_request(entry, request.request_id)
        try:
            asset = _build_asset_from_accepted(
                project,
                folder_name=folder,
                request_id=request.request_id,
                accepted_asset_id=request.accepted_asset_id,
                accepted_asset_path=request.accepted_asset_path,
                manifest_entry=entry,
                validation=validation,
            )
            extend_folder_inventory(project, folder_name=folder, asset=asset)
        except (OSError, ValueError) as exc:
            report.skipped.append(f"{request.request_id}: {exc}")
            continue

        report.imported += 1
        report.imported_by_folder[folder] = report.imported_by_folder.get(folder, 0) + 1

    return report


def is_external_inventory_media_path(path: Path | str) -> bool:
    """True für Supplement-Pfade außerhalb des Top-Level-Asset-Ordners."""
    from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME

    parts = Path(path).parts
    if SUPPLEMENTAL_FOLDER_NAME in parts:
        return True
    return _is_cut_plan_supplement_path(path)
