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
from otio_app.services.analysis_progress import AnalysisRunReport, ProgressCallback, noop_progress
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    describe_media_from_frames,
)
from otio_app.services.inventory_loader import (
    materialize_folder_inventory_from_cache,
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


def _count_media_for_folders(project: Project, folder_names: list[str]) -> int:
    total = 0
    for folder_name in folder_names:
        total += len(list_media_files(project.project_root_path / folder_name))
    return total


def _analyze_single_media(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    use_api: bool,
    model: Optional[str],
) -> tuple[AssetMediaAnalysis, str]:
    cache_file = media_cache_path(project, folder_name, media_path)
    cached = load_cached_media(cache_file)
    if cached is not None and is_completed_analysis(cached):
        if cached.error and not cached.description.strip():
            return cached, "fehler"
        return cached, "cache"

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
        save_cached_media(cache_file, entry)
        return entry, "fehler"
    if use_api:
        try:
            entry.description = describe_media_from_frames(
                media_path.name,
                folder_name,
                frames,
                project.language,
                model=model,
            )
            entry.error = None
            save_cached_media(cache_file, entry)
            return entry, "neu"
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            entry.error = str(exc)
            entry.description = ""
            save_cached_media(cache_file, entry)
            return entry, "fehler"
    entry.error = "API-Aufruf nicht bestätigt."
    save_cached_media(cache_file, entry)
    return entry, "fehler"


def _analyze_folder(
    project: Project,
    folder_name: str,
    *,
    use_api: bool,
    model: Optional[str],
    on_progress: ProgressCallback = noop_progress,
    folder_index: int = 0,
    folder_count: int = 1,
    report: AnalysisRunReport | None = None,
) -> AssetFolderAnalysis:
    folder_path = project.project_root_path / folder_name
    media_paths = list_media_files(folder_path)

    materialize_folder_inventory_from_cache(project, folder_name)

    existing = should_skip_folder_analysis(project, folder_name, media_paths)
    if existing is not None:
        on_progress(
            "folder_skip",
            {
                "folder": folder_name,
                "folder_index": folder_index,
                "folder_count": folder_count,
                "reason": "Ordner-Inventar bereits vollständig",
            },
        )
        if report is not None:
            report.folders_skipped.append(folder_name)
        return existing

    on_progress(
        "folder_start",
        {
            "folder": folder_name,
            "folder_index": folder_index,
            "folder_count": folder_count,
            "media_count": len(media_paths),
        },
    )

    assets: list[AssetMediaAnalysis] = []
    for media_index, media_path in enumerate(media_paths, start=1):
        on_progress(
            "media_start",
            {
                "folder": folder_name,
                "media_name": media_path.name,
                "media_index": media_index,
                "media_count": len(media_paths),
                "folder_index": folder_index,
                "folder_count": folder_count,
            },
        )
        try:
            entry, outcome = _analyze_single_media(
                project,
                folder_name,
                media_path,
                use_api=use_api,
                model=model,
            )
            assets.append(entry)
            if report is not None:
                if outcome == "cache":
                    report.media_cached += 1
                elif outcome == "neu":
                    report.media_analyzed += 1
                else:
                    report.media_failed += 1
                    if entry.error:
                        report.failures.append(f"{folder_name}/{media_path.name}: {entry.error}")
                    else:
                        report.failures.append(
                            f"{folder_name}/{media_path.name}: Keine analysierbaren Medien"
                        )
            on_progress(
                "media_done",
                {
                    "folder": folder_name,
                    "media_name": media_path.name,
                    "media_index": media_index,
                    "media_count": len(media_paths),
                    "folder_index": folder_index,
                    "folder_count": folder_count,
                    "outcome": outcome,
                    "error": entry.error,
                },
            )
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            entry = AssetMediaAnalysis(path=str(media_path), error=str(exc))
            assets.append(entry)
            if report is not None:
                report.media_failed += 1
                report.failures.append(f"{folder_name}/{media_path.name}: {exc}")
            on_progress(
                "media_done",
                {
                    "folder": folder_name,
                    "media_name": media_path.name,
                    "media_index": media_index,
                    "media_count": len(media_paths),
                    "outcome": "fehler",
                    "error": str(exc),
                },
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
    if report is not None:
        report.folders_processed.append(folder_name)
    on_progress(
        "folder_done",
        {
            "folder": folder_name,
            "folder_index": folder_index,
            "folder_count": folder_count,
            "asset_count": len(assets),
        },
    )
    return item


def analyze_asset_folders(
    project: Project,
    folder_names: list[str],
    *,
    use_api: bool = True,
    model: Optional[str] = None,
    on_progress: ProgressCallback = noop_progress,
) -> tuple[InventoryDocument, AnalysisRunReport]:
    """Analysiert Asset-Ordner und schreibt pro Ordner eine JSON unter _otio/inventory/."""
    selected = validate_asset_selection(project.asset_subdir_names, folder_names)
    report = AnalysisRunReport()
    total_media = _count_media_for_folders(project, selected)
    on_progress(
        "start",
        {
            "folder_count": len(selected),
            "total_media": total_media,
        },
    )

    items: list[AssetFolderAnalysis] = []
    media_step = 0

    for folder_index, folder_name in enumerate(selected, start=1):
        item = _analyze_folder(
            project,
            folder_name,
            use_api=use_api,
            model=model,
            on_progress=on_progress,
            folder_index=folder_index,
            folder_count=len(selected),
            report=report,
        )
        items.append(item)

    on_progress(
        "complete",
        {
            "processed_media": media_step,
            "total_media": max(total_media, 1),
            "done": True,
        },
    )
    document = InventoryDocument(project_id=project.id, items=items)
    return document, report
