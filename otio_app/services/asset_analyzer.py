"""Asset-Ordner-Analyse mit Frame-Extraktion und Gemini."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    InventoryDocument,
)
from otio_app.models import Project, validate_asset_selection
from otio_app.project_layout import get_folder_inventory_path, safe_folder_slug
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    describe_media_from_frames,
)
from otio_app.services.inventory_loader import (
    save_folder_inventory,
    should_skip_folder_analysis,
)
from otio_app.services.media_inventory_cache import (
    is_completed_analysis,
    load_cached_media,
    media_cache_path,
    save_cached_media,
)
from otio_app.services.media_utils import list_media_files


def _frames_dir(project: Project, folder_name: str, media_path: Path) -> Path:
    return (
        project.work_dir_path
        / "frames"
        / safe_folder_slug(folder_name)
        / safe_folder_slug(media_path.stem)
    )


def _folder_summary(assets: list[AssetMediaAnalysis]) -> str:
    parts: list[str] = []
    for asset in assets:
        if asset.description:
            parts.append(f"{Path(asset.path).name}: {asset.description}")
    return "\n\n".join(parts)


def _analyze_single_media(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    use_api: bool,
    model: Optional[str],
) -> AssetMediaAnalysis:
    cache_file = media_cache_path(project, folder_name, media_path)
    cached = load_cached_media(cache_file)
    if cached is not None and is_completed_analysis(cached):
        return cached

    per_file = max(1, project.frames_per_shot)
    frames = extract_frames(
        media_path,
        _frames_dir(project, folder_name, media_path),
        per_file,
    )
    entry = AssetMediaAnalysis(
        path=str(media_path),
        frames_used=[str(frame) for frame in frames],
    )

    if not frames:
        entry.description = "Keine analysierbaren Medien gefunden."
    elif use_api:
        try:
            entry.description = describe_media_from_frames(
                media_path.name,
                folder_name,
                frames,
                project.language,
                model=model,
            )
            entry.error = None
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            entry.error = str(exc)
            entry.description = ""
    else:
        entry.error = "API-Aufruf nicht bestätigt."

    save_cached_media(cache_file, entry)
    return entry


def _analyze_folder(
    project: Project,
    folder_name: str,
    *,
    use_api: bool,
    model: Optional[str],
) -> AssetFolderAnalysis:
    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)

    existing = should_skip_folder_analysis(project, folder_name, media_paths)
    if existing is not None:
        return existing

    assets: list[AssetMediaAnalysis] = []
    for media_path in media_paths:
        try:
            assets.append(
                _analyze_single_media(
                    project,
                    folder_name,
                    media_path,
                    use_api=use_api,
                    model=model,
                )
            )
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            assets.append(
                AssetMediaAnalysis(
                    path=str(media_path),
                    error=str(exc),
                )
            )

    item = AssetFolderAnalysis(
        folder=folder_name,
        media_files=[asset.path for asset in assets],
        assets=assets,
        frames_used=[frame for asset in assets for frame in asset.frames_used],
        description=_folder_summary(assets),
    )
    if not assets:
        item.description = "Keine analysierbaren Medien gefunden."

    out_path = get_folder_inventory_path(project.work_dir_path, folder_name)
    save_folder_inventory(out_path, item)
    return item


def analyze_asset_folders(
    project: Project,
    folder_names: list[str],
    *,
    use_api: bool = True,
    model: Optional[str] = None,
) -> InventoryDocument:
    """Analysiert Asset-Ordner und schreibt pro Ordner eine JSON unter _otio/inventory/."""
    selected = validate_asset_selection(project.asset_subdir_names, folder_names)
    items: list[AssetFolderAnalysis] = []

    for folder_name in selected:
        items.append(
            _analyze_folder(
                project,
                folder_name,
                use_api=use_api,
                model=model,
            )
        )

    return InventoryDocument(project_id=project.id, items=items)
