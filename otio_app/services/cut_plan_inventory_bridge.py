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
    analyze_if_needed: bool = False,
    gemini_model: str = "",
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

    prompt_version = "cut_plan_import_v1"
    description_model = ""
    motion = ""
    framing = ""
    people = None
    people_action = None
    defects = None
    needs_describe = not description or description.startswith("Cut-Plan-Supplement ")
    if analyze_if_needed and needs_describe:
        from otio_app.services.gemini_client import (
            ASSET_DESCRIPTION_PROMPT_VERSION,
            analyze_media_from_frames,
            get_default_gemini_model,
            is_gemini_configured,
        )

        model = gemini_model or get_default_gemini_model()
        if is_gemini_configured():
            analysis = analyze_media_from_frames(
                local_path.name,
                folder_name,
                frames,
                "de",
                model=model,
            )
            description = analysis.description.strip()
            motion = analysis.motion
            framing = analysis.framing
            people = analysis.people
            people_action = analysis.people_action
            defects = analysis.defects
            description_model = model
            prompt_version = ASSET_DESCRIPTION_PROMPT_VERSION
        if not description:
            description = f"Cut-Plan-Supplement {local_path.name}"
    elif not description:
        description = f"Cut-Plan-Supplement {local_path.name}"

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
        motion=motion,
        framing=framing,
        people=people,
        people_action=people_action,
        defects=defects,
        supplement_validation_status=validation_status or "PASS",
        supplement_validation_score=validation_score,
        approved_for_cut_plan=approved,
        analysis_status="complete",
        description_model=description_model,
        description_prompt_version=prompt_version,
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
                analyze_if_needed=False,
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


def _inventory_index(project: Project) -> tuple[set[str], dict[str, set[str]]]:
    """Alle Inventory-Pfade (resolved) und Asset-IDs je Ordner."""
    all_paths: set[str] = set()
    ids_by_folder: dict[str, set[str]] = {}
    for folder_name in project.asset_subdir_names:
        inventory = load_folder_inventory(project, folder_name)
        ids: set[str] = set()
        if inventory is not None:
            for asset in inventory.assets:
                all_paths.add(asset.path)
                try:
                    all_paths.add(str(Path(asset.path).resolve()))
                except OSError:
                    pass
                if asset.asset_id:
                    ids.add(asset.asset_id)
        ids_by_folder[folder_name] = ids
    return all_paths, ids_by_folder


def _path_in_inventory(path: Path, all_paths: set[str]) -> bool:
    if str(path) in all_paths:
        return True
    try:
        return str(path.resolve()) in all_paths
    except OSError:
        return False


def _guess_folder_for_cut_plan_media(
    project: Project,
    media_path: Path,
    *,
    manifest_entry=None,
    request_id: str = "",
) -> str:
    if manifest_entry is not None and manifest_entry.folder_name:
        return manifest_entry.folder_name
    requests_doc = load_cut_plan_supplement_requests(project)
    if requests_doc is not None:
        for request in requests_doc.requests:
            if request_id and request.request_id == request_id and request.folder_name:
                return request.folder_name
            if request.accepted_asset_path:
                try:
                    if Path(request.accepted_asset_path).resolve() == media_path.resolve():
                        return request.folder_name
                except OSError:
                    if request.accepted_asset_path == str(media_path):
                        return request.folder_name
    # Dateiname beginnt oft mit Folder-Slug
    name = media_path.name.casefold()
    for folder_name in project.asset_subdir_names:
        slug = folder_name.replace(" ", "_").casefold()
        if name.startswith(slug + "_"):
            return folder_name
    return ""


