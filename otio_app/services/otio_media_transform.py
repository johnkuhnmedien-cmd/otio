"""Zoom-, Titel- und Trim-Hilfen für OTIO-Export und Clean Media."""

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


def media_matches_target_resolution(
    width: int | None,
    height: int | None,
    target_width: int,
    target_height: int,
) -> bool:
    return width == target_width and height == target_height


def aspect_fill_warning(
    project: Project,
    media_path: Path,
    *,
    label: str | None = None,
) -> str | None:
    """Warntext, wenn die Datei nicht exakt zur Projektauflösung passt."""
    width, height = media_resolution_probe(media_path)
    name = label or media_path.name
    if not width or not height:
        return (
            f"{name}: Auflösung nicht lesbar (ffprobe) — "
            "Zoom-Regel konnte nicht geprüft werden."
        )
    if not media_matches_target_resolution(width, height, project.width, project.height):
        return (
            f"{name}: {width}×{height} statt Ziel {project.width}×{project.height} — "
            f"Resolve verweist evtl. auf eine andere Datei. Pfad: `{media_path}`"
        )
    return None


def format_folder_display_name(folder_name: str) -> str:
    """Ordnername für Overlay — Unterstriche werden zu Leerzeichen."""
    return folder_name.replace("_", " ").strip()


def escape_drawtext_value(value: str) -> str:
    """Escaping für ffmpeg drawtext text=/fontfile=."""
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("%", "\\%")
    return escaped


