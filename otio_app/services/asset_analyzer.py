"""Asset-Ordner-Analyse mit Frame-Extraktion und Gemini."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, InventoryDocument
from otio_app.models import Project, validate_asset_selection
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    describe_folder_from_frames,
)
from otio_app.services.media_utils import list_media_files


def _folder_cache_path(project: Project, folder_name: str) -> Path:
    cache_dir = project.work_dir_path / "cache" / "inventory"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_name = folder_name.replace(" ", "_").replace("/", "_")
    return cache_dir / f"{safe_name}.json"


def _collect_frames_for_folder(
    project: Project,
    folder_path: Path,
    folder_name: str,
) -> tuple[list[Path], list[str]]:
    media_files = list_media_files(folder_path)
    if not media_files:
        return [], []

    frames_root = project.work_dir_path / "frames" / folder_name.replace(" ", "_")
    all_frames: list[Path] = []
    used_media: list[str] = []

    per_file = max(1, project.frames_per_shot)
    for media_path in media_files:
        frame_dir = frames_root / media_path.stem
        frames = extract_frames(media_path, frame_dir, per_file)
        if frames:
            used_media.append(str(media_path))
            all_frames.extend(frames)
        if len(all_frames) >= project.frames_per_shot:
            break

    return all_frames[: project.frames_per_shot], used_media


def analyze_asset_folders(
    project: Project,
    folder_names: list[str],
    *,
    use_api: bool = True,
) -> InventoryDocument:
    """Analysiert ausgewählte Asset-Ordner und schreibt inventory.json."""
    selected = validate_asset_selection(project.asset_subdir_names, folder_names)
    items: list[AssetFolderAnalysis] = []

    for folder_name in selected:
        cache_file = _folder_cache_path(project, folder_name)
        if cache_file.is_file():
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            items.append(AssetFolderAnalysis.model_validate(payload))
            continue

        folder_path = project.project_root_path / folder_name
        media_files = [str(path) for path in list_media_files(folder_path)]
        item = AssetFolderAnalysis(folder=folder_name, media_files=media_files)

        frames, used_media = _collect_frames_for_folder(
            project, folder_path, folder_name
        )
        item.frames_used = [str(path) for path in frames]
        item.media_files = used_media or media_files

        if not frames:
            item.description = "Keine analysierbaren Medien gefunden."
        elif use_api:
            try:
                item.description = describe_folder_from_frames(
                    folder_name,
                    frames,
                    project.language,
                )
            except GeminiNotConfiguredError:
                raise
            except Exception as exc:  # noqa: BLE001
                item.error = str(exc)
                item.description = ""
        else:
            item.error = "API-Aufruf nicht bestätigt."

        cache_file.write_text(item.model_dump_json(indent=2), encoding="utf-8")
        items.append(item)

    document = InventoryDocument(project_id=project.id, items=items)
    project.inventory_path.write_text(
        document.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return document
