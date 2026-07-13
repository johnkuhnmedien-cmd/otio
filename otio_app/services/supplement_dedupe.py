"""Deduplizierung von Supplement-Downloads unter `{folder}/_supplemental/_provider/`."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import SupplementAssetSidecar, SupplementManifest
from otio_app.models import Project
from otio_app.project_layout import (
    clean_output_path_for_media,
    get_folder_inventory_path,
    get_folder_supplemental_dir,
    get_supplement_manifest_path,
)
from otio_app.services.clean_media import (
    folder_manifest_path,
    load_clean_media_manifest,
    save_clean_media_manifest,
)
from otio_app.services.inventory_loader import load_folder_inventory, save_folder_inventory
from otio_app.services.media_utils import list_media_files


def _sidecar_path(local_path: Path) -> Path:
    return local_path.with_suffix(local_path.suffix + ".asset.json")


def load_sidecar(local_path: Path) -> SupplementAssetSidecar | None:
    path = _sidecar_path(local_path)
    if not path.is_file():
        return None
    try:
        return SupplementAssetSidecar.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


# Dateiname: …_pexels_27608379.mp4 bzw. …_adobe_12345.mov bzw. reused_pexels_27608379.mp4
_FILENAME_PROVIDER_ASSET_RE = re.compile(
    r"(?:^|_)(?P<provider>pexels|adobe)(?:_stock)?_(?P<asset_id>[A-Za-z0-9]+)(?:\.[^.]+)+$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SupplementOnDisk:
    path: Path
    provider: str
    provider_asset_id: str
    sidecar: SupplementAssetSidecar | None


@dataclass
class DuplicateGroup:
    provider: str
    provider_asset_id: str
    keep: Path | None
    remove: list[Path] = field(default_factory=list)

    @property
    def count(self) -> int:
        return (1 if self.keep is not None else 0) + len(self.remove)


@dataclass
class CleanupReport:
    folder_name: str
    groups: list[DuplicateGroup] = field(default_factory=list)
    deleted_media: list[str] = field(default_factory=list)
    deleted_sidecars: list[str] = field(default_factory=list)
    deleted_clean: list[str] = field(default_factory=list)
    inventory_pruned: int = 0
    manifest_pruned: int = 0
    clean_manifest_pruned: int = 0
    dry_run: bool = True

    @property
    def duplicate_file_count(self) -> int:
        return sum(len(group.remove) for group in self.groups)

    @property
    def group_count(self) -> int:
        return len(self.groups)


def _provider_from_dir(provider_dir: Path) -> str:
    name = provider_dir.name
    return name[1:] if name.startswith("_") else name


def _identity_from_path(path: Path, *, default_provider: str) -> tuple[str, str] | None:
    sidecar = load_sidecar(path)
    if sidecar is not None and sidecar.provider_asset_id:
        provider = (sidecar.provider or default_provider).strip().casefold()
        return provider, str(sidecar.provider_asset_id).strip()
    match = _FILENAME_PROVIDER_ASSET_RE.search(path.name)
    if match is None:
        return None
    provider = match.group("provider").casefold()
    if provider == "adobe":
        provider = "adobe_stock"
    return provider, match.group("asset_id")


def iter_supplement_on_disk(project: Project, folder_name: str) -> list[SupplementOnDisk]:
    """Alle Supplement-Medien eines Ordners mit Provider-Asset-Identität."""
    root = get_folder_supplemental_dir(project.project_root_path, folder_name)
    if not root.is_dir():
        return []
    found: list[SupplementOnDisk] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    for provider_dir in children:
        try:
            if not provider_dir.is_dir() or not provider_dir.name.startswith("_"):
                continue
        except OSError:
            continue
        default_provider = _provider_from_dir(provider_dir)
        for media_path in list_media_files(provider_dir):
            identity = _identity_from_path(media_path, default_provider=default_provider)
            if identity is None:
                continue
            provider, provider_asset_id = identity
            found.append(
                SupplementOnDisk(
                    path=media_path,
                    provider=provider,
                    provider_asset_id=provider_asset_id,
                    sidecar=load_sidecar(media_path),
                )
            )
    return found


def find_existing_provider_asset(
    project: Project,
    folder_name: str,
    *,
    provider: str,
    provider_asset_id: str,
) -> Path | None:
    """Erste vorhandene Datei für `(provider, provider_asset_id)` — sonst None."""
    wanted_provider = provider.strip().casefold()
    wanted_id = str(provider_asset_id).strip()
    if not wanted_provider or not wanted_id:
        return None
    for entry in iter_supplement_on_disk(project, folder_name):
        if entry.provider == wanted_provider and entry.provider_asset_id == wanted_id:
            if entry.path.is_file() and entry.path.stat().st_size > 0:
                return entry.path
    return None


def provider_asset_already_downloaded(
    project: Project,
    folder_name: str,
    *,
    provider: str,
    provider_asset_id: str,
) -> bool:
    return find_existing_provider_asset(
        project,
        folder_name,
        provider=provider,
        provider_asset_id=provider_asset_id,
    ) is not None


def _keeper_rank(entry: SupplementOnDisk) -> tuple:
    sidecar = entry.sidecar
    approved = 0
    downloaded = 0.0
    if sidecar is not None:
        if sidecar.approved_for_cut_plan or sidecar.supplement_validation_status == "PASS":
            approved = 1
        if sidecar.downloaded_at is not None:
            dt = sidecar.downloaded_at
            if dt.tzinfo is None:
                downloaded = dt.timestamp()
            else:
                downloaded = dt.timestamp()
    try:
        stat = entry.path.stat()
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        mtime = 0.0
        size = 0
    # Höher = besserer Keeper
    return (approved, downloaded, mtime, size)


def scan_supplement_duplicates(project: Project, folder_name: str) -> list[DuplicateGroup]:
    """Gruppiert gleiche Provider-Asset-IDs; Keeper bleibt, Rest ist entfernen."""
    by_key: dict[tuple[str, str], list[SupplementOnDisk]] = {}
    for entry in iter_supplement_on_disk(project, folder_name):
        by_key.setdefault((entry.provider, entry.provider_asset_id), []).append(entry)

    groups: list[DuplicateGroup] = []
    for (provider, provider_asset_id), entries in sorted(by_key.items()):
        if len(entries) < 2:
            continue
        ranked = sorted(entries, key=_keeper_rank, reverse=True)
        keep = ranked[0].path
        remove = [item.path for item in ranked[1:]]
        groups.append(
            DuplicateGroup(
                provider=provider,
                provider_asset_id=provider_asset_id,
                keep=keep,
                remove=remove,
            )
        )
    return groups


def _delete_path(path: Path) -> bool:
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def _prune_inventory(project: Project, folder_name: str, deleted_paths: set[str]) -> int:
    if not deleted_paths:
        return 0
    inventory = load_folder_inventory(project, folder_name)
    if inventory is None:
        return 0
    before = len(inventory.assets)
    assets = [asset for asset in inventory.assets if asset.path not in deleted_paths]
    media_files = [path for path in inventory.media_files if path not in deleted_paths]
    if len(assets) == before and len(media_files) == len(inventory.media_files):
        return 0
    updated = inventory.model_copy(update={"assets": assets, "media_files": media_files})
    save_folder_inventory(get_folder_inventory_path(project.work_dir_path, folder_name), updated)
    return before - len(assets)


def _prune_supplement_manifest(project: Project, deleted_paths: set[str]) -> int:
    if not deleted_paths:
        return 0
    path = get_supplement_manifest_path(project.language_work_dir_path)
    if not path.is_file():
        return 0
    try:
        manifest = SupplementManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return 0
    before = len(manifest.entries)
    entries = [entry for entry in manifest.entries if entry.local_path not in deleted_paths]
    if len(entries) == before:
        return 0
    updated = manifest.model_copy(
        update={"entries": entries, "generated_at": datetime.now(timezone.utc)}
    )
    path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    return before - len(entries)


def _prune_clean_artifacts(
    project: Project,
    folder_name: str,
    deleted_media: list[Path],
) -> tuple[list[str], int]:
    deleted_clean: list[str] = []
    deleted_keys = {_path_key(path) for path in deleted_media}
    for media_path in deleted_media:
        clean = clean_output_path_for_media(project.work_dir_path, folder_name, media_path)
        if _delete_path(clean):
            deleted_clean.append(str(clean))
        # Zoom/Crop-Varianten: gleiches Verzeichnis, Stem-Prefix
        parent = clean.parent
        if parent.is_dir():
            stem = clean.stem
            for sibling in parent.glob(f"{stem}_*.mp4"):
                if _delete_path(sibling):
                    deleted_clean.append(str(sibling))

    manifest_path = folder_manifest_path(project, folder_name)
    manifest = load_clean_media_manifest(manifest_path)
    pruned = 0
    if manifest is not None and deleted_keys:
        kept = []
        for entry in manifest.entries:
            try:
                key = str(Path(entry.original_path).expanduser().resolve())
            except OSError:
                key = str(Path(entry.original_path))
            if key in deleted_keys:
                pruned += 1
                continue
            kept.append(entry)
        if pruned:
            updated = manifest.model_copy(update={"entries": kept})
            save_clean_media_manifest(manifest_path, updated)
    return deleted_clean, pruned


def _path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path)


def cleanup_supplement_duplicates(
    project: Project,
    folder_name: str,
    *,
    dry_run: bool = True,
) -> CleanupReport:
    """Entfernt doppelte Provider-Assets; behält je `(provider, provider_asset_id)` eine Datei."""
    groups = scan_supplement_duplicates(project, folder_name)
    report = CleanupReport(folder_name=folder_name, groups=groups, dry_run=dry_run)
    if dry_run or not groups:
        return report

    deleted_media: list[Path] = []
    for group in groups:
        for path in group.remove:
            sidecar = _sidecar_path(path)
            if _delete_path(path):
                report.deleted_media.append(str(path))
                deleted_media.append(path)
            if _delete_path(sidecar):
                report.deleted_sidecars.append(str(sidecar))

    deleted_strs: set[str] = set()
    for path in deleted_media:
        deleted_strs.add(str(path))
        deleted_strs.add(_path_key(path))
        try:
            deleted_strs.add(str(path.resolve()))
        except OSError:
            pass

    report.inventory_pruned = _prune_inventory(project, folder_name, deleted_strs)
    report.manifest_pruned = _prune_supplement_manifest(project, deleted_strs)
    deleted_clean, clean_pruned = _prune_clean_artifacts(project, folder_name, deleted_media)
    report.deleted_clean = deleted_clean
    report.clean_manifest_pruned = clean_pruned
    report.dry_run = False
    return report


def _cut_plan_referenced_paths(project: Project) -> set[str]:
    """Pfade, die Draft, Manifest oder akzeptierte Requests noch brauchen."""
    refs: set[str] = set()

    def _add(raw: str | Path | None) -> None:
        if not raw:
            return
        path = Path(raw)
        refs.add(str(path))
        refs.add(_path_key(path))
        try:
            refs.add(str(path.resolve()))
        except OSError:
            pass

    from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft
    from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
        load_cut_plan_supplement_manifest,
        load_cut_plan_supplement_requests,
    )

    draft = load_cut_plan_draft(project)
    if draft is not None:
        for item in draft.items:
            for segment in item.planned_visual_segments:
                _add(segment.asset_path)

    manifest = load_cut_plan_supplement_manifest(project)
    for entry in manifest.entries:
        _add(entry.asset_path)

    requests_doc = load_cut_plan_supplement_requests(project)
    if requests_doc is not None:
        for request in requests_doc.requests:
            _add(request.accepted_asset_path)
    return refs


def _identity_from_cut_plan_media(path: Path) -> tuple[str, str] | None:
    # reused_pexels_<id> oder …_pexels_<id>
    match = _FILENAME_PROVIDER_ASSET_RE.search(path.name)
    if match is None:
        # Fallback: reused_<provider>_<id>
        reused = re.search(
            r"^reused_(?P<provider>[A-Za-z0-9_]+)_(?P<asset_id>[A-Za-z0-9]+)\.[^.]+$",
            path.name,
            re.IGNORECASE,
        )
        if reused is None:
            return None
        provider = reused.group("provider").casefold()
        if provider == "adobe":
            provider = "adobe_stock"
        return provider, reused.group("asset_id")
    provider = match.group("provider").casefold()
    if provider == "adobe":
        provider = "adobe_stock"
    return provider, match.group("asset_id")


def iter_cut_plan_supplement_on_disk(project: Project) -> list[SupplementOnDisk]:
    from otio_app.project_layout import get_cut_plan_supplement_assets_dir

    root = get_cut_plan_supplement_assets_dir(project.language_work_dir_path)
    if not root.is_dir():
        return []
    found: list[SupplementOnDisk] = []
    try:
        request_dirs = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    for request_dir in request_dirs:
        try:
            if not request_dir.is_dir():
                continue
        except OSError:
            continue
        for media_path in list_media_files(request_dir):
            identity = _identity_from_cut_plan_media(media_path)
            if identity is None:
                continue
            provider, provider_asset_id = identity
            found.append(
                SupplementOnDisk(
                    path=media_path,
                    provider=provider,
                    provider_asset_id=provider_asset_id,
                    sidecar=load_sidecar(media_path),
                )
            )
    return found


def scan_cut_plan_supplement_orphans(project: Project) -> list[DuplicateGroup]:
    """Ungenutzte Cut-Plan-Kopien derselben Provider-Asset-ID."""
    referenced = _cut_plan_referenced_paths(project)
    by_key: dict[tuple[str, str], list[SupplementOnDisk]] = {}
    for entry in iter_cut_plan_supplement_on_disk(project):
        by_key.setdefault((entry.provider, entry.provider_asset_id), []).append(entry)

    groups: list[DuplicateGroup] = []
    for (provider, provider_asset_id), entries in sorted(by_key.items()):
        referenced_entries = [
            entry for entry in entries if _path_key(entry.path) in referenced or str(entry.path) in referenced
        ]
        unreferenced = [
            entry for entry in entries if _path_key(entry.path) not in referenced and str(entry.path) not in referenced
        ]
        if not unreferenced:
            continue
        if referenced_entries:
            keep = referenced_entries[0].path
            remove = [entry.path for entry in unreferenced]
        elif len(entries) >= 2:
            ranked = sorted(entries, key=_keeper_rank, reverse=True)
            keep = ranked[0].path
            remove = [item.path for item in ranked[1:]]
        else:
            # Einzelne unreferenzierte Datei — als Orphan löschbar
            keep = None
            remove = [entries[0].path]
        if not remove:
            continue
        groups.append(
            DuplicateGroup(
                provider=provider,
                provider_asset_id=provider_asset_id,
                keep=keep,
                remove=remove,
            )
        )
    return groups


def cleanup_cut_plan_supplement_orphans(
    project: Project,
    *,
    dry_run: bool = True,
) -> CleanupReport:
    groups = scan_cut_plan_supplement_orphans(project)
    report = CleanupReport(folder_name="cut_plan/supplement_assets", groups=groups, dry_run=dry_run)
    if dry_run or not groups:
        return report
    for group in groups:
        for path in group.remove:
            sidecar = _sidecar_path(path)
            if _delete_path(path):
                report.deleted_media.append(str(path))
            if _delete_path(sidecar):
                report.deleted_sidecars.append(str(sidecar))
            # Leere Request-Ordner aufräumen
            parent = path.parent
            try:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
    report.dry_run = False
    return report
