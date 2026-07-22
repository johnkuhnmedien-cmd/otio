"""OTIO-Export ausschließlich aus der technisch aufgelösten Timeline (R1 fail-closed)."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.clean_media import resolve_effective_media_path
from otio_app.services.still_image_export_style import ensure_styled_still_for_export
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    is_http_url,
    list_export_ready_supplements,
    require_export_ready_local_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    assert_enhanced_work_root,
    exports_dir,
    resolved_timeline_path,
)


class EnhancedOtioExportError(RuntimeError):
    pass


def _assert_local_media_reference(path: str, *, label: str) -> str:
    text = str(path or "").strip()
    if not text:
        raise EnhancedOtioExportError(f"{label}: leere Medienreferenz.")
    if is_http_url(text):
        raise EnhancedOtioExportError(
            f"{label}: OTIO darf keine Web-URL enthalten ({text})."
        )
    if text.lower().startswith("http://") or text.lower().startswith("https://"):
        raise EnhancedOtioExportError(
            f"{label}: OTIO darf keine Web-URL enthalten ({text})."
        )
    local = Path(text).expanduser()
    if not local.is_file():
        raise EnhancedOtioExportError(f"{label}: lokale Datei fehlt: {local}")
    return str(local)


def _media_ref_for_asset(project: Project, asset_id: str) -> tuple[str, str]:
    """Returns (local_path, folder_name_for_still_cache)."""
    for folder in project.selected_asset_subdirs:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        for asset in getattr(inventory, "assets", []) or []:
            path = getattr(asset, "path", None) or getattr(asset, "source_path", None)
            if path is None:
                continue
            current_id = getattr(asset, "asset_id", None) or asset_id_for_path(str(path))
            if str(current_id) == asset_id:
                effective = resolve_effective_media_path(
                    project, folder, Path(str(path))
                )
                local = _assert_local_media_reference(
                    str(effective), label=f"Asset {asset_id}"
                )
                return local, folder

    def _supplement_ref(local_path: str) -> tuple[str, str]:
        source = Path(local_path)
        folder = (
            project.selected_asset_subdirs[0]
            if project.selected_asset_subdirs
            else "_supplemental"
        )
        for candidate_folder in list(project.selected_asset_subdirs) or [folder]:
            try:
                effective = resolve_effective_media_path(
                    project, candidate_folder, source
                )
            except Exception:  # noqa: BLE001
                effective = source
            if effective.is_file():
                local = _assert_local_media_reference(
                    str(effective), label=f"Supplement {asset_id}"
                )
                return local, candidate_folder
        local = _assert_local_media_reference(
            local_path, label=f"Supplement {asset_id}"
        )
        return local, folder

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        for supplement in accepted.supplements:
            if supplement.candidate_id != asset_id:
                continue
            try:
                local_path = require_export_ready_local_path(supplement)
            except Exception as exc:  # LocalMediaError
                raise EnhancedOtioExportError(str(exc)) from exc
            return _supplement_ref(local_path)

    for supplement in list_export_ready_supplements(project):
        if supplement.candidate_id == asset_id:
            return _supplement_ref(str(supplement.local_media_path))

    raise EnhancedOtioExportError(
        f"Supplement {asset_id} besitzt keine validierte lokale Mediendatei. "
        "Ordne zuerst eine lokale Originaldatei zu."
    )


def _media_path_for_asset(project: Project, asset_id: str) -> str:
    path, _folder = _media_ref_for_asset(project, asset_id)
    return path


def _export_media_path_for_asset(project: Project, asset_id: str) -> str:
    """Lokaler Pfad inkl. optionalem Still-Styling aus CutPlanOptions."""
    path, folder = _media_ref_for_asset(project, asset_id)
    options = load_cut_plan_options(project)
    styled = ensure_styled_still_for_export(
        project,
        folder or "_enhanced",
        Path(path),
        enabled=bool(options.still_image_style_enabled),
        zoom=float(options.still_image_zoom),
        background_style=str(options.still_image_background_style),
    )
    return _assert_local_media_reference(
        str(styled), label=f"Asset {asset_id} (export)"
    )


def _collect_target_urls(timeline: otio.schema.Timeline) -> list[str]:
    urls: list[str] = []
    for track in timeline.tracks:
        for item in track:
            media = getattr(item, "media_reference", None)
            if media is None:
                continue
            target = getattr(media, "target_url", None)
            if target:
                urls.append(str(target))
    return urls


def export_otio_from_resolved_timeline(
    project: Project,
    *,
    basename: str = "enhanced_timeline",
    allow_errors: bool = False,
) -> Path:
    """Exportiert die aufgelöste Timeline als OTIO.

    ``allow_errors=True`` ist ein Test-/Preview-Modus: vorhandene Resolve-Fehler
    blockieren nicht. Fehlende/ungültige Shots bleiben als OTIO-Gaps sichtbar.
    Produktions-Export bleibt fail-closed (``allow_errors=False``).
    """
    assert_enhanced_work_root(project)
    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is None:
        raise EnhancedOtioExportError("Aufgelöste Timeline fehlt — kein OTIO-Export.")
    if resolved.errors and not allow_errors:
        raise EnhancedOtioExportError(
            "Aufgelöste Timeline enthält Fehler: " + "; ".join(resolved.errors)
        )

    fps = resolved.fps or float(project.fps)
    timeline = otio.schema.Timeline(name=f"{project.name} enhanced")
    video_track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="Narration", kind=otio.schema.TrackKind.Audio)

    cursor = 0.0
    for shot in sorted(resolved.shots, key=lambda s: s.timeline_start_seconds):
        if shot.timeline_start_seconds > cursor + 1e-6:
            gap = shot.timeline_start_seconds - cursor
            video_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(gap * fps, fps),
                    )
                )
            )
        media_path = _export_media_path_for_asset(project, shot.asset_id)
        available = otio.schema.ExternalReference(target_url=media_path)
        duration = shot.timeline_end_seconds - shot.timeline_start_seconds
        clip = otio.schema.Clip(
            name=shot.shot_id,
            media_reference=available,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(
                    shot.source_start_seconds * fps, fps
                ),
                duration=otio.opentime.RationalTime(duration * fps, fps),
            ),
        )
        video_track.append(clip)
        cursor = shot.timeline_end_seconds

    audio_cursor = 0.0
    for segment in resolved.audio_segments:
        if segment.timeline_start_seconds > audio_cursor + 1e-6:
            gap = segment.timeline_start_seconds - audio_cursor
            audio_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(gap * fps, fps),
                    )
                )
            )
        audio_path = _assert_local_media_reference(
            segment.audio_path, label=f"Audio {segment.segment_id}"
        )
        duration = segment.timeline_end_seconds - segment.timeline_start_seconds
        clip = otio.schema.Clip(
            name=segment.segment_id,
            media_reference=otio.schema.ExternalReference(target_url=audio_path),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, fps),
                duration=otio.opentime.RationalTime(duration * fps, fps),
            ),
        )
        audio_track.append(clip)
        audio_cursor = segment.timeline_end_seconds
        if segment.pause_after_seconds > 0:
            audio_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(
                            segment.pause_after_seconds * fps, fps
                        ),
                    )
                )
            )
            audio_cursor += segment.pause_after_seconds

    timeline.tracks.append(video_track)
    timeline.tracks.append(audio_track)

    for url in _collect_target_urls(timeline):
        if is_http_url(url):
            raise EnhancedOtioExportError(
                f"OTIO enthält verbotene Web-URL als Medienreferenz: {url}"
            )

    out_dir = exports_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{basename}.otio"
    otio.adapters.write_to_file(timeline, str(out_path))
    return out_path
