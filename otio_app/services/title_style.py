"""TitleStyle — einzige Quelle für Opening-Title-Rendering aus dem Schnittplan."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from otio_app.analysis_models import TimelineItem, TitleStyle
from otio_app.models import Project
from otio_app.services.clean_media import path_is_readable_file
from otio_app.services.font_utils import (
    OPENING_TITLE_FALLBACK_FONT,
    resolve_font_face_index,
    resolve_font_with_fallback,
)

RENDERER_VERSION = "3.0.0"
TITLE_FONT_SIZE_NOT_APPLIED = "TITLE_FONT_SIZE_NOT_APPLIED"
GENERATED_TITLES_SUBDIR = "generated_titles"
OPENING_TITLE_POSITION_LOWER_THIRD = "lower_third"
DEFAULT_OPENING_TITLE_FONT = "Helvetica Neue"
DEFAULT_OPENING_TITLE_DURATION_SEC = 5.0


def lower_third_font_size(video_height: int) -> float:
    return max(28.0, min(72.0, video_height / 14.0))


def lower_third_margins(video_width: int, video_height: int) -> tuple[int, int]:
    margin_x = max(48, video_width // 25)
    margin_y = max(54, video_height // 10)
    return margin_x, margin_y


def resolve_font_size_px(explicit_px: float | None, video_height: int) -> float:
    if explicit_px is not None and explicit_px > 0:
        return max(12.0, min(200.0, float(explicit_px)))
    return lower_third_font_size(video_height)


def generated_titles_dir(work_dir: Path) -> Path:
    return work_dir / GENERATED_TITLES_SUBDIR


def validation_report_path(work_dir: Path) -> Path:
    return work_dir / "validation_report.json"


@dataclass(frozen=True)
class FontResolution:
    font_path: Path | None
    requested_font_family: str
    resolved_font_family: str
    resolved_font_file_path: str
    font_fallback_used: bool
    font_resolution_warning: str = ""
    resolved_font_face_index: int = 0


def resolve_title_font(requested_font_family: str) -> FontResolution:
    requested = requested_font_family.strip() or DEFAULT_OPENING_TITLE_FONT
    font_path, resolved_font, fallback_used = resolve_font_with_fallback(requested)
    face_index = 0
    if font_path is not None:
        probe_name = resolved_font if fallback_used else requested
        face_index = resolve_font_face_index(probe_name, font_path)
    warning = ""
    if font_path is None:
        warning = (
            f"Keine Schriftdatei für «{requested}» gefunden "
            f"(Fallback «{OPENING_TITLE_FALLBACK_FONT}» ebenfalls nicht verfügbar)."
        )
    elif fallback_used:
        warning = f"Schrift «{requested}» nicht gefunden — Fallback «{resolved_font}»."
    return FontResolution(
        font_path=font_path,
        requested_font_family=requested,
        resolved_font_family=resolved_font,
        resolved_font_file_path=str(font_path.resolve()) if font_path else "",
        font_fallback_used=fallback_used,
        font_resolution_warning=warning,
        resolved_font_face_index=max(0, int(face_index or 0)),
    )


def title_style_from_legacy_item(item: TimelineItem, project: Project) -> TitleStyle:
    """Migration: flache Felder → TitleStyle (nur opening_title)."""
    margin_x, margin_y = lower_third_margins(project.width, project.height)
    font_res = resolve_title_font(item.requested_font_family or DEFAULT_OPENING_TITLE_FONT)
    return TitleStyle(
        text=item.text,
        timeline_width=int(project.width),
        timeline_height=int(project.height),
        duration_sec=float(item.duration_sec),
        fps=float(project.fps),
        requested_font_family=font_res.requested_font_family,
        resolved_font_family=item.resolved_font_family or font_res.resolved_font_family,
        resolved_font_file_path=item.resolved_font_file_path or font_res.resolved_font_file_path,
        resolved_font_face_index=int(font_res.resolved_font_face_index or 0),
        font_fallback_used=item.font_fallback_used or font_res.font_fallback_used,
        font_resolution_warning=font_res.font_resolution_warning,
        font_size_px=float(item.font_size_px or item.font_size or lower_third_font_size(project.height)),
        shadow_enabled=bool(item.shadow_enabled),
        shadow_opacity=float(item.shadow_opacity),
        shadow_offset_x=float(item.shadow_offset_x),
        shadow_offset_y=float(item.shadow_offset_y),
        position=item.position or OPENING_TITLE_POSITION_LOWER_THIRD,
        margin_x=margin_x,
        margin_y=margin_y,
        fade_in_sec=float(item.fade_in_sec),
        fade_out_sec=float(item.fade_out_sec),
    )


def extract_title_style(item: TimelineItem, project: Project) -> TitleStyle:
    if item.type != "opening_title":
        raise ValueError(f"Kein opening_title-Item: {item.type}")
    if item.title_style is not None:
        return item.title_style
    return title_style_from_legacy_item(item, project)


def compute_render_hash(style: TitleStyle, *, renderer_version: str = RENDERER_VERSION) -> str:
    payload = {
        "text": style.text,
        "timeline_width": style.timeline_width,
        "timeline_height": style.timeline_height,
        "fps": style.fps,
        "duration_sec": round(style.duration_sec, 4),
        "requested_font_family": style.requested_font_family,
        "resolved_font_family": style.resolved_font_family,
        "resolved_font_file_path": style.resolved_font_file_path,
        "resolved_font_face_index": int(style.resolved_font_face_index or 0),
        "font_size_px": round(style.font_size_px, 2),
        "font_color": style.font_color,
        "shadow_enabled": style.shadow_enabled,
        "shadow_color": style.shadow_color,
        "shadow_opacity": round(style.shadow_opacity, 4),
        "shadow_offset_x": round(style.shadow_offset_x, 2),
        "shadow_offset_y": round(style.shadow_offset_y, 2),
        "position": style.position,
        "margin_x": style.margin_x,
        "margin_y": style.margin_y,
        "fade_in_sec": round(style.fade_in_sec, 4),
        "fade_out_sec": round(style.fade_out_sec, 4),
        "render_format": style.render_format,
        "alpha_required": style.alpha_required,
        "renderer_version": renderer_version,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def opening_title_output_paths(work_dir: Path, section_id: str, render_hash: str) -> tuple[Path, Path, Path]:
    base = f"{section_id}_opening_title_{render_hash}"
    directory = generated_titles_dir(work_dir)
    return (
        directory / f"{base}.mov",
        directory / f"{base}.png",
        directory / f"{base}.render.json",
    )


def clamp_title_fades(
    duration_sec: float,
    fade_in_sec: float,
    fade_out_sec: float,
) -> tuple[float, float]:
    """Fade-In/Out auf Titel-Dauer begrenzen (Summe ≤ Dauer)."""
    duration = max(0.1, float(duration_sec))
    fade_in = max(0.0, float(fade_in_sec))
    fade_out = max(0.0, float(fade_out_sec))
    total = fade_in + fade_out
    if total > duration + 1e-9:
        scale = duration / total if total > 0 else 1.0
        fade_in *= scale
        fade_out *= scale
    return round(fade_in, 4), round(fade_out, 4)


def build_title_style_for_plan(
    *,
    text: str,
    project: Project,
    requested_font_family: str,
    duration_sec: float,
    font_size_px: float | None,
    shadow_enabled: bool = True,
    shadow_opacity: float = 0.5,
    shadow_offset_x: float = 3.0,
    shadow_offset_y: float = 3.0,
    position: str = OPENING_TITLE_POSITION_LOWER_THIRD,
    fade_in_sec: float = 0.35,
    fade_out_sec: float = 0.35,
) -> TitleStyle:
    """Erzeugt TitleStyle beim Schnittplan-Vorschlag (Regeln → Plan)."""
    font_res = resolve_title_font(requested_font_family)
    margin_x, margin_y = lower_third_margins(project.width, project.height)
    resolved_px = resolve_font_size_px(font_size_px, project.height)
    duration = round(max(0.1, float(duration_sec)), 4)
    fade_in, fade_out = clamp_title_fades(duration, fade_in_sec, fade_out_sec)
    style = TitleStyle(
        text=text,
        timeline_width=int(project.width),
        timeline_height=int(project.height),
        duration_sec=duration,
        fps=float(project.fps),
        requested_font_family=font_res.requested_font_family,
        resolved_font_family=font_res.resolved_font_family,
        resolved_font_file_path=font_res.resolved_font_file_path,
        resolved_font_face_index=int(font_res.resolved_font_face_index or 0),
        font_fallback_used=font_res.font_fallback_used,
        font_resolution_warning=font_res.font_resolution_warning,
        font_size_px=resolved_px,
        shadow_enabled=shadow_enabled,
        shadow_opacity=shadow_opacity,
        shadow_offset_x=shadow_offset_x,
        shadow_offset_y=shadow_offset_y,
        position=position,
        margin_x=margin_x,
        margin_y=margin_y,
        fade_in_sec=fade_in,
        fade_out_sec=fade_out,
    )
    render_hash = compute_render_hash(style)
    return style.model_copy(update={"render_hash": render_hash})


def attach_output_paths(style: TitleStyle, *, work_dir: Path, section_id: str) -> TitleStyle:
    render_hash = style.render_hash or compute_render_hash(style)
    mov_path, png_path, manifest_path = opening_title_output_paths(work_dir, section_id, render_hash)
    return style.model_copy(
        update={
            "render_hash": render_hash,
            "output_mov_path": str(mov_path),
            "output_png_path": str(png_path),
            "render_manifest_path": str(manifest_path),
        }
    )


def render_cache_valid(style: TitleStyle, manifest_path: Path) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if payload.get("render_hash") != style.render_hash:
        return False
    mov_path = Path(payload.get("output_mov_path", ""))
    if not path_is_readable_file(mov_path):
        return False
    if mov_path.suffix.lower() in {".jpg", ".jpeg"}:
        return False
    validation = payload.get("validation", {})
    return bool(validation.get("mov_exists")) and bool(validation.get("font_size_applied", True))


def measure_text_bbox(png_path: Path) -> tuple[int, int]:
    from PIL import Image

    image = Image.open(png_path).convert("RGBA")
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return 0, 0
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def validate_font_size_applied(
    *,
    font_size_px: float,
    bbox_height: int,
    previous_bbox_height: int | None = None,
) -> tuple[bool, str | None]:
    if bbox_height <= 0:
        return False, TITLE_FONT_SIZE_NOT_APPLIED
    expected_min = max(8, int(round(font_size_px * 0.45)))
    if bbox_height < expected_min:
        return False, TITLE_FONT_SIZE_NOT_APPLIED
    if previous_bbox_height is not None and abs(bbox_height - previous_bbox_height) < 2:
        if font_size_px >= 40:
            return False, TITLE_FONT_SIZE_NOT_APPLIED
    return True, None


def build_render_manifest(
    *,
    title_id: str,
    style: TitleStyle,
    renderer: str,
    text_bbox_width: int,
    text_bbox_height: int,
    font_size_applied: bool,
    mov_exists: bool,
    mov_has_alpha: bool,
) -> dict[str, Any]:
    return {
        "title_id": title_id,
        "text": style.text,
        "timeline_width": style.timeline_width,
        "timeline_height": style.timeline_height,
        "fps": style.fps,
        "duration_sec": style.duration_sec,
        "requested_font_family": style.requested_font_family,
        "resolved_font_family": style.resolved_font_family,
        "resolved_font_file_path": style.resolved_font_file_path,
        "resolved_font_face_index": int(style.resolved_font_face_index or 0),
        "font_fallback_used": style.font_fallback_used,
        "font_resolution_warning": style.font_resolution_warning,
        "font_size_px": style.font_size_px,
        "font_color": style.font_color,
        "shadow_enabled": style.shadow_enabled,
        "shadow_color": style.shadow_color,
        "shadow_opacity": style.shadow_opacity,
        "shadow_offset_x": style.shadow_offset_x,
        "shadow_offset_y": style.shadow_offset_y,
        "position": style.position,
        "margin_x": style.margin_x,
        "margin_y": style.margin_y,
        "fade_in_sec": style.fade_in_sec,
        "fade_out_sec": style.fade_out_sec,
        "render_format": style.render_format,
        "alpha_required": style.alpha_required,
        "render_hash": style.render_hash,
        "renderer": renderer,
        "renderer_version": RENDERER_VERSION,
        "output_mov_path": style.output_mov_path,
        "output_png_path": style.output_png_path,
        "text_bbox_width": text_bbox_width,
        "text_bbox_height": text_bbox_height,
        "validation": {
            "mov_exists": mov_exists,
            "mov_has_alpha": mov_has_alpha,
            "duration_matches": True,
            "font_size_applied": font_size_applied,
        },
    }