def ffmpeg_folder_title_filter(
    *,
    text: str,
    font_path: Path,
    duration_sec: float,
    target_width: int,
    target_height: int,
) -> str:
    """drawtext-Filter: unten links, Schatten, nur erste N Sekunden."""
    safe_text = escape_drawtext_value(text)
    safe_font = escape_drawtext_value(str(font_path.resolve()))
    margin_x = max(24, target_width // 100)
    margin_y = max(24, target_height // 100)
    duration = max(0.1, duration_sec)
    return (
        f"drawtext=fontfile='{safe_font}':"
        f"text='{safe_text}':"
        f"fontsize=max(28\\,min(96\\,h/14)):"
        f"fontcolor=white:"
        f"x={margin_x}:"
        f"y=h-th-{margin_y}:"
        f"shadowcolor=black@0.65:"
        f"shadowx=3:"
        f"shadowy=3:"
        f"enable='lte(t\\,{duration:.3f})'"
    )


def build_export_video_filter(
    *,
    source_width: int | None,
    source_height: int | None,
    project: Project,
    auto_zoom_fill: bool,
) -> tuple[str | None, int | None, int | None, str | None]:
    """Baut vf-Kette für Zoom/Crop. Liefert (filter, w, h, error)."""
    parts: list[str] = []
    expected_width: int | None = None
    expected_height: int | None = None

    if auto_zoom_fill and source_width and source_height:
        scale = ffmpeg_video_filter_for_target_resolution(
            source_width,
            source_height,
            project.width,
            project.height,
        )
        if scale:
            parts.append(scale)
            expected_width = project.width
            expected_height = project.height

    if not parts:
        return None, None, None, None
    return ",".join(parts), expected_width, expected_height, None


def export_processing_required(
    *,
    auto_zoom_fill: bool,
    is_image: bool,
    needs_zoom: bool,
) -> bool:
    if is_image:
        return False
    if auto_zoom_fill and needs_zoom:
        return True
    return False


def ensure_export_media_for_export(
    project: Project,
    folder_name: str,
    original_path: Path,
    *,
    notes: list[str] | None = None,
    auto_zoom_fill: bool | None = None,
) -> Path:
    """Transkodiert bei Bedarf (Aspect Cover-Fill) und liefert Export-Pfad.

    ``auto_zoom_fill=None`` → Clean-Media-Setting. Explizites True/False
    überschreibt das Setting (Enhanced-OTIO erzwingt Cover-Fill).

    Wichtig: nur bei **Seitenverhältnis ≠ Ziel** (z. B. 2048×1080 → 16:9).
    Bereits 16:9 (auch 4K/HD) wird **nicht** neu encodiert — nur Ultrawide/
    Portrait/DCI o. Ä. bekommen Cover-Fill auf Projektpixel.
    """
    from otio_app.services.clean_media import (
        CLEAN_STATUS_FAILED,
        export_processed_output_path_for_media,
        path_is_readable_file,
        process_media_file,
        resolve_effective_media_path,
    )
    from otio_app.services.clean_media_settings import load_clean_media_settings

    if auto_zoom_fill is None:
        auto_zoom_fill = bool(load_clean_media_settings(project).auto_zoom_fill)
    else:
        auto_zoom_fill = bool(auto_zoom_fill)
    fallback = resolve_effective_media_path(project, folder_name, original_path)
    if is_image_media(original_path):
        return fallback

    src_w, src_h = media_resolution_probe(original_path)
    if not src_w or not src_h:
        src_w, src_h = resolve_media_dimensions(project, folder_name, original_path)

    # Nur Aspect-Fill — kein Downscale von bereits korrektem 16:9 (z. B. 4K).
    needs_zoom = bool(
        src_w
        and src_h
        and media_needs_aspect_fill(src_w, src_h, project.width, project.height)
    )
    if not export_processing_required(
        auto_zoom_fill=auto_zoom_fill,
        is_image=False,
        needs_zoom=needs_zoom,
    ):
        return fallback

    # Hot path: vorhandene gefüllte Datei wiederverwenden (nur Probe, kein
    # Full-Decode / Re-Encode) — OTIO-Export sonst Minuten pro Kapitel.
    filled_candidate = export_processed_output_path_for_media(
        project.work_dir_path,
        folder_name,
        original_path,
        width=project.width,
        height=project.height,
    )
    if path_is_readable_file(filled_candidate) and filled_candidate.stat().st_size >= 1024:
        out_w, out_h = media_resolution_probe(filled_candidate)
        if media_matches_target_resolution(
            out_w, out_h, project.width, project.height
        ):
            if notes is not None and src_w and src_h and out_w and out_h:
                notes.append(
                    f"{original_path.name}: Cache {src_w}×{src_h} → "
                    f"{out_w}×{out_h} · `{filled_candidate}`"
                )
            return filled_candidate.resolve()

    def _resolved_path(entry) -> Path:
        if entry.clean_path:
            return Path(entry.clean_path).expanduser().resolve()
        return fallback

    entry = process_media_file(
        project,
        folder_name,
        original_path,
        auto_zoom_fill=auto_zoom_fill,
    )
    media_path = _resolved_path(entry)

    out_w, out_h = media_resolution_probe(media_path)
    still_wrong = auto_zoom_fill and needs_zoom and not media_matches_target_resolution(
        out_w,
        out_h,
        project.width,
        project.height,
    )
    if still_wrong:
        entry = process_media_file(
            project,
            folder_name,
            original_path,
            force_transcode=True,
            auto_zoom_fill=auto_zoom_fill,
        )
        media_path = _resolved_path(entry)
        if entry.status == CLEAN_STATUS_FAILED and entry.error and notes is not None:
            notes.append(f"{original_path.name}: Export-Transcode fehlgeschlagen — {entry.error}")
        out_w, out_h = media_resolution_probe(media_path)

    if auto_zoom_fill and needs_zoom:
        warning = aspect_fill_warning(project, media_path, label=original_path.name)
        if warning:
            if notes is not None:
                notes.append(warning)
        elif notes is not None and src_w and src_h and out_w and out_h:
            notes.append(
                f"{original_path.name}: {src_w}×{src_h} → {out_w}×{out_h} · `{media_path}`"
            )

    return media_path


def ensure_zoomed_media_for_export(
    project: Project,
    folder_name: str,
    original_path: Path,
    *,
    notes: list[str] | None = None,
    auto_zoom_fill: bool | None = None,
) -> Path:
    """Abwärtskompatibel — delegiert an ensure_export_media_for_export."""
    return ensure_export_media_for_export(
        project,
        folder_name,
        original_path,
        notes=notes,
        auto_zoom_fill=auto_zoom_fill,
    )


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


def ffmpeg_video_filter_for_target_resolution(
    asset_width: int,
    asset_height: int,
    target_width: int,
    target_height: int,
) -> str | None:
    """Liefert den passenden ffmpeg-vf für die Zielauflösung (Fill-Zoom oder einfaches Scale)."""
    if asset_width <= 0 or asset_height <= 0 or target_width <= 0 or target_height <= 0:
        return None
    if asset_width == target_width and asset_height == target_height:
        return None
    fill = ffmpeg_scale_crop_filter(asset_width, asset_height, target_width, target_height)
    if fill:
        return fill
    return f"scale={target_width}:{target_height}"


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
