"""Zoom- und Trim-Hilfen für OTIO-Export und Clean Media."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.models import Project
from otio_app.services.clean_media import (
    _entry_for_original,
    folder_manifest_path,
    load_clean_media_manifest,
    probe_media,
)
from otio_app.services.media_utils import is_image_media


def compute_fill_zoom_factor(
    asset_width: int,
    asset_height: int,
    target_width: int,
    target_height: int,
    *,
    tolerance: float = 0.005,
) -> float | None:
    """Zoom-Faktor, um Letterboxing/Pillarboxing auf Ziel-Seitenverhältnis zu füllen."""
    if asset_width <= 0 or asset_height <= 0 or target_width <= 0 or target_height <= 0:
        return None
    asset_aspect = asset_width / asset_height
    target_aspect = target_width / target_height
    if abs(asset_aspect - target_aspect) <= tolerance:
        return None
    return max(asset_aspect / target_aspect, target_aspect / asset_aspect)


def resolve_media_dimensions(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> tuple[int | None, int | None]:
    """Breite/Höhe aus Clean-Media-Manifest oder ffprobe."""
    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    entry = _entry_for_original(manifest, media_path) if manifest else None
    if entry is not None and entry.probe is not None:
        if entry.probe.width and entry.probe.height:
            return entry.probe.width, entry.probe.height
    probe = probe_media(media_path)
    return probe.width, probe.height


def media_needs_aspect_fill(
    asset_width: int,
    asset_height: int,
    target_width: int,
    target_height: int,
    *,
    tolerance: float = 0.005,
) -> bool:
    return (
        compute_fill_zoom_factor(
            asset_width,
            asset_height,
            target_width,
            target_height,
            tolerance=tolerance,
        )
        is not None
    )


def ensure_zoomed_media_for_export(
    project: Project,
    folder_name: str,
    original_path: Path,
) -> Path:
    """Liefert einen auf Projekt-Seitenverhältnis gezoomten Clean-Pfad (falls Regel aktiv)."""
    from otio_app.services.clean_media import process_media_file, resolve_effective_media_path
    from otio_app.services.edit_plan_rules import export_rule_options, load_edit_plan_rules

    opts = export_rule_options(load_edit_plan_rules(project))
    if not opts.auto_zoom_fill or is_image_media(original_path):
        return resolve_effective_media_path(project, folder_name, original_path)

    entry = process_media_file(project, folder_name, original_path)
    if entry.clean_path:
        return Path(entry.clean_path).expanduser().resolve()
    return resolve_effective_media_path(project, folder_name, original_path)


def ffmpeg_scale_crop_filter(
    asset_width: int,
    asset_height: int,
    target_width: int,
    target_height: int,
) -> str | None:
    """ffmpeg vf-Kette: hochskalieren und auf Ziel-Seitenverhältnis beschneiden."""
    zoom = compute_fill_zoom_factor(asset_width, asset_height, target_width, target_height)
    if zoom is None:
        return None
    return (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height}"
    )


def build_resolve_zoom_effect(zoom: float) -> otio.schema.Effect:
    """Versucht einen statischen Zoom für Resolve-OTIO-Import (best effort)."""
    return otio.schema.Effect(
        name="Zoom Fill",
        effect_name="Transform",
        metadata={
            "Resolve_OTIO": {
                "ZoomX": zoom,
                "ZoomY": zoom,
                "Pan": 0.0,
                "Tilt": 0.0,
            },
            "otio_app": {
                "zoom_mode": "fill",
                "zoom_factor": round(zoom, 4),
            },
        },
    )
