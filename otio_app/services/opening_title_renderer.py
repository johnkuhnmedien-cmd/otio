"""Opening-Title-Rendering und Validierungsberichte."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import TimelineItem
from otio_app.models import Project
from otio_app.services.clean_media import path_is_readable_file
from otio_app.services.font_utils import OPENING_TITLE_FALLBACK_FONT, resolve_font_with_fallback
from otio_app.services.otio_media_transform import escape_drawtext_value, format_folder_display_name

GENERATED_TITLES_SUBDIR = "generated_titles"
DEFAULT_OPENING_TITLE_DURATION_SEC = 5.0
DEFAULT_OPENING_TITLE_FONT_SIZE = 96.0


def generated_titles_dir(work_dir: Path) -> Path:
    return work_dir / GENERATED_TITLES_SUBDIR


def opening_title_media_path(work_dir: Path, section_id: str) -> Path:
    return generated_titles_dir(work_dir) / f"{section_id}_opening_title_v001.mov"


def validation_report_path(work_dir: Path) -> Path:
    return work_dir / "validation_report.json"


def append_validation_report(
    work_dir: Path,
    *,
    warnings: list[str],
    font_warnings: list[dict[str, str | bool]],
) -> Path:
    """Schreibt oder ergänzt validation_report.json im work_dir."""
    path = validation_report_path(work_dir)
    payload: dict = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
    existing_warnings = list(payload.get("warnings", []))
    existing_font = list(payload.get("font_warnings", []))
    merged_warnings = existing_warnings + [w for w in warnings if w not in existing_warnings]
    merged_font = existing_font + font_warnings
    payload.update(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "warnings": merged_warnings,
            "font_warnings": merged_font,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def build_opening_title_item(
    *,
    folder_name: str,
    voice_file: str,
    section_id: str,
    work_dir: Path,
    requested_font_family: str = "Phosphate",
    duration_sec: float = DEFAULT_OPENING_TITLE_DURATION_SEC,
    font_size: float = DEFAULT_OPENING_TITLE_FONT_SIZE,
) -> TimelineItem:
    """Erzeugt ein opening_title-Timeline-Element für den Schnittplan."""
    text = format_folder_display_name(folder_name)
    font_path, resolved_font, fallback_used = resolve_font_with_fallback(requested_font_family)
    warnings: list[str] = []
    if fallback_used:
        warnings.append(
            f"Schrift «{requested_font_family}» nicht gefunden — Fallback «{resolved_font}»."
        )
    if font_path is None:
        warnings.append(
            f"Keine Schrift verfügbar (angefragt: {requested_font_family}, "
            f"Fallback: {OPENING_TITLE_FALLBACK_FONT})."
        )

    target_path = opening_title_media_path(work_dir, section_id)
    duration = round(max(0.1, float(duration_sec)), 4)

    return TimelineItem(
        timeline_item_id=f"title_{section_id}",
        type="opening_title",
        section_id=section_id,
        folder_name=folder_name,
        voice_file=voice_file,
        asset_role="opening_title",
        track="V2",
        text=text,
        timeline_in_sec=0.0,
        timeline_out_sec=duration,
        duration_sec=duration,
        final_duration_sec=duration,
        source_in_sec=0.0,
        source_out_sec=duration,
        requested_font_family=requested_font_family,
        resolved_font_family=resolved_font,
        font_fallback_used=fallback_used,
        font_size=font_size,
        shadow_enabled=True,
        shadow_opacity=0.65,
        shadow_offset_x=6.0,
        shadow_offset_y=6.0,
        position="center",
        fade_in_sec=0.5,
        fade_out_sec=0.5,
        render_required=True,
        rendered_media_path=str(target_path),
        resolved_media_path=str(target_path),
        selection_reason="Opening Title aus Ordnername",
        confidence=1.0,
        motif=text,
        warnings=warnings,
        media_source_type="generated",
    )


def _ffmpeg_opening_title_filter(item: TimelineItem, font_path: Path, project: Project) -> str:
    from otio_app.services.otio_media_transform import escape_drawtext_value

    safe_text = escape_drawtext_value(item.text)
    safe_font = escape_drawtext_value(str(font_path.resolve()))
    shadow_opacity = max(0.0, min(1.0, float(item.shadow_opacity)))
    shadow_x = int(round(item.shadow_offset_x))
    shadow_y = int(round(item.shadow_offset_y))
    fontsize = max(12, int(round(item.font_size)))
    duration = max(0.1, float(item.duration_sec))

    if item.position == "center":
        x_expr = "(w-text_w)/2"
        y_expr = "(h-text_h)/2"
    else:
        margin_x = max(24, project.width // 100)
        margin_y = max(24, project.height // 100)
        x_expr = str(margin_x)
        y_expr = f"h-th-{margin_y}"

    shadow = ""
    if item.shadow_enabled:
        shadow = (
            f":shadowcolor=black@{shadow_opacity:.2f}"
            f":shadowx={shadow_x}:shadowy={shadow_y}"
        )

    return (
        f"drawtext=fontfile='{safe_font}':"
        f"text='{safe_text}':"
        f"fontsize={fontsize}:"
        f"fontcolor=white:"
        f"x={x_expr}:"
        f"y={y_expr}"
        f"{shadow}:"
        f"enable='lte(t\\,{duration:.3f})'"
    )


def render_opening_title_media(
    project: Project,
    item: TimelineItem,
    *,
    font_warnings: list[dict[str, str | bool]] | None = None,
) -> Path:
    """Rendert transparentes ProRes 4444 MOV für einen Opening Title."""
    if item.type != "opening_title":
        raise ValueError(f"Kein opening_title-Item: {item.type}")

    output_path = Path(item.rendered_media_path or opening_title_media_path(project.work_dir_path, item.section_id))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font_path, resolved_font, fallback_used = resolve_font_with_fallback(
        item.requested_font_family or "Phosphate"
    )
    if font_path is None:
        raise RuntimeError(
            f"Keine Schrift für Opening Title «{item.text}» "
            f"(angefragt: {item.requested_font_family})."
        )

    if font_warnings is not None and (fallback_used or item.font_fallback_used):
        font_warnings.append(
            {
                "section_id": item.section_id,
                "folder_name": item.folder_name,
                "requested_font_family": item.requested_font_family,
                "resolved_font_family": resolved_font,
                "font_fallback_used": True,
            }
        )

    duration = max(0.1, float(item.duration_sec))
    fps = max(1, int(project.fps))
    width = max(320, int(project.width))
    height = max(240, int(project.height))
    vf = _ffmpeg_opening_title_filter(item, font_path, project)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black@0.0:s={width}x{height}:d={duration:.3f}:r={fps}",
        "-vf",
        f"format=rgba,{vf}",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output_path.is_file():
        png_path = output_path.with_suffix(".png")
        png_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black@0.0:s={width}x{height}:d={duration:.3f}:r={fps}",
            "-frames:v",
            "1",
            "-vf",
            f"format=rgba,{vf}",
            str(png_path),
        ]
        png_result = subprocess.run(png_cmd, capture_output=True, text=True, check=False)
        if png_result.returncode != 0 or not png_path.is_file():
            detail = (result.stderr or png_result.stderr or "ffmpeg fehlgeschlagen").strip()
            raise RuntimeError(f"Opening-Title-Render fehlgeschlagen: {detail}")
        return png_path

    return output_path


def ensure_opening_titles_rendered(
    project: Project,
    items: list[TimelineItem],
) -> tuple[list[TimelineItem], list[str]]:
    """Rendert fehlende Opening Titles und aktualisiert Pfade."""
    font_warnings: list[dict[str, str | bool]] = []
    report_warnings: list[str] = []
    updated: list[TimelineItem] = []

    for item in items:
        if item.type != "opening_title":
            updated.append(item)
            continue
        media_path = Path(item.rendered_media_path) if item.rendered_media_path else None
        if media_path is not None and path_is_readable_file(media_path):
            updated.append(
                item.model_copy(
                    update={
                        "resolved_media_path": str(media_path),
                    }
                )
            )
            continue
        rendered = render_opening_title_media(project, item, font_warnings=font_warnings)
        updated.append(
            item.model_copy(
                update={
                    "rendered_media_path": str(rendered),
                    "resolved_media_path": str(rendered),
                }
            )
        )
        report_warnings.append(f"Opening Title gerendert: {rendered}")

    if font_warnings or report_warnings:
        append_validation_report(
            project.work_dir_path,
            warnings=report_warnings,
            font_warnings=font_warnings,
        )

    return updated, report_warnings
