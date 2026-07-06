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
from otio_app.services.edit_plan_rules import ExportRuleOptions
from otio_app.services.generic_outro_selector import section_id_for_folder
from otio_app.services.otio_media_transform import escape_drawtext_value, format_folder_display_name

GENERATED_TITLES_SUBDIR = "generated_titles"
DEFAULT_OPENING_TITLE_DURATION_SEC = 5.0
DEFAULT_OPENING_TITLE_FONT = "Helvetica Neue"
DEFAULT_OPENING_TITLE_FONT_SIZE = 72.0
OPENING_TITLE_POSITION_LOWER_THIRD = "lower_third"


def lower_third_font_size(video_height: int) -> float:
    """Responsive Schriftgröße — ähnlich Resolve „Clean and Simple Lower Third“."""
    return max(28.0, min(72.0, video_height / 14.0))


def lower_third_margins(video_width: int, video_height: int) -> tuple[int, int]:
    """Abstand unten links in Pixeln."""
    margin_x = max(48, video_width // 25)
    margin_y = max(54, video_height // 10)
    return margin_x, margin_y


def generated_titles_dir(work_dir: Path) -> Path:
    return work_dir / GENERATED_TITLES_SUBDIR


def opening_title_media_path(work_dir: Path, section_id: str) -> Path:
    return generated_titles_dir(work_dir) / f"{section_id}_opening_title_v002.mov"


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
    requested_font_family: str = DEFAULT_OPENING_TITLE_FONT,
    duration_sec: float = DEFAULT_OPENING_TITLE_DURATION_SEC,
    font_size: float | None = None,
    video_width: int = 1920,
    video_height: int = 1080,
) -> TimelineItem:
    """Erzeugt ein opening_title-Timeline-Element für den Schnittplan."""
    text = format_folder_display_name(folder_name)
    font_path, resolved_font, fallback_used = resolve_font_with_fallback(requested_font_family)
    resolved_font_size = font_size if font_size is not None else lower_third_font_size(video_height)
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
        font_size=resolved_font_size,
        shadow_enabled=True,
        shadow_opacity=0.5,
        shadow_offset_x=3.0,
        shadow_offset_y=3.0,
        position=OPENING_TITLE_POSITION_LOWER_THIRD,
        fade_in_sec=0.35,
        fade_out_sec=0.35,
        render_required=True,
        rendered_media_path=str(target_path),
        resolved_media_path=str(target_path),
        selection_reason="Opening Title aus Ordnername",
        confidence=1.0,
        motif=text,
        warnings=warnings,
        media_source_type="generated",
    )


def _opening_title_signature(item: TimelineItem) -> tuple:
    return (
        item.text,
        item.requested_font_family,
        round(float(item.font_size), 2),
        round(float(item.duration_sec), 2),
        item.position,
    )


def sync_opening_titles_from_rules(
    project: Project,
    items: list[TimelineItem],
    *,
    folder_name: str,
    export_opts: ExportRuleOptions,
) -> tuple[list[TimelineItem], bool]:
    """Passt opening_title-Items an aktuelle Regeln an (oder entfernt sie)."""
    voice_file = next((item.voice_file for item in items if item.voice_file), "")
    non_titles = [item for item in items if item.type != "opening_title"]
    existing = next((item for item in items if item.type == "opening_title"), None)

    if not export_opts.folder_title_enabled:
        return non_titles, existing is not None

    section_id = section_id_for_folder(folder_name)
    refreshed = build_opening_title_item(
        folder_name=folder_name,
        voice_file=voice_file,
        section_id=section_id,
        work_dir=project.work_dir_path,
        requested_font_family=export_opts.folder_title_font,
        duration_sec=export_opts.folder_title_duration_sec,
        font_size=export_opts.folder_title_font_size,
        video_width=project.width,
        video_height=project.height,
    )
    if existing is not None:
        refreshed = refreshed.model_copy(
            update={
                "timeline_item_id": existing.timeline_item_id,
                "timeline_in_sec": existing.timeline_in_sec,
                "timeline_out_sec": existing.timeline_out_sec,
            }
        )

    changed = existing is None or _opening_title_signature(existing) != _opening_title_signature(refreshed)
    if changed:
        _invalidate_title_render_cache(
            Path(existing.rendered_media_path) if existing and existing.rendered_media_path else None
        )

    return [refreshed, *non_titles], changed


