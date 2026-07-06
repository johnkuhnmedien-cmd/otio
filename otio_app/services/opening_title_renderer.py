"""Opening-Title-Rendering und Validierungsberichte."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
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


@lru_cache(maxsize=1)
def ffmpeg_has_drawtext() -> bool:
    """Prüft, ob ffmpeg den drawtext-Filter enthält (fehlt bei manchen macOS-Builds)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout}\n{result.stderr}"
    return " drawtext " in output or output.strip().endswith("drawtext")


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
    if not ffmpeg_has_drawtext():
        warnings.append(
            "ffmpeg ohne drawtext-Filter — Titel wird per Pillow gerendert."
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


def _render_title_png_pillow(
    item: TimelineItem,
    font_path: Path,
    project: Project,
    output_png: Path,
) -> None:
    """Transparente PNG mit Pillow — funktioniert ohne ffmpeg drawtext."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "Pillow fehlt — bitte `pip install Pillow` ausführen "
            "(benötigt für Opening Titles ohne ffmpeg drawtext)."
        ) from exc

    width = max(320, int(project.width))
    height = max(240, int(project.height))
    fontsize = max(12, int(round(item.font_size)))
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(str(font_path), fontsize)
    except OSError as exc:
        raise RuntimeError(f"Schriftdatei nicht lesbar: {font_path}") from exc

    bbox = draw.textbbox((0, 0), item.text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    if item.position == "center":
        x = (width - text_w) // 2 - bbox[0]
        y = (height - text_h) // 2 - bbox[1]
    else:
        margin_x = max(24, width // 100)
        margin_y = max(24, height // 100)
        x = margin_x - bbox[0]
        y = height - text_h - margin_y - bbox[1]

    if item.shadow_enabled:
        shadow_x = int(round(item.shadow_offset_x))
        shadow_y = int(round(item.shadow_offset_y))
        shadow_alpha = int(max(0, min(255, round(float(item.shadow_opacity) * 255))))
        draw.text(
            (x + shadow_x, y + shadow_y),
            item.text,
            font=font,
            fill=(0, 0, 0, shadow_alpha),
        )
    draw.text((x, y), item.text, font=font, fill=(255, 255, 255, 255))
    output_png.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_png)


def _encode_png_to_title_mov(
    png_path: Path,
    output_mov: Path,
    *,
    duration: float,
    fps: int,
) -> bool:
    """PNG → transparentes ProRes MOV (ohne drawtext)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-i",
        str(png_path),
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        str(output_mov),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode == 0 and output_mov.is_file()


def _render_with_ffmpeg_drawtext(
    project: Project,
    item: TimelineItem,
    font_path: Path,
    output_path: Path,
) -> bool:
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
    return result.returncode == 0 and output_path.is_file()


def _render_with_pillow(
    project: Project,
    item: TimelineItem,
    font_path: Path,
    output_path: Path,
) -> Path:
    png_path = output_path.with_suffix(".png")
    _render_title_png_pillow(item, font_path, project, png_path)
    duration = max(0.1, float(item.duration_sec))
    fps = max(1, int(project.fps))
    if _encode_png_to_title_mov(png_path, output_path, duration=duration, fps=fps):
        return output_path
    return png_path


def render_opening_title_media(
    project: Project,
    item: TimelineItem,
    *,
    font_warnings: list[dict[str, str | bool]] | None = None,
) -> Path:
    """Rendert transparentes ProRes 4444 MOV (oder PNG-Fallback) für einen Opening Title."""
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

    if ffmpeg_has_drawtext() and _render_with_ffmpeg_drawtext(project, item, font_path, output_path):
        return output_path

    return _render_with_pillow(project, item, font_path, output_path)


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
        backend = "ffmpeg drawtext" if ffmpeg_has_drawtext() and rendered.suffix.lower() == ".mov" else "Pillow"
        report_warnings.append(f"Opening Title gerendert ({backend}): {rendered}")

    if font_warnings or report_warnings:
        append_validation_report(
            project.work_dir_path,
            warnings=report_warnings,
            font_warnings=font_warnings,
        )

    return updated, report_warnings
