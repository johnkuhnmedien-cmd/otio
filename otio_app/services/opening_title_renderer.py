"""Opening-Title-Rendering — liest ausschließlich opening_title.title_style aus dem Schnittplan."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import TimelineItem, TitleStyle
from otio_app.models import Project
from otio_app.services.clean_media import path_is_readable_file
from otio_app.services.media_utils import ffmpeg_has_drawtext
from otio_app.services.otio_media_transform import escape_drawtext_value, format_folder_display_name
from otio_app.services.title_style import (
    DEFAULT_OPENING_TITLE_DURATION_SEC,
    DEFAULT_OPENING_TITLE_FONT,
    OPENING_TITLE_POSITION_LOWER_THIRD,
    RENDERER_VERSION,
    TITLE_FONT_SIZE_NOT_APPLIED,
    attach_output_paths,
    build_render_manifest,
    build_title_style_for_plan,
    compute_render_hash,
    extract_title_style,
    measure_text_bbox,
    render_cache_valid,
    validate_font_size_applied,
    validation_report_path,
)

GENERATED_TITLES_SUBDIR = "generated_titles"


def generated_titles_dir(work_dir: Path) -> Path:
    return work_dir / GENERATED_TITLES_SUBDIR


def append_validation_report(
    work_dir: Path,
    *,
    warnings: list[str],
    font_warnings: list[dict[str, str | bool]],
    errors: list[str] | None = None,
) -> Path:
    path = validation_report_path(work_dir)
    payload: dict = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
    existing_warnings = list(payload.get("warnings", []))
    existing_font = list(payload.get("font_warnings", []))
    existing_errors = list(payload.get("errors", []))
    merged_warnings = existing_warnings + [w for w in warnings if w not in existing_warnings]
    merged_font = existing_font + font_warnings
    merged_errors = existing_errors + [e for e in (errors or []) if e not in existing_errors]
    payload.update(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "warnings": merged_warnings,
            "font_warnings": merged_font,
            "errors": merged_errors,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _mirror_legacy_title_fields(style: TitleStyle) -> dict:
    return {
        "text": style.text,
        "requested_font_family": style.requested_font_family,
        "resolved_font_family": style.resolved_font_family,
        "resolved_font_file_path": style.resolved_font_file_path,
        "font_fallback_used": style.font_fallback_used,
        "font_size_px": style.font_size_px,
        "font_size": style.font_size_px,
        "shadow_enabled": style.shadow_enabled,
        "shadow_opacity": style.shadow_opacity,
        "shadow_offset_x": style.shadow_offset_x,
        "shadow_offset_y": style.shadow_offset_y,
        "position": style.position,
        "fade_in_sec": style.fade_in_sec,
        "fade_out_sec": style.fade_out_sec,
        "render_hash": style.render_hash,
        "rendered_media_path": style.output_mov_path,
        "resolved_media_path": style.output_mov_path,
    }


def build_opening_title_item(
    *,
    folder_name: str,
    voice_file: str,
    section_id: str,
    work_dir: Path,
    project: Project,
    requested_font_family: str = DEFAULT_OPENING_TITLE_FONT,
    duration_sec: float = DEFAULT_OPENING_TITLE_DURATION_SEC,
    font_size_px: float | None = None,
    text: str | None = None,
) -> TimelineItem:
    """Erzeugt opening_title beim Schnittplan-Vorschlag (Regeln → Plan, noch kein Render).

    `text` überschreibt den Ordner-Anzeigenamen (für spätere Übersetzungen).
    """
    display_text = (text or "").strip() or format_folder_display_name(folder_name)
    style = build_title_style_for_plan(
        text=display_text,
        project=project,
        requested_font_family=requested_font_family,
        duration_sec=duration_sec,
        font_size_px=font_size_px,
    )
    style = attach_output_paths(style, work_dir=work_dir, section_id=section_id)
    warnings: list[str] = []
    if style.font_resolution_warning:
        warnings.append(style.font_resolution_warning)
    if not style.resolved_font_file_path:
        warnings.append("Keine Schriftdatei aufgelöst — Render wird fehlschlagen.")
    if not ffmpeg_has_drawtext():
        warnings.append("ffmpeg ohne drawtext-Filter — Titel wird per Pillow gerendert.")

    duration = round(max(0.1, float(duration_sec)), 4)
    return TimelineItem(
        timeline_item_id=f"title_{section_id}",
        type="opening_title",
        section_id=section_id,
        folder_name=folder_name,
        voice_file=voice_file,
        asset_role="opening_title",
        track="V2",
        timeline_in_sec=0.0,
        timeline_out_sec=duration,
        duration_sec=duration,
        final_duration_sec=duration,
        source_in_sec=0.0,
        source_out_sec=duration,
        title_style=style,
        render_required=True,
        selection_reason="Opening Title aus Ordnername",
        confidence=1.0,
        motif=display_text,
        warnings=warnings,
        media_source_type="generated",
        **_mirror_legacy_title_fields(style),
    )


def _layout_from_style(style: TitleStyle) -> tuple[str, str, int]:
    fontsize = max(12, int(round(style.font_size_px)))
    if style.position == "center":
        return "(w-text_w)/2", "(h-text_h)/2", fontsize
    return str(style.margin_x), f"h-th-{style.margin_y}", fontsize


def _ffmpeg_filter(style: TitleStyle, font_path: Path) -> str:
    safe_text = escape_drawtext_value(style.text)
    safe_font = escape_drawtext_value(str(font_path.resolve()))
    x_expr, y_expr, fontsize = _layout_from_style(style)
    shadow = ""
    if style.shadow_enabled:
        shadow = (
            f":shadowcolor=black@{max(0.0, min(1.0, style.shadow_opacity)):.2f}"
            f":shadowx={int(round(style.shadow_offset_x))}"
            f":shadowy={int(round(style.shadow_offset_y))}"
        )
    return (
        f"drawtext=fontfile='{safe_font}':"
        f"text='{safe_text}':"
        f"fontsize={fontsize}:"
        f"fontcolor=white:"
        f"x={x_expr}:"
        f"y={y_expr}"
        f"{shadow}:"
        f"enable='lte(t\\,{style.duration_sec:.3f})'"
    )


def _render_png_pillow(style: TitleStyle, font_path: Path, output_png: Path) -> tuple[int, int]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "Pillow fehlt — bitte `pip install Pillow` ausführen."
        ) from exc

    fontsize = max(12, int(round(style.font_size_px)))
    width = max(320, int(style.timeline_width))
    height = max(320, int(style.timeline_height))
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(str(font_path), fontsize)
    except OSError as exc:
        raise RuntimeError(
            f"Schriftdatei nicht lesbar: {font_path} (font_size_px={fontsize})"
        ) from exc

    bbox = draw.textbbox((0, 0), style.text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    if style.position == "center":
        x = (width - text_w) // 2 - bbox[0]
        y = (height - text_h) // 2 - bbox[1]
    else:
        x = style.margin_x - bbox[0]
        y = height - text_h - style.margin_y - bbox[1]

    if style.shadow_enabled:
        shadow_alpha = int(max(0, min(255, round(style.shadow_opacity * 255))))
        draw.text(
            (
                x + int(round(style.shadow_offset_x)),
                y + int(round(style.shadow_offset_y)),
            ),
            style.text,
            font=font,
            fill=(0, 0, 0, shadow_alpha),
        )
    draw.text((x, y), style.text, font=font, fill=(255, 255, 255, 255))
    output_png.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_png)
    return measure_text_bbox(output_png)


def _encode_png_to_mov(png_path: Path, output_mov: Path, *, style: TitleStyle) -> bool:
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
        f"{style.duration_sec:.3f}",
        "-r",
        str(max(1, int(round(style.fps)))),
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


def _render_with_ffmpeg_drawtext(style: TitleStyle, font_path: Path, output_mov: Path) -> bool:
    width = max(320, int(style.timeline_width))
    height = max(320, int(style.timeline_height))
    fps = max(1, int(round(style.fps)))
    vf = _ffmpeg_filter(style, font_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black@0.0:s={width}x{height}:d={style.duration_sec:.3f}:r={fps}",
        "-vf",
        f"format=rgba,{vf}",
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


def render_opening_title_from_style(
    item: TimelineItem,
    style: TitleStyle,
    *,
    previous_bbox_height: int | None = None,
) -> tuple[TitleStyle, dict, list[str]]:
    """Rendert MOV+PNG ausschließlich aus TitleStyle."""
    if not style.resolved_font_file_path:
        raise RuntimeError(
            f"Keine Schriftdatei im Schnittplan für «{style.text}» "
            f"(angefragt: {style.requested_font_family})."
        )
    font_path = Path(style.resolved_font_file_path)
    if not font_path.is_file():
        raise RuntimeError(f"Schriftdatei nicht gefunden: {font_path}")

    mov_path = Path(style.output_mov_path)
    png_path = Path(style.output_png_path)
    manifest_path = Path(style.render_manifest_path)
    mov_path.parent.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    renderer = "pillow"
    bbox_w, bbox_h = 0, 0

    if ffmpeg_has_drawtext() and _render_with_ffmpeg_drawtext(style, font_path, mov_path):
        renderer = "ffmpeg_drawtext"
        _render_png_pillow(style, font_path, png_path)
        bbox_w, bbox_h = measure_text_bbox(png_path)
    else:
        bbox_w, bbox_h = _render_png_pillow(style, font_path, png_path)
        if not _encode_png_to_mov(png_path, mov_path, style=style):
            raise RuntimeError(f"MOV-Encoding fehlgeschlagen für {mov_path}")

    if mov_path.suffix.lower() in {".jpg", ".jpeg"}:
        raise RuntimeError("JPG darf nicht als Titel-Overlay verwendet werden.")

    font_ok, font_err = validate_font_size_applied(
        font_size_px=style.font_size_px,
        bbox_height=bbox_h,
        previous_bbox_height=previous_bbox_height,
    )
    if not font_ok and font_err:
        errors.append(font_err)

    updated_style = style.model_copy(
        update={
            "output_mov_path": str(mov_path),
            "output_png_path": str(png_path),
            "render_manifest_path": str(manifest_path),
        }
    )
    manifest = build_render_manifest(
        title_id=item.timeline_item_id,
        style=updated_style,
        renderer=renderer,
        text_bbox_width=bbox_w,
        text_bbox_height=bbox_h,
        font_size_applied=font_ok,
        mov_exists=path_is_readable_file(mov_path),
        mov_has_alpha=True,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return updated_style, manifest, errors


def ensure_opening_titles_rendered(
    project: Project,
    items: list[TimelineItem],
    *,
    force: bool = False,
) -> tuple[list[TimelineItem], list[str]]:
    """Rendert opening_title-Items — nur Werte aus dem Schnittplan."""
    report_warnings: list[str] = []
    report_errors: list[str] = []
    font_warnings: list[dict[str, str | bool]] = []
    updated: list[TimelineItem] = []

    for item in items:
        if item.type != "opening_title":
            updated.append(item)
            continue

        style = extract_title_style(item, project)
        style = attach_output_paths(style, work_dir=project.language_work_dir_path, section_id=item.section_id)
        manifest_path = Path(style.render_manifest_path)

        if not force and render_cache_valid(style, manifest_path):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                cached_style = style.model_copy(
                    update={
                        "output_mov_path": manifest.get("output_mov_path", style.output_mov_path),
                        "output_png_path": manifest.get("output_png_path", style.output_png_path),
                        "render_manifest_path": str(manifest_path),
                    }
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                cached_style = style
            updated.append(
                item.model_copy(
                    update={
                        "title_style": cached_style,
                        **_mirror_legacy_title_fields(cached_style),
                    }
                )
            )
            continue

        rendered_style, manifest, errors = render_opening_title_from_style(item, style)
        report_warnings.append(
            f"Opening Title gerendert ({manifest.get('renderer')}, "
            f"{int(round(rendered_style.font_size_px))}px, hash={rendered_style.render_hash}): "
            f"{rendered_style.output_mov_path}"
        )
        if rendered_style.font_fallback_used:
            font_warnings.append(
                {
                    "section_id": item.section_id,
                    "folder_name": item.folder_name,
                    "requested_font_family": rendered_style.requested_font_family,
                    "resolved_font_family": rendered_style.resolved_font_family,
                    "resolved_font_file_path": rendered_style.resolved_font_file_path,
                    "font_fallback_used": True,
                }
            )
        report_errors.extend(errors)
        updated.append(
            item.model_copy(
                update={
                    "title_style": rendered_style,
                    "render_required": False,
                    **_mirror_legacy_title_fields(rendered_style),
                }
            )
        )

    if report_warnings or font_warnings or report_errors:
        append_validation_report(
            project.language_work_dir_path,
            warnings=report_warnings,
            font_warnings=font_warnings,
            errors=report_errors,
        )

    return updated, report_warnings + report_errors


def title_render_is_stale(item: TimelineItem, project: Project) -> bool:
    if item.type != "opening_title":
        return False
    style = extract_title_style(item, project)
    style = attach_output_paths(style, work_dir=project.language_work_dir_path, section_id=item.section_id)
    return not render_cache_valid(style, Path(style.render_manifest_path))
