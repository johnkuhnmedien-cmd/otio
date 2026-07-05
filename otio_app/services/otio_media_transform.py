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


def media_resolution_probe(path: Path) -> tuple[int | None, int | None]:
    """Aktuelle Breite/Höhe per ffprobe (nicht aus Cache/Manifest)."""
    from otio_app.services.clean_media import probe_media as probe_media_fn

    probe = probe_media_fn(path)
    return probe.width, probe.height


def aspect_fill_warning(
    project: Project,
    media_path: Path,
    *,
    label: str | None = None,
) -> str | None:
    """Warntext, wenn die Datei nicht zum Projekt-Seitenverhältnis passt."""
    width, height = media_resolution_probe(media_path)
    name = label or media_path.name
    if not width or not height:
        return (
            f"{name}: Auflösung nicht lesbar (ffprobe) — "
            "Zoom-Regel konnte nicht geprüft werden."
        )
    if media_needs_aspect_fill(width, height, project.width, project.height):
        return (
            f"{name}: {width}×{height} statt Ziel {project.width}×{project.height} — "
            "Letterboxing möglich."
        )
    return None


def ensure_zoomed_media_for_export(
    project: Project,
    folder_name: str,
    original_path: Path,
    *,
    notes: list[str] | None = None,
) -> Path:
    """Prüft jedes Asset, transkodiert bei Bedarf und verifiziert die Ausgabe-Auflösung."""
    from otio_app.services.clean_media import (
        CLEAN_STATUS_FAILED,
        process_media_file,
        resolve_effective_media_path,
    )
    from otio_app.services.edit_plan_rules import export_rule_options, load_edit_plan_rules

    opts = export_rule_options(load_edit_plan_rules(project))
    fallback = resolve_effective_media_path(project, folder_name, original_path)
    if not opts.auto_zoom_fill or is_image_media(original_path):
        return fallback

    src_w, src_h = media_resolution_probe(original_path)
    if not src_w or not src_h:
        src_w, src_h = resolve_media_dimensions(project, folder_name, original_path)
    if not src_w or not src_h:
        if notes is not None:
            notes.append(
                f"{original_path.name}: Quell-Auflösung nicht lesbar — Zoom übersprungen."
            )
        return fallback

    if not media_needs_aspect_fill(src_w, src_h, project.width, project.height):
        return fallback

    def _resolved_path(entry) -> Path:
        if entry.clean_path:
            return Path(entry.clean_path).expanduser().resolve()
        return fallback

    entry = process_media_file(project, folder_name, original_path)
    media_path = _resolved_path(entry)

    out_w, out_h = media_resolution_probe(media_path)
    still_wrong = (
        not out_w
        or not out_h
        or media_needs_aspect_fill(out_w, out_h, project.width, project.height)
    )
    if still_wrong:
        entry = process_media_file(
            project,
            folder_name,
            original_path,
            force_transcode=True,
        )
        media_path = _resolved_path(entry)
        if entry.status == CLEAN_STATUS_FAILED and entry.error and notes is not None:
            notes.append(f"{original_path.name}: Zoom-Transcode fehlgeschlagen — {entry.error}")
        out_w, out_h = media_resolution_probe(media_path)

    warning = aspect_fill_warning(project, media_path, label=original_path.name)
    if warning:
        if notes is not None:
            notes.append(warning)
    elif notes is not None and out_w and out_h:
        notes.append(
            f"{original_path.name}: {src_w}×{src_h} → {out_w}×{out_h} (16:9 angepasst)"
        )

    return media_path


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