def list_supplement_assets_missing_from_inventory(project: Project) -> list[dict[str, str]]:
    """Alle Supplement-Dateien (Cut-Plan + `_supplemental/`), die noch nicht im Inventory sind."""
    from otio_app.project_layout import (
        get_cut_plan_supplement_assets_dir,
        get_folder_supplemental_dir,
    )
    from otio_app.services.media_utils import list_media_files
    from otio_app.services.supplement_pipeline import load_sidecar

    missing: list[dict[str, str]] = []
    seen: set[str] = set()
    all_paths, _ids = _inventory_index(project)
    manifest = load_cut_plan_supplement_manifest(project)

    # 1) Akzeptierte Requests
    for entry in list_accepted_cut_plan_supplements_pending_inventory(project):
        key = entry["asset_path"]
        try:
            key = str(Path(entry["asset_path"]).resolve())
        except OSError:
            pass
        if key in seen:
            continue
        seen.add(key)
        missing.append({**entry, "source": "cut_plan_accepted"})

    # 2) Alle Dateien unter cut_plan/supplement_assets
    root = get_cut_plan_supplement_assets_dir(project.language_work_dir_path)
    if root.is_dir():
        try:
            request_dirs = sorted(root.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            request_dirs = []
        for request_dir in request_dirs:
            try:
                if not request_dir.is_dir():
                    continue
            except OSError:
                continue
            for media_path in list_media_files(request_dir):
                if _path_in_inventory(media_path, all_paths):
                    continue
                try:
                    key = str(media_path.resolve())
                except OSError:
                    key = str(media_path)
                if key in seen:
                    continue
                entry = _manifest_entry_for_path(manifest, str(media_path))
                folder = _guess_folder_for_cut_plan_media(
                    project,
                    media_path,
                    manifest_entry=entry,
                    request_id=request_dir.name,
                )
                if not folder:
                    continue
                seen.add(key)
                missing.append(
                    {
                        "folder_name": folder,
                        "request_id": request_dir.name,
                        "asset_path": str(media_path),
                        "asset_id": (entry.asset_id if entry else ""),
                        "source": "cut_plan_disk",
                    }
                )

    # 3) `{folder}/_supplemental/_provider/`
    for folder_name in project.asset_subdir_names:
        supplemental_root = get_folder_supplemental_dir(project.project_root_path, folder_name)
        if not supplemental_root.is_dir():
            continue
        try:
            providers = sorted(supplemental_root.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            continue
        for provider_dir in providers:
            try:
                if not provider_dir.is_dir() or not provider_dir.name.startswith("_"):
                    continue
            except OSError:
                continue
            for media_path in list_media_files(provider_dir):
                if _path_in_inventory(media_path, all_paths):
                    continue
                try:
                    key = str(media_path.resolve())
                except OSError:
                    key = str(media_path)
                if key in seen:
                    continue
                sidecar = load_sidecar(media_path)
                seen.add(key)
                missing.append(
                    {
                        "folder_name": folder_name,
                        "request_id": (sidecar.supplement_request_id if sidecar else ""),
                        "asset_path": str(media_path),
                        "asset_id": (sidecar.asset_id if sidecar else ""),
                        "source": "folder_supplemental",
                    }
                )
    return missing


def analyze_and_import_missing_supplement_assets(
    project: Project,
    *,
    folder_names: list[str] | None = None,
    gemini_model: str = "",
) -> CutPlanInventoryImportReport:
    """Analysiert fehlende Supplement-Assets und schreibt sie ins Inventory.

    Nutzt vorhandene Validierungs-Beschreibungen, sonst Gemini-Frame-Beschreibung.
    """
    from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME
    from otio_app.services.supplement_pipeline import analyze_supplement_asset, load_sidecar

    report = CutPlanInventoryImportReport()
    allowed = set(folder_names) if folder_names is not None else None
    missing = list_supplement_assets_missing_from_inventory(project)
    manifest = load_cut_plan_supplement_manifest(project)

    for item in missing:
        folder = item["folder_name"]
        if allowed is not None and folder not in allowed:
            continue
        report.considered += 1
        media_path = Path(item["asset_path"])
        try:
            if SUPPLEMENTAL_FOLDER_NAME in media_path.parts:
                sidecar = load_sidecar(media_path)
                if sidecar is None:
                    raise ValueError("Sidecar fehlt unter _supplemental/")
                from otio_app.services.clean_media import process_and_persist_media_file

                try:
                    process_and_persist_media_file(project, folder, media_path)
                except Exception:
                    pass
                asset = analyze_supplement_asset(
                    project,
                    folder_name=folder,
                    local_path=media_path,
                    sidecar=sidecar,
                )
                extend_folder_inventory(project, folder_name=folder, asset=asset)
            else:
                entry = _manifest_entry_for_path(manifest, str(media_path))
                validation = _best_validation_for_request(entry, item.get("request_id", ""))
                asset = _build_asset_from_accepted(
                    project,
                    folder_name=folder,
                    request_id=item.get("request_id") or "cut_plan_disk",
                    accepted_asset_id=item.get("asset_id") or "",
                    accepted_asset_path=str(media_path),
                    manifest_entry=entry,
                    validation=validation,
                    analyze_if_needed=True,
                    gemini_model=gemini_model,
                )
                extend_folder_inventory(project, folder_name=folder, asset=asset)
        except (OSError, ValueError) as exc:
            report.skipped.append(f"{media_path.name}: {exc}")
            continue

        report.imported += 1
        report.imported_by_folder[folder] = report.imported_by_folder.get(folder, 0) + 1

    return report
