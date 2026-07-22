"""Inventar vor Cut Plan vorbereiten: Asset-Dauern messen + Slim schreiben.

Läuft bewusst als eigener Schritt vor LLM-Lauf 2, damit Dauern persistent in
``inventory/{folder}.json`` und ``{folder}.slim.json`` liegen — nicht nur
ephemeral beim Prompt-Bau.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import load_folder_inventory, save_folder_inventory
from otio_app.services.inventory_prompt_view import slim_inventory_path_for
from otio_app.services.media_utils import (
    is_image_media,
    probe_duration_seconds,
    probe_leading_black_seconds,
)

__all__ = [
    "InventoryPrepareReport",
    "prepare_inventories_for_cut_plan",
    "inventory_duration_coverage",
]


@dataclass
class InventoryPrepareReport:
    folders_touched: int = 0
    assets_total: int = 0
    assets_with_duration: int = 0
    assets_missing_duration: int = 0
    assets_non_video: int = 0
    durations_newly_measured: int = 0
    usable_in_newly_measured: int = 0
    slim_files_written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _is_video_asset(path: str, media_type: str | None) -> bool:
    raw = (media_type or "").strip().lower()
    if raw in {"image", "photo", "audio"}:
        return False
    if raw == "video":
        return True
    try:
        if is_image_media(Path(path)):
            return False
    except Exception:  # noqa: BLE001
        pass
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".gif"}:
        return False
    if suffix in {".mp3", ".wav", ".aac", ".m4a", ".flac"}:
        return False
    return True


def _folder_names_for_project(project: Project) -> list[str]:
    names: list[str] = []
    try:
        from otio_app.services.without_voiceover_enhanced.script_author_service import (
            list_enabled_dramaturgy_folders,
        )

        for entry in list_enabled_dramaturgy_folders(project):
            if entry.folder_name and entry.folder_name not in names:
                names.append(entry.folder_name)
    except Exception:  # noqa: BLE001
        pass
    for folder in project.selected_asset_subdirs or []:
        if folder and folder not in names:
            names.append(folder)
    return names


def inventory_duration_coverage(
    project: Project, *, folder_names: list[str] | None = None
) -> tuple[int, int, int]:
    """(videos_with_duration, videos_total, folders_with_inventory)."""
    folders = folder_names if folder_names is not None else _folder_names_for_project(project)
    with_dur = 0
    total_video = 0
    folders_ok = 0
    for folder in folders:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        folders_ok += 1
        for asset in inventory.assets or []:
            path = str(asset.path or "").strip()
            if not path:
                continue
            if not _is_video_asset(path, asset.media_type):
                continue
            total_video += 1
            if asset.duration_seconds is not None and float(asset.duration_seconds) > 0:
                with_dur += 1
    return with_dur, total_video, folders_ok


def prepare_inventories_for_cut_plan(
    project: Project,
    *,
    folder_names: list[str] | None = None,
    force_reprobe: bool = False,
) -> InventoryPrepareReport:
    """Misst fehlende Video-Dauern, speichert Inventar + Slim-JSON."""
    report = InventoryPrepareReport()
    folders = folder_names if folder_names is not None else _folder_names_for_project(project)
    if not folders:
        report.errors.append("Keine Kapitel/Ordner für Inventar-Vorbereitung gefunden.")
        return report

    for folder in folders:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            report.errors.append(f"Kein Inventar für „{folder}“.")
            continue

        changed = False
        updated_assets = []
        for asset in inventory.assets or []:
            path = str(asset.path or "").strip()
            report.assets_total += 1
            if not path:
                updated_assets.append(asset)
                continue

            # Enhanced: eindeutige Asset-IDs (Ordner-Scope + Stem + Hash).
            from otio_app.services.without_voiceover_enhanced.asset_identity import (
                canonicalize_inventory_asset_id,
            )

            media_path_for_id = Path(path)
            if not media_path_for_id.is_file():
                media_path_for_id = (
                    Path(project.project_root).expanduser() / path
                )
            canonical_id = canonicalize_inventory_asset_id(
                project,
                path=media_path_for_id if media_path_for_id.is_file() else path,
                folder_name=folder,
                existing_id=str(asset.asset_id or ""),
            )
            id_updates: dict = {}
            if canonical_id and canonical_id != str(asset.asset_id or ""):
                id_updates["asset_id"] = canonical_id
                changed = True

            if not _is_video_asset(path, asset.media_type):
                report.assets_non_video += 1
                # Bilder/Audio: Dauer/Lead-In explizit leeren (null in Slim).
                updates: dict = dict(id_updates)
                if asset.duration_seconds is not None:
                    updates["duration_seconds"] = None
                if asset.usable_in_s is not None:
                    updates["usable_in_s"] = None
                if updates:
                    updated_assets.append(asset.model_copy(update=updates))
                    changed = True
                else:
                    updated_assets.append(asset)
                continue

            existing = asset.duration_seconds
            updates: dict = dict(id_updates)
            media_path = Path(path)

            if (
                force_reprobe
                or existing is None
                or float(existing) <= 0
            ):
                if not media_path.is_file():
                    report.assets_missing_duration += 1
                    report.errors.append(f"Datei fehlt: {path}")
                    updated_assets.append(asset)
                    continue
                try:
                    probed = probe_duration_seconds(media_path)
                except Exception as exc:  # noqa: BLE001
                    report.assets_missing_duration += 1
                    report.errors.append(
                        f"Dauer nicht lesbar ({media_path.name}): {exc}"
                    )
                    updated_assets.append(asset)
                    continue
                if probed is None or probed <= 0:
                    report.assets_missing_duration += 1
                    report.errors.append(f"Dauer ≤ 0: {media_path.name}")
                    updated_assets.append(asset)
                    continue
                updates["duration_seconds"] = round(float(probed), 3)
                report.durations_newly_measured += 1

            # usable_in_s: Schwarz-Lead-In (auch wenn Dauer schon bekannt).
            need_usable = force_reprobe or asset.usable_in_s is None
            if need_usable:
                if not media_path.is_file():
                    if "duration_seconds" not in updates:
                        report.assets_missing_duration += 1
                        report.errors.append(f"Datei fehlt: {path}")
                        updated_assets.append(asset)
                        continue
                else:
                    try:
                        leading = probe_leading_black_seconds(media_path)
                    except Exception as exc:  # noqa: BLE001
                        report.errors.append(
                            f"usable_in_s nicht lesbar ({media_path.name}): {exc}"
                        )
                        leading = None
                    if leading is not None:
                        updates["usable_in_s"] = round(float(leading), 3)
                        report.usable_in_newly_measured += 1

            if updates:
                updated_assets.append(asset.model_copy(update=updates))
                changed = True
            else:
                updated_assets.append(asset)
            report.assets_with_duration += 1

        folder_doc = inventory.model_copy(update={"assets": updated_assets})
        inv_path = get_folder_inventory_path(project.work_dir_path, folder)
        # Immer speichern → Slim mit dauer_s neu schreiben (auch wenn nichts neu gemessen).
        save_folder_inventory(inv_path, folder_doc)
        report.folders_touched += 1
        slim_path = slim_inventory_path_for(inv_path)
        if slim_path.is_file():
            report.slim_files_written.append(str(slim_path))
        elif changed:
            report.errors.append(f"Slim-Datei fehlt nach Speichern: {slim_path}")

    return report
