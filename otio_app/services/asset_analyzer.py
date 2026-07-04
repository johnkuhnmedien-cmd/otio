"""Asset-Ordner-Analyse mit Frame-Extraktion und Gemini."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    InventoryDocument,
)
from otio_app.models import Project, validate_asset_selection
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    describe_media_from_frames,
)
from otio_app.services.media_utils import list_media_files


def _safe_cache_name(value: str) -> str:
    return value.replace(" ", "_").replace("/", "_")


def _media_cache_path(project: Project, folder_name: str, media_path: Path) -> Path:
    cache_dir = (
        project.work_dir_path
        / "cache"
        / "inventory"
        / _safe_cache_name(folder_name)
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{_safe_cache_name(media_path.name)}.json"


def _load_cached_media(cache_file: Path) -> Optional[AssetMediaAnalysis]:
    """Lädt einen gültigen Cache-Eintrag oder None bei Fehler/kaputtem Cache."""
    if not cache_file.is_file():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        return AssetMediaAnalysis.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _is_completed_analysis(entry: AssetMediaAnalysis) -> bool:
    """True, wenn dieses Asset bereits analysiert wurde (Erfolg oder dokumentierter Fehler)."""
    if entry.description.strip():
        return True
    return bool(entry.error)


def _save_cached_media(cache_file: Path, entry: AssetMediaAnalysis) -> None:
    cache_file.write_text(entry.model_dump_json(indent=2), encoding="utf-8")


def _frames_dir(project: Project, folder_name: str, media_path: Path) -> Path:
    return (
        project.work_dir_path
        / "frames"
        / _safe_cache_name(folder_name)
        / _safe_cache_name(media_path.stem)
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
    cache_file = _media_cache_path(project, folder_name, media_path)
    cached = _load_cached_media(cache_file)
    if cached is not None and _is_completed_analysis(cached):
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

    _save_cached_media(cache_file, entry)
    return entry


def analyze_asset_folders(
    project: Project,
    folder_names: list[str],
    *,
    use_api: bool = True,
    model: Optional[str] = None,
) -> InventoryDocument:
    """Analysiert alle Medien in ausgewählten Asset-Ordnern und schreibt inventory.json."""
    selected = validate_asset_selection(project.asset_subdir_names, folder_names)
    items: list[AssetFolderAnalysis] = []

    for folder_name in selected:
        folder_path = project.project_root_path / folder_name
        media_paths = list_media_files(folder_path)
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
            frames_used=[
                frame for asset in assets for frame in asset.frames_used
            ],
            description=_folder_summary(assets),
        )
        if not assets:
            item.description = "Keine analysierbaren Medien gefunden."
        items.append(item)

    document = InventoryDocument(project_id=project.id, items=items)
    project.inventory_path.write_text(
        document.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return document