def _title_layout(
    item: TimelineItem,
    project: Project,
) -> tuple[str, str, int, int, int]:
    """Liefert (x_expr, y_expr, margin_x, margin_y, fontsize) für ffmpeg/Pillow."""
    fontsize = max(12, int(round(item.font_size)))
    duration = max(0.1, float(item.duration_sec))

    if item.position == "center":
        return "(w-text_w)/2", "(h-text_h)/2", 0, 0, fontsize

    margin_x, margin_y = lower_third_margins(project.width, project.height)
    return str(margin_x), f"h-th-{margin_y}", margin_x, margin_y, fontsize


def _ffmpeg_opening_title_filter(item: TimelineItem, font_path: Path, project: Project) -> str:
    safe_text = escape_drawtext_value(item.text)
    safe_font = escape_drawtext_value(str(font_path.resolve()))
    shadow_opacity = max(0.0, min(1.0, float(item.shadow_opacity)))
    shadow_x = int(round(item.shadow_offset_x))
    shadow_y = int(round(item.shadow_offset_y))
    x_expr, y_expr, _, _, fontsize = _title_layout(item, project)
    duration = max(0.1, float(item.duration_sec))

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
    _, _, margin_x, margin_y, _ = _title_layout(item, project)
    if item.position == "center":
        x = (width - text_w) // 2 - bbox[0]
        y = (height - text_h) // 2 - bbox[1]
    else:
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


def _title_render_meta_path(media_path: Path) -> Path:
    return media_path.parent / f"{media_path.stem}.render.json"


def _write_title_render_meta(media_path: Path, item: TimelineItem) -> None:
    meta_path = _title_render_meta_path(media_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(list(_opening_title_signature(item)), ensure_ascii=False),
        encoding="utf-8",
    )


def _title_render_cache_matches(item: TimelineItem, media_path: Path) -> bool:
    if not path_is_readable_file(media_path):
        return False
    meta_path = _title_render_meta_path(media_path)
    if not meta_path.is_file():
        return False
    try:
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
        return tuple(stored) == _opening_title_signature(item)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False


def _invalidate_title_render_cache(media_path: Path | None) -> None:
    if media_path is None:
        return
    for path in (media_path, _title_render_meta_path(media_path)):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def apply_opening_titles_to_plan(
    project: Project,
    items: list[TimelineItem],
    *,
    folder_name: str,
    export_opts: ExportRuleOptions,
) -> tuple[list[TimelineItem], list[str]]:
    """Synchronisiert Titel mit Regeln und rendert bei Bedarf neu."""
    notes: list[str] = []
    if not export_opts.folder_title_enabled:
        return [item for item in items if item.type != "opening_title"], notes

    synced_items, changed = sync_opening_titles_from_rules(
        project,
        items,
        folder_name=folder_name,
        export_opts=export_opts,
    )
    if changed:
        notes.append("Opening Title aus Regeln übernommen.")
    rendered_items, render_notes = ensure_opening_titles_rendered(project, synced_items)
    notes.extend(render_notes)
    return rendered_items, notes


def ensure_opening_titles_rendered(
    project: Project,
    items: list[TimelineItem],
    *,
    force: bool = False,
) -> tuple[list[TimelineItem], list[str]]:
    """Rendert Opening Titles neu, wenn Datei fehlt oder Render-Parameter geändert wurden."""
    font_warnings: list[dict[str, str | bool]] = []
    report_warnings: list[str] = []
    updated: list[TimelineItem] = []

    for item in items:
        if item.type != "opening_title":
            updated.append(item)
            continue
        media_path = Path(item.rendered_media_path) if item.rendered_media_path else None
        if (
            not force
            and media_path is not None
            and _title_render_cache_matches(item, media_path)
        ):
            updated.append(
                item.model_copy(
                    update={
                        "resolved_media_path": str(media_path),
                    }
                )
            )
            continue

        _invalidate_title_render_cache(media_path)
        rendered = render_opening_title_media(project, item, font_warnings=font_warnings)
        _write_title_render_meta(rendered, item)
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
